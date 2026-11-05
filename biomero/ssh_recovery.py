"""Bounded transport recovery for explicitly replayable HPC operations.

An arbitrary command, especially sbatch, must never be retried on the strength
of an SSH exception alone. Callers own replay safety and durable job identity.
"""
import errno
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import io
import json
import logging
import os
import posixpath
import shlex
import socket
import stat
import tempfile
import time
from uuid import uuid4

from invoke.exceptions import UnexpectedExit
from invoke.runners import Result
from paramiko import AuthenticationException, BadHostKeyException, SSHException
from paramiko.ssh_exception import NoValidConnectionsError

logger = logging.getLogger(__name__)

DEFAULT_SSH_RECOVERY_TIMEOUT = 900


class SSHRecoveryTimeout(ConnectionError):
    """Connectivity did not recover within the configured time window."""


class SSHRecoveryCancelled(InterruptedError):
    """The caller cancelled while waiting for connectivity."""


class RecoveryPending(ConnectionError):
    """A durable operation is still locked or awaiting job reconciliation."""


def is_transient(error):
    """Recognize transport loss, excluding credentials and remote failures."""
    if isinstance(error, (AuthenticationException, BadHostKeyException)):
        return False
    if isinstance(error, UnexpectedExit):
        return _missing_exit_status(error.result)
    if isinstance(error, NoValidConnectionsError):
        return bool(error.errors) and all(
            is_transient(e) for e in error.errors.values())
    if isinstance(error, (EOFError, ConnectionError, TimeoutError)):
        return not isinstance(
            error, (SSHRecoveryTimeout, SSHRecoveryCancelled))
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    if isinstance(error, OSError):
        # Paramiko Channel._send reports a closed SFTP channel as socket.error
        # (OSError) without errno. Do not broaden this to filesystem errors or
        # messages which merely contain the same words.
        if (type(error) is OSError and error.errno is None and
                str(error) == 'Socket is closed'):
            return True
        return error.errno in {
            errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED,
            errno.EPIPE, errno.ETIMEDOUT, errno.ENETUNREACH,
            errno.EHOSTUNREACH,
            errno.ENETDOWN, 10050, 10051, 10053, 10054, 10060, 10061, 10065,
        }
    if isinstance(error, SSHException):
        if type(error) is SSHException and str(error) == 'Timeout opening channel.':
            return True
        message = str(error).lower()
        return any(text in message for text in (
            'ssh session not active', 'no existing session',
            'error reading ssh protocol banner', 'socket is closed',
            'server connection dropped', 'connection reset',
        ))
    # Invoke may deliver a Paramiko error from its output-reading thread.
    exceptions = getattr(error, 'exceptions', ())
    return bool(exceptions) and all(
        is_transient(item.value) for item in exceptions)


def enabled(client):
    value = getattr(client, 'ssh_recovery_timeout', 0)
    return isinstance(value, (int, float)) and value > 0


def _missing_exit_status(result):
    # Fabric uses -1 when the SSH channel closes without a remote exit status.
    # This is interpreted only inside an explicitly replay-safe boundary.
    return isinstance(result, Result) and result.exited == -1


@dataclass
class _RecoveryState:
    timeout: float
    deadline: float | None = None
    heartbeats: dict = field(default_factory=dict)
    heartbeat_error: Exception | None = None
    allow_quiet: bool = False

    def pulse(self):
        seen = set()
        for callback in tuple(self.heartbeats.values()):
            if id(callback) in seen:
                continue
            seen.add(id(callback))
            try:
                callback()
            except Exception as error:
                # A caller-owned liveness failure must not become an SSH retry
                # simply because it happens to be an EOFError/ConnectionError.
                self.heartbeat_error = error
                raise

    def wait(self, seconds, cancel, description):
        remaining = seconds
        while remaining > 0:
            self.check_cancelled(cancel, description)
            interval = min(remaining, 10) if self.heartbeats else remaining
            if cancel is None:
                time.sleep(interval)
            else:
                cancel.wait(interval)
            remaining -= interval
            self.check_cancelled(cancel, description)
            self.pulse()

    @staticmethod
    def check_cancelled(cancel, description):
        if cancel is not None and cancel.is_set():
            raise SSHRecoveryCancelled(
                f'Cancelled while waiting for SSH: {description}')


