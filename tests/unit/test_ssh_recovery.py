"""Transport recovery must never replay arbitrary remote side effects."""
import errno
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock
from pathlib import Path
from uuid import uuid4
import os
import shlex
import shutil
import subprocess

import pytest
from fabric import Result
from invoke.exceptions import UnexpectedExit
from paramiko import AuthenticationException, SSHException

from biomero import ssh_recovery
from .test_slurm_client import (  # noqa: F401
    slurm_client, set_env_vars, slurm_client_from_config_factory)


@pytest.mark.parametrize('error,expected', [
    (EOFError(), True),
    (ConnectionResetError(), True),
    (TimeoutError(), True),
    (OSError(errno.EHOSTUNREACH, 'host unreachable'), True),
    (OSError('Socket is closed'), True),
    (OSError('Permission denied'), False),
    (OSError(errno.EACCES, 'Socket is closed'), False),
    (PermissionError('Socket is closed'), False),
    (OSError('remote file Socket is closed not found'), False),
    (SSHException('SSH session not active'), True),
    (SSHException('Error reading SSH protocol banner'), True),
    (AuthenticationException('bad credentials'), False),
    (PermissionError('denied'), False),
    (FileNotFoundError('missing file'), False),
    (SSHException('remote command failed'), False),
    (ValueError('bad configuration'), False),
    (UnexpectedExit(Result(connection=None, exited=1, stderr='permission denied')), False),
])
def test_transient_classifier(error, expected):
    assert ssh_recovery.is_transient(error) is expected


@pytest.fixture
def recovery_clock(monkeypatch):
    clock = SimpleNamespace(now=0, waits=[])

    def sleep(seconds):
        clock.now += seconds
        clock.waits.append(seconds)

    monkeypatch.setattr(ssh_recovery.time, 'monotonic', lambda: clock.now)
    monkeypatch.setattr(ssh_recovery.time, 'sleep', sleep)
    return clock


def test_ten_minute_outage_resumes_same_operation(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock())
    calls = []

    def operation():
        calls.append(recovery_clock.now)
        if recovery_clock.now < 600:
            raise ConnectionResetError('HPC unavailable')
        return 'existing job 42'

    assert ssh_recovery.retry(client, operation, 'monitor job 42') == 'existing job 42'
    assert 600 <= recovery_clock.now < 630
    assert max(recovery_clock.waits) <= 30
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


def test_command_timeout_is_recoverable_only_through_safe_read(recovery_clock):
    from invoke.exceptions import CommandTimedOut
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


def test_explicit_longer_read_timeout_is_preserved_until_outage(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock())
    operation = Mock(side_effect=[EOFError(), 'observed'])
    @ssh_recovery.read_only
    def read(client, **kwargs):
        return operation(**kwargs)
    assert read(client, timeout=120) == 'observed'
    assert [call.kwargs['timeout'] for call in operation.call_args_list] == [120, 120]
    operation = Mock(return_value='observed')
    with ssh_recovery.scope(client) as state:
        state.deadline = recovery_clock.now + 2
        assert read(client, timeout=120) == 'observed'
    assert operation.call_args.kwargs['timeout'] == 2


def test_read_decorator_preserves_metadata_and_calling_contract():
    import inspect
    from biomero.slurm_client import SlurmClient
    for name in ('run_read_only', 'run_commands_read_only'):
        method = getattr(SlurmClient, name)
        assert method.__name__ == name
        assert method.__doc__ == method.__wrapped__.__doc__
        assert inspect.signature(method) == inspect.signature(method.__wrapped__)


def test_nested_native_bounds_use_remaining_outage_budget(recovery_clock):
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True, ssh_recovery_timeout=20)
    original = dict(client.connect_kwargs)
    with ssh_recovery.scope(client) as state:
        state.deadline = 1.5
        with ssh_recovery.native_timeouts(client):
            assert client.connect_kwargs['auth_timeout'] == 1.5
            assert client.connect_timeout == 1.5
            assert ssh_recovery.io_timeout(client) == 1.5
            assert ssh_recovery.io_timeout(client, .2) == .2
        assert dict(client.connect_kwargs) == original


def test_disabled_keeps_single_attempt(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=0, close=Mock())
    operation = Mock(side_effect=EOFError())
    with pytest.raises(EOFError):
        ssh_recovery.retry(client, operation, 'read')
    operation.assert_called_once()
    client.close.assert_not_called()
    assert not recovery_clock.waits


