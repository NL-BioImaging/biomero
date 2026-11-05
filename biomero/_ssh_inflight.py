"""Supervise Fabric I/O inside explicit replay-safe recovery boundaries.

Only network calls move to a helper. Retry orchestration, database work and
caller-owned heartbeats stay on the calling thread. An independent SSH channel
round trip distinguishes a quiet healthy command from an unresponsive link.
"""
from contextlib import contextmanager, ExitStack
import threading
import time
import logging
import sys
import inspect

from paramiko import ChannelException

from . import ssh_recovery

logger = logging.getLogger(__name__)
CLEANUP_SECONDS = 11


class _Call:
    def __init__(self, operation, name, *, transfer=False):
        self.done = threading.Event()
        self.result = self.error = None
        self.quiet = False
        self.transfer = transfer
        self.channels = {}
        self.progress = time.monotonic()

        def invoke():
            try:
                self.result = operation()
            except BaseException as error:
                self.error = error
            finally:
                self.done.set()

        self.thread = threading.Thread(target=invoke, name=name)
        self.thread.start()

    def value(self):
        if self.error is not None:
            raise self.error
        return self.result


def _transport(client):
    # During connect(), Fabric.transport is not assigned yet. Paramiko already
    # exposes the negotiating transport, which must also be closed on abort.
    return client.client.get_transport()


def _disconnect(client):
    # Close the transport before SFTP channels: sending channel-close on a
    # silently blocked socket must not itself hold up cancellation.
    for close in (client.client.close, client.close):
        try:
            close()
        except Exception:
            logger.warning('Could not close an SSH resource during cleanup',
                           exc_info=True)


@contextmanager
def _connect_limits(client, seconds):
    """Bound reconnect stages too, restoring the caller's normal settings."""
    missing = object()
    kwargs = client.connect_kwargs
    saved = {key: kwargs.get(key, missing) for key in (
        'timeout', 'banner_timeout', 'auth_timeout', 'channel_timeout')}
    original_timeout = client.connect_timeout

    def cap(value):
        if isinstance(value, (int, float)) and value > 0:
            return min(value, seconds)
        return seconds

    try:
        if saved['timeout'] is not missing:
            kwargs['timeout'] = cap(saved['timeout'])
            client.connect_timeout = None
        else:
            client.connect_timeout = cap(original_timeout)
        for key in ('banner_timeout', 'auth_timeout', 'channel_timeout'):
            kwargs[key] = cap(saved[key])
        yield
    finally:
        client.connect_timeout = original_timeout
        for key, value in saved.items():
            if value is missing:
                kwargs.pop(key, None)
            else:
                kwargs[key] = value


def _probe(transport, timeout):
    try:
        channel = transport.open_session(timeout=timeout)
    except ChannelException:
        # Even a server rejecting extra sessions has answered the SSH request.
        return
    channel.close()


