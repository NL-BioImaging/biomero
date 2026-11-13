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
from typing import Any, Dict, List
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
# Releases whose history must stay in the suite: the first with event
# sourcing, the latest stable release recorded, and a 2.9 prerelease.
REQUIRED_VERSIONS = ("2.0.0", "2.8.3", "2.9.0b25")
CLAIM_OWNER = "replay-test-supervisor"
OTHER_OWNER = "other-supervisor"
CANCEL_REASON = "Cancelled by the replay test"

# View columns compared with the recorded rows. Timestamps differ per run.
# The workflow view's ``task`` (the task shown as current) is left out: the
# releases before 2.9 projected a different task as current than the present
# projection does when it rebuilds, so the recorded value is not reproducible.
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


def to_json_value(value: Any) -> Any:
    """Convert aggregate or row values to the JSON types of the fixtures.

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
    assert set(REQUIRED_VERSIONS) <= present


@pytest.fixture(params=HISTORY_FILES, ids=lambda path: path.stem)
def history(request) -> Dict:
    """One recorded history, as the recording script wrote it."""
    recorded = json.loads(request.param.read_text(encoding="utf-8"))
    assert recorded["format"] == HISTORY_FORMAT
    return recorded


@pytest.fixture
def replayed(history, tmp_path, monkeypatch):
    """The recorded events in a fresh SQLite store, opened by a client.

    Yields:
        SimpleNamespace-like dict: ``client`` (all four view listeners),
        ``tracker``, ``history`` and ``url``.
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
    for name in ("workflowTracker", "jobAccounting", "jobProgress",
                 "wfProgress", "workflowAnalytics"):
        getattr(client, name).close()
    client.close()
    if views._tracker is not None:
        views._tracker.close()
    views._tracker = None
    EngineManager.close_engine()


def rebuild_views(replayed: Dict) -> None:
    """Rebuild every view from the history, as the init script does."""
    replayed["client"].initialize_analytics_system(reset_tables=True)
    replayed["tracker"] = replayed["client"].workflowTracker


def workflow_id(replayed: Dict, label: str) -> UUID:
    return UUID(replayed["history"]["scenario"]["workflows"][label])


def task_id(replayed: Dict, label: str) -> UUID:
    return UUID(replayed["history"]["scenario"]["tasks"][label])


def supports(replayed: Dict, feature: str) -> bool:
    return feature in replayed["history"]["scenario"]["features"]


def view_rows(table_name: str) -> List[Dict]:
    """Rows of a view table, restricted to the compared columns."""
    model, columns = VIEW_MODELS[table_name]
    with EngineManager.get_session() as session:
        rows = [{column: to_json_value(getattr(row, column))
                 for column in columns}
                for row in session.query(model).all()]
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def recorded_view_rows(history: Dict, table_name: str) -> List[Dict]:
    columns = VIEW_MODELS[table_name][1]
    rows = [{column: row[column] for column in columns}
            for row in history["expected"]["views"][table_name]]
    return sorted(rows, key=lambda row: json.dumps(row, sort_keys=True))


def workflow_view_status(workflow: UUID) -> str:
    with EngineManager.get_session() as session:
        return session.query(WorkflowProgressView).filter_by(
            workflow_id=workflow).one().status


def test_events_load_in_recorded_order(replayed):
    notifications = replayed["tracker"].notification_log.select(
        start=1, limit=replayed["tracker"].notification_log.section_size)
    recorded = replayed["history"]["events"]
    assert notifications[0].topic == recorded[0]["topic"]
    page_size = len(notifications)
    assert [n.topic for n in notifications] == \
        [e["topic"] for e in recorded[:page_size]]


