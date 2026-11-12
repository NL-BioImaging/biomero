# -*- coding: utf-8 -*-
# Copyright 2026 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Bounded recovery from interrupted SSH connections.

An SSH connection to the cluster can drop while a Slurm job keeps running.
This module retries an operation that is safe to repeat, within a time window
(``ssh_recovery_timeout``), and stops with a clear error when the window ends.
A window of 0 means no retries: the same code runs once and raises the
original error.

Only transport loss is retried: never authentication, never a command that
failed on the cluster, and never an arbitrary command. The caller decides
which operations are safe to repeat and wraps them in ``retry``. The Slurm
side (never submitting twice) lives in ``SlurmClient`` and ``remote_scripts``.
"""
import errno
import io
import logging
import os
import random
import socket
import stat
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from functools import wraps
from typing import Any, Callable, Iterator, List, Optional, Union

from fabric import Connection
from fabric.transfer import Result as TransferResult
from invoke.exceptions import CommandTimedOut, UnexpectedExit
from invoke.runners import Result
from paramiko import AuthenticationException, BadHostKeyException, SSHException
from paramiko.ssh_exception import NoValidConnectionsError

from biomero.constants import (
    DEFAULT_SSH_RECOVERY_TIMEOUT, SSH_DETECTION_LIMIT_SECONDS,
    SSH_HEARTBEAT_INTERVAL_SECONDS, SSH_RETRY_INITIAL_DELAY_SECONDS,
    SSH_RETRY_JITTER_FRACTION, SSH_RETRY_MAX_DELAY_SECONDS,
    TRANSFER_PARTIAL_SUFFIX)

logger = logging.getLogger(__name__)

# Recovery state of a client lives in its __dict__: Fabric redirects attribute
# assignment into its configuration proxy, and this state is not configuration.
# One client is assumed to be driven by one thread at a time.
STATE_KEY = "_ssh_recovery_state"
NATIVE_CHANNELS_KEY = "_ssh_native_channels"
# The shortest native timeout we hand out when a window is nearly used up.
MINIMUM_TIMEOUT_SECONDS = 0.001

# Errors that mean the connection was lost. On Windows the same names carry
# the WSA codes (for example ``errno.ECONNRESET`` is 10054), so there is no
# separate list per platform.
TRANSIENT_ERRNOS = frozenset({
    errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED, errno.EPIPE,
    errno.ETIMEDOUT, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN,
})
# Paramiko reports some lost connections only through its message text. They
# are matched only on the exact exception class, so that a reworded or an
# unrelated error is never mistaken for a lost connection.
PARAMIKO_SOCKET_CLOSED_MESSAGE = "Socket is closed"
PARAMIKO_CHANNEL_TIMEOUT_MESSAGE = "Timeout opening channel."
TRANSIENT_SSH_MESSAGES = (
    "ssh session not active", "no existing session",
    "error reading ssh protocol banner", "socket is closed",
    "server connection dropped", "connection reset",
)


class SSHRecoveryTimeout(ConnectionError):
    """Connectivity did not recover within the configured time window."""


class SSHRecoveryCancelled(InterruptedError):
    """The caller cancelled while waiting for connectivity."""


class RecoveryPending(Exception):
    """A durable remote operation is still locked or not yet visible.

    The connection works; the operation may still be running remotely. It is
    asked again after a pause, and never resubmitted.
    """


class RemoteOperationStillPending(RuntimeError):
    """A remote operation was still running when its waiting time ended.

    The SSH connection is healthy and nothing was repeated: the operation
    continues on the cluster and can be asked again later.
    """


class IncompleteTransfer(ConnectionError):
    """A file transfer ended with a different size than its source."""


class ReadOnlyCommandTimeout(TimeoutError):
    """A short read-only command did not answer within its bound."""


# --- Which errors are a lost connection --------------------------------------

def _missing_exit_status(result: Any) -> bool:
    """Tell whether a command ended without a remote exit status.

    Fabric uses -1 when the SSH channel closes before the command reports one.
    This is only interpreted inside an explicitly repeatable operation.
    """
    return isinstance(result, Result) and result.exited == -1


def _transient_os_error_reason(error: OSError) -> Optional[str]:
    """Classify a plain OS error: a lost connection, or a local problem."""
    # Paramiko reports a closed SFTP channel as a bare OSError without errno.
    # Do not widen this to filesystem errors or to messages that merely
    # contain the same words.
    if (type(error) is OSError and error.errno is None
            and str(error) == PARAMIKO_SOCKET_CLOSED_MESSAGE):
        return "SFTP channel closed"
    if error.errno in TRANSIENT_ERRNOS:
        return f"network error {errno.errorcode.get(error.errno, error.errno)}"
    return None


def _transient_ssh_error_reason(error: SSHException) -> Optional[str]:
    """Classify a Paramiko error by its exact class and wording."""
    if (type(error) is SSHException
            and str(error) == PARAMIKO_CHANNEL_TIMEOUT_MESSAGE):
        return "channel open timed out"
    message = str(error).lower()
    for text in TRANSIENT_SSH_MESSAGES:
        if text in message:
            return f"SSH message {text!r}"
    return None


def transient_reason(error: BaseException) -> Optional[str]:
    """Explain why an error counts as a lost connection, if it does.

    Credentials, host keys and remote command failures never count.

    Args:
        error (BaseException): The error raised by an SSH operation.

    Returns:
        Optional[str]: A short reason when the error is transport loss, else
            None.
    """
    if isinstance(error, (AuthenticationException, BadHostKeyException,
                          SSHRecoveryTimeout, SSHRecoveryCancelled)):
        return None
    if isinstance(error, UnexpectedExit):
        return ("channel closed without exit status"
                if _missing_exit_status(error.result) else None)
    if isinstance(error, NoValidConnectionsError):
        all_transient = bool(error.errors) and all(
            transient_reason(cause) for cause in error.errors.values())
        return "no connection could be opened" if all_transient else None
    if isinstance(error, (EOFError, ConnectionError, TimeoutError)):
        return type(error).__name__
    if isinstance(error, socket.gaierror):
        return ("temporary name resolution failure"
                if error.errno == socket.EAI_AGAIN else None)
    if isinstance(error, OSError):
        return _transient_os_error_reason(error)
    if isinstance(error, SSHException):
        return _transient_ssh_error_reason(error)
    # Invoke may deliver a Paramiko error from its output-reading thread.
    wrapped = getattr(error, "exceptions", ())
    if wrapped and all(transient_reason(item.value) for item in wrapped):
        return "transport error in the output reader"
    return None


def is_transient(error: BaseException) -> bool:
    """Tell whether an error is transport loss (see ``transient_reason``).

    Args:
        error (BaseException): The error raised by an SSH operation.

    Returns:
        bool: True if retrying may help.
    """
    return transient_reason(error) is not None


# --- Time windows and shared state -------------------------------------------

@dataclass
class OutageWindow:
    """The time budget of one ``retry`` call.

    A deadline starts at the first failure of its kind and is never moved by
    anything else, so that repeated failures cannot extend a call forever.
    A new ``retry`` call (the next poll, the next transfer) gets a new window.

    Attributes:
        timeout (float): Seconds a kind of failure may last.
        outage_deadline (Optional[float]): End of the SSH outage window.
        pending_deadline (Optional[float]): End of the window in which a
            healthy remote operation is awaited.
        delay (float): The next pause between attempts, in seconds.
    """
    timeout: float
    outage_deadline: Optional[float] = None
    pending_deadline: Optional[float] = None
    delay: float = SSH_RETRY_INITIAL_DELAY_SECONDS

    def start_if_new(self, error: BaseException, now: float) -> None:
        """Open the window of this kind of failure at its first occurrence."""
        if isinstance(error, RecoveryPending):
            if self.pending_deadline is None:
                self.pending_deadline = now + self.timeout
        elif self.outage_deadline is None:
            self.outage_deadline = now + self.timeout

    def seconds_left(self, error: BaseException, now: float) -> float:
        """Seconds left in the window of this kind of failure."""
        deadline = (self.pending_deadline if isinstance(error, RecoveryPending)
                    else self.outage_deadline)
        return deadline - now

    def next_pause(self, seconds_left: float) -> float:
        """Return the pause before the next attempt and grow the delay."""
        jitter = random.uniform(0, SSH_RETRY_JITTER_FRACTION) * self.delay
        pause = min(self.delay + jitter, seconds_left)
        self.delay = min(self.delay * 2, SSH_RETRY_MAX_DELAY_SECONDS)
        return pause


@dataclass
class RecoveryState:
    """What nested operations of one client share while recovering.

    Attributes:
        timeout (float): The recovery window in seconds; 0 means no retries.
        windows (List[OutageWindow]): The windows of the active ``retry``
            calls, outermost first.
        heartbeats (List[Callable[[], None]]): Caller callbacks that run
            during waits (for example an OMERO session keepalive).
        heartbeat_error (Optional[Exception]): An error raised by a callback;
            it is the caller's, so it is never retried.
    """
    timeout: float
    windows: List[OutageWindow] = field(default_factory=list)
    heartbeats: List[Callable[[], None]] = field(default_factory=list)
    heartbeat_error: Optional[Exception] = None

    def seconds_left_in_outage(self) -> Optional[float]:
        """Seconds left in the tightest active outage window, if any."""
        deadlines = [window.outage_deadline for window in self.windows
                     if window.outage_deadline is not None]
        return min(deadlines) - time.monotonic() if deadlines else None

    def pulse(self) -> None:
        """Run the caller callbacks once each.

        Raises:
            Exception: Whatever a callback raised; it is remembered so that
                it is not mistaken for an SSH error.
        """
        for callback in {id(item): item for item in self.heartbeats}.values():
            try:
                callback()
            except Exception as error:
                self.heartbeat_error = error
                raise

    def wait(self, seconds: float, cancel: Optional[Any],
             description: str) -> None:
        """Sleep, staying responsive to cancellation and heartbeats."""
        remaining = seconds
        while remaining > 0:
            self.raise_if_cancelled(cancel, description)
            interval = (min(remaining, SSH_HEARTBEAT_INTERVAL_SECONDS)
                        if self.heartbeats else remaining)
            if cancel is None:
                time.sleep(interval)
            else:
                cancel.wait(interval)
            remaining -= interval
            self.raise_if_cancelled(cancel, description)
            self.pulse()

    @staticmethod
    def raise_if_cancelled(cancel: Optional[Any], description: str) -> None:
        """Raise ``SSHRecoveryCancelled`` when the caller's event is set."""
        if cancel is not None and cancel.is_set():
            raise SSHRecoveryCancelled(
                f"Cancelled while waiting for SSH: {description}")