def call(client, operation, *, transfer=False):
    """Run one I/O call with caller-thread liveness and transport supervision."""
    if client.__dict__.get('_ssh_cleanup_failed'):
        raise RuntimeError('Previous SSH I/O could not stop; create a new client')
    state = client.__dict__.get('_ssh_recovery_state')
    if state is None or not ssh_recovery.enabled(client):
        return operation()
    owner = client.__dict__.get('_ssh_inflight_owner')
    if owner is not None:
        # Fabric transfers call sftp() internally in this same I/O helper.
        if owner.thread.ident == threading.get_ident():
            return operation()
        raise RuntimeError('Concurrent I/O on one recovery client is unsupported')

    cancel = getattr(client, 'ssh_recovery_cancel', None)
    interval = min(1., state.timeout / 4)
    probe_interval = min(10., state.timeout / 4)
    started = last_response = time.monotonic()
    next_pulse = started + interval
    next_probe = started + probe_interval
    probe = None
    worker = None
    limits = ExitStack()
    remaining = state.deadline - started if state.deadline else state.timeout
    limits.enter_context(_connect_limits(client, min(10., max(.001, remaining))))
    try:
        # Publish ownership before starting the helper, including recursive
        # Fabric transfer calls which can run immediately.
        gate = threading.Event()
        worker = _Call(lambda: (gate.wait(), operation())[1], 'biomero-ssh-io',
                       transfer=transfer)
        client.__dict__['_ssh_inflight_owner'] = worker
        gate.set()
        while True:
            complete = worker.done.wait(min(.05, interval))
            now = time.monotonic()
            state.check_cancelled(cancel, 'in-flight SSH operation')
            if now >= next_pulse:
                state.pulse()
                next_pulse = now + interval
            if complete:
                if (worker.error is not None and
                        ssh_recovery.is_transient(worker.error)):
                    if state.deadline is None:
                        state.deadline = last_response + state.timeout
                return worker.value()

            if worker.transfer and worker.quiet:
                last_response = max(last_response, worker.progress)

            if probe is not None and probe.done.is_set():
                probe.thread.join()
                if probe.error is not None:
                    if state.deadline is None:
                        state.deadline = last_response + state.timeout
                    raise probe.error
                if worker.quiet:
                    last_response = now
                probe = None
                next_probe = now + probe_interval

            deadline = state.deadline or last_response + state.timeout
            if now >= deadline:
                state.deadline = deadline
                raise ssh_recovery.SSHRecoveryTimeout(
                    'SSH recovery deadline exceeded during in-flight I/O; '
                    'existing jobs and recovery data are retained')
            transport = _transport(client)
            if (worker.quiet and not worker.transfer and probe is None and
                    now >= next_probe and transport is not None
                    and transport.is_authenticated()):
                probe = _Call(
                    lambda target=transport, timeout=min(10., deadline - now):
                    _probe(target, timeout), 'biomero-ssh-probe')
    finally:
        # Never retry or return while an old owned I/O call is still running.
        # Closing Paramiko wakes channel-open, exec-ack, receive and SFTP waits.
        original_error = sys.exception()
        calls = [item for item in (worker, probe) if item is not None]
        # A completed operation can race a healthy liveness probe. Give its
        # reply a brief chance before closing a reusable SFTP/SSH transport.
        if worker is not None and worker.done.is_set() and probe is not None:
            probe.done.wait(.1)
        if any(not item.done.is_set() for item in calls):
            _disconnect(client)
        cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
        for item in calls:
            while not item.done.wait(.05):
                # A reconnect can publish its transport concurrently with the
                # first abort. Close that transport as well before joining.
                _disconnect(client)
                if time.monotonic() >= cleanup_deadline:
                    break
            if item.done.is_set():
                item.thread.join()
        pending = any(not item.done.is_set() for item in calls)
        if pending:
            # Python cannot interrupt a platform resolver/local callback. Do
            # not retry or reuse this client while its owned call still runs.
            client.__dict__['_ssh_cleanup_failed'] = True
            message = ('SSH cleanup did not finish; client cannot be reused. '
                       'A platform resolver or local I/O may still be running.')
            logger.error(message)
            if original_error is not None:
                original_error.add_note(message)
            else:
                raise RuntimeError(message)
        else:
            for channel, timeout in worker.channels.items() if worker else ():
                channel.settimeout(timeout)
            client.__dict__.pop('_ssh_inflight_owner', None)
            limits.close()


def sftp_ready(client, sftp):
    """Bound the SFTP channel's individual reads without timing whole copies."""
    owner = client.__dict__.get('_ssh_inflight_owner')
    state = client.__dict__.get('_ssh_recovery_state')
    if owner is not None and state is not None:
        channel = sftp.get_channel()
        owner.channels.setdefault(channel, channel.gettimeout())
        timeout = min(30., state.timeout)
        if state.deadline is not None:
            timeout = min(timeout, max(.001, state.deadline - time.monotonic()))
        previous = channel.gettimeout()
        channel.settimeout(min(previous, timeout) if previous is not None else timeout)
        owner.quiet = owner.transfer


def runner_started(client):
    owner = client.__dict__.get('_ssh_inflight_owner')
    state = client.__dict__.get('_ssh_recovery_state')
    if owner is not None and state is not None:
        owner.quiet = state.allow_quiet


class SFTP:
    """Apply the same guard to direct stat/rename/etc. within a safe scope."""
    def __init__(self, client, sftp):
        self._client = client
        self._sftp = sftp

    def __getattr__(self, name):
        value = getattr(self._sftp, name)
        if callable(value):
            if name == 'get_channel':
                return value
            def invoke(*args, **kwargs):
                def operation():
                    sftp_ready(self._client, self._sftp)
                    owner = self._client.__dict__.get('_ssh_inflight_owner')
                    if owner is not None and name in ('get', 'getfo', 'put', 'putfo'):
                        bound = inspect.signature(value).bind(*args, **kwargs)
                        previous = bound.arguments.get('callback')

                        def progress(transferred, total):
                            owner.progress = time.monotonic()
                            if previous is not None:
                                previous(transferred, total)

                        bound.arguments['callback'] = progress
                        return value(*bound.args, **bound.kwargs)
                    return value(*args, **kwargs)
                return call(self._client, operation,
                            transfer=name in ('get', 'getfo', 'put', 'putfo'))
            return invoke
        return value