def test_nested_retries_share_the_original_deadline(recovery_clock):
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
    ({'ssh_recovery_timeout': '900'}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': '1200'}, 1200),
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


def test_older_client_without_recovery_capability_remains_compatible():
    assert not ssh_recovery.enabled(SimpleNamespace())


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
    client.run_commands_read_only = Mock(side_effect=FileNotFoundError('no log'))
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


def test_status_recovers_without_publishing_failed(slurm_client, recovery_clock):
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
        stat=Mock(return_value=remote_stat), get_channel=Mock(return_value=Mock())))
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


@pytest.fixture
def shell_client(tmp_path):
    """Execute receipt scripts against fake scheduler commands, not an HPC."""
    if not shutil.which('sh') or not shutil.which('flock'):
        pytest.skip('Receipt execution requires POSIX sh and flock')
    binary = tmp_path / 'bin'
    binary.mkdir()
    jobs = tmp_path / 'jobs'
    counter = tmp_path / 'submissions'
    (binary / 'sbatch').write_text(
        '#!/bin/sh\n'
        f'echo submitted >> {shlex.quote(str(counter))}\n'
        'for arg in "$@"; do case "$arg" in --job-name=*) '
        'token=${arg#--job-name=};; esac; done\n'
        f'printf "42|%s\\n" "$token" >> {shlex.quote(str(jobs))}\n'
        'case " $* " in *" --parsable "*) echo 42;; '
        '*) echo "Submitted batch job 42";; esac\n')
    for name in ('squeue', 'sacct'):
        (binary / name).write_text(
            f'#!/bin/sh\ncat {shlex.quote(str(jobs))} 2>/dev/null || true\n')
    for script in binary.iterdir():
        script.chmod(0o755)
    environment = {**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH']}
    client = SimpleNamespace(
        slurm_data_path=str(tmp_path), ssh_recovery_timeout=35, close=Mock(),
        counter=counter, jobs=jobs)

    def run(commands, env=None, warn=False):
        result = subprocess.run(' && '.join(commands), shell=True,
                                capture_output=True, text=True, env=environment)
        return Result(connection=None, exited=result.returncode,
                      stdout=result.stdout, stderr=result.stderr)

    client.run_commands = run
    return client


@pytest.mark.parametrize('lose_receipt', [False, True])
def test_lost_submission_acknowledgement_never_submits_twice(
        shell_client, recovery_clock, lose_receipt):
    task_id = uuid4()
    command = 'sbatch --job-name=biomero-' + str(task_id) + ' job.sh'
    original = shell_client.run_commands
    calls = []

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        assert result.ok, result.stderr
        calls.append(result)
        if len(calls) == 1:
            if lose_receipt:
                (Path(shell_client.slurm_data_path) / '.biomero-recovery' /
                 (str(task_id) + '.job')).unlink()
            raise EOFError('accepted but SSH response lost')
        return result

    shell_client.run_commands = lose_response
    result = ssh_recovery.submit(shell_client, command, {}, task_id)
    assert result.stdout == 'Submitted batch job 42\n'
    assert shell_client.counter.read_text().splitlines() == ['submitted']
    assert len(calls) == 2


def test_unknown_submission_waits_without_resubmission(
        shell_client, recovery_clock):
    task_id = uuid4()
    state = Path(shell_client.slurm_data_path) / '.biomero-recovery'
    state.mkdir()
    (state / (str(task_id) + '.intent')).write_text('2026-10-30T00:00:00')
    with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
        ssh_recovery.submit(shell_client, 'sbatch job.sh', {}, task_id)
    assert not shell_client.counter.exists()


@pytest.mark.parametrize('lose_receipt', [False, True])
def test_shallower_lost_acknowledgement_adopts_one_job(
        shell_client, recovery_clock, lose_receipt):
    from biomero.remote_shallower import _submit_once
    state = str(Path(shell_client.slurm_data_path) / 'shallow')
    original = shell_client.run_commands
    calls = []

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            assert result.ok, result.stderr
            if lose_receipt:
                Path(state + '.job').unlink()
            # The actual Fabric result after a lost channel, not a synthetic
            # EOFError after a successful acknowledgement.
            return Result(connection=None, exited=-1)
        return result

    shell_client.run_commands = lose_response
    assert ssh_recovery.retry(shell_client, lambda: _submit_once(
        shell_client, 'sbatch --parsable --job-name=helper job.sh', state,
        job_name='helper'), 'helper submission') == 42
    assert shell_client.counter.read_text().splitlines() == ['submitted']


def test_shallower_ambiguous_intent_never_resubmits(shell_client, recovery_clock):
    from biomero.remote_shallower import _submit_once
    state = str(Path(shell_client.slurm_data_path) / 'shallow')
    Path(state + '.intent').write_text('2026-11-03T00:00:00')
    shell_client.jobs.write_text('41|helper\n42|helper\n99|other\n')
    with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
        ssh_recovery.retry(shell_client, lambda: _submit_once(
            shell_client, 'sbatch --parsable job.sh', state, job_name='helper'),
            'helper submission')
    assert not shell_client.counter.exists()


def test_submission_receipt_survives_a_new_client(shell_client, recovery_clock):
    task_id = uuid4()
    command = 'sbatch --job-name=biomero-' + str(task_id) + ' job.sh'
    ssh_recovery.submit(shell_client, command, {}, task_id)
    replacement = SimpleNamespace(**shell_client.__dict__)
    ssh_recovery.submit(replacement, command, {}, task_id)
    assert shell_client.counter.read_text().splitlines() == ['submitted']


def test_changed_submission_is_rejected_without_new_job(
        shell_client, recovery_clock):
    task_id = uuid4()
    ssh_recovery.submit(shell_client, 'sbatch job.sh', {}, task_id)
    with pytest.raises(UnexpectedExit) as rejected:
        ssh_recovery.submit(shell_client, 'sbatch different.sh', {}, task_id)
    assert 'Submission settings changed' in rejected.value.result.stderr
    assert shell_client.counter.read_text().splitlines() == ['submitted']


def test_archive_response_loss_reuses_complete_archive(
        shell_client, recovery_clock):
    root = Path(shell_client.slurm_data_path)
    count = root / 'archives'
    shell_client.get_zip_command = lambda location, name: (
        f'echo created >> {shlex.quote(str(count))}; '
        f'printf "archive" > {shlex.quote(str(root / (name + ".zip")))}')
    original = shell_client.run_commands
    calls = []

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            raise EOFError()
        return result

    shell_client.run_commands = lose_response
    ssh_recovery.archive(shell_client, str(root), '42_out', {})
    assert count.read_text().splitlines() == ['created']
    assert (root / '42_out.zip').read_text() == 'archive'


def test_input_extraction_response_loss_is_not_repeated(
        shell_client, recovery_clock):
    root = Path(shell_client.slurm_data_path)
    count = root / 'extractions'
    shell_client.get_unzip_command = lambda name: (
        f'echo extracted >> {shlex.quote(str(count))}')
    original = shell_client.run_commands
    calls = []

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            raise EOFError()
        return result

    shell_client.run_commands = lose_response
    ssh_recovery.unpack(shell_client, 'inputs', {})
    assert count.read_text().splitlines() == ['extracted']


def test_restart_reuses_task_with_missing_job_acknowledgement(slurm_client):
    wf_id, task_id = uuid4(), uuid4()
    task = SimpleNamespace(id=task_id, task_name='segment',
                           task_version='v1', input_data='inputs', params={'x': 1})
    tracker = Mock()
    tracker.repository.get.side_effect = lambda key: (
        SimpleNamespace(tasks=[task_id]) if key == wf_id else task)
    slurm_client.workflowTracker = tracker
    slurm_client.ssh_recovery_timeout = 900
    assert slurm_client._submission_task(
        wf_id, 'segment', 'v1', 'inputs', {'x': 1}) == task_id
    tracker.add_task_to_workflow.assert_not_called()


def test_changed_resume_parameters_do_not_submit(slurm_client):
    wf_id, task_id = uuid4(), uuid4()
    task = SimpleNamespace(id=task_id, task_name='segment',
                           task_version='v1', input_data='inputs', params={'x': 1})
    tracker = Mock()
    tracker.repository.get.side_effect = lambda key: (
        SimpleNamespace(tasks=[task_id]) if key == wf_id else task)
    slurm_client.workflowTracker = tracker
    slurm_client.ssh_recovery_timeout = 900
    with pytest.raises(RuntimeError, match='parameters changed'):
        slurm_client._submission_task(
            wf_id, 'segment', 'v1', 'inputs', {'x': 2})
    tracker.add_task_to_workflow.assert_not_called()


def test_conversion_uses_reconcilable_job_name(slurm_client):
    token = 'biomero-' + str(uuid4())
    command, _, _, _ = slurm_client.get_conversion_command(
        '/data/inputs', 'config.txt', submission_token=token)
    assert '--job-name=' + token in command
    assert '--job-name=conversion' not in command


def test_workflow_token_is_not_a_workflow_parameter(slurm_client):
    token = 'biomero-' + str(uuid4())
    slurm_client.slurm_model_paths = {'wf': 'workflow'}
    slurm_client.slurm_model_jobs = {'wf': 'wf.sh'}
    slurm_client.slurm_model_jobs_params = {'wf': []}
    slurm_client.slurm_model_images = {'wf': 'registry/image'}
    command, environment = slurm_client.get_workflow_command(
        'wf', '1.0', 'input', _submission_token=token)
    assert '--job-name=' + token in command
    assert not any('SUBMISSION_TOKEN' in key for key in environment)


def test_recorded_job_is_authoritative_even_if_remote_receipt_is_missing(
        slurm_client, monkeypatch):
    task_id = uuid4()
    slurm_client.ssh_recovery_timeout = 900
    slurm_client.workflowTracker = Mock()
    slurm_client.workflowTracker.repository.get.return_value = (
        SimpleNamespace(job_ids=[42]))
    submit = Mock(side_effect=AssertionError('Must not submit a known job'))
    monkeypatch.setattr(ssh_recovery, 'submit', submit)
    result = slurm_client._submit_tracked('sbatch job.sh', {}, task_id)
    assert slurm_client.extract_job_id(result) == 42
    submit.assert_not_called()


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
    assert ssh_recovery.run_durable(slurm_client, 'locked command', {},
                                    'durable operation') is complete


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

    assert ssh_recovery.retry(client, operation, 'helper lifecycle') == 'second'
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
    heartbeat = lambda: heartbeat_times.append(recovery_clock.now)

    def wait():
        return job.wait_for_completion(
            slurm_client, heartbeat=heartbeat, track_progress=False,
            update_task=False, strict_status=True)

    assert (ssh_recovery.retry(slurm_client, wait, 'helper', heartbeat=heartbeat)
            if outer_retry else wait()) == 'COMPLETED'
    assert max(b - a for a, b in zip(
        heartbeat_times, heartbeat_times[1:])) <= 10
    assert job.job_state == 'COMPLETED'


def test_nested_heartbeat_transport_shaped_error_is_not_retried(recovery_clock):
    client = SimpleNamespace(ssh_recovery_timeout=20, close=Mock())
    failure = EOFError('caller-owned heartbeat failed')
    heartbeat = Mock(side_effect=failure)
    with pytest.raises(EOFError) as caught:
        ssh_recovery.retry(client, lambda: ssh_recovery.retry(
            client, Mock(), 'nested', heartbeat=heartbeat), 'outer')
    assert caught.value is failure
    heartbeat.assert_called_once()
    assert not recovery_clock.waits


def test_shallower_contention_recovers_after_lock_release(
        shell_client, recovery_clock):
    import fcntl
    from threading import Timer
    from biomero.remote_shallower import _submit_once
    state = str(Path(shell_client.slurm_data_path) / 'shallow')
    lock = open(state + '.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    original = shell_client.run_commands

    def short_lock_wait(commands, **kwargs):
        commands = [c.replace('flock -w 30 ', 'flock -w 0.05 ').replace(
            'flock -w 5 ', 'flock -w 0.05 ') for c in commands]
        return original(commands, **kwargs)

    shell_client.run_commands = short_lock_wait
    shell_client.ssh_recovery_timeout = 900
    release = Timer(0.25, lambda: fcntl.flock(lock, fcntl.LOCK_UN))
    release.start()
    try:
        assert ssh_recovery.retry(shell_client, lambda: _submit_once(
            shell_client, 'sbatch --parsable --job-name=helper job.sh', state,
            job_name='helper'), 'helper submission') == 42
    finally:
        release.join()
        lock.close()
    assert shell_client.counter.read_text().splitlines() == ['submitted']