def _state(client: Any) -> Optional[RecoveryState]:
    """Return the active recovery state of a client, if inside a scope."""
    return client.__dict__.get(STATE_KEY)


@contextmanager
def scope(client: Any, heartbeat: Optional[Callable[[], None]] = None
          ) -> Iterator[RecoveryState]:
    """Share recovery state and caller callbacks with nested operations.

    The outermost scope creates the state from the client's
    ``ssh_recovery_timeout``; nested scopes join it.

    Args:
        client (Any): The ``SlurmClient``.
        heartbeat (Optional[Callable[[], None]]): A caller callback that runs
            during waits, such as a session keepalive.

    Yields:
        RecoveryState: The shared state.

    Raises:
        TypeError: If ``heartbeat`` is not callable.
    """
    if heartbeat is not None and not callable(heartbeat):
        raise TypeError("heartbeat must be callable")
    state = _state(client)
    owner = state is None
    if owner:
        state = RecoveryState(getattr(client, "ssh_recovery_timeout", 0))
        client.__dict__[STATE_KEY] = state
    if heartbeat is not None:
        state.heartbeats.append(heartbeat)
    try:
        yield state
    finally:
        if heartbeat is not None:
            state.heartbeats.remove(heartbeat)
        if owner:
            client.__dict__.pop(STATE_KEY, None)


