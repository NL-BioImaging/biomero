"""Projection reads must not roll back the producer's shared event transaction."""

import itertools
import logging
from unittest.mock import MagicMock

import pytest
from eventsourcing.persistence import IntegrityError as EventSourcingIntegrityError
from eventsourcing.persistence import Tracking
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from biomero.constants import (IMPORT_RESULTS_SCRIPT, LAUNCHER_MARKER,
                               LEGACY_CLAIMED_STATUS, PROGRESS_DONE,
                               PROGRESS_RETRIEVING,
                               RUN_WF_SCRIPT, workflow_status as wfs)
from biomero.database import (EngineManager, TaskExecution, WorkflowProgressView,
                              retry_on_database_conflict)
from biomero.eventsourcing import WorkflowTracker
from biomero.views import (JobAccounting, JobProgress, WorkflowAnalytics,
                           WorkflowProgress)


def events(observer, aggregate_id):
    def identity(value):
        if isinstance(value, (bytes, memoryview)):
            return bytes(value).hex()
        return str(value).replace('-', '')

    with observer.connect() as connection:
        return [(row.originator_version, row.topic.rsplit('.', 1)[-1])
                for row in connection.execute(text(
                    'SELECT originator_id, originator_version, topic '
                    'FROM workflowtracker_events ORDER BY originator_version'))
                if identity(row.originator_id) == aggregate_id.hex]


def workflow_status(observer, workflow_id):
    with observer.connect() as connection:
        return connection.execute(WorkflowProgressView.__table__.select().where(
            WorkflowProgressView.workflow_id == workflow_id)).one().status


@pytest.mark.parametrize('full', [False, True])
@pytest.mark.parametrize('terminal', ['failed', 'completed'])
def test_cold_client_terminal_event_survives_projection_rebuild(
        tracking_database, full, terminal):
    make_client, observer = tracking_database
    initial = make_client(full)
    workflow_id = initial.workflowTracker.initiate_workflow(
        'durability fixture', 'no HPC submission', 1, 1)
    task_id = initial.workflowTracker.add_task_to_workflow(
        workflow_id, RUN_WF_SCRIPT, 'test', [], {LAUNCHER_MARKER: 'test'})
    # A 2.9 beta stored its claim as a status; the current one is an event.
    initial.workflowTracker.update_task_status(task_id, LEGACY_CLAIMED_STATUS)
    assert initial.workflowTracker.claim_launcher_task(task_id, 'supervisor')
    before = events(observer, workflow_id)
    EngineManager.remove_session()
    cold = make_client(full)
    assert workflow_id not in cold.wfProgress.workflows
    if terminal == 'failed':
        cold.workflowTracker.fail_workflow(workflow_id, 'setup failed')
        expected_event, expected_status = 'WorkflowFailed', wfs.FAILED
    else:
        cold.workflowTracker.complete_workflow(workflow_id)
        expected_event, expected_status = 'WorkflowCompleted', wfs.DONE
    expected = before + [(before[-1][0] + 1, expected_event)]
    assert events(observer, workflow_id) == expected
    assert workflow_status(observer, workflow_id) == expected_status
    cold.initialize_analytics_system(reset_tables=True)
    assert events(observer, workflow_id) == expected
    assert workflow_status(observer, workflow_id) == expected_status
    assert cold.workflowTracker.find_launcher_task(workflow_id).id == task_id
    # The detached supervisor discovers candidates with this projection filter.
    with observer.connect() as connection:
        candidates = connection.execute(WorkflowProgressView.__table__.select().where(
            WorkflowProgressView.status.notin_(wfs.FINAL))).all()
    assert workflow_id not in [row.workflow_id for row in candidates]


@pytest.mark.parametrize('event_name, command, arguments', [
    ('TaskStarted', 'start_task', ()),
    ('TaskCompleted', 'complete_task', ('result retained',)),
    ('TaskFailed', 'fail_task', ('task failed',)),
])
def test_cold_analytics_lookup_preserves_task_event(
        tracking_database, event_name, command, arguments):
    make_client, observer = tracking_database
    initial = make_client()
    workflow_id = initial.workflowTracker.initiate_workflow('task fixture', '', 1, 1)
    task_id = initial.workflowTracker.add_task_to_workflow(workflow_id, 'analysis', 'test', [], {})
    before = events(observer, task_id)
    EngineManager.remove_session()
    cold = make_client()
    assert task_id not in cold.workflowAnalytics.tasks
    getattr(cold.workflowTracker, command)(task_id, *arguments)
    assert events(observer, task_id) == before + [(before[-1][0] + 1, event_name)]
    with observer.connect() as connection:
        projected = connection.execute(TaskExecution.__table__.select().where(
            TaskExecution.task_id == task_id)).one()
    assert projected.status == 'CREATED'
    if event_name in ('TaskCompleted', 'TaskFailed'):
        assert projected.end_time is not None
    if event_name == 'TaskFailed':
        assert projected.error_type == 'task failed'


