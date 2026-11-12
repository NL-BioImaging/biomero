"""Transport recovery must never replay arbitrary remote side effects."""
import errno
import inspect
import socket
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fabric import Result
from invoke.exceptions import CommandTimedOut, UnexpectedExit
from paramiko import AuthenticationException, SSHException
from paramiko.ssh_exception import NoValidConnectionsError

from biomero import ssh_recovery
from biomero.constants import (
    SSH_RETRY_INITIAL_DELAY_SECONDS, SSH_RETRY_JITTER_FRACTION,
    SSH_RETRY_MAX_DELAY_SECONDS)
from .test_slurm_client import (  # noqa: F401
    slurm_client, set_env_vars, slurm_client_from_config_factory)


@pytest.mark.parametrize('error,expected', [
    (EOFError(), True),
    (ConnectionResetError(), True),
    (TimeoutError(), True),
    (OSError(errno.EHOSTUNREACH, 'host unreachable'), True),
    (OSError(errno.ECONNRESET, 'reset'), True),
    (OSError(errno.ENETDOWN, 'network down'), True),
    (OSError('Socket is closed'), True),
    (OSError('Permission denied'), False),
    (OSError(errno.EACCES, 'Socket is closed'), False),
    (PermissionError('Socket is closed'), False),
    (OSError('remote file Socket is closed not found'), False),
    (SSHException('SSH session not active'), True),
    (SSHException('Error reading SSH protocol banner'), True),
    (SSHException('Timeout opening channel.'), True),
    (AuthenticationException('bad credentials'), False),
    (PermissionError('denied'), False),
    (FileNotFoundError('missing file'), False),
    (SSHException('remote command failed'), False),
    (ValueError('bad configuration'), False),
    (socket.gaierror(socket.EAI_AGAIN, 'temporary failure'), True),
    (socket.gaierror(socket.EAI_NONAME, 'unknown host'), False),
    (NoValidConnectionsError({('h', 22): ConnectionRefusedError()}), True),
    (NoValidConnectionsError({('h', 22): PermissionError()}), False),
    (ssh_recovery.SSHRecoveryTimeout('window ended'), False),
    (ssh_recovery.SSHRecoveryCancelled('cancelled'), False),
    (UnexpectedExit(Result(connection=None, exited=-1)), True),
    (UnexpectedExit(Result(connection=None, exited=1, stderr='denied')), False),
])
def test_transient_classifier(error, expected):
    assert ssh_recovery.is_transient(error) is expected


def test_classifier_names_the_reason_for_the_log():
    reason = ssh_recovery.transient_reason(
        OSError(errno.ENETDOWN, 'network down'))
    assert 'ENETDOWN' in reason
    assert ssh_recovery.transient_reason(ValueError('bad')) is None


