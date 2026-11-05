"""Native SSH bounds return monitoring to the existing recovery policy."""
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
def test_native_bounds_return_silent_monitoring_to_recovery(private_ssh, phase):
    client, relay = private_ssh.client, private_ssh.relay
    pulses = []
    original_channel_timeout = client.transport.channel_timeout
    original_kwargs = dict(client.connect_kwargs)

    def operation():
        with ssh_recovery.scope(client, heartbeat=lambda: pulses.append(
                threading.get_ident())):
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
    worker.join(2.5)
    bounded = not worker.is_alive()
    relay.paused.clear()
    worker.join(3)
    assert bounded
    assert isinstance(outcome.get('error'), ssh_recovery.SSHRecoveryTimeout)
    # Initial detection and the subsequent outage window are separate.
    assert 1.2 < outcome['ended'] - began < 2.5
    assert len(pulses) >= 2
    assert set(pulses) == {worker.ident}
    assert dict(client.connect_kwargs) == original_kwargs
    if client.transport is not None:
        assert client.transport.channel_timeout == original_channel_timeout
    assert not any(t.name.startswith('biomero-ssh-') for t in threading.enumerate())


def test_channel_timeout_reconnects_and_observes_same_job(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    client.ssh_recovery_timeout = 2
    client.connect_kwargs['channel_timeout'] = .2
    relay.paused.set()
    worker, outcome = observe(lambda: client.check_job_status([42]))
    time.sleep(.5)
    relay.paused.clear()
    worker.join(3)
    assert not worker.is_alive()
    assert outcome['result'][0] == {42: 'COMPLETED'}
    assert client.connect_kwargs['channel_timeout'] == .2
    assert all('sbatch' not in command for command in private_ssh.commands)


def test_first_connection_restores_native_defaults(private_ssh):
    client = private_ssh.client
    original = client.transport.channel_timeout
    client.close()
    client.transport = None
    assert client.transport is None
    assert 'channel_timeout' not in client.connect_kwargs
    assert client.check_job_status([42])[0] == {42: 'COMPLETED'}
    assert client.transport.channel_timeout == original
    assert 'channel_timeout' not in client.connect_kwargs
    client.ssh_recovery_timeout = 0
    assert client.run('read-only-example').ok
    assert client.transport.channel_timeout == original


def test_custom_transport_timeout_survives_reconnection(private_ssh):
    client = private_ssh.client
    client.close()

    def transport_factory(sock, **kwargs):
        transport = paramiko.Transport(sock, **kwargs)
        transport.channel_timeout = 47
        return transport

    client.connect_kwargs['transport_factory'] = transport_factory
    assert client.check_job_status([42])[0] == {42: 'COMPLETED'}
    assert client.transport.channel_timeout == 47


def test_quiet_healthy_durable_command_has_no_total_runtime_cap(private_ssh):
    client = private_ssh.client
    worker, outcome = observe(lambda: ssh_recovery.run_durable(
        client, 'wait-output', {}, 'existing protected operation'))
    assert private_ssh.started.wait(2)
    time.sleep(1)
    private_ssh.release_output.set()
    worker.join(2)
    assert outcome['result'].ok
    assert private_ssh.commands == ['wait-output']


@pytest.mark.parametrize('enabled', [True, False])
def test_raw_run_never_replays_or_inherits_read_timeout(private_ssh, enabled):
    client = private_ssh.client
    if not enabled:
        client.ssh_recovery_timeout = 0
    worker, outcome = observe(lambda: client.run('wait-output', timeout=.2))
    assert private_ssh.started.wait(2)
    worker.join(1)
    from invoke.exceptions import CommandTimedOut, UnexpectedExit
    # Invoke can observe the timer's channel close as missing exit status.
    assert isinstance(outcome['error'], (CommandTimedOut, UnexpectedExit))
    assert private_ssh.commands == ['wait-output']


def test_zero_keeps_read_timeout_semantics(private_ssh):
    client = private_ssh.client
    client.ssh_recovery_timeout = 0
    worker, outcome = observe(lambda: client.run_read_only('wait-output', timeout=.2))
    assert private_ssh.started.wait(2)
    worker.join(1)
    from invoke.exceptions import CommandTimedOut, UnexpectedExit
    assert isinstance(outcome['error'], (CommandTimedOut, UnexpectedExit))
    assert private_ssh.commands == ['wait-output']


def test_cancellation_checked_when_native_channel_wait_returns(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    client.ssh_recovery_cancel = threading.Event()
    relay.paused.set()
    worker, outcome = observe(lambda: client.check_job_status([42]))
    time.sleep(.15)
    client.ssh_recovery_cancel.set()
    worker.join(1.5)
    relay.paused.clear()
    worker.join(2)
    assert isinstance(outcome['error'], ssh_recovery.SSHRecoveryCancelled)
    assert private_ssh.commands == []


def test_callback_error_preserves_identity_and_caller_thread(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    error = ConnectionError('caller heartbeat failed')
    threads = []

    def heartbeat():
        threads.append(threading.get_ident())
        if len(threads) > 1:
            raise error

    def operation():
        with ssh_recovery.scope(client, heartbeat):
            return client.check_job_status([42])

    relay.paused.set()
    worker, outcome = observe(operation)
    worker.join(2)
    relay.paused.clear()
    worker.join(2)
    assert outcome['error'] is error
    assert set(threads) == {worker.ident}


def test_existing_sftp_stall_is_bounded_and_timeout_restored(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    sftp = client.sftp()
    channel = sftp.get_channel()
    channel.settimeout(.2)

    def operation():
        with ssh_recovery.sftp_timeout(client) as bounded:
            return bounded.stat('data')

    relay.paused.set()
    worker, outcome = observe(lambda: ssh_recovery.retry(client, operation, 'stat result'))
    worker.join(2)
    relay.paused.clear()
    worker.join(2)
    assert isinstance(outcome['error'], ssh_recovery.SSHRecoveryTimeout)
    assert channel.gettimeout() == .2


def test_final_native_timeout_closes_transport_at_existing_deadline(private_ssh):
    client, relay = private_ssh.client, private_ssh.relay
    relay.paused.set()

    def operation():
        with ssh_recovery.scope(client) as state:
            state.deadline = time.monotonic() + .2
            return client.check_job_status([42])

    worker, outcome = observe(operation)
    worker.join(1)
    was_connected = client.is_connected
    relay.paused.clear()
    worker.join(2)
    assert isinstance(outcome['error'], ssh_recovery.SSHRecoveryTimeout)
    assert not was_connected


def test_healthy_long_download_uses_read_timeout_not_total_cap(private_ssh, tmp_path):
    client = private_ssh.client
    client.ssh_recovery_timeout = .25
    private_ssh.data_path.write_bytes(b'x' * 400000)
    private_ssh.file_state.delay = .05
    sftp = client.sftp()
    original = sftp.get_channel().gettimeout()
    destination = tmp_path / 'download'
    destination.mkdir()
    began = time.monotonic()
    result = ssh_recovery.download(client, 'data', str(destination))
    assert time.monotonic() - began > .25
    assert (destination / 'data').read_bytes() == private_ssh.data_path.read_bytes()
    assert result.local == str(destination / 'data')
    assert client.sftp() is sftp
    assert sftp.get_channel().gettimeout() == original
