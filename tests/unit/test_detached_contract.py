"""The mechanisms core offers a detached supervisor: marker, lookup, claim.

Scripts decide what a launcher task holds and how a run resumes, and the
processor decides what to run; these tests pin what both rely on.
"""
from datetime import datetime, timedelta, timezone
import logging
import os
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from eventsourcing.utils import get_topic

from biomero import constants
from biomero.database import EngineManager
from biomero.eventsourcing import (Task, WorkflowRun, WorkflowTracker,
                                   is_launcher_task)

LAUNCHER_PARAMS = {constants.LAUNCHER_MARKER: "v1"}


@pytest.fixture(autouse=True)
def tracking_database(tmp_path):
    """One database file, so independent trackers see each other's events."""
    os.environ["PERSISTENCE_MODULE"] = "eventsourcing_sqlalchemy"
    os.environ["SQLALCHEMY_URL"] = f"sqlite:///{(tmp_path / 'wf.db').as_posix()}"
    EngineManager.create_scoped_session()
    yield
    del os.environ["PERSISTENCE_MODULE"]
    del os.environ["SQLALCHEMY_URL"]
    EngineManager.close_engine()


@pytest.fixture
def tracker():
    return WorkflowTracker()


@pytest.fixture
def launcher(tracker):
    """A queued workflow: (workflow id, launcher task id)."""
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)
    task_id = tracker.add_task_to_workflow(
        workflow_id, constants.RUN_WF_SCRIPT, "v1", [], dict(LAUNCHER_PARAMS))
    return workflow_id, task_id


def claim_events(tracker, task_id):
    return [item for item in tracker.recorder.select_events(task_id)
            if item.topic == get_topic(Task.LauncherClaimed)]


# ------------------------- the launcher rule ------------------------- #

@pytest.mark.parametrize("task_name, params, expected", [
    (constants.RUN_WF_SCRIPT, LAUNCHER_PARAMS, True),
    (constants.RUN_WF_BATCHED_SCRIPT, LAUNCHER_PARAMS, True),
    # One ordinary task per batch carries the launching script's name.
    (constants.RUN_WF_BATCHED_SCRIPT, {"batch_index": 0}, False),
    (constants.RUN_WF_SCRIPT, {}, False),
    (constants.RUN_WF_SCRIPT, None, False),
    ("some-workflow", LAUNCHER_PARAMS, False),
])
def test_launcher_rule_needs_script_name_and_marker(task_name, params,
                                                    expected):
    assert is_launcher_task(
        SimpleNamespace(task_name=task_name, params=params)) is expected


def test_launcher_rule_reads_aggregates_and_events_alike(tracker, launcher):
    _, task_id = launcher
    created_event = tracker.recorder.select_events(task_id)[0]
    task = tracker.repository.get(task_id)
    assert is_launcher_task(task)
    # The projections apply the rule to the event they are handed.
    assert is_launcher_task(SimpleNamespace(
        task_name=task.task_name, params=task.params))
    assert created_event.topic == get_topic(Task.TaskCreated)


# ---------------------------- task lookup ---------------------------- #

def test_launcher_is_found_however_many_tasks_precede_it(tracker):
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)
    for index in range(15):
        tracker.add_task_to_workflow(workflow_id, f"step-{index}", "v1", [],
                                     {})
    launcher_id = tracker.add_task_to_workflow(
        workflow_id, constants.RUN_WF_SCRIPT, "v1", [], dict(LAUNCHER_PARAMS))
    assert tracker.find_launcher_task(workflow_id).id == launcher_id


def test_workflow_without_launcher_has_none(tracker):
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)
    tracker.add_task_to_workflow(workflow_id, "step", "v1", [], {})
    assert tracker.find_launcher_task(workflow_id) is None


def test_read_error_is_raised_not_reported_as_no_launcher(tracker, launcher):
    workflow_id, _ = launcher
    with patch.object(tracker.repository, "get",
                      side_effect=RuntimeError("database down")):
        with pytest.raises(RuntimeError, match="database down"):
            tracker.find_launcher_task(workflow_id)


# ------------------------------- claim ------------------------------- #