@pytest.fixture
def recovery_clock(monkeypatch):
    """A fake clock; the retry jitter is removed so waits are exact."""
    clock = SimpleNamespace(now=0, waits=[])

    def sleep(seconds):
        clock.now += seconds
        clock.waits.append(seconds)

    monkeypatch.setattr(ssh_recovery.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(ssh_recovery.time, 'sleep', sleep)
    monkeypatch.setattr(ssh_recovery.random, 'uniform', lambda low, high: 0)
    return clock


def test_ten_minute_outage_resumes_same_operation(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock())
    calls = []

    def operation():
        calls.append(recovery_clock.now)
        if recovery_clock.now < 600:
            raise ConnectionResetError('HPC unavailable')
        return 'existing job 42'

    assert ssh_recovery.retry(
        client, operation, 'monitor job 42') == 'existing job 42'
    assert 600 <= recovery_clock.now < 630
    assert max(recovery_clock.waits) <= SSH_RETRY_MAX_DELAY_SECONDS
    assert client.close.call_count == len(calls) - 1


def test_deadline_bounds_retries_and_retains_cause(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=35, close=Mock())
    operation = Mock(side_effect=EOFError('lost connection'))
    with pytest.raises(ssh_recovery.SSHRecoveryTimeout) as exc:
        ssh_recovery.retry(client, operation, 'retrieve results')
    assert recovery_clock.now == 35
    assert isinstance(exc.value.__cause__, EOFError)
    assert 'retrieve results' in str(exc.value)


def test_expired_budget_never_starts_a_successful_late_attempt(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=1, close=Mock())
    error = EOFError('connection lost')
    operation = Mock(side_effect=[error, 'must not resurrect expired attempt'])
    with pytest.raises(ssh_recovery.SSHRecoveryTimeout) as caught:
        ssh_recovery.retry(client, operation, 'monitor original job')
    assert caught.value.__cause__ is error
    assert operation.call_count == 1


def test_inner_success_does_not_extend_a_failing_outer_operation(
        recovery_clock):
    """The window of a call is never moved by successful nested work."""
    client = SimpleNamespace(ssh_recovery_timeout=35, close=Mock())
    outer_attempts = []

    def outer():
        outer_attempts.append(recovery_clock.now)
        assert len(outer_attempts) < 50, 'retried past the window'
        ssh_recovery.retry(client, lambda: 'inner step ok', 'inner step')
        raise EOFError('outer step lost')

    with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
        ssh_recovery.retry(client, outer, 'outer operation')
    assert recovery_clock.now == 35
    assert len(outer_attempts) < 10


def test_flapping_link_cannot_retry_past_the_window(recovery_clock):
    """Alternating success and failure inside one call stays bounded."""
    client = SimpleNamespace(ssh_recovery_timeout=20, close=Mock())
    attempts = []

    def operation():
        attempts.append(True)
        assert len(attempts) < 50, 'retried past the window'
        ssh_recovery.retry(client, lambda: 'works', 'poll')
        raise EOFError('link dropped again')

    with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
        ssh_recovery.retry(client, operation, 'long operation')
    assert recovery_clock.now == 20


def test_busy_remote_operation_is_not_reported_as_an_ssh_outage(
        recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=35, close=Mock())
    pending = ssh_recovery.RecoveryPending('zip is running')
    with pytest.raises(ssh_recovery.RemoteOperationStillPending) as caught:
        ssh_recovery.retry(client, Mock(side_effect=pending), 'zip results')
    assert not isinstance(caught.value, ssh_recovery.SSHRecoveryTimeout)
    assert caught.value.__cause__ is pending
    assert 'nothing was repeated' in str(caught.value)
    assert recovery_clock.now == 35
    # The connection works: it is not closed and reopened.
    client.close.assert_not_called()


def test_pending_operation_that_finishes_returns_its_result(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock())
    operation = Mock(side_effect=[
        ssh_recovery.RecoveryPending('busy'),
        ssh_recovery.RecoveryPending('busy'), 'done'])
    assert ssh_recovery.retry(client, operation, 'unpack') == 'done'
    client.close.assert_not_called()


def test_backoff_doubles_up_to_its_cap_with_bounded_jitter(monkeypatch):
    window = ssh_recovery.OutageWindow(timeout=900)
    monkeypatch.setattr(ssh_recovery.random, 'uniform',
                        lambda low, high: high)
    pauses = [window.next_pause(900) for _ in range(8)]
    delays = [SSH_RETRY_INITIAL_DELAY_SECONDS * 2 ** step
              for step in range(8)]
    for pause, delay in zip(pauses, delays):
        capped = min(delay, SSH_RETRY_MAX_DELAY_SECONDS)
        assert pause == pytest.approx(
            capped * (1 + SSH_RETRY_JITTER_FRACTION))
    # A pause never runs past the end of the window.
    assert ssh_recovery.OutageWindow(900).next_pause(0.5) == 0.5


def test_command_timeout_is_recoverable_only_through_safe_read(recovery_clock):
    error = CommandTimedOut(Result(connection=None), timeout=1)
    client = SimpleNamespace(ssh_recovery_timeout=20, close=Mock())
    assert not ssh_recovery.is_transient(error)
    operation = Mock(side_effect=[error, 'same job observed'])

    @ssh_recovery.read_only
    def read(client, **kwargs):
        return operation(**kwargs)

    assert read(client) == 'same job observed'
    operation = Mock(side_effect=error)
    with pytest.raises(CommandTimedOut):
        ssh_recovery.retry(client, operation, 'protected long work')
    assert operation.call_count == 1


def test_read_timeout_keeps_its_type_when_recovery_is_off():
    error = CommandTimedOut(Result(connection=None), timeout=1)
    client = SimpleNamespace(ssh_recovery_timeout=0, close=Mock())

    @ssh_recovery.read_only
    def read(client, **kwargs):
        raise error

    with pytest.raises(CommandTimedOut):
        read(client)


def test_explicit_longer_read_timeout_is_preserved_until_outage(
        recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock())
    operation = Mock(side_effect=[EOFError(), 'observed'])

    @ssh_recovery.read_only
    def read(client, **kwargs):
        return operation(**kwargs)

    assert read(client, timeout=120) == 'observed'
    assert [call.kwargs['timeout']
            for call in operation.call_args_list] == [120, 120]
    operation = Mock(return_value='observed')
    with ssh_recovery.scope(client) as state:
        state.windows.append(ssh_recovery.OutageWindow(
            900, outage_deadline=recovery_clock.now + 2))
        assert read(client, timeout=120) == 'observed'
    assert operation.call_args.kwargs['timeout'] == 2


def test_read_decorator_preserves_metadata_and_calling_contract():
    from biomero.slurm_client import SlurmClient
    for name in ('run_read_only', 'run_commands_read_only'):
        method = getattr(SlurmClient, name)
        assert method.__name__ == name
        assert method.__doc__ == method.__wrapped__.__doc__
        assert inspect.signature(method) == inspect.signature(
            method.__wrapped__)


def test_nested_native_bounds_use_remaining_outage_budget(recovery_clock):
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True, ssh_recovery_timeout=20)
    original = dict(client.connect_kwargs)
    with ssh_recovery.scope(client) as state:
        state.windows.append(ssh_recovery.OutageWindow(
            20, outage_deadline=1.5))
        with ssh_recovery.native_timeouts(client):
            assert client.connect_kwargs['auth_timeout'] == 1.5
            assert client.connect_timeout == 1.5
            assert ssh_recovery.io_timeout(client) == 1.5
            assert ssh_recovery.io_timeout(client, .2) == .2
        assert dict(client.connect_kwargs) == original


