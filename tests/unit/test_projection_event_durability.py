"""Projection reads must not roll back the producer's shared event transaction."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from biomero import SlurmClient
from biomero.constants import (LAUNCHER_MARKER, LEGACY_CLAIMED_STATUS,
                               RUN_WF_SCRIPT, workflow_status as wfs)
from biomero.database import EngineManager, TaskExecution, WorkflowProgressView
import biomero.views as views


@pytest.fixture(params=['sqlite', 'postgres'])
def tracking_database(request, tmp_path, monkeypatch):
    """Independent observer connection; optional PostgreSQL uses a new schema."""
    owner = None
    schema = None
    if request.param == 'postgres':
        base = os.environ.get('BIOMERO_TEST_POSTGRES_URL')
        if not base:
            pytest.skip('BIOMERO_TEST_POSTGRES_URL not configured')
        owner = create_engine(base)
        schema = 'biomero_durability_' + uuid4().hex
        with owner.begin() as connection:
            connection.execute(text('CREATE SCHEMA ' + schema))
        url = make_url(base).update_query_dict({'options': '-csearch_path=' + schema})
        url = url.render_as_string(hide_password=False)
    else:
        url = 'sqlite:///' + (tmp_path / 'tracking.sqlite').as_posix()
    monkeypatch.setenv('SQLALCHEMY_URL', url)
    monkeypatch.setenv('PERSISTENCE_MODULE', 'eventsourcing_sqlalchemy')
    monkeypatch.setattr(SlurmClient, 'validate', lambda *a, **kw: None)
    monkeypatch.setattr(SlurmClient, 'init_workflows', lambda self: None)
    EngineManager.close_engine()
    if views._tracker is not None:
        views._tracker.close()
    views._tracker = None
    observer = create_engine(url)
    clients = []

    def client(full=True):
        value = SlurmClient(host='localhost', init_slurm=False,
                            track_workflows=True, enable_job_progress=True,
                            enable_job_accounting=full,
                            enable_workflow_analytics=full,
                            sqlalchemy_url=url)
        clients.append(value)
        return value

    yield client, observer
    for value in clients:
        for name in ('workflowTracker', 'jobAccounting', 'jobProgress',
                     'wfProgress', 'workflowAnalytics'):
            app = getattr(value, name, None)
            if app is not None:
                app.close()
        value.close()
    if views._tracker is not None:
        views._tracker.close()
    views._tracker = None
    observer.dispose()
    EngineManager.close_engine()
    if owner is not None:
        with owner.begin() as connection:
            connection.execute(text('DROP SCHEMA ' + schema + ' CASCADE'))
        owner.dispose()


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
            WorkflowProgressView.status.notin_([wfs.DONE, wfs.FAILED]))).all()
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
