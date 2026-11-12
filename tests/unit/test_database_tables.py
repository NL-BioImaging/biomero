"""Creating the view tables: tolerate losing a race, never hide a real failure."""
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError

from biomero.constants import slurm_env
from biomero.database import Base, EngineManager


@pytest.fixture
def sqlite_url(tmp_path):
    EngineManager.close_engine()
    yield f"sqlite:///{(tmp_path / 'views.sqlite').as_posix()}"
    EngineManager.close_engine()


def test_tables_are_created_on_first_use(sqlite_url):
    EngineManager.create_scoped_session(sqlite_url)
    existing = inspect(create_engine(sqlite_url))
    assert all(existing.has_table(name) for name in Base.metadata.tables)


def test_url_defaults_to_the_environment_variable(sqlite_url, monkeypatch):
    monkeypatch.setenv(slurm_env.SQLALCHEMY_URL, sqlite_url)
    EngineManager.create_scoped_session()
    assert str(EngineManager._engine.url) == sqlite_url


def test_losing_the_creation_race_is_tolerated(sqlite_url):
    """The tables exist after the failed DDL: another process created them."""
    Base.metadata.create_all(create_engine(sqlite_url))
    race_error = ProgrammingError('CREATE TABLE', {}, Exception('exists'))
    with patch.object(Base.metadata, 'create_all',
                      side_effect=race_error):
        EngineManager.create_scoped_session(sqlite_url)
    assert EngineManager._engine is not None


@pytest.mark.parametrize('error', [
    OperationalError('CREATE TABLE', {}, Exception('permission denied')),
    ProgrammingError('CREATE TABLE', {}, Exception('syntax')),
])
def test_creation_failure_without_tables_is_raised(sqlite_url, error):
    with patch.object(Base.metadata, 'create_all',
                      side_effect=error):
        with pytest.raises(type(error)):
            EngineManager.create_scoped_session(sqlite_url)
    # No engine is kept that would skip creating the tables next time.
    assert EngineManager._engine is None


def test_unexpected_creation_error_is_raised(sqlite_url):
    with patch.object(Base.metadata, 'create_all',
                      side_effect=RuntimeError('boom')):
        with pytest.raises(RuntimeError, match='boom'):
            EngineManager.create_scoped_session(sqlite_url)
    assert EngineManager._engine is None


@pytest.mark.parametrize("error", [
    IntegrityError("CREATE TABLE", {}, Exception("duplicate type")),
    OperationalError("CREATE TABLE", {}, Exception("table exists")),
])
def test_any_database_error_is_tolerated_when_the_tables_exist(sqlite_url, error):
    """PostgreSQL reports a lost creation race as an IntegrityError."""
    Base.metadata.create_all(create_engine(sqlite_url))
    with patch.object(Base.metadata, "create_all", side_effect=error):
        EngineManager.create_scoped_session(sqlite_url)
    assert EngineManager._engine is not None


def test_other_errors_are_raised_even_when_the_tables_exist(sqlite_url):
    Base.metadata.create_all(create_engine(sqlite_url))
    with patch.object(Base.metadata, "create_all",
                      side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError, match="boom"):
            EngineManager.create_scoped_session(sqlite_url)
    assert EngineManager._engine is None


def test_the_creation_error_is_reported_when_the_table_check_fails_too(sqlite_url):
    creation_error = OperationalError("CREATE", {}, Exception("no privileges"))
    with patch.object(Base.metadata, "create_all", side_effect=creation_error):
        with patch("biomero.database.inspect",
                   side_effect=OperationalError("SELECT", {}, Exception("gone"))):
            with pytest.raises(OperationalError) as raised:
                EngineManager.create_scoped_session(sqlite_url)
    assert raised.value is creation_error