def test_every_aggregate_replays_to_the_recorded_state(replayed):
    tracker = replayed["tracker"]
    aggregates = replayed["history"]["expected"]["aggregates"]
    assert aggregates
    for aggregate_id, recorded in aggregates.items():
        aggregate = tracker.repository.get(UUID(aggregate_id))
        assert type(aggregate).__name__ == recorded["class"]
        assert aggregate.version == recorded["version"]
        for name, recorded_value in recorded["state"].items():
            attribute = "_" + name if name in (
                "id", "version", "created_on", "modified_on") else name
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
    assert tracker.is_workflow_finished(workflow_id(replayed, "completed"))
    assert tracker.is_workflow_finished(workflow_id(replayed, "failed"))
    assert not tracker.is_workflow_finished(workflow_id(replayed, "running"))
    completed_run = tracker.repository.get(task_id(replayed, "completed.run"))
    assert completed_run.completed and not completed_run.failed
    failed_run = tracker.repository.get(task_id(replayed, "failed.run"))
    assert failed_run.failed and not failed_run.completed
    running_run = tracker.repository.get(task_id(replayed, "running.run"))
    assert not running_run.completed and not running_run.failed


@pytest.mark.parametrize("table_name", sorted(VIEW_MODELS))
def test_views_rebuild_with_the_recorded_rows(replayed, table_name):
    rebuild_views(replayed)
    assert view_rows(table_name) == \
        recorded_view_rows(replayed["history"], table_name)


def test_running_workflow_can_still_complete(replayed):
    rebuild_views(replayed)
    tracker, running = replayed["tracker"], workflow_id(replayed, "running")
    version_before = tracker.repository.get(running).version

    tracker.complete_workflow(running)

    assert tracker.repository.get(running).version == version_before + 1
    assert tracker.is_workflow_finished(running)
    assert workflow_view_status(running) == wfs.DONE


def test_running_workflow_can_still_fail(replayed):
    rebuild_views(replayed)
    tracker, running = replayed["tracker"], workflow_id(replayed, "running")

    tracker.fail_workflow(running, "failed after replay")

    assert workflow_view_status(running) == wfs.FAILED


def test_a_finished_workflow_refuses_a_second_final_event(replayed):
    tracker = replayed["tracker"]
    completed = workflow_id(replayed, "completed")
    version_before = tracker.repository.get(completed).version

    tracker.fail_workflow(completed, "too late")
    tracker.complete_workflow(completed)

    assert tracker.repository.get(completed).version == version_before


def test_old_workflow_accepts_a_cancel_request(replayed):
    tracker, running = replayed["tracker"], workflow_id(replayed, "running")
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
    if not supports(replayed, "claimed_launcher"):
        pytest.skip("this release has no detached launcher")
    tracker = replayed["tracker"]
    launcher_id = task_id(replayed, "claimed.launcher")
    claimed_workflow = workflow_id(replayed, "claimed")
    assert tracker.find_launcher_task(claimed_workflow).id == launcher_id
    assert tracker.find_launcher_task(workflow_id(replayed, "running")) is None

    # Whatever the release recorded as its claim, the launcher is claimable.
    assert tracker.claim_launcher_task(launcher_id, CLAIM_OWNER) is True
    assert tracker.claim_launcher_task(launcher_id, OTHER_OWNER) is False
    assert tracker.repository.get(launcher_id).claimed_by == CLAIM_OWNER
    assert tracker.release_launcher_task(launcher_id, CLAIM_OWNER) is True
    assert tracker.repository.get(launcher_id).claimed_by is None


def test_old_workflow_accepts_new_tasks_and_events(replayed):
    rebuild_views(replayed)
    tracker, running = replayed["tracker"], workflow_id(replayed, "running")
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
    with EngineManager.get_session() as session:
        assert session.query(JobProgressView).filter_by(
            slurm_job_id=1300).one().progress == "10%"


def test_old_workflow_accepts_a_canonical_input_snapshot(replayed):
    tracker, running = replayed["tracker"], workflow_id(replayed, "running")
    assert tracker.get_canonical_inputs(running)["inputs"] == []

    tracker.record_canonical_inputs(
        running, task_id(replayed, "running.transfer"), [])

    snapshot = tracker.get_canonical_inputs(running)
    assert snapshot["export_task_id"] == task_id(replayed, "running.transfer")


def test_metadata_refresh_requests_replay_and_resume(replayed):
    if not supports(replayed, "metadata_refresh"):
        pytest.skip("this release has no metadata maintenance requests")
    tracker = replayed["tracker"]
    refreshes = replayed["history"]["scenario"]["metadata_refreshes"]
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
