"""Shared fixtures: an independent observer on a tracking database."""
import os
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from biomero import SlurmClient
from biomero.database import EngineManager
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