# --- Native timeouts ---------------------------------------------------------

def io_timeout(client: Any, configured: Optional[float] = None) -> float:
    """Return the native limit for connection stages and short reads.

    This is a detection limit, not a limit on healthy long commands. It is
    never longer than the recovery window, and during an outage never longer
    than what is left of it.

    Args:
        client (Any): The ``SlurmClient``, inside a ``scope``.
        configured (Optional[float]): A stricter limit set by the caller,
            which stays in force.

    Returns:
        float: The limit in seconds.
    """
    state = _state(client)
    seconds = min(float(SSH_DETECTION_LIMIT_SECONDS), state.timeout)
    seconds_left = state.seconds_left_in_outage()
    if seconds_left is not None:
        seconds = min(seconds, max(MINIMUM_TIMEOUT_SECONDS, seconds_left))
    if isinstance(configured, (int, float)) and configured > 0:
        seconds = min(seconds, configured)
    return seconds


def bounded_read_timeout(client: Any, requested: Optional[float]
                         ) -> Optional[float]:
    """Return the timeout for a short read-only command.

    Args:
        client (Any): The ``SlurmClient``, inside a ``scope``.
        requested (Optional[float]): A longer timeout the caller asked for
            because the read is legitimately slow.

    Returns:
        Optional[float]: The requested timeout (capped by what is left of an
            outage), or the detection limit when none was requested. The
            request is returned unchanged when recovery is off.
    """
    state = _state(client)
    if state is None or state.timeout <= 0:
        return requested
    if not (isinstance(requested, (int, float)) and requested > 0):
        return io_timeout(client)
    seconds_left = state.seconds_left_in_outage()
    if seconds_left is None:
        return requested
    return min(requested, max(MINIMUM_TIMEOUT_SECONDS, seconds_left))


