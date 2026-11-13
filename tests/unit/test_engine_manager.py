"""The database engine is owned by EngineManager and shared by its holders."""
import gc
import threading
from unittest.mock import MagicMock, patch

import pytest

from biomero.database import EngineHolder, EngineManager
from biomero.slurm_client import SlurmClient

from .test_slurm_client import slurm_client, set_env_vars  # noqa: F401


@pytest.fixture
def real_engine(tmp_path):
    """A clean EngineManager (registry cleared in place) on a sqlite file."""
    EngineManager.close_engine()
    EngineManager._holders.clear()
    yield f"sqlite:///{tmp_path / 'engine.db'}"
    EngineManager.close_engine()
    EngineManager._holders.clear()


def _engine_is_open():
    return EngineManager._engine is not None


def test_last_holder_disposes_the_engine(real_engine):
    holder = EngineHolder()
    EngineManager.acquire(holder, real_engine)
    assert _engine_is_open()

    EngineManager.release(holder)

    assert not _engine_is_open()


def test_engine_stays_while_another_holder_is_not_released_yet(real_engine):
    """B is constructed but not entered while A exits: B keeps its engine."""
    holder_a, holder_b = EngineHolder(), EngineHolder()
    EngineManager.acquire(holder_a, real_engine)
    EngineManager.acquire(holder_b, real_engine)

    EngineManager.release(holder_a)

    assert _engine_is_open()
    assert EngineManager._session is not None
    EngineManager.release(holder_b)
    assert not _engine_is_open()


def test_releasing_twice_or_without_acquiring_is_harmless(real_engine):
    holder, other, stranger = EngineHolder(), EngineHolder(), EngineHolder()
    EngineManager.acquire(holder, real_engine)
    EngineManager.acquire(other, real_engine)

    EngineManager.release(holder)
    EngineManager.release(holder)
    EngineManager.release(stranger)

    assert _engine_is_open()
    EngineManager.release(other)
    assert not _engine_is_open()


def test_other_holder_exiting_during_acquisition_cannot_dispose_the_engine(
        real_engine):
    """Acquiring and registering are atomic against a concurrent release."""
    leaving, arriving = EngineHolder(), EngineHolder()
    EngineManager.acquire(leaving, real_engine)
    real_create = EngineManager.create_scoped_session.__func__
    release_started = threading.Event()

    def release_leaving():
        release_started.set()
        EngineManager.release(leaving)

    def create_while_other_exits(cls, sqlalchemy_url=None):
        topic = real_create(cls, sqlalchemy_url=sqlalchemy_url)
        # The engine exists, the arriving holder is not registered yet: the
        # other holder exits now. It must wait for the registration.
        thread = threading.Thread(target=release_leaving)
        thread.start()
        release_started.wait()
        thread.join(timeout=0.3)
        return topic

    with patch.object(EngineManager, "create_scoped_session",
                      classmethod(create_while_other_exits)):
        EngineManager.acquire(arriving, real_engine)
    for thread in threading.enumerate():
        if thread is not threading.current_thread():
            thread.join(timeout=5)

    assert _engine_is_open()
    assert EngineManager._session is not None
    assert list(EngineManager._holders) == [arriving]


def test_a_holder_that_is_never_released_does_not_keep_the_engine(
        real_engine):
    """The registry holds its tokens weakly (a strong registry fails this)."""
    forgotten = EngineHolder()
    EngineManager.acquire(forgotten, real_engine)
    del forgotten
    gc.collect()

    assert len(EngineManager._holders) == 0
    closer = EngineHolder()
    EngineManager.acquire(closer, real_engine)
    EngineManager.release(closer)
    assert not _engine_is_open()


# ---------------------------------------------------------------------------
# SlurmClient: exit and initialisation use the EngineManager registry
# ---------------------------------------------------------------------------

def test_every_client_has_its_own_engine_token():
    first = SlurmClient(config_only=True)
    second = SlurmClient(config_only=True)  # equal to first: same host

    assert first == second
    assert first._engine_token is not second._engine_token


def test_engine_token_survives_setting_other_client_attributes():
    """Fabric proxies attribute assignment into a config that deep-copies."""
    client = SlurmClient(config_only=True)
    token = client._engine_token

    client.some_other_attribute = ["copied"]

    assert client._engine_token is token


def test_exit_releases_the_clients_engine_token(slurm_client):
    with patch.object(EngineManager, "release") as release:
        with slurm_client:
            pass

    release.assert_called_once_with(slurm_client._engine_token)


def test_engine_is_released_when_closing_the_connection_fails(slurm_client):
    with patch("biomero.slurm_client.Connection.__exit__",
               side_effect=OSError("close failed")), \
            patch.object(EngineManager, "release") as release:
        with pytest.raises(OSError, match="close failed"):
            slurm_client.__exit__(None, None, None)

    release.assert_called_once_with(slurm_client._engine_token)


def test_release_failure_does_not_mask_the_exception_of_closing(
        slurm_client, caplog):
    with patch("biomero.slurm_client.Connection.__exit__",
               side_effect=OSError("close failed")), \
            patch.object(EngineManager, "release",
                         side_effect=RuntimeError("release failed")):
        with pytest.raises(OSError, match="close failed"):
            slurm_client.__exit__(None, None, None)

    assert "Could not release the database engine" in caplog.text


def test_close_alone_does_not_release_the_engine(slurm_client):
    with patch.object(EngineManager, "release") as release, \
            patch("biomero.slurm_client.Connection.close"):
        slurm_client.close()

    release.assert_not_called()


def test_initializing_analytics_acquires_the_engine_for_the_client(
        slurm_client):
    slurm_client.sqlalchemy_url = "sqlite:///test.db"
    slurm_client.track_workflows = True
    slurm_client.setup_listeners = MagicMock()

    with patch.object(EngineManager, "acquire",
                      return_value="topic") as acquire, \
            patch("biomero.slurm_client.SingleThreadedRunner"):
        slurm_client.initialize_analytics_system()

    acquire.assert_called_once_with(
        slurm_client._engine_token, sqlalchemy_url="sqlite:///test.db")