def test_claim_is_recorded_as_event_and_not_as_status(tracker, launcher):
    _, task_id = launcher
    assert tracker.claim_launcher_task(task_id, "supervisor-a")
    task = tracker.repository.get(task_id)
    assert task.claimed_by == "supervisor-a"
    assert task.claimed_at is not None
    assert task.status is None
    assert len(claim_events(tracker, task_id)) == 1


def test_only_one_of_two_supervisors_wins_the_same_launcher(tracker, launcher):
    """Both read the unclaimed task; the event store accepts one claim."""
    _, task_id = launcher
    other_supervisor = WorkflowTracker()
    unclaimed_view = other_supervisor.repository.get(task_id)
    assert tracker.claim_launcher_task(task_id, "supervisor-a")
    with patch.object(other_supervisor.repository, "get",
                      return_value=unclaimed_view):
        assert other_supervisor.claim_launcher_task(
            task_id, "supervisor-b") is False
    assert tracker.repository.get(task_id).claimed_by == "supervisor-a"
    assert len(claim_events(tracker, task_id)) == 1


def test_lost_race_is_logged(tracker, launcher, caplog):
    _, task_id = launcher
    other_supervisor = WorkflowTracker()
    unclaimed_view = other_supervisor.repository.get(task_id)
    tracker.claim_launcher_task(task_id, "supervisor-a")
    with patch.object(other_supervisor.repository, "get",
                      return_value=unclaimed_view):
        with caplog.at_level(logging.INFO, logger="biomero.eventsourcing"):
            other_supervisor.claim_launcher_task(task_id, "supervisor-b")
    assert any(record.levelno == logging.INFO
               and "Lost the claim" in record.getMessage()
               for record in caplog.records)


def test_live_claim_of_another_owner_is_respected(tracker, launcher):
    _, task_id = launcher
    tracker.claim_launcher_task(task_id, "supervisor-a")
    assert tracker.claim_launcher_task(task_id, "supervisor-b") is False
    assert tracker.repository.get(task_id).claimed_by == "supervisor-a"


def test_owner_renews_its_own_claim(tracker, launcher):
    _, task_id = launcher
    tracker.claim_launcher_task(task_id, "supervisor-a")
    first_claim = tracker.repository.get(task_id).claimed_at
    assert tracker.claim_launcher_task(task_id, "supervisor-a")
    assert tracker.repository.get(task_id).claimed_at > first_claim


def test_expired_claim_can_be_taken_over(tracker, launcher):
    _, task_id = launcher
    tracker.claim_launcher_task(task_id, "supervisor-a")
    lease = constants.LAUNCHER_CLAIM_LEASE_SECONDS
    later = datetime.now(timezone.utc) + timedelta(seconds=lease + 1)
    with patch("biomero.eventsourcing.datetime") as clock:
        clock.now.return_value = later
        assert tracker.claim_launcher_task(task_id, "supervisor-b")
    assert tracker.repository.get(task_id).claimed_by == "supervisor-b"


def test_lease_length_is_a_parameter(tracker, launcher):
    _, task_id = launcher
    tracker.claim_launcher_task(task_id, "supervisor-a")
    assert tracker.claim_launcher_task(task_id, "supervisor-b",
                                       lease_seconds=0)


def test_release_hands_the_run_over_at_once(tracker, launcher):
    _, task_id = launcher
    tracker.claim_launcher_task(task_id, "supervisor-a")
    assert tracker.release_launcher_task(task_id, "supervisor-b") is False
    assert tracker.repository.get(task_id).claimed_by == "supervisor-a"
    assert tracker.release_launcher_task(task_id, "supervisor-a")
    assert tracker.repository.get(task_id).claimed_by is None
    assert tracker.claim_launcher_task(task_id, "supervisor-b")


def test_only_a_launcher_can_be_claimed(tracker):
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)
    task_id = tracker.add_task_to_workflow(
        workflow_id, constants.RUN_WF_SCRIPT, "v1", [], {})
    with pytest.raises(ValueError, match="not a launcher"):
        tracker.claim_launcher_task(task_id, "supervisor-a")


def test_launcher_claimed_by_a_2_9_beta_can_be_claimed_again(
        tracker, launcher):
    """The betas wrote status CLAIMED and no owner; nobody holds that."""
    _, task_id = launcher
    tracker.update_task_status(task_id, constants.LEGACY_CLAIMED_STATUS)
    task = tracker.repository.get(task_id)
    assert task.claimed_by is None
    assert tracker.claim_launcher_task(task_id, "supervisor-a")