class _RestorableSettings:
    """Set values temporarily and put the original values back afterwards."""

    _MISSING = object()

    def __init__(self) -> None:
        self._restorers: List[Callable[[], None]] = []

    def set_item(self, mapping: dict, key: str, value: Any) -> None:
        """Set ``mapping[key]``; restore (or remove) it on ``restore``."""
        original = mapping.get(key, self._MISSING)

        def restore() -> None:
            if original is self._MISSING:
                mapping.pop(key, None)
            else:
                mapping[key] = original
        self._restorers.append(restore)
        mapping[key] = value

    def set_attribute(self, target: Any, name: str, value: Any) -> None:
        """Set ``target.name``; restore it on ``restore``."""
        original = getattr(target, name)
        self._restorers.append(lambda: setattr(target, name, original))
        setattr(target, name, value)

    def restore(self) -> None:
        """Put every original value back, last change first."""
        while self._restorers:
            self._restorers.pop()()


@contextmanager
def native_timeouts(client: Any) -> Iterator[None]:
    """Bound connection stages with the supported Fabric/Paramiko settings.

    Within an attempt the banner, authentication, connect and channel-open
    timeouts are limited by ``io_timeout``; the previous values are restored
    afterwards, also on a reconnected transport. Nothing happens when
    recovery is off. Paramiko's exec/subsystem acknowledgement waits and name
    resolution are not covered by these settings.

    Args:
        client (Any): The ``SlurmClient``, inside a ``scope``.

    Yields:
        None: Control, while the temporary limits are in force.
    """
    state = _state(client)
    connect_kwargs = getattr(client, "connect_kwargs", None)
    if state is None or state.timeout <= 0 or connect_kwargs is None:
        yield
        return
    settings = _RestorableSettings()
    previous_channels = client.__dict__.get(NATIVE_CHANNELS_KEY)
    client.__dict__[NATIVE_CHANNELS_KEY] = []
    try:
        for key in ("banner_timeout", "auth_timeout"):
            settings.set_item(
                connect_kwargs, key,
                io_timeout(client, connect_kwargs.get(key)))
        if "timeout" in connect_kwargs:
            settings.set_item(
                connect_kwargs, "timeout",
                io_timeout(client, connect_kwargs["timeout"]))
            settings.set_attribute(client, "connect_timeout", None)
        else:
            settings.set_attribute(
                client, "connect_timeout",
                io_timeout(client, client.connect_timeout))
        limit_channel_open(client)
        yield
    finally:
        settings.restore()
        for transport, channel_timeout in client.__dict__[NATIVE_CHANNELS_KEY]:
            transport.channel_timeout = channel_timeout
        if previous_channels is None:
            client.__dict__.pop(NATIVE_CHANNELS_KEY, None)
        else:
            client.__dict__[NATIVE_CHANNELS_KEY] = previous_channels


