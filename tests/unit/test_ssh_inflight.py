"""Real private SSH connections must not hide silent in-flight outages."""
import socket
import select
import threading
import time
from types import SimpleNamespace

import paramiko
import pytest

from biomero import ssh_recovery
from biomero.slurm_client import SlurmClient


class PrivateRelay:
    def __init__(self, upstream):
        self.upstream = upstream
        self.paused = threading.Event()
        self.stopped = threading.Event()
        self.sockets = []
        self.listener = socket.socket()
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen()
        self.listener.settimeout(.1)
        self.port = self.listener.getsockname()[1]
        self.thread = threading.Thread(target=self.accept, daemon=True)
        self.thread.start()

    def accept(self):
        while not self.stopped.is_set():
            try:
                local, _ = self.listener.accept()
                remote = socket.create_connection(self.upstream, timeout=1)
            except socket.timeout:
                continue
            except OSError:
                return
            self.sockets.extend([local, remote])
            threading.Thread(target=self.forward, args=(local, remote),
                             daemon=True).start()

    def forward(self, local, remote):
        try:
            while not self.stopped.is_set():
                if self.paused.wait(0) or self.stopped.is_set():
                    self.stopped.wait(.01)
                    continue
                for source in select.select([local, remote], [], [], .02)[0]:
                    data = source.recv(65536)
                    if not data:
                        return
                    (remote if source is local else local).sendall(data)
        except OSError:
            pass
        finally:
            local.close()
            remote.close()

    def close(self):
        self.stopped.set()
        self.paused.clear()
        self.listener.close()
        for sock in self.sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            sock.close()
        self.thread.join(1)


@pytest.fixture
def private_ssh(tmp_path):
    """An authenticated SSH server/relay; no system SSH daemon or Slurm needed."""
    key = paramiko.RSAKey.generate(1024)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(.1)
    stopped = threading.Event()
    started = threading.Event()
    release_output = threading.Event()
    transports, commands = [], []
    file_state = SimpleNamespace(delay=0, auth_delay=0,
                                 read_started=threading.Event())

    class Handle(paramiko.SFTPHandle):
        def read(self, offset, length):
            file_state.read_started.set()
            time.sleep(file_state.delay)
            return super().read(offset, length)

    class Files(paramiko.SFTPServerInterface):
        def stat(self, path):
            return paramiko.SFTPAttributes.from_stat((tmp_path / 'data').stat())

        def open(self, path, flags, attr):
            handle = Handle(flags)
            handle.readfile = (tmp_path / 'data').open('rb')
            return handle

    (tmp_path / 'data').write_bytes(b'complete test data')

    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            time.sleep(file_state.auth_delay)
            return paramiko.AUTH_SUCCESSFUL

        def get_allowed_auths(self, username):
            return 'password'

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED

        def check_channel_exec_request(self, channel, command):
            command = command.decode()
            commands.append(command)

            def answer():
                started.set()
                if command == 'wait-output':
                    release_output.wait(5)
                # Let Paramiko send the exec acknowledgement first.
                time.sleep(.03)
                try:
                    channel.sendall(b'42 COMPLETED\n')
                    channel.send_exit_status(0)
                except (OSError, EOFError):
                    pass
                finally:
                    channel.close()

            threading.Thread(target=answer, daemon=True).start()
            return True

    def accept():
        while not stopped.is_set():
            try:
                sock, _ = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            transport = paramiko.Transport(sock)
            transports.append(transport)
            transport.add_server_key(key)
            transport.set_subsystem_handler('sftp', paramiko.SFTPServer, Files)
            try:
                transport.start_server(server=Server())
            except (paramiko.SSHException, EOFError):
                transport.close()

    server = threading.Thread(target=accept, daemon=True)
    server.start()
    relay = PrivateRelay(listener.getsockname())
    client = SlurmClient(
        host='127.0.0.1', port=relay.port, user='test', config_only=True,
        connect_kwargs={'password': 'private-test-only', 'allow_agent': False,
                        'look_for_keys': False}, ssh_recovery_timeout=.7)
    client.client.get_host_keys().add(
        '[127.0.0.1]:' + str(relay.port), key.get_name(), key)
    client.client.set_missing_host_key_policy(paramiko.RejectPolicy())
    client.config.run.in_stream = False
    client.config.run.hide = True
    client.open()
    fixture = SimpleNamespace(client=client, relay=relay, started=started,
                              release_output=release_output, commands=commands,
                              file_state=file_state, data_path=tmp_path / 'data')
    yield fixture
    release_output.set()
    relay.close()
    client.close()
    stopped.set()
    listener.close()
    for transport in transports:
        transport.close()
    server.join(1)