def test_stored_event_names_are_pinned():
    assert get_topic(Task.LauncherClaimed) == (
        "biomero.eventsourcing:Task.LauncherClaimed")
    assert get_topic(Task.LauncherReleased) == (
        "biomero.eventsourcing:Task.LauncherReleased")
    assert get_topic(WorkflowRun.CancelRequested) == (
        "biomero.eventsourcing:WorkflowRun.CancelRequested")


# ----------------------------- cancellation ----------------------------- #

def test_cancel_request_is_durable_and_idempotent(tracker, launcher):
    workflow_id, _ = launcher
    assert tracker.is_workflow_cancel_requested(workflow_id) is False
    tracker.request_workflow_cancel(workflow_id, "user asked")
    tracker.request_workflow_cancel(workflow_id, "asked twice")
    cold_reader = WorkflowTracker()
    assert cold_reader.is_workflow_cancel_requested(workflow_id) is True
    workflow = cold_reader.repository.get(workflow_id)
    assert workflow.cancel_reason == "user asked"
    cancel_events = [item for item in tracker.recorder.select_events(
        workflow_id) if item.topic == get_topic(WorkflowRun.CancelRequested)]
    assert len(cancel_events) == 1


# ------------------------- detached-mode setting ------------------------- #

@pytest.mark.parametrize("value, expected", [
    (None, False), ("", False), ("false", False), ("0", False),
    ("garbage", False), ("true", True), ("TRUE", True), (" yes ", True),
    ("1", True), ("on", True), ("y", True),
])
def test_detached_mode_setting_has_one_reader(monkeypatch, value, expected):
    from biomero.detached import detached_mode_enabled
    name = constants.slurm_env.BIOMERO_DETACHED_WORKFLOWS
    if value is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, value)
    assert detached_mode_enabled() is expected


def test_detached_mode_decision_is_logged_at_debug(monkeypatch, caplog):
    from biomero.detached import detached_mode_enabled
    monkeypatch.delenv(constants.slurm_env.BIOMERO_DETACHED_WORKFLOWS,
                       raising=False)
    with caplog.at_level(logging.DEBUG, logger="biomero.detached"):
        detached_mode_enabled()
    assert [record.levelno for record in caplog.records] == [logging.DEBUG]
    assert "disabled" in caplog.records[0].getMessage()


# ------------------------ deprecated 2.9 beta names ------------------------ #

def test_old_constant_names_still_resolve_with_a_deprecation_warning():
    from biomero import detached
    with pytest.warns(DeprecationWarning, match="LAUNCHER_MARKER"):
        assert detached.LAUNCHER_MARKER == constants.LAUNCHER_MARKER
    with pytest.warns(DeprecationWarning, match="CLAIMED"):
        assert detached.CLAIMED == "CLAIMED"


def test_old_functions_still_work_with_a_deprecation_warning(
        tracker, launcher):
    from biomero import detached
    workflow_id, task_id = launcher
    with pytest.warns(DeprecationWarning):
        assert detached.find_launcher_task(tracker, workflow_id).id == task_id
    with pytest.warns(DeprecationWarning):
        assert detached.claim_launcher_task(tracker, task_id)
    with pytest.warns(DeprecationWarning):
        assert detached.is_claimed(tracker.repository.get(task_id))
    with pytest.warns(DeprecationWarning):
        assert detached.is_launcher_task(tracker.repository.get(task_id))


def test_names_that_moved_to_the_scripts_say_where_they_went():
    from biomero import detached
    with pytest.raises(AttributeError, match="moved to biomero-scripts"):
        detached.register_detached_launcher
    with pytest.raises(AttributeError, match="no attribute"):
        detached.nothing_like_this


# ------------------------- registering a launcher ------------------------- #

def test_add_launcher_task_marks_the_task_without_touching_the_callers_dict(
        tracker):
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)
    script_params = {"workflows": ["a"]}

    task_id = tracker.add_launcher_task(
        workflow_id, constants.RUN_WF_BATCHED_SCRIPT, "v7", [1], script_params)

    task = tracker.repository.get(task_id)
    assert task.params == {"workflows": ["a"],
                           constants.LAUNCHER_MARKER: "v7"}
    assert script_params == {"workflows": ["a"]}
    assert is_launcher_task(task)
    assert tracker.find_launcher_task(workflow_id).id == task_id


