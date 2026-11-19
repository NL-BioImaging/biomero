"""View writes and lookups must not raise inside the producer's save()."""
import logging
from unittest.mock import MagicMock

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

import biomero.views as views
from biomero.constants import RUN_WF_SCRIPT
from biomero.database import EngineManager, JobView
from .test_projection_event_durability import (  # noqa: F401
    events, tracking_database)


def test_dropped_view_table_does_not_fail_the_producer_save(
        tracking_database, caplog):
    make_client, observer = tracking_database
    client = make_client()
    tracker = client.workflowTracker
    workflow_id = tracker.initiate_workflow('view tolerance', '', 1, 1)
    with observer.begin() as connection:
        connection.execute(text('DROP TABLE biomero_task_execution'))
        connection.execute(text('DROP TABLE biomero_job_progress_view'))
        connection.execute(text('DROP TABLE biomero_workflow_progress_view'))
    before = events(observer, workflow_id)

    with caplog.at_level(logging.WARNING, logger='biomero.views'):
        task_id = tracker.add_task_to_workflow(
            workflow_id, RUN_WF_SCRIPT, 'test', [], {})
        tracker.start_task(task_id)

    assert events(observer, task_id)
    assert events(observer, workflow_id) != before
    assert any(record.levelno == logging.WARNING and record.exc_info
               for record in caplog.records)


def test_write_view_row_does_not_raise_on_programming_error(
        monkeypatch, caplog):
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.merge.side_effect = ProgrammingError('INSERT', {}, Exception(
        'no such table'))
    monkeypatch.setattr(EngineManager, 'get_session',
                        classmethod(lambda cls: session))
    rollback = MagicMock()
    monkeypatch.setattr(EngineManager, 'rollback', rollback)
    with caplog.at_level(logging.WARNING, logger='biomero.views'):
        views.write_view_row('job 1', lambda s: s.merge(JobView()))
    rollback.assert_called_once()
    assert 'job 1' in caplog.text


def test_write_view_row_still_raises_integrity_error(monkeypatch):
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.merge.side_effect = IntegrityError('INSERT', {}, Exception(
        'unique constraint'))
    monkeypatch.setattr(EngineManager, 'get_session',
                        classmethod(lambda cls: session))
    with pytest.raises(IntegrityError):
        views.write_view_row('job 1', lambda s: s.merge(JobView()))
    session.rollback.assert_called_once()


def test_failed_rollback_in_write_view_row_does_not_raise(
        monkeypatch, caplog):
    session = MagicMock()
    session.__enter__.return_value = session
    session.__exit__.return_value = False
    session.merge.side_effect = ProgrammingError('INSERT', {}, Exception(
        'no such table'))
    monkeypatch.setattr(EngineManager, 'get_session',
                        classmethod(lambda cls: session))
    monkeypatch.setattr(EngineManager, 'rollback',
                        classmethod(lambda cls: 1 / 0))
    with caplog.at_level(logging.WARNING, logger='biomero.views'):
        views.write_view_row('job 1', lambda s: s.merge(JobView()))
    assert 'Rollback of the view session' in caplog.text


def test_invalidated_connection_in_lookup_leaves_session_usable(
        tracking_database):
    make_client, observer = tracking_database
    make_client()
    session = EngineManager.get_session()

    def lookup(active_session):
        active_session.connection().invalidate()
        raise OperationalError('SELECT', {}, Exception('server closed'),
                               connection_invalidated=True)

    assert views.first_view_row(lookup, 'job 1') is None
    EngineManager.commit()
    assert session.execute(text('SELECT 1')).scalar() == 1


def test_engine_checks_pooled_connections_before_use(tracking_database):
    make_client, observer = tracking_database
    make_client()
    assert EngineManager._engine.pool._pre_ping