def observe(call):
    outcome = {}

    def run():
        try:
            outcome['result'] = call()
        except BaseException as error:
            outcome['error'] = error
        finally:
            outcome['ended'] = time.monotonic()

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    return worker, outcome


def test_channel_timeout_is_transient_but_not_arbitrary_ssh_error():
    assert ssh_recovery.is_transient(
        paramiko.SSHException('Timeout opening channel.'))
    assert not ssh_recovery.is_transient(
        paramiko.SSHException('Timeout opening channel. Permission denied'))


@pytest.mark.parametrize('phase', ['channel-open', 'command-read'])
def test_silent_outage_bounds_real_io_and_services_heartbeat(private_ssh, phase):
    client, relay = private_ssh.client, private_ssh.relay
    heartbeats = []
    assert client.check_job_status([42])[0] == {42: 'COMPLETED'}
    private_ssh.started.clear()

    def operation():
        with ssh_recovery.scope(client, heartbeat=lambda: heartbeats.append(
                (time.monotonic(), threading.get_ident()))):
            if phase == 'channel-open':
                return client.check_job_status([42])
            return client.run_read_only('wait-output', hide=True)

    if phase == 'channel-open':
        relay.paused.set()
    began = time.monotonic()
    worker, outcome = observe(operation)
    if phase == 'command-read':
        assert private_ssh.started.wait(2)
        time.sleep(.1)
        relay.paused.set()
        private_ssh.release_output.set()
    worker.join(2)
    bounded = not worker.is_alive()
    # Always restore the relay, including on the unfixed baseline.
    relay.paused.clear()
    worker.join(3)
    assert bounded, 'safe monitoring stayed blocked beyond the outage window'
    assert isinstance(outcome.get('error'), ssh_recovery.SSHRecoveryTimeout)
    assert outcome['ended'] - began < 2
    assert len(heartbeats) >= 2
    assert {thread for _, thread in heartbeats} == {worker.ident}
    assert not any(t.name.startswith('biomero-ssh-') for t in threading.enumerate())


def test_quiet_healthy_command_can_exceed_recovery_window(private_ssh):
    client = private_ssh.client
    timer = threading.Timer(1.5, private_ssh.release_output.set)
    timer.start()
    try:
        began = time.monotonic()
        result = ssh_recovery.run_durable(client, 'wait-output', {}, 'quiet archive')
        assert result.stdout == '42 COMPLETED\n'
        assert time.monotonic() - began > client.ssh_recovery_timeout
        assert private_ssh.commands == ['wait-output']
    finally:
        timer.cancel()