def test_only_launching_scripts_can_register_a_launcher(tracker):
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)
    with pytest.raises(ValueError, match="cannot launch"):
        tracker.add_launcher_task(workflow_id, "_SLURM_Image_Transfer.py",
                                  "v1", [], {})


# ------------------------- task outcome on the aggregate ------------------------- #

def test_task_aggregate_knows_whether_it_completed_or_failed(tracker, launcher):
    workflow_id, _ = launcher
    done = tracker.add_task_to_workflow(workflow_id, "a", "v1", [], {})
    broken = tracker.add_task_to_workflow(workflow_id, "b", "v1", [], {})
    open_task = tracker.add_task_to_workflow(workflow_id, "c", "v1", [], {})
    tracker.complete_task(done, "fine")
    tracker.fail_task(broken, "broke")
    outcomes = {name: (tracker.repository.get(task_id).completed,
                       tracker.repository.get(task_id).failed)
                for name, task_id in (("done", done), ("broken", broken),
                                      ("open", open_task))}
    assert outcomes == {"done": (True, False), "broken": (False, True),
                        "open": (False, False)}


def test_a_run_stops_between_steps_once_cancel_was_requested(tracker, launcher):
    from biomero.eventsourcing import WorkflowCancelled
    workflow_id, _ = launcher
    tracker.raise_if_cancel_requested(workflow_id)  # nothing requested
    tracker.request_workflow_cancel(workflow_id, "user asked")
    with pytest.raises(WorkflowCancelled, match="user asked"):
        tracker.raise_if_cancel_requested(workflow_id)


def test_interruptions_are_not_os_errors():
    """SSH recovery and `except OSError` handlers must never catch them."""
    from biomero.eventsourcing import WorkflowCancelled, WorkflowInterrupted
    assert issubclass(WorkflowCancelled, WorkflowInterrupted)
    assert not issubclass(WorkflowInterrupted, OSError)
    assert not issubclass(WorkflowCancelled, OSError)


def test_workflow_is_finished_once_it_completed_or_failed(tracker):
    running = tracker.initiate_workflow("wf", "", user=1, group=2)
    completed = tracker.initiate_workflow("wf", "", user=1, group=2)
    failed = tracker.initiate_workflow("wf", "", user=1, group=2)
    tracker.complete_workflow(completed)
    tracker.fail_workflow(failed, "broke")
    assert [tracker.is_workflow_finished(w) for w in (running, completed, failed)] == [
        False, True, True]


def final_events(tracker, workflow_id):
    return [e.topic.rsplit(".", 1)[-1]
            for e in tracker.recorder.select_events(workflow_id)
            if e.topic.rsplit(".", 1)[-1] in ("WorkflowCompleted",
                                              "WorkflowFailed")]


@pytest.mark.parametrize("first, second", [
    ("complete", "complete"), ("complete", "fail"), ("fail", "complete"),
    ("fail", "fail")])
def test_a_final_workflow_refuses_a_second_final_state(tracker, first, second,
                                                       caplog):
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)

    def end(kind):
        if kind == "complete":
            tracker.complete_workflow(workflow_id)
        else:
            tracker.fail_workflow(workflow_id, "broke")
    end(first)
    with caplog.at_level(logging.WARNING, logger="biomero.eventsourcing"):
        end(second)

    assert len(final_events(tracker, workflow_id)) == 1
    assert tracker.is_workflow_finished(workflow_id)
    assert any(r.levelno == logging.WARNING and "already final" in r.getMessage()
               for r in caplog.records)


def test_streams_with_two_final_events_still_replay(tracker):
    """Old streams may hold a failure after a completion; they must load."""
    workflow_id = tracker.initiate_workflow("wf", "", user=1, group=2)
    workflow = tracker.repository.get(workflow_id)
    workflow._complete()  # what an old, unguarded writer recorded
    workflow._fail("late failure")
    tracker.save(workflow)
    assert len(final_events(tracker, workflow_id)) == 2

    replayed = WorkflowTracker().repository.get(workflow_id)

    assert replayed.finished
    assert WorkflowTracker().is_workflow_finished(workflow_id)