@contextmanager
def scope(client, heartbeat=None, *, allow_quiet=None):
    """Share an outage budget and caller callbacks with nested retries."""
    if not enabled(client):
        yield None
        return
    if heartbeat is not None and not callable(heartbeat):
        raise TypeError('heartbeat must be callable')
    state = client.__dict__.get('_ssh_recovery_state')
    owner = state is None
    if owner:
        state = _RecoveryState(client.ssh_recovery_timeout)
        # Fabric redirects attribute assignment into its configuration proxy.
        # Recovery state belongs to this client instance, never its config.
        client.__dict__['_ssh_recovery_state'] = state
    marker = object()
    previous_quiet = state.allow_quiet
    if allow_quiet is not None:
        state.allow_quiet = allow_quiet
    if heartbeat is not None:
        state.heartbeats[marker] = heartbeat
    try:
        yield state
    finally:
        state.allow_quiet = previous_quiet
        state.heartbeats.pop(marker, None)
        if owner:
            client.__dict__.pop('_ssh_recovery_state', None)


def retry(client, operation, description, *, heartbeat=None, allow_quiet=False):
    """Recover an explicitly safe operation within each contiguous outage."""
    with scope(client, heartbeat, allow_quiet=allow_quiet) as state:
        if state is None:
            return operation()
        cancel = getattr(client, 'ssh_recovery_cancel', None)
        delay, failures = 1, 0
        while True:
            state.check_cancelled(cancel, description)
            state.pulse()
            try:
                result = operation()
                if _missing_exit_status(result):
                    raise UnexpectedExit(result)
            except Exception as error:
                if (error is state.heartbeat_error or
                        client.__dict__.get('_ssh_cleanup_failed') or
                        not is_transient(error)):
                    raise
                now = time.monotonic()
                if state.deadline is None:
                    state.deadline = now + state.timeout
                    delay = 1
                remaining = state.deadline - now
                if remaining <= 0:
                    raise SSHRecoveryTimeout(
                        'SSH recovery deadline exceeded during '
                        f'{description}; existing jobs and recovery data '
                        'are retained') from error
                failures += 1
                if not isinstance(error, RecoveryPending):
                    try:
                        client.close()
                    except Exception:
                        logger.debug('Could not close stale SSH transport',
                                     exc_info=True)
                wait = min(delay, remaining)
                logger.warning(
                    'Waiting for %s during %s (%s); retry in %.1fs, '
                    '%.1fs remaining. Existing job state is unchanged.',
                    'remote operation' if isinstance(error, RecoveryPending)
                    else 'SSH connection', description, type(error).__name__,
                    wait, remaining)
                state.wait(wait, cancel, description)
                delay = min(delay * 2, 30)
                continue
            # A successful operation ends this interruption. Subsequent healthy
            # queue/runtime must not consume the next outage's recovery window.
            state.deadline = None
            if failures:
                logger.info('SSH operation resumed: %s', description)
            return result


def download(client, remote, directory):
    """Publish a complete download without exposing partial data."""
    destination = os.path.join(directory, remote.rsplit('/', 1)[-1])

    def attempt():
        sftp = client.sftp()
        sftp.get_channel().settimeout(30)
        before = sftp.stat(remote)
        fd, partial = tempfile.mkstemp(
            prefix='.' + os.path.basename(destination) + '.', suffix='.part',
            dir=directory)
        os.close(fd)
        try:
            result = client.get(remote=remote, local=partial)
            after = sftp.stat(remote)
            if ((before.st_size, before.st_mtime) !=
                    (after.st_size, after.st_mtime) or
                    os.path.getsize(partial) != after.st_size):
                raise RuntimeError(
                    'Remote file changed or download is incomplete')
            os.replace(partial, destination)
            result.local = destination
            return result
        finally:
            if os.path.exists(partial):
                os.unlink(partial)

    return retry(client, attempt, 'download ' + remote.rsplit('/', 1)[-1])