def test_outage_restored_inside_window_preserves_job_identity(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    assert client.check_job_status([42])[0] == {42: 'COMPLETED'}
    relay.paused.set()
    timer = threading.Timer(.3, relay.paused.clear)
    timer.start()
    try:
        assert client.check_job_status([42])[0] == {42: 'COMPLETED'}
        assert len(private_ssh.commands) == 2
        assert client.__dict__.get('_ssh_recovery_state') is None
    finally:
        relay.paused.clear()
        timer.cancel()


@pytest.mark.parametrize('failure', ['cancel', 'heartbeat'])
def test_inflight_cancellation_and_heartbeat_failure_cleanup(private_ssh, failure):
    client = private_ssh.client
    beats = []
    client.ssh_recovery_cancel = threading.Event()
    marker = EOFError('caller heartbeat failed')

    def pulse():
        beats.append(threading.get_ident())
        if len(beats) > 1 and failure == 'heartbeat':
            raise marker

    timer = threading.Timer(.3, client.ssh_recovery_cancel.set)
    if failure == 'cancel':
        timer.start()
    began = time.monotonic()
    try:
        with ssh_recovery.scope(client, heartbeat=pulse):
            with pytest.raises((ssh_recovery.SSHRecoveryCancelled, EOFError)) as exc:
                client.run_read_only('wait-output', hide=True)
        if failure == 'heartbeat':
            assert exc.value is marker
        else:
            assert isinstance(exc.value, ssh_recovery.SSHRecoveryCancelled)
        assert time.monotonic() - began < 1.5
        assert set(beats) == {threading.get_ident()}
        assert private_ssh.commands == ['wait-output']
        assert not any(t.name.startswith('biomero-ssh-') for t in threading.enumerate())
    finally:
        timer.cancel()


@pytest.mark.parametrize('disabled', [True, False])
def test_disabled_or_raw_mutating_call_is_not_timed_out_or_replayed(private_ssh, disabled):
    client, relay = private_ssh.client, private_ssh.relay
    if disabled:
        client.ssh_recovery_timeout = 0
    relay.paused.set()
    timer = threading.Timer(1., relay.paused.clear)
    timer.start()
    try:
        began = time.monotonic()
        call = client.run_read_only if disabled else client.run
        assert call('single-attempt-command', hide=True).exited == 0
        assert time.monotonic() - began >= .9
        assert private_ssh.commands == ['single-attempt-command']
    finally:
        relay.paused.clear()
        timer.cancel()


def test_existing_recovery_deadline_is_not_extended(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    relay.paused.set()
    began = time.monotonic()
    try:
        with ssh_recovery.scope(client) as state:
            state.deadline = began + .25
            with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
                client.check_job_status([42])
        assert time.monotonic() - began < .65
    finally:
        relay.paused.clear()


def test_silent_sftp_stat_uses_same_bound_and_restores_connect_settings(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    assert client.sftp().stat('data').st_size == len(b'complete test data')
    kwargs, timeout = dict(client.connect_kwargs), client.connect_timeout
    relay.paused.set()
    began = time.monotonic()
    try:
        with pytest.raises(ssh_recovery.SSHRecoveryTimeout):
            ssh_recovery.retry(client, lambda: client.sftp().stat('data'), 'stat')
        assert time.monotonic() - began < 2
        assert dict(client.connect_kwargs) == kwargs
        assert client.connect_timeout == timeout
        assert not any(t.name.startswith('biomero-ssh-') for t in threading.enumerate())
    finally:
        relay.paused.clear()


def test_healthy_download_progress_outlives_window_and_keeps_sftp(private_ssh, tmp_path):
    client = private_ssh.client
    payload = b'complete data\n' * 12000
    private_ssh.data_path.write_bytes(payload)
    private_ssh.file_state.delay = .2
    initial_sftp = client.sftp()
    (tmp_path / 'download').mkdir()
    began = time.monotonic()
    result = ssh_recovery.download(client, 'data', str(tmp_path / 'download'))
    assert time.monotonic() - began > client.ssh_recovery_timeout
    assert open(result.local, 'rb').read() == payload
    assert client.sftp() is initial_sftp
    assert initial_sftp.stat('data').st_size == len(payload)


def test_cleanup_failure_is_bounded_preserves_heartbeat_error_and_blocks_reuse(
        private_ssh, monkeypatch):
    from biomero import _ssh_inflight
    client, relay = private_ssh.client, private_ssh.relay
    monkeypatch.setattr(_ssh_inflight, 'CLEANUP_SECONDS', .15)
    original_close = client.client.close
    marker = EOFError('heartbeat stopped')
    pulses = []

    def fail_close():
        raise OSError('test close failure')

    def pulse():
        pulses.append(True)
        if len(pulses) > 1:
            raise marker

    relay.paused.set()
    monkeypatch.setattr(client.client, 'close', fail_close)
    try:
        began = time.monotonic()
        with ssh_recovery.scope(client, heartbeat=pulse):
            with pytest.raises(EOFError) as exc:
                client.check_job_status([42])
        assert exc.value is marker
        assert time.monotonic() - began < 1.5
        assert 'cleanup did not finish' in '\n'.join(marker.__notes__)
        assert client.__dict__['_ssh_cleanup_failed']
        with pytest.raises(RuntimeError, match='create a new client'):
            client.run_read_only('never replay this')
        assert not private_ssh.commands
    finally:
        monkeypatch.setattr(client.client, 'close', original_close)
        original_close()
        relay.paused.clear()
        # A failed cleanup retains explicit ownership; it is not declared a
        # cleanly cancelled call. Resolve the test's artificial close failure.
        owned = client.__dict__.get('_ssh_inflight_owner')
        if owned is not None:
            owned.thread.join(3)
            assert not owned.thread.is_alive()


def test_healthy_slow_authentication_is_not_cut_off_at_one_second(private_ssh):
    client = private_ssh.client
    client.close()
    client.ssh_recovery_timeout = 3
    private_ssh.file_state.auth_delay = 1.2
    began = time.monotonic()
    assert client.check_job_status([42])[0] == {42: 'COMPLETED'}
    assert 1.2 <= time.monotonic() - began < 3