def limit_channel_open(client: Any) -> None:
    """Limit channel opening on the current transport, within native_timeouts.

    Remembers the transport's own setting so that ``native_timeouts`` can put
    it back. Does nothing outside ``native_timeouts``.

    Args:
        client (Any): The ``SlurmClient``.
    """
    channels = client.__dict__.get(NATIVE_CHANNELS_KEY)
    transport = client.transport
    if channels is None or transport is None:
        return
    if not any(known is transport for known, _ in channels):
        channels.append((transport, transport.channel_timeout))
    transport.channel_timeout = io_timeout(client, transport.channel_timeout)


@contextmanager
def sftp_timeout(client: Any) -> Iterator[Any]:
    """Bound each SFTP read, not the whole transfer.

    Args:
        client (Any): The ``SlurmClient``, inside a ``scope``.

    Yields:
        paramiko.SFTPClient: The SFTP client.
    """
    sftp = client.sftp()
    state = _state(client)
    if state is None or state.timeout <= 0:
        yield sftp
        return
    channel = sftp.get_channel()
    previous = channel.gettimeout()
    channel.settimeout(io_timeout(client, previous))
    try:
        yield sftp
    finally:
        channel.settimeout(previous)


# --- Retrying an operation ---------------------------------------------------

def _run_attempt(client: Any, operation: Callable[[], Any]) -> Any:
    """Run one attempt, treating a missing exit status as a lost connection."""
    with native_timeouts(client):
        result = operation()
    if _missing_exit_status(result):
        raise UnexpectedExit(result)
    return result


def _should_retry(state: RecoveryState, error: BaseException) -> bool:
    """Decide whether an attempt's error may be retried, and log why not."""
    if state.timeout <= 0:
        return False
    if error is state.heartbeat_error:
        # A caller-owned liveness failure must not become an SSH retry just
        # because it looks like a connection error.
        return False
    if isinstance(error, RecoveryPending):
        return True
    reason = transient_reason(error)
    if reason is None:
        logger.debug(f"Not retrying {type(error).__name__}: not a lost "
                     f"connection ({error})")
    return reason is not None


def _close_stale_transport(client: Any) -> None:
    """Close the broken transport so that the next attempt reconnects."""
    try:
        client.close()
    except Exception as error:
        logger.debug(f"Could not close the stale SSH transport: {error}")


def _raise_window_expired(error: BaseException, description: str) -> None:
    """Raise the error that ends a window, naming what stays intact."""
    if isinstance(error, RecoveryPending):
        raise RemoteOperationStillPending(
            f"{description} is still running or unresolved on the cluster "
            "after the waiting time. The SSH connection works and nothing "
            "was repeated; ask again later") from error
    raise SSHRecoveryTimeout(
        f"SSH recovery deadline exceeded during {description}; existing "
        "jobs and recovery data are retained") from error


