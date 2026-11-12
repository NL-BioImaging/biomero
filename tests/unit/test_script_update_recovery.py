"""Script preparation must recover without publishing a partial executable."""
import io
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fabric import Result

from biomero import ssh_recovery
from .test_ssh_recovery import recovery_clock  # noqa: F401
from .test_slurm_client import slurm_client, set_env_vars  # noqa: F401

PARTIAL = '/jobs/work.sh.biomero-partial'
SCRIPT = 'echo complete\n'


def test_script_publication_retries_with_complete_content(recovery_clock):
    files = {'/jobs/work.sh': b'previous'}
    attempts = []
    sftp = Mock()
    sftp.stat.side_effect = lambda path: SimpleNamespace(
        st_size=len(files[path]), st_mode=0o100750)
    sftp.posix_rename.side_effect = lambda src, dst: files.update(
        {dst: files.pop(src)})

    def put(*, local, remote):
        assert files['/jobs/work.sh'] == b'previous'
        content = local.read().encode()
        assert content == b'echo complete\n'
        attempts.append(remote)
        files[remote] = content
        if len(attempts) == 1:
            files[remote] = b'echo'
            raise EOFError('server connection dropped')
        return SimpleNamespace(remote=remote)

    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock(),
                             sftp=Mock(return_value=sftp), put=put)
    result = ssh_recovery.atomic_put(
        client, io.StringIO(SCRIPT), '/jobs/work.sh')
    assert result.remote == '/jobs/work.sh'
    # The interrupted partial file is overwritten by the retry, never
    # published, and no partial file is left behind.
    assert files == {'/jobs/work.sh': b'echo complete\n'}
    assert attempts == [PARTIAL, PARTIAL]
    sftp.chmod.assert_called_once_with(PARTIAL, 0o750)


@pytest.mark.parametrize('window,retried', [(0, False), (900, True)])
def test_generated_script_update_retries_only_within_the_window(
        slurm_client, recovery_clock, monkeypatch, window, retried):
    client = slurm_client
    client.ssh_recovery_timeout = window
    client.close = Mock()
    client.slurm_model_jobs = {'workflow': 'work.sh'}
    client.get_workflow_parameters = Mock(return_value={})
    client.generic_descriptor_from_github = Mock(return_value={})
    client.workflow_params_to_subs = Mock(return_value={})
    client.workflow_template_subs = Mock(return_value={})
    client.generate_slurm_job_for_workflow = Mock(return_value='echo done\n')
    client.run = Mock(side_effect=[EOFError('lost mkdir response'),
                                   Result(connection=None, exited=0)])
    publish = Mock(return_value='published')
    monkeypatch.setattr(ssh_recovery, 'atomic_put', publish)
    if retried:
        assert client.update_slurm_scripts(generate_jobs=True) == 'published'
        assert client.run.call_count == 2
        publish.assert_called_once()
    else:
        with pytest.raises(EOFError):
            client.update_slurm_scripts(generate_jobs=True)
        client.run.assert_called_once()
        publish.assert_not_called()


def test_git_update_serializes_surviving_command(slurm_client, recovery_clock):
    client = slurm_client
    client.ssh_recovery_timeout = 900
    client.slurm_script_repo = 'https://example.invalid/jobs.git'
    client.slurm_script_path = '/jobs'
    client.close = Mock()
    client.run_commands = Mock(side_effect=[
        EOFError('lost update response'),
        Result(connection=None, exited=200),
        Result(connection=None, exited=0)])
    assert client.update_slurm_scripts().ok
    assert client.run_commands.call_count == 3
    commands = [call.args[0][0] for call in client.run_commands.call_args_list]
    assert len(set(commands)) == 1
    assert 'flock -w 5 -E 200 /jobs.biomero-update.lock' in commands[0]


def test_truncated_upload_is_never_published_and_is_retried(recovery_clock):
    sftp = Mock()
    sftp.stat.return_value.st_size = 2
    sftp.stat.return_value.st_mode = 0o100750
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock(),
                             sftp=Mock(return_value=sftp), put=Mock())
    with pytest.raises(ssh_recovery.SSHRecoveryTimeout) as expired:
        ssh_recovery.atomic_put(client, io.StringIO(SCRIPT), '/jobs/work.sh')
    assert isinstance(expired.value.__cause__, ssh_recovery.IncompleteTransfer)
    sftp.posix_rename.assert_not_called()
    assert client.put.call_count > 1


def test_lost_rename_ack_recovers_complete_script_and_mode(recovery_clock):
    sftp = Mock()
    sftp.stat.return_value = SimpleNamespace(st_size=14, st_mode=0o100750)
    sftp.posix_rename.side_effect = [
        EOFError('rename committed but ack lost'), None]
    client = SimpleNamespace(ssh_recovery_timeout=900, close=Mock(),
                             sftp=Mock(return_value=sftp), put=Mock())
    result = ssh_recovery.atomic_put(
        client, io.StringIO(SCRIPT), '/jobs/work.sh')
    assert result.remote == '/jobs/work.sh'
    assert sftp.posix_rename.call_count == 2
    assert sftp.chmod.call_count == 2
    assert all(call.args[1] == 0o750 for call in sftp.chmod.call_args_list)


@pytest.mark.parametrize('window', [0, 900])
def test_closed_sftp_after_upload_preserves_script_and_recovers(
        recovery_clock, window):
    files = {'/jobs/work.sh': b'previous'}
    attempts = []
    connected = True
    sftp = Mock()

    def check_stat(path):
        if not connected:
            # Paramiko Channel._send raises socket.error without an errno.
            raise OSError('Socket is closed')
        return SimpleNamespace(st_size=len(files[path]), st_mode=0o100750)

    def reconnect():
        nonlocal connected
        connected = True
        return sftp

    def put(*, local, remote):
        nonlocal connected
        assert files['/jobs/work.sh'] == b'previous'
        files[remote] = local.read().encode()
        attempts.append(remote)
        if len(attempts) == 1:
            connected = False
        return SimpleNamespace(remote=remote)

    sftp.stat.side_effect = check_stat
    sftp.posix_rename.side_effect = lambda src, dst: files.update(
        {dst: files.pop(src)})
    client = SimpleNamespace(ssh_recovery_timeout=window, close=Mock(),
                             sftp=reconnect, put=put)
    if not window:
        with pytest.raises(OSError, match='Socket is closed'):
            ssh_recovery.atomic_put(
                client, io.StringIO(SCRIPT), '/jobs/work.sh')
        assert files['/jobs/work.sh'] == b'previous'
        assert len(attempts) == 1
        client.close.assert_not_called()
        return
    result = ssh_recovery.atomic_put(
        client, io.StringIO(SCRIPT), '/jobs/work.sh')
    assert result.remote == '/jobs/work.sh'
    assert files['/jobs/work.sh'] == b'echo complete\n'
    assert attempts == [PARTIAL, PARTIAL]
    client.close.assert_called_once()
    sftp.chmod.assert_called_once_with(PARTIAL, 0o750)