PROJECTIONS = (JobAccounting, JobProgress, WorkflowProgress, WorkflowAnalytics)


def runner_projection(client, projection_cls):
    """The projection that the client's runner notifies on each save.

    The client's own listener attributes are copies (fabric keeps unknown
    attributes in its config), so patch the runner's instance.
    """
    return client.workflowTracker.followers[0].get(projection_cls)


def let_other_process_project_first(monkeypatch, producer, other_process,
                                    event_name=None):
    """Let another process project the producer's new events first.

    Each projection of the other process (sharing the tracking rows) handles
    the new events after the producer's projection has selected them, but
    before it has recorded them.

    Args:
        event_name: Only race for notifications of this event, if given.
    """
    def patch(projection_cls):
        mine = runner_projection(producer, projection_cls)
        theirs = runner_projection(other_process, projection_cls)
        convert = mine.convert_notifications

        def convert_after_other_process(leader_name, notifications):
            if event_name is None or any(
                    notification.topic.endswith('.' + event_name)
                    for notification in notifications):
                theirs.pull_and_process(leader_name=leader_name)
            return convert(leader_name, notifications)

        monkeypatch.setattr(mine, 'convert_notifications',
                            convert_after_other_process)

    for projection_cls in PROJECTIONS:
        patch(projection_cls)


def test_events_projected_by_another_process_do_not_fail_the_save(
        tracking_database, monkeypatch):
    make_client, observer = tracking_database
    producer = make_client()
    other_process = make_client()
    workflow_id = producer.workflowTracker.initiate_workflow('two runners', '', 1, 1)

    let_other_process_project_first(monkeypatch, producer, other_process)
    task_id = producer.workflowTracker.add_task_to_workflow(
        workflow_id, RUN_WF_SCRIPT, 'test', [], {})

    assert producer.workflowTracker.repository.get(workflow_id).tasks == [task_id]
    assert (1, 'TaskAdded') in events(observer, workflow_id)


def test_skipped_task_added_does_not_drop_later_task_events(
        tracking_database, monkeypatch):
    make_client, observer = tracking_database
    producer = make_client()
    other_process = make_client()
    workflow_id = producer.workflowTracker.initiate_workflow('skipped', '', 1, 1)
    # The producer projects TaskCreated itself; the other process TaskAdded.
    with monkeypatch.context() as race:
        let_other_process_project_first(race, producer, other_process,
                                        event_name='TaskAdded')
        task_id = producer.workflowTracker.add_task_to_workflow(
            workflow_id, IMPORT_RESULTS_SCRIPT, 'test', [], {})

    producer.workflowTracker.update_task_status(task_id, wfs.IMPORTED)

    with observer.connect() as connection:
        row = connection.execute(WorkflowProgressView.__table__.select().where(
            WorkflowProgressView.workflow_id == workflow_id)).one()
        task_row = connection.execute(TaskExecution.__table__.select().where(
            TaskExecution.task_id == task_id)).one()
    assert (row.status, row.progress) == (wfs.IMPORTED, PROGRESS_RETRIEVING)
    assert (task_row.user_id, task_row.group_id) == (1, 1)


def test_stale_status_does_not_overwrite_a_finished_workflow(tracking_database):
    make_client, observer = tracking_database
    initial = make_client()
    tracker = initial.workflowTracker
    workflow_id = tracker.initiate_workflow('stale status', '', 1, 1)
    task_id = tracker.add_task_to_workflow(
        workflow_id, IMPORT_RESULTS_SCRIPT, 'test', [], {})
    tracker.update_task_status(task_id, wfs.IMPORTED)
    stale_notification = tracker.notification_log.select(
        start=1, limit=10)[-1]
    tracker.complete_workflow(workflow_id)
    assert workflow_status(observer, workflow_id) == wfs.DONE
    EngineManager.remove_session()

    cold = make_client(full=False)
    leader_name = WorkflowTracker.__name__
    stale_event = cold.workflowTracker.mapper.to_domain_event(stale_notification)
    fresh_id = cold.wfProgress.recorder.max_tracking_id(leader_name) + 1
    cold.wfProgress.process_event(stale_event, Tracking(
        application_name=leader_name, notification_id=fresh_id))

    with observer.connect() as connection:
        row = connection.execute(WorkflowProgressView.__table__.select().where(
            WorkflowProgressView.workflow_id == workflow_id)).one()
    assert (row.status, row.progress) == (wfs.DONE, PROGRESS_DONE)
    assert cold.wfProgress.workflows[workflow_id]['status'] == wfs.DONE