def retry(client: Any, operation: Callable[[], Any], description: str, *,
          heartbeat: Optional[Callable[[], None]] = None) -> Any:
    """Run an operation that is safe to repeat, recovering from SSH loss.

    On a lost connection the stale transport is closed and the operation is
    tried again with growing pauses, until the window set by
    ``client.ssh_recovery_timeout`` ends. The window starts at the first
    failure of this call and is not extended by anything else. With a window
    of 0 the operation runs once and its own error is raised.

    Args:
        client (Any): The ``SlurmClient``.
        operation (Callable[[], Any]): The repeatable operation.
        description (str): What the operation does, for logs and errors.
        heartbeat (Optional[Callable[[], None]]): A callback that runs during
            waits.

    Returns:
        Any: The result of the first successful attempt.

    Raises:
        SSHRecoveryTimeout: If the connection did not come back in time.
        RemoteOperationStillPending: If a healthy remote operation was still
            pending when the window ended.
        SSHRecoveryCancelled: If the caller cancelled during a wait.
    """
    with scope(client, heartbeat) as state:
        # A nested call never outlasts the outage of the call around it.
        seconds_left = state.seconds_left_in_outage()
        window = OutageWindow(
            state.timeout if seconds_left is None
            else min(state.timeout, max(MINIMUM_TIMEOUT_SECONDS,
                                        seconds_left)))
        state.windows.append(window)
        try:
            return _attempt_until_done(
                client, state, window, operation, description)
        finally:
            state.windows.pop()


def _attempt_until_done(client: Any, state: RecoveryState,
                        window: OutageWindow, operation: Callable[[], Any],
                        description: str) -> Any:
    """Run the attempts of ``retry`` until one succeeds or a window ends."""
    cancel = getattr(client, "ssh_recovery_cancel", None)
    last_error: Optional[BaseException] = None
    while True:
        state.raise_if_cancelled(cancel, description)
        state.pulse()
        if (last_error is not None
                and window.seconds_left(last_error, time.monotonic()) <= 0):
            # The wait used up the window: never start a late attempt.
            _raise_window_expired(last_error, description)
        try:
            result = _run_attempt(client, operation)
        except Exception as error:
            if not _should_retry(state, error):
                if isinstance(error, RecoveryPending):
                    # No retries (window 0): still the documented error.
                    _raise_window_expired(error, description)
                raise
            last_error = error
            now = time.monotonic()
            window.start_if_new(error, now)
            seconds_left = window.seconds_left(error, now)
            if seconds_left <= 0:
                _raise_window_expired(error, description)
            if not isinstance(error, RecoveryPending):
                _close_stale_transport(client)
            pause = window.next_pause(seconds_left)
            waiting_for = ("the remote operation"
                           if isinstance(error, RecoveryPending)
                           else "the SSH connection")
            logger.warning(
                f"Waiting for {waiting_for} during {description} "
                f"({type(error).__name__}: "
                f"{transient_reason(error) or error}); retry in "
                f"{pause:.1f}s, {seconds_left:.1f}s left. Existing job "
                "state is unchanged.")
            state.wait(pause, cancel, description)
            continue
        if last_error is not None:
            logger.info(f"Operation resumed after an interruption: "
                        f"{description}")
        return result


def read_only(function: Callable[..., Any]) -> Callable[..., Any]:
    """Declare a short remote read that is safe to repeat.

    The decorated method keeps its signature. It runs through ``retry`` with
    a bounded timeout, so that a silent stall returns control; a timeout
    counts as a lost connection (the Slurm job has not failed).

    Args:
        function (Callable[..., Any]): A method taking ``client`` first.

    Returns:
        Callable[..., Any]: The wrapped method.
    """
    @wraps(function)
    def wrapped(client: Any, *args: Any, **kwargs: Any) -> Any:
        def attempt() -> Any:
            options = dict(kwargs)
            timeout = bounded_read_timeout(client, options.get("timeout"))
            if timeout is not None:
                options["timeout"] = timeout
            try:
                return function(client, *args, **options)
            except CommandTimedOut as error:
                if client.__dict__[STATE_KEY].timeout <= 0:
                    raise
                raise ReadOnlyCommandTimeout(
                    "Read-only SSH command timed out") from error
        return retry(client, attempt, function.__name__)
    return wrapped


# --- Transfers published atomically ------------------------------------------

def _local_size(local: Union[str, io.StringIO, io.BytesIO]) -> int:
    """Return the size in bytes of a local file or in-memory source."""
    if isinstance(local, io.StringIO):
        return len(local.getvalue().encode("utf-8"))
    if isinstance(local, io.BytesIO):
        return len(local.getvalue())
    return os.path.getsize(local)


