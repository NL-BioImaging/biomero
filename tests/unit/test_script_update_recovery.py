"""Script preparation must recover without publishing a partial executable."""
import io
import shlex
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


class _RemoteScripts:
    """In-memory remote folder behind the client's put, sftp and run."""

    def __init__(self, client, during_first_put=None):
        self.files = {}
        self.put_targets = []
        self.removed = []
        self._during_first_put = during_first_put
        sftp = Mock()
        sftp.stat.side_effect = self._stat
        sftp.posix_rename.side_effect = self._rename
        client.sftp = Mock(return_value=sftp)
        client.put = self._put
        client.run = Mock(side_effect=self._run)

    def _stat(self, path):
        if path not in self.files:
            raise FileNotFoundError(path)
        return SimpleNamespace(st_size=len(self.files[path]),
                               st_mode=0o100750)

    def _rename(self, source, destination):
        self.files[destination] = self.files.pop(source)

    def _put(self, *, local, remote):
        self.put_targets.append(remote)
        self.files[remote] = local.read().encode()
        if len(self.put_targets) == 1 and self._during_first_put:
            # A second workflow publishes the same script right now.
            self._during_first_put()
        return SimpleNamespace(remote=remote)

    def _run(self, command, **kwargs):
        if command.startswith('rm -f'):
            self.removed.append(command)
            _, _, *paths = shlex.split(command)
            for path in paths:
                self.files.pop(path, None)
        return Result(connection=None, exited=0)


@pytest.fixture
def script_client(slurm_client, recovery_clock):
    client = slurm_client
    client.ssh_recovery_timeout = 900
    client.close = Mock()
    client.slurm_script_path = '/jobs'
    client.slurm_model_jobs = {'workflow': 'work.sh'}
    client.get_workflow_parameters = Mock(return_value={})
    client.generic_descriptor_from_github = Mock(return_value={})
    client.workflow_params_to_subs = Mock(return_value={})
    client.workflow_template_subs = Mock(return_value={})
    client.generate_slurm_job_for_workflow = Mock(return_value=SCRIPT)
    return client


def test_concurrent_publications_of_one_script_do_not_share_staging(
        script_client):
    client = script_client
    remote = _RemoteScripts(
        client, lambda: client.update_slurm_scripts(generate_jobs=True))
    client.update_slurm_scripts(generate_jobs=True)
    assert len(remote.put_targets) == 2
    assert len(set(remote.put_targets)) == 2
    assert all(target.startswith('/jobs/work.sh.biomero-partial-')
               for target in remote.put_targets)
    # Both succeeded, the script is complete and no staging file is left.
    assert remote.files == {'/jobs/work.sh': SCRIPT.encode()}


def test_failed_publication_removes_its_own_staging_file(script_client):
    client = script_client
    remote = _RemoteScripts(client)
    remote.files['/jobs/work.sh'] = b'previous'
    sftp = client.sftp.return_value
    sftp.posix_rename.side_effect = PermissionError('read-only folder')
    with pytest.raises(PermissionError):
        client.update_slurm_scripts(generate_jobs=True)
    staging = remote.put_targets[0]
    assert remote.removed == [f"rm -f -- {shlex.quote(staging)}"]
    # Only the staging file went; the published script is untouched.
    assert remote.files == {'/jobs/work.sh': b'previous'}


def test_failed_cleanup_does_not_hide_the_upload_error(script_client):
    client = script_client
    remote = _RemoteScripts(client)
    sftp = client.sftp.return_value
    sftp.posix_rename.side_effect = PermissionError('read-only folder')
    prepare_directory = client.run

    def run(command, **kwargs):
        if command.startswith('rm -f'):
            raise EOFError('connection is gone')
        return prepare_directory(command, **kwargs)

    client.run = run
    with pytest.raises(PermissionError, match='read-only folder'):
        client.update_slurm_scripts(generate_jobs=True)
    assert len(remote.put_targets) == 1


def test_staging_name_is_stable_across_retries_of_one_publication(
        script_client):
    client = script_client
    remote = _RemoteScripts(client)
    sftp = client.sftp.return_value
    renames = []

    def drop_first_rename(source, destination):
        renames.append(source)
        if len(renames) == 1:
            raise EOFError('connection dropped')
        remote._rename(source, destination)

    sftp.posix_rename.side_effect = drop_first_rename
    client.update_slurm_scripts(generate_jobs=True)
    assert len(remote.put_targets) == 2
    assert len(set(remote.put_targets)) == 1