def publish_script(client, content, remote):
    """Publish generated job scripts completely, including after channel loss."""
    payload = content.encode('utf-8')

    def attempt():
        sftp = client.sftp()
        sftp.get_channel().settimeout(30)
        partial = remote + '.biomero-partial-' + uuid4().hex
        try:
            mode = stat.S_IMODE(sftp.stat(remote).st_mode)
        except FileNotFoundError:
            mode = None
        try:
            result = client.put(local=io.BytesIO(payload), remote=partial)
            if sftp.stat(partial).st_size != len(payload):
                raise RuntimeError('Incomplete generated script upload')
            if mode is not None:
                sftp.chmod(partial, mode)
            sftp.posix_rename(partial, remote)
            result.remote = remote
            return result
        finally:
            try:
                sftp.remove(partial)
            except FileNotFoundError:
                # Successful rename removes the temporary name. On transport
                # loss a private partial may remain; it is not published as
                # the job script.
                pass
            except Exception:
                logger.debug('Could not remove partial script', exc_info=True)

    return retry(client, attempt, 'publish generated job script')


def upload(client, local, directory):
    """Retry immutable input archives without publishing partial files."""
    remote = posixpath.join(directory, os.path.basename(local))
    partial = remote + '.biomero-partial'

    def attempt():
        sftp = client.sftp()
        sftp.get_channel().settimeout(30)
        result = client.put(local=local, remote=partial)
        if sftp.stat(partial).st_size != os.path.getsize(local):
            raise RuntimeError('Incomplete input archive upload')
        sftp.posix_rename(partial, remote)
        result.remote = remote
        return result

    return retry(client, attempt, 'upload input archive')


def run_durable(client, command, env, description):
    """Replay commands protected by a remote lock and completion record."""
    def attempt():
        result = client.run_commands([command], env=env, warn=True)
        if result.exited == 200:
            raise RecoveryPending('Remote operation is pending reconciliation')
        if not result.ok:
            raise UnexpectedExit(result)
        return result

    return retry(client, attempt, description, allow_quiet=True)


def locked_command(lock, body, *, wait=5):
    """Reserve exit 200 for lock contention or an unresolved submission."""
    return (f'flock -w {wait} -E 200 {shlex.quote(lock)} '
            f'sh -c {shlex.quote(body)}')


def reconcile_submission(state, job_name):
    """Find exactly one allocation for an operation-specific intent."""
    quote = shlex.quote
    return (
        'live=$(squeue -h -u "$(id -un)" -o "%A|%j") || exit 1; '
        f'recorded=$(sacct -n -X -u "$(id -un)" --name={quote(job_name)} '
        f'-S "$(cat {quote(state + ".intent")})" '
        '--format=JobID,JobName%100 -P) || exit 1; '
        'printf "%s\\n%s\\n" "$live" "$recorded" | '
        f'awk -F\'|\' -v token={quote(job_name)} '
        "'$2 == token {split($1, a, \"_\"); "
        "if (a[1] ~ /^[0-9]+$/) print a[1]}' | sort -u")


def publish_job_id(state):
    """Validate and publish an allocation before acknowledging it."""
    quote = shlex.quote
    return (
        'case "$job" in ""|*[!0-9]*) '
        'echo "Waiting for Slurm submission reconciliation" >&2; '
        'exit 200;; esac; '
        f'printf "%s\\n" "$job" > {quote(state + ".job.tmp")} && '
        f'mv -f {quote(state + ".job.tmp")} {quote(state + ".job")} '
        '|| exit 1; ')


