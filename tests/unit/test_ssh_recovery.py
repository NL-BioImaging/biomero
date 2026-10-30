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
    ({}, {}, 0),
    ({'ssh_recovery_timeout': '900'}, {}, 900),
    ({'ssh_recovery_timeout': '900'}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': '1200'}, 1200),
    ({'ssh_recovery_timeout': '900'}, {'BIOMERO_SSH_RECOVERY_TIMEOUT': ''}, 900),
])
def test_config_precedence(slurm_client_from_config_factory, ini,
                           environment, expected):
    client = slurm_client_from_config_factory(ini, environment)
    assert client.ssh_recovery_timeout == expected


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
        'for arg in "$@"; do case "$arg" in --comment=*) '
        'token=${arg#--comment=};; esac; done\n'
        f'printf "42|%s\\n" "$token" >> {shlex.quote(str(jobs))}\n'
        'echo "Submitted batch job 42"\n')
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
    command = 'sbatch --comment=biomero-' + str(task_id) + ' job.sh'
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


def test_submission_receipt_survives_a_new_client(shell_client, recovery_clock):
    task_id = uuid4()
    command = 'sbatch --comment=biomero-' + str(task_id) + ' job.sh'
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