def test_zero_window_runs_once_and_raises_the_original_error(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=0, close=Mock())
    operation = Mock(side_effect=EOFError())
    with pytest.raises(EOFError):
        ssh_recovery.retry(client, operation, 'read')
    operation.assert_called_once()
    client.close.assert_not_called()
    assert not recovery_clock.waits


def test_zero_window_leaves_native_timeouts_alone(recovery_clock):
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True, ssh_recovery_timeout=0)
    before = (dict(client.connect_kwargs), client.connect_timeout)
    seen = []

    def operation():
        with ssh_recovery.native_timeouts(client):
            seen.append((dict(client.connect_kwargs), client.connect_timeout))
        return 'ok'

    assert ssh_recovery.retry(client, operation, 'read') == 'ok'
    assert seen == [before]


def test_inner_timeout_is_final_for_the_outer_operation(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=35, close=Mock())
    operation = Mock(side_effect=EOFError())
    with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
        ssh_recovery.retry(client, lambda: ssh_recovery.retry(
            client, operation, 'inner operation'), 'outer operation')
    assert recovery_clock.now == 35


@pytest.mark.parametrize('ini,environment,expected', [
    ({}, {}, 900),
    ({}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': ''}, 900),
    ({'ssh_recovery_timeout': ''}, {}, 900),
    ({'ssh_recovery_timeout': ''}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': ''}, 900),
    ({'ssh_recovery_timeout': '0'}, {}, 0),
    ({'ssh_recovery_timeout': '0'}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': ''}, 0),
    ({'ssh_recovery_timeout': '900'}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': '0'}, 0),
    ({'ssh_recovery_timeout': '900'}, {}, 900),
    ({'ssh_recovery_timeout': '900'},
     {'BIOMERO_SSH_RECOVERY_TIMEOUT': '1200'}, 1200),
    ({'ssh_recovery_timeout': '900'}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': ''}, 900),
])
def test_config_precedence(slurm_client_from_config_factory, ini,
                           environment, expected):
    client = slurm_client_from_config_factory(ini, environment)
    assert client.ssh_recovery_timeout == expected


def test_constructor_recovers_without_new_configuration(recovery_clock):
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True)
    assert client.ssh_recovery_timeout == 900
    client.close = Mock()
    client.run = Mock(side_effect=[
        EOFError('HPC connection dropped'),
        Result(connection=None, exited=0, stdout='existing job')])
    assert client.run_read_only('squeue -h').stdout == 'existing job'
    assert client.run.call_count == 2
    assert recovery_clock.now == 1


def test_connection_stage_timeouts_do_not_change_a_shared_config_dict():
    from biomero.slurm_client import SlurmClient
    shared = {'password': 'x'}
    client = SlurmClient(config_only=True, connect_kwargs=shared)
    assert 'banner_timeout' not in shared
    assert 'auth_timeout' not in shared
    assert client.connect_kwargs['banner_timeout'] == 30
    assert client.connect_kwargs['auth_timeout'] == 30
    assert client.connect_timeout == 10


def test_a_client_without_the_setting_runs_once_and_keeps_its_error():
    client = SimpleNamespace()
    assert ssh_recovery.retry(client, lambda: 'ok', 'read') == 'ok'
    with pytest.raises(EOFError):
        ssh_recovery.retry(client, Mock(side_effect=EOFError()), 'read')


@pytest.mark.parametrize('error', [
    ssh_recovery.SSHRecoveryTimeout('progress outage exhausted'),
    ssh_recovery.SSHRecoveryCancelled('progress wait cancelled'),
])
def test_progress_propagates_recovery_stopping_conditions(error):
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True)
    client.run_commands_read_only = Mock(side_effect=error)
    with pytest.raises(type(error)) as caught:
        client.get_active_job_progress(42)
    assert caught.value is error


def test_progress_retains_best_effort_for_ordinary_log_errors():
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True)
    client.run_commands_read_only = Mock(
        side_effect=FileNotFoundError('no log'))
    assert client.get_active_job_progress(42) is None


def test_authentication_is_not_retried(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock())
    operation = Mock(side_effect=AuthenticationException('denied'))
    with pytest.raises(AuthenticationException):
        ssh_recovery.retry(client, operation, 'read')
    operation.assert_called_once()
    assert not recovery_clock.waits


def test_cancelled_recovery_does_not_restart_operation(recovery_clock):
    cancelled = Event()
    cancelled.set()
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock(),
                             ssh_recovery_cancel=cancelled)
    operation = Mock()
    with pytest.raises(ssh_recovery.SSHRecoveryCancelled):
        ssh_recovery.retry(client, operation, 'read')
    operation.assert_not_called()


def test_fabric_client_cancellation_is_honoured(slurm_client, recovery_clock):
    slurm_client.ssh_recovery_timeout = 900
    cancelled = Event()
    cancelled.set()
    slurm_client.ssh_recovery_cancel = cancelled
    operation = Mock()
    with pytest.raises(ssh_recovery.SSHRecoveryCancelled):
        ssh_recovery.retry(slurm_client, operation, 'read')
    operation.assert_not_called()


def test_status_recovers_without_publishing_failed(slurm_client,
                                                   recovery_clock):
    slurm_client.ssh_recovery_timeout = 900
    slurm_client.close = Mock()
    slurm_client.run = Mock()
    slurm_client.workflowTracker = Mock()
    slurm_client.run.side_effect = [
        EOFError(), Result(connection=None, exited=0, stdout='42 RUNNING\n')]
    states, result = slurm_client.check_job_status([42])
    assert result.ok
    assert states == {42: 'RUNNING'}
    slurm_client.workflowTracker.fail_task.assert_not_called()


def test_arbitrary_commands_are_never_replayed(slurm_client, recovery_clock):
    slurm_client.ssh_recovery_timeout = 900
    slurm_client.run = Mock(side_effect=EOFError())
    with pytest.raises(EOFError):
        slurm_client.run_commands(['sbatch important.sh'])
    slurm_client.run.assert_called_once()


def test_download_retries_partial_file_and_publishes_atomically(
        slurm_client, recovery_clock, tmp_path):
    slurm_client.ssh_recovery_timeout = 900
    slurm_client.close = Mock()
    target = tmp_path / '42_out.zip'
    target.write_bytes(b'previous-complete-result')
    remote_stat = SimpleNamespace(st_size=8, st_mtime=100)
    slurm_client.sftp = Mock(return_value=SimpleNamespace(
        stat=Mock(return_value=remote_stat),
        get_channel=Mock(return_value=Mock())))
    attempts = []

    def get(*, remote, local):
        from pathlib import Path
        assert target.read_bytes() == b'previous-complete-result'
        assert local != str(target)
        attempts.append(local)
        Path(local).write_bytes(b'partial' if len(attempts) == 1 else b'complete')
        if len(attempts) == 1:
            raise EOFError()
        return SimpleNamespace(local=local, remote=remote)

    slurm_client.get = get
    result = slurm_client.copy_zip_locally(str(tmp_path), '/remote/42_out')
    assert target.read_bytes() == b'complete'
    assert result.local == str(target)
    assert len(attempts) == 2
    assert not list(tmp_path.glob('*.part'))


def test_download_that_ends_short_is_retried(slurm_client, recovery_clock,
                                             tmp_path):
    """A short download is a lost connection, not a permanent failure."""
    slurm_client.ssh_recovery_timeout = 900
    slurm_client.close = Mock()
    slurm_client.sftp = Mock(return_value=SimpleNamespace(
        stat=Mock(return_value=SimpleNamespace(st_size=8, st_mtime=100)),
        get_channel=Mock(return_value=Mock())))
    writes = [b'short', b'complete']

    def get(*, remote, local):
        with open(local, 'wb') as partial:
            partial.write(writes.pop(0))
        return SimpleNamespace(local=local, remote=remote)

    slurm_client.get = get
    slurm_client.copy_zip_locally(str(tmp_path), '/remote/42_out')
    assert (tmp_path / '42_out.zip').read_bytes() == b'complete'
    assert not writes


def test_download_local_permission_error_is_not_retried(
        slurm_client, recovery_clock, tmp_path):
    slurm_client.ssh_recovery_timeout = 900
    slurm_client.sftp = Mock(return_value=SimpleNamespace(
        stat=Mock(return_value=SimpleNamespace(st_size=1, st_mtime=100)),
        get_channel=Mock(return_value=Mock())))
    slurm_client.get = Mock(side_effect=PermissionError('local storage denied'))
    with pytest.raises(PermissionError):
        slurm_client.copy_zip_locally(str(tmp_path), '/remote/42_out')
    slurm_client.get.assert_called_once()


@pytest.mark.parametrize('warn', [False, True])
def test_read_recovers_missing_exit_status(slurm_client, recovery_clock, warn):
    slurm_client.ssh_recovery_timeout = 20
    slurm_client.close = Mock()
    lost = Result(connection=None, exited=-1, stdout='partial')
    complete = Result(connection=None, exited=0, stdout='complete')
    slurm_client.run = Mock(side_effect=[
        lost if warn else UnexpectedExit(lost), complete])
    assert slurm_client.run_read_only('read', warn=warn) is complete
    assert slurm_client.run.call_count == 2


def test_durable_command_recovers_missing_exit_status(
        slurm_client, recovery_clock):
    slurm_client.ssh_recovery_timeout = 20
    slurm_client.close = Mock()
    complete = Result(connection=None, exited=0, stdout='complete')
    slurm_client.run_commands = Mock(side_effect=[
        Result(connection=None, exited=-1), complete])
    assert slurm_client._run_locked(
        'work', '/data/work.lock', 'durable operation') is complete


def test_long_healthy_attempt_does_not_consume_recovery_window(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock())
    attempts = []

    def operation():
        attempts.append(True)
        if len(attempts) == 1:
            recovery_clock.now += 901
            raise EOFError()
        return 'recovered'

    assert ssh_recovery.retry(client, operation, 'long archive') == 'recovered'
    assert recovery_clock.now == 902


def test_new_outage_gets_full_budget_after_healthy_work(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=10, close=Mock())

    def operation():
        first = Mock(side_effect=[EOFError(), 'first'])
        assert ssh_recovery.retry(client, first, 'first outage') == 'first'
        recovery_clock.now += 100
        second = Mock(side_effect=[EOFError(), 'second'])
        return ssh_recovery.retry(client, second, 'second outage')

    assert ssh_recovery.retry(
        client, operation, 'helper lifecycle') == 'second'
    assert recovery_clock.now == 102


@pytest.mark.parametrize('outer_retry', [False, True])
def test_job_wait_keeps_heartbeat_during_nested_poll_recovery(
        slurm_client, recovery_clock, outer_retry):
    from biomero.slurm_client import SlurmJob
    slurm_client.ssh_recovery_timeout = 900
    slurm_client.close = Mock()
    heartbeat_times = []

    def poll(*args, **kwargs):
        if recovery_clock.now < 600:
            raise EOFError()
        return Result(connection=None, exited=0, stdout='42 COMPLETED\n')

    slurm_client.run_commands = poll
    job = SlurmJob.from_job_id(42)

    def heartbeat():
        heartbeat_times.append(recovery_clock.now)

    def wait():
        return job.wait_for_completion(
            slurm_client, heartbeat=heartbeat, track_progress=False,
            update_task=False, strict_status=True)

    outcome = (ssh_recovery.retry(slurm_client, wait, 'helper',
                                  heartbeat=heartbeat)
               if outer_retry else wait())
    assert outcome == 'COMPLETED'
    assert max(b - a for a, b in zip(
        heartbeat_times, heartbeat_times[1:])) <= 10
    assert job.job_state == 'COMPLETED'


def test_nested_heartbeat_transport_shaped_error_is_not_retried(
        recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=20, close=Mock())
    failure = EOFError('caller-owned heartbeat failed')
    heartbeat = Mock(side_effect=failure)
    with pytest.raises(EOFError) as caught:
        ssh_recovery.retry(client, lambda: ssh_recovery.retry(
            client, Mock(), 'nested', heartbeat=heartbeat), 'outer')
    assert caught.value is failure
    heartbeat.assert_called_once()
    assert not recovery_clock.waits


def test_scope_rejects_a_heartbeat_that_is_not_callable():
    with pytest.raises(TypeError):
        with ssh_recovery.scope(SimpleNamespace(), heartbeat='not callable'):
            pass


DEFAULT_LOCK = 'flock -w {wait_seconds} -E {busy_exit_code} {lock_file}'


@pytest.mark.parametrize('ini,environment,expected', [
    ('', {}, DEFAULT_LOCK),
    ('', {'BIOMERO_SLURM_LOCK_CMD': ''}, DEFAULT_LOCK),
    ('lockf {busy_exit_code} {lock_file}', {}, 'lockf {busy_exit_code} {lock_file}'),
    ('lockf {busy_exit_code} {lock_file}', {'BIOMERO_SLURM_LOCK_CMD': ''}, 'lockf {busy_exit_code} {lock_file}'),
    ('lockf {busy_exit_code} {lock_file}', {'BIOMERO_SLURM_LOCK_CMD': 'mylock {busy_exit_code} {lock_file}'},
     'mylock {busy_exit_code} {lock_file}'),
    ('', {'BIOMERO_SLURM_LOCK_CMD': 'mylock {busy_exit_code} {lock_file}'},
     'mylock {busy_exit_code} {lock_file}'),
])
def test_lock_command_precedence(slurm_client_from_config_factory, ini,
                                 environment, expected):
    """Default, then ini, then environment; blank values keep the default."""
    client = slurm_client_from_config_factory(
        {'slurm_lock_cmd': ini}, environment)
    assert client.slurm_lock_cmd == expected


def test_lock_command_defaults_to_flock_without_configuration():
    from biomero.slurm_client import SlurmClient
    assert SlurmClient(config_only=True).slurm_lock_cmd == DEFAULT_LOCK


def test_a_lock_command_with_an_unknown_placeholder_is_rejected(
        slurm_client_from_config_factory):
    with pytest.raises(ValueError, match='slurm_lock_cmd'):
        slurm_client_from_config_factory(
            {'slurm_lock_cmd': 'flock {file}'})


def test_a_busy_operation_with_window_zero_raises_the_pending_error(
        slurm_client):
    slurm_client.ssh_recovery_timeout = 0
    slurm_client.run_commands = Mock(return_value=Result(
        connection=None, exited=200, stderr='locked'))
    with pytest.raises(ssh_recovery.RemoteOperationStillPending):
        slurm_client._run_locked('work', '/data/work.lock', 'zip results')
    slurm_client.run_commands.assert_called_once()


def test_exit_127_names_the_lock_command_setting(slurm_client):
    slurm_client.run_commands = Mock(return_value=Result(
        connection=None, exited=127, stderr='flock: not found'))
    with pytest.raises(RuntimeError, match='slurm_lock_cmd'):
        slurm_client._run_locked('work', '/data/work.lock', 'zip results')


def test_an_inner_window_never_outlasts_the_outer_outage(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=100, close=Mock())
    inner_failures = []

    def outer():
        recovery_clock.now += 0  # the outer outage starts at its failure

        def inner():
            inner_failures.append(recovery_clock.now)
            raise EOFError()
        ssh_recovery.retry(client, inner, 'inner')

    with ssh_recovery.scope(client) as state:
        state.windows.append(ssh_recovery.OutageWindow(
            100, outage_deadline=10))
        with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
            outer()
    assert recovery_clock.now == 10


def test_recovery_window_sets_native_timeouts_and_keepalive(monkeypatch):
    from fabric import Connection
    from biomero.slurm_client import SlurmClient
    on = SlurmClient(config_only=True, ssh_recovery_timeout=900)
    assert on.connect_timeout == 10
    assert on.connect_kwargs['banner_timeout'] == 30
    assert on.connect_kwargs['auth_timeout'] == 30
    transport = Mock()
    monkeypatch.setattr(Connection, 'open', lambda self: None)
    monkeypatch.setattr(SlurmClient, 'transport',
                        property(lambda self: transport), raising=False)
    on.open()
    transport.set_keepalive.assert_called_once_with(15)


def test_window_zero_sets_no_native_timeouts_and_no_keepalive(monkeypatch):
    from fabric import Connection
    from biomero.slurm_client import SlurmClient
    off = SlurmClient(config_only=True, ssh_recovery_timeout=0)
    assert off.connect_timeout is None
    assert 'banner_timeout' not in off.connect_kwargs
    assert 'auth_timeout' not in off.connect_kwargs
    transport = Mock()
    monkeypatch.setattr(Connection, 'open', lambda self: None)
    monkeypatch.setattr(SlurmClient, 'transport',
                        property(lambda self: transport), raising=False)
    off.open()
    transport.set_keepalive.assert_not_called()