def _existing_mode(sftp: Any, remote: str) -> Optional[int]:
    """Return the permission bits of a remote file, or None if it is new."""
    try:
        return stat.S_IMODE(sftp.stat(remote).st_mode)
    except FileNotFoundError:
        return None


def atomic_put(connection: Connection,
               local: Union[str, io.StringIO, io.BytesIO], remote: str,
               partial_suffix: str = TRANSFER_PARTIAL_SUFFIX
               ) -> TransferResult:
    """Upload a file so that the remote name never shows a partial file.

    The data goes to ``<remote><partial_suffix>`` first, is checked for its
    size, and is renamed to ``remote``. An interrupted upload restarts from the
    beginning. A previous remote file keeps its permission bits. Concurrent
    uploads to one remote name are not coordinated: the last rename wins.

    Args:
        connection (Connection): The ``SlurmClient``.
        local (Union[str, io.StringIO, io.BytesIO]): A local path or an
            in-memory source.
        remote (str): The final remote path.
        partial_suffix (str): Suffix of the temporary remote name.

    Returns:
        TransferResult: The result, with ``remote`` set to the final path.

    Raises:
        IncompleteTransfer: If the uploaded size is wrong (retried).
    """
    partial = remote + partial_suffix
    expected_size = _local_size(local)

    def attempt() -> TransferResult:
        if hasattr(local, "seek"):
            local.seek(0)
        with sftp_timeout(connection) as sftp:
            mode = _existing_mode(sftp, remote)
            result = connection.put(local=local, remote=partial)
            uploaded_size = sftp.stat(partial).st_size
            if uploaded_size != expected_size:
                raise IncompleteTransfer(
                    f"Uploaded {uploaded_size} of {expected_size} bytes "
                    f"to {partial}")
            if mode is not None:
                sftp.chmod(partial, mode)
            sftp.posix_rename(partial, remote)
            result.remote = remote
            return result

    return retry(connection, attempt, f"upload to {remote}")


def atomic_get(connection: Connection, remote: str, directory: str
               ) -> TransferResult:
    """Download a file so that the local name never shows a partial file.

    The data goes to a temporary local file, is checked against the remote
    file (unchanged and complete), and then replaces the local file. An
    earlier complete local file stays available until then.

    Args:
        connection (Connection): The ``SlurmClient``.
        remote (str): The remote path.
        directory (str): The local directory to download into.

    Returns:
        TransferResult: The result, with ``local`` set to the final path.

    Raises:
        IncompleteTransfer: If the remote file changed or the download is
            incomplete (retried).
    """
    name = remote.rsplit("/", 1)[-1]
    destination = os.path.join(directory, name)

    def attempt() -> TransferResult:
        with sftp_timeout(connection) as sftp:
            before = sftp.stat(remote)
            descriptor, partial = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".part", dir=directory)
            os.close(descriptor)
            try:
                result = connection.get(remote=remote, local=partial)
                after = sftp.stat(remote)
                remote_unchanged = ((before.st_size, before.st_mtime)
                                    == (after.st_size, after.st_mtime))
                if (not remote_unchanged
                        or os.path.getsize(partial) != after.st_size):
                    raise IncompleteTransfer(
                        f"Remote file {remote} changed or the download is "
                        "incomplete")
                os.replace(partial, destination)
                result.local = destination
                return result
            finally:
                if os.path.exists(partial):
                    os.unlink(partial)

    return retry(connection, attempt, f"download {name}")


# Deprecated alias: biomero-scripts' acceptance scripts call ``download``.
download = atomic_get


__all__ = [
    "DEFAULT_SSH_RECOVERY_TIMEOUT", "IncompleteTransfer", "OutageWindow",
    "ReadOnlyCommandTimeout", "RecoveryPending", "RecoveryState",
    "RemoteOperationStillPending", "SSHRecoveryCancelled",
    "SSHRecoveryTimeout", "atomic_get", "atomic_put", "bounded_read_timeout",
    "download", "io_timeout", "is_transient", "limit_channel_open",
    "native_timeouts", "read_only", "retry", "scope", "sftp_timeout",
    "transient_reason",
]