def test_tracking_conflict_without_recorded_position_still_raises(
        tracking_database, monkeypatch):
    make_client, _ = tracking_database
    client = make_client(full=False)
    tracker = client.workflowTracker
    tracker.initiate_workflow('genuine conflict', '', 1, 1)
    notification = tracker.notification_log.select(start=1, limit=1)[0]
    domain_event = tracker.mapper.to_domain_event(notification)
    projection = client.wfProgress

    def conflicting_record(processing_event):
        raise EventSourcingIntegrityError()

    monkeypatch.setattr(projection, '_record', conflicting_record)
    monkeypatch.setattr(projection.recorder, 'has_tracking_id',
                        lambda *args: False)
    with pytest.raises(EventSourcingIntegrityError):
        projection.process_event(domain_event, Tracking(
            application_name=WorkflowTracker.__name__,
            notification_id=notification.id + 100))


def test_database_tracking_conflict_is_raised_for_rollback(
        tracking_database, monkeypatch):
    make_client, _ = tracking_database
    client = make_client(full=False)
    tracker = client.workflowTracker
    tracker.initiate_workflow('database conflict', '', 1, 1)
    notification = tracker.notification_log.select(start=1, limit=1)[0]
    domain_event = tracker.mapper.to_domain_event(notification)
    projection = client.wfProgress

    # Two processes inserting a projection's first tracking row: the
    # database refuses one, and its transaction is aborted.
    def database_conflict(processing_event):
        try:
            raise SQLAlchemyIntegrityError(
                'INSERT', {}, Exception('duplicate key value violates unique '
                                        'constraint "workflowprogress_tracking_pkey"'))
        except SQLAlchemyIntegrityError as error:
            raise EventSourcingIntegrityError(error) from error

    # Not recorded when processing starts, recorded (by the other process)
    # by the time the database refuses this one.
    recorded = itertools.cycle([False, True])
    monkeypatch.setattr(projection, '_record', database_conflict)
    monkeypatch.setattr(projection.recorder, 'has_tracking_id',
                        lambda *args: next(recorded))
    tracking = Tracking(application_name=WorkflowTracker.__name__,
                        notification_id=notification.id + 100)
    with pytest.raises(EventSourcingIntegrityError):
        projection.process_event(domain_event, tracking)

    # Raised, the conflict reaches the retry, which rolls the session back;
    # the retry then finds the event projected.
    rollback = MagicMock()
    monkeypatch.setattr(EngineManager, 'rollback', rollback)
    next(recorded)
    retry_on_database_conflict(max_retries=1, base_delay=0)(
        projection.process_event)(domain_event, tracking)
    rollback.assert_called_once()


def test_tracking_conflict_after_another_process_recorded_is_skipped(
        tracking_database, monkeypatch, caplog):
    make_client, _ = tracking_database
    client = make_client(full=False)
    tracker = client.workflowTracker
    tracker.initiate_workflow('racing conflict', '', 1, 1)
    notification = tracker.notification_log.select(start=1, limit=1)[0]
    domain_event = tracker.mapper.to_domain_event(notification)
    projection = client.wfProgress

    def conflicting_record(processing_event):
        raise EventSourcingIntegrityError()

    # Not recorded when processing starts, recorded by the time it conflicts.
    recorded = iter([False, True])
    monkeypatch.setattr(projection, '_record', conflicting_record)
    monkeypatch.setattr(projection.recorder, 'has_tracking_id',
                        lambda *args: next(recorded))
    with caplog.at_level(logging.DEBUG, logger='biomero.views'):
        projection.process_event(domain_event, Tracking(
            application_name=WorkflowTracker.__name__,
            notification_id=notification.id + 100))
    assert 'already projected by another process' in caplog.text