def archive(client, data_location, filename, env):
    """Serialize archive creation and reuse only a completed publication."""
    quote = shlex.quote
    destination = posixpath.join(data_location, filename + '.zip')
    partial_name = filename + '.biomero-partial'
    partial = posixpath.join(data_location, partial_name + '.zip')
    ready = destination + '.biomero-ready'
    body = (
        f'if test -f {quote(ready)} && test -f {quote(destination)} && '
        f'test "$(cat {quote(ready)})" = '
        f'"$(wc -c < {quote(destination)})"; then '
        'echo "Reusing completed result archive"; else '
        f'rm -f -- {quote(partial)}; '
        f'({client.get_zip_command(data_location, partial_name)}) || exit 1; '
        f'mv -f -- {quote(partial)} {quote(destination)} || exit 1; '
        f'wc -c < {quote(destination)} > {quote(ready + ".tmp")} && '
        f'mv -f -- {quote(ready + ".tmp")} {quote(ready)} || exit 1; fi')
    return run_durable(client, locked_command(
        destination + '.biomero-lock', body), env, 'create result archive')


def unpack(client, filename, env):
    """Do not race a still-running extraction after losing its SSH response."""
    quote = shlex.quote
    state = posixpath.join(
        client.slurm_data_path, filename + '.biomero-unpacked')
    body = (f'if test -f {quote(state)}; then echo "Input already extracted"; '
            f'else ({client.get_unzip_command(filename)}) || exit 1; '
            f'printf "complete\\n" > {quote(state + ".tmp")} && '
            f'mv -f {quote(state + ".tmp")} {quote(state)} || exit 1; fi')
    return run_durable(client, locked_command(state + '.lock', body), env,
                       'extract input archive')


def submit(client, command, env, task_id):
    """Reconcile lost sbatch responses by task identity, never resubmit intent.

    A remote receipt bridges a process restart and the gap between Slurm's
    acceptance and recording the job locally. Unresolved intent
    waits for accounting visibility; an absent match never authorizes a second
    submission. Failed submissions retain their original diagnostics.
    """
    from uuid import UUID
    token = 'biomero-' + str(UUID(str(task_id)))
    directory = posixpath.join(client.slurm_data_path, '.biomero-recovery')
    state = posixpath.join(directory, str(task_id))
    quote = shlex.quote
    digest = hashlib.sha256(json.dumps(
        [command, env], sort_keys=True).encode()).hexdigest()

    def p(suffix):
        return quote(state + suffix)

    find_job = reconcile_submission(state, token)
    publish = publish_job_id(state)
    body = (
        f'if test -e {p(".digest")} && '
        f'test "$(cat {p(".digest")})" != {quote(digest)}; then '
        'echo "Submission settings changed; manual reconciliation required" '
        '>&2; '
        'exit 1; fi; '
        f'if test -f {p(".failed")}; then cat {p(".stdout")}; '
        f'cat {p(".stderr")} >&2; exit 1; fi; '
        f'if test -s {p(".job")}; then job=$(cat {p(".job")}); '
        f'elif test -e {p(".intent")}; then job=$({find_job}) || exit 1; '
        f'{publish}'
        f'else printf "%s\\n" {quote(digest)} > {p(".digest")} && '
        f'date +%Y-%m-%dT%H:%M:%S > {p(".intent")} || exit 1; '
        f'({command}) > {p(".stdout")} 2> {p(".stderr")}; rc=$?; '
        f'if test "$rc" != 0; then printf "%s\\n" "$rc" > {p(".failed")}; '
        f'cat {p(".stdout")}; cat {p(".stderr")} >&2; exit 1; fi; '
        f'job=$(sed -n "s/^Submitted batch job \\([0-9][0-9]*\\)$/\\1/p" '
        f'{p(".stdout")} | tail -1); '
        f'{publish}fi; '
        'case "$job" in ""|*[!0-9]*) exit 1;; esac; '
        'printf "Submitted batch job %s\\n" "$job"')
    wrapped = (f'mkdir -p {quote(directory)} && ' +
               locked_command(state + '.lock', body))
    return run_durable(client, wrapped, env, 'submit/reconcile Slurm job')
