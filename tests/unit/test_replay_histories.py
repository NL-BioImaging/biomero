# -*- coding: utf-8 -*-
# Copyright 2024 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Event histories written by released versions must keep replaying.

Every install keeps the events of the release it ran. Each fixture in
``tests/replay/histories`` is the event store of one released version,
recorded by ``tests/replay/record_history.py`` with that version's own code.
These tests load it into a fresh SQLite store and check, with the current
code, that the aggregates replay to the recorded state, that the views
rebuild from it, and that today's operations work on the old aggregates.

A failure here means the change breaks installs that upgrade. Do not edit a
fixture to make it pass: fix the code (new event classes and attributes only,
defaults for new parameters, initialised attributes), or record the change as
a migration. See ``docs/developer/eventsourcing.rst``.
"""
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Tuple
from uuid import UUID

import pytest
from eventsourcing.persistence import StoredEvent

from biomero import SlurmClient
from biomero.constants import workflow_status as wfs
from biomero.database import (
    EngineManager,
    JobProgressView,
    JobView,
    TaskExecution,
    WorkflowProgressView,
)
from biomero.eventsourcing import WorkflowCancelled, WorkflowTracker
from biomero.maintenance import (
    metadata_refresh_statuses,
    pending_metadata_refreshes,
)
import biomero.views as views

HISTORIES_DIR = Path(__file__).resolve().parents[1] / "replay" / "histories"
HISTORY_FILES = sorted(HISTORIES_DIR.glob("*.json"))
HISTORY_FORMAT = 1

# The features each fixture must have recorded. A recording that silently
# lacks one (the script discovers them from the release) must fail here, not
# skip its tests. A new fixture needs an entry: the first release with event
# sourcing, the latest stable release of the older library era, and a 2.9
# prerelease; later releases add theirs, for example the cancel request.
REQUIRED_FEATURES: Dict[str, FrozenSet[str]] = {
    "2.0.0": frozenset(),
    "2.8.3": frozenset(),
    "2.9.0b25": frozenset({"storage_provenance", "canonical_inputs",
                           "claimed_launcher", "metadata_refresh"}),
}
CLAIM_OWNER = "replay-test-supervisor"
OTHER_OWNER = "other-supervisor"
CANCEL_REASON = "Cancelled by the replay test"
FEATURE_CLAIMED_LAUNCHER = "claimed_launcher"
FEATURE_METADATA_REFRESH = "metadata_refresh"
LISTENERS = ("workflowTracker", "jobAccounting", "jobProgress", "wfProgress",
             "workflowAnalytics")

# Workflow labels of the recording script (tests/replay/record_history.py).
# A task label is "<workflow label>.<role>".
WF_COMPLETED = "completed"
WF_FAILED = "failed"
WF_RUNNING = "running"
WF_CLAIMED = "claimed"
WF_BATCHED = "batched"
FINAL_WORKFLOW_STATUSES = (wfs.DONE, wfs.FAILED)

# The recording script writes aggregate bookkeeping under public names; this
# maps them back to the aggregate attributes.
AGGREGATE_ATTRIBUTE_NAMES = {
    "id": "_id", "version": "_version", "created_on": "_created_on",
    "modified_on": "_modified_on"}

# View columns compared with the recorded rows. Timestamps differ per run.
VIEW_MODELS = {
    "biomero_job_view": (JobView, ("slurm_job_id", "user", "group", "task_id")),
    "biomero_job_progress_view": (
        JobProgressView, ("slurm_job_id", "status", "progress")),
    "biomero_workflow_progress_view": (
        WorkflowProgressView,
        ("workflow_id", "status", "progress", "user", "group", "name",
         "main_task_name")),
    "biomero_task_execution": (
        TaskExecution,
        ("task_id", "task_name", "task_version", "status", "error_type")),
}
# The task view's user_id and group_id are not compared: while a release
# projects live, a task that gets no event after being added keeps them empty
# (recorded as None), and a rebuild fills them from the workflow.
# The workflow view's ``task`` (the task shown as current) is compared for
# finished workflows only. Since 2.9.0-beta.6 the row is written when a task
# is added, so an unfinished workflow shows the last added task, where
# earlier releases showed the last started one (a harmless display change);
# test_unfinished_workflows_show_their_last_task pins the current behaviour.
WORKFLOW_VIEW = "biomero_workflow_progress_view"
FINISHED_ONLY_COLUMN = "task"
# Rows that the current projection deliberately shows differently than the
# release that recorded them, by version and workflow label. Releases before
# 2.9 do not know the batched launcher, so their view left such a workflow
# INITIALIZING; the rebuild now shows the batched run as RUNNING.
PROJECTION_CHANGES: Dict[str, Dict[str, Dict[str, Any]]] = {
    version: {WF_BATCHED: {
        "status": wfs.RUNNING, "progress": "50%",
        "main_task_name": "SLURM_Run_Workflow_Batched.py"}}
    for version in ("2.0.0", "2.8.3")}


def to_json_value(value: Any) -> Any:
    """Convert aggregate or row values to the JSON types of the fixtures.

    This duplicates the function of the recording script on purpose: the
    script runs inside the virtual environment of an old release and must not
    import this repository, and the tests must not import a script that runs
    pip.

    Args:
        value (Any): A value read from the current code.

    Returns:
        Any: The value with UUIDs and datetimes as strings.
    """
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): to_json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_value(item) for item in value]
    return value


def test_the_required_histories_are_present():
    # A missing fixture must fail, not silently remove the parametrised tests.
    present = {path.stem for path in HISTORY_FILES}
    assert set(REQUIRED_FEATURES) <= present
    assert present <= set(REQUIRED_FEATURES), "add REQUIRED_FEATURES entry"


@pytest.fixture(params=HISTORY_FILES, ids=lambda path: path.stem)
def history(request) -> Dict:
    """One recorded history, as the recording script wrote it."""
    recorded = json.loads(request.param.read_text(encoding="utf-8"))
    assert recorded["format"] == HISTORY_FORMAT
    assert set(recorded["scenario"]["features"]) == \
        REQUIRED_FEATURES[recorded["biomero_version"]]
    return recorded


@pytest.fixture
def replayed(history, tmp_path, monkeypatch):
    """The recorded events in a fresh SQLite store, opened by a client.

    Yields:
        dict: ``client`` (all four view listeners), ``tracker``, ``history``
        and ``url``.
    """
    url = f"sqlite:///{(tmp_path / 'replay.sqlite').as_posix()}"
    monkeypatch.setenv("SQLALCHEMY_URL", url)
    monkeypatch.setenv("PERSISTENCE_MODULE", "eventsourcing_sqlalchemy")
    monkeypatch.setattr(SlurmClient, "validate", lambda *a, **kw: None)
    monkeypatch.setattr(SlurmClient, "init_workflows", lambda self: None)
    EngineManager.close_engine()
    if views._tracker is not None:
        views._tracker.close()
    views._tracker = None

    loader = WorkflowTracker()
    loader.recorder.insert_events([
        StoredEvent(originator_id=UUID(event["originator_id"]),
                    originator_version=event["originator_version"],
                    topic=event["topic"],
                    state=json.dumps(event["state"]).encode("utf-8"))
        for event in history["events"]])
    loader.close()

    client = SlurmClient(host="localhost", init_slurm=False,
                         track_workflows=True, enable_job_progress=True,
                         enable_job_accounting=True,
                         enable_workflow_analytics=True, sqlalchemy_url=url)
    yield {"client": client, "tracker": client.workflowTracker,
           "history": history, "url": url}
    close_listeners(client)
    client.close()
    if views._tracker is not None:
        views._tracker.close()
    views._tracker = None
    EngineManager.close_engine()


def close_listeners(client: SlurmClient) -> None:
    """Close the tracker and the four view listeners of a client."""
    for name in LISTENERS:
        getattr(client, name).close()


def rebuild_views(replayed: Dict) -> None:
    """Rebuild every view from the history, as the init script does."""
    replayed["client"].initialize_analytics_system(reset_tables=True)
    replayed["tracker"] = replayed["client"].workflowTracker


def workflow_id(replayed: Dict, label: str) -> UUID:
    return UUID(replayed["history"]["scenario"]["workflows"][label])


def task_id(replayed: Dict, label: str) -> UUID:
    return UUID(replayed["history"]["scenario"]["tasks"][label])


def has_feature(replayed: Dict, feature: str) -> bool:
    """Whether this fixture must contain the feature; checks both ways."""
    required = feature in REQUIRED_FEATURES[
        replayed["history"]["biomero_version"]]
    assert (feature in replayed["history"]["scenario"]["features"]) == required
    return required


def compared_columns(table_name: str, row: Dict) -> Tuple[str, ...]:
    """The columns of a view row that must match the recorded row."""
    columns = VIEW_MODELS[table_name][1]
    if table_name == WORKFLOW_VIEW and row["status"] in FINAL_WORKFLOW_STATUSES:
        return columns + (FINISHED_ONLY_COLUMN,)
    return columns


def comparable_rows(table_name: str, rows: List[Dict]) -> List[Dict]:
    reduced = [{column: row[column]
                for column in compared_columns(table_name, row)}
               for row in rows]
    return sorted(reduced, key=lambda row: json.dumps(row, sort_keys=True))


def recorded_rows(replayed: Dict, table_name: str) -> List[Dict]:
    """The recorded rows of a view, with the deliberate changes applied."""
    history = replayed["history"]
    rows = [dict(row) for row in history["expected"]["views"][table_name]]
    changes = PROJECTION_CHANGES.get(history["biomero_version"], {})
    if table_name == WORKFLOW_VIEW:
        for label, columns in changes.items():
            workflow = str(workflow_id(replayed, label))
            for row in rows:
                if row["workflow_id"] == workflow:
                    row.update(columns)
    return rows


def view_rows(table_name: str) -> List[Dict]:
    """All rows of a view table, with every column."""
    model, columns = VIEW_MODELS[table_name]
    if table_name == WORKFLOW_VIEW:
        columns += (FINISHED_ONLY_COLUMN,)
    with EngineManager.get_session() as session:
        return [{column: to_json_value(getattr(row, column))
                 for column in columns}
                for row in session.query(model).all()]


def workflow_view_status(workflow: UUID) -> str:
    with EngineManager.get_session() as session:
        return session.query(WorkflowProgressView).filter_by(
            workflow_id=workflow).one().status


def job_view_row(job_id: int) -> JobProgressView:
    with EngineManager.get_session() as session:
        return session.query(JobProgressView).filter_by(
            slurm_job_id=job_id).one()


def test_events_load_in_recorded_order(replayed):
    log = replayed["tracker"].notification_log
    topics: List[str] = []
    start = 1
    while True:
        notifications = log.select(start=start, limit=log.section_size)
        topics += [notification.topic for notification in notifications]
        if len(notifications) < log.section_size:
            break
        start = notifications[-1].id + 1
    assert topics == [event["topic"] for event in replayed["history"]["events"]]


def test_every_aggregate_replays_to_the_recorded_state(replayed):
    tracker = replayed["tracker"]
    aggregates = replayed["history"]["expected"]["aggregates"]
    assert aggregates
    for aggregate_id, recorded in aggregates.items():
        aggregate = tracker.repository.get(UUID(aggregate_id))
        assert type(aggregate).__name__ == recorded["class"]
        assert aggregate.version == recorded["version"]
        for name, recorded_value in recorded["state"].items():
            attribute = AGGREGATE_ATTRIBUTE_NAMES.get(name, name)
            assert to_json_value(getattr(aggregate, attribute)) == \
                recorded_value, f"{recorded['class']}.{name} of {aggregate_id}"


def test_every_aggregate_replays_at_each_of_its_versions(replayed):
    tracker = replayed["tracker"]
    # Aggregates start at version 0 or 1, depending on their INITIAL_VERSION.
    first_versions: Dict[str, int] = {}
    for event in replayed["history"]["events"]:
        first_versions.setdefault(event["originator_id"],
                                  event["originator_version"])
    for aggregate_id, recorded in \
            replayed["history"]["expected"]["aggregates"].items():
        for version in range(first_versions[aggregate_id],
                             recorded["version"] + 1):
            aggregate = tracker.repository.get(UUID(aggregate_id),
                                               version=version)
            assert aggregate.version == version


def test_lifecycle_flags_follow_the_old_events(replayed):
    tracker = replayed["tracker"]
    assert tracker.is_workflow_finished(workflow_id(replayed, WF_COMPLETED))
    assert tracker.is_workflow_finished(workflow_id(replayed, WF_FAILED))
    assert not tracker.is_workflow_finished(workflow_id(replayed, WF_RUNNING))
    completed_run = tracker.repository.get(task_id(replayed, "completed.run"))
    assert completed_run.completed and not completed_run.failed
    failed_run = tracker.repository.get(task_id(replayed, "failed.run"))
    assert failed_run.failed and not failed_run.completed
    running_run = tracker.repository.get(task_id(replayed, "running.run"))
    assert not running_run.completed and not running_run.failed


def test_views_rebuild_with_the_recorded_rows(replayed):
    rebuild_views(replayed)
    for table_name in VIEW_MODELS:
        recorded = recorded_rows(replayed, table_name)
        assert comparable_rows(table_name, view_rows(table_name)) == \
            comparable_rows(table_name, recorded), table_name


def test_unfinished_workflows_show_their_last_task(replayed):
    rebuild_views(replayed)
    tracker = replayed["tracker"]
    unfinished = [row for row in view_rows(WORKFLOW_VIEW)
                  if row["status"] not in FINAL_WORKFLOW_STATUSES]
    assert unfinished
    for row in unfinished:
        workflow = tracker.repository.get(UUID(row["workflow_id"]))
        last_task = tracker.repository.get(workflow.tasks[-1])
        assert row["task"] == last_task.task_name


def test_listeners_restarted_on_existing_views_keep_projecting(replayed):
    # The upgrade path: a new process starts against view rows that exist
    # already, and its listeners have to resolve old aggregates on their own.
    rebuild_views(replayed)
    client = replayed["client"]
    close_listeners(client)
    views._tracker = None
    client.initialize_analytics_system(reset_tables=False)
    tracker = client.workflowTracker
    running_run = task_id(replayed, "running.run")
    job_id = tracker.repository.get(running_run).job_ids[0]

    tracker.update_task_status(running_run, "COMPLETED")

    assert workflow_view_status(workflow_id(replayed, WF_RUNNING)) == \
        wfs.JOB_STATUS + "COMPLETED"
    # The job listener resolves the job of a task it has not seen from the
    # tracker, so the job registered before the restart is projected too and
    # keeps its stored progress.
    row = job_view_row(job_id)
    assert row.status == "COMPLETED"
    assert row.progress == "43%"


def test_running_workflow_can_still_complete(replayed):
    rebuild_views(replayed)
    tracker = replayed["tracker"]
    running = workflow_id(replayed, WF_RUNNING)
    version_before = tracker.repository.get(running).version

    tracker.complete_workflow(running)

    assert tracker.repository.get(running).version == version_before + 1
    assert tracker.is_workflow_finished(running)
    assert workflow_view_status(running) == wfs.DONE


def test_running_workflow_can_still_fail(replayed):
    rebuild_views(replayed)
    tracker = replayed["tracker"]
    running = workflow_id(replayed, WF_RUNNING)

    tracker.fail_workflow(running, "failed after replay")

    assert workflow_view_status(running) == wfs.FAILED


def test_a_finished_workflow_refuses_a_second_final_event(replayed):
    tracker = replayed["tracker"]
    completed = workflow_id(replayed, WF_COMPLETED)
    version_before = tracker.repository.get(completed).version

    tracker.fail_workflow(completed, "too late")
    tracker.complete_workflow(completed)

    assert tracker.repository.get(completed).version == version_before


def test_old_workflow_accepts_a_cancel_request(replayed):
    tracker = replayed["tracker"]
    running = workflow_id(replayed, WF_RUNNING)
    assert not tracker.is_workflow_cancel_requested(running)
    tracker.raise_if_cancel_requested(running)

    tracker.request_workflow_cancel(running, CANCEL_REASON)
    version_after_request = tracker.repository.get(running).version
    tracker.request_workflow_cancel(running, CANCEL_REASON)

    assert tracker.is_workflow_cancel_requested(running)
    assert tracker.repository.get(running).version == version_after_request
    with pytest.raises(WorkflowCancelled, match=CANCEL_REASON):
        tracker.raise_if_cancel_requested(running)
    tracker.fail_workflow(running, CANCEL_REASON)
    assert tracker.is_workflow_finished(running)


def test_old_task_cannot_be_claimed_as_launcher(replayed):
    tracker = replayed["tracker"]
    with pytest.raises(ValueError, match="not a launcher"):
        tracker.claim_launcher_task(task_id(replayed, "running.run"),
                                    CLAIM_OWNER)


def test_old_launcher_can_be_found_claimed_and_released(replayed):
    scenario = replayed["history"]["scenario"]
    if not has_feature(replayed, FEATURE_CLAIMED_LAUNCHER):
        assert WF_CLAIMED not in scenario["workflows"]
        return
    tracker = replayed["tracker"]
    launcher_id = task_id(replayed, "claimed.launcher")
    claimed_workflow = workflow_id(replayed, WF_CLAIMED)
    assert tracker.find_launcher_task(claimed_workflow).id == launcher_id
    assert tracker.find_launcher_task(
        workflow_id(replayed, WF_RUNNING)) is None

    # Whatever the release recorded as its claim, the launcher is claimable.
    assert tracker.claim_launcher_task(launcher_id, CLAIM_OWNER) is True
    assert tracker.claim_launcher_task(launcher_id, OTHER_OWNER) is False
    assert tracker.repository.get(launcher_id).claimed_by == CLAIM_OWNER
    assert tracker.release_launcher_task(launcher_id, CLAIM_OWNER) is True
    assert tracker.repository.get(launcher_id).claimed_by is None


def test_old_workflow_accepts_new_tasks_and_events(replayed):
    rebuild_views(replayed)
    tracker = replayed["tracker"]
    running = workflow_id(replayed, WF_RUNNING)
    tasks_before = len(tracker.repository.get(running).tasks)

    new_task = tracker.add_task_to_workflow(
        running, "SLURM_Get_Results.py", "1.0", {"Image": [301]}, {})
    tracker.start_task(new_task)
    tracker.add_job_id(new_task, 1300)
    tracker.update_task_status(new_task, "RUNNING")
    tracker.update_task_progress(new_task, "10%")
    tracker.complete_task(new_task, "Imported")
    tracker.record_storage_provenance(
        task_id(replayed, "running.run"), "Plate:10", {"storage": "zarr"})

    assert len(tracker.repository.get(running).tasks) == tasks_before + 1
    assert tracker.repository.get(new_task).completed
    old_task = tracker.repository.get(task_id(replayed, "running.run"))
    assert old_task.storage_provenance["Plate:10"] == {"storage": "zarr"}
    assert job_view_row(1300).progress == "10%"


def test_old_workflow_accepts_a_canonical_input_snapshot(replayed):
    tracker = replayed["tracker"]
    running = workflow_id(replayed, WF_RUNNING)
    assert tracker.get_canonical_inputs(running)["inputs"] == []

    tracker.record_canonical_inputs(
        running, task_id(replayed, "running.transfer"), [])

    snapshot = tracker.get_canonical_inputs(running)
    assert snapshot["export_task_id"] == task_id(replayed, "running.transfer")


def test_metadata_refresh_requests_replay_and_resume(replayed):
    scenario = replayed["history"]["scenario"]
    if not has_feature(replayed, FEATURE_METADATA_REFRESH):
        assert not scenario["metadata_refreshes"]
        return
    tracker = replayed["tracker"]
    refreshes = scenario["metadata_refreshes"]
    running, finished = UUID(refreshes["running"]), UUID(refreshes["finished"])

    assert pending_metadata_refreshes(tracker)[1] == {running}
    statuses = {row["request_id"]: row["status"]
                for row in metadata_refresh_statuses(tracker)}
    assert statuses == {str(running): "RUNNING", str(finished): "DONE"}

    request = tracker.repository.get(running)
    request.finished({"counts": {"failed": 0}})
    tracker.save(request)
    tracker.commit_open_transaction()
    assert pending_metadata_refreshes(tracker)[1] == set()
