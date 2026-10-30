"""Bounded transport recovery for explicitly replayable HPC operations.

An arbitrary command, especially sbatch, must never be retried on the strength
of an SSH exception alone. Callers own replay safety and durable job identity.
"""
import errno
import hashlib
import json
import logging
import os
import posixpath
import shlex
import socket
import tempfile
import time

from paramiko import AuthenticationException, BadHostKeyException, SSHException
from paramiko.ssh_exception import NoValidConnectionsError

logger = logging.getLogger(__name__)


class SSHRecoveryTimeout(ConnectionError):
    """Connectivity did not recover within the configured time window."""


class SSHRecoveryCancelled(InterruptedError):
    """The caller cancelled while waiting for connectivity."""


class RecoveryPending(ConnectionError):
    """A durable operation is still locked or awaiting job reconciliation."""


def is_transient(error):
    """Recognize transport loss, excluding credentials and remote exit codes."""
    if isinstance(error, (AuthenticationException, BadHostKeyException)):
        return False
    if isinstance(error, NoValidConnectionsError):
        return bool(error.errors) and all(
            is_transient(e) for e in error.errors.values())
    if isinstance(error, (EOFError, ConnectionError, TimeoutError)):
        return not isinstance(error, (SSHRecoveryTimeout, SSHRecoveryCancelled))
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    if isinstance(error, OSError):
        return error.errno in {
            errno.ECONNRESET, errno.ECONNREFUSED, errno.ECONNABORTED,
            errno.EPIPE, errno.ETIMEDOUT, errno.ENETUNREACH, errno.EHOSTUNREACH,
            errno.ENETDOWN, 10050, 10051, 10053, 10054, 10060, 10061, 10065,
        }
    if isinstance(error, SSHException):
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


def retry(client, operation, description, *, heartbeat=None):
    """Retry an explicitly safe operation; nested calls share one deadline."""
    if not enabled(client):
        return operation()
    previous = client.__dict__.get('_ssh_recovery_deadline')
    deadline = previous if previous is not None else (
        time.monotonic() + client.ssh_recovery_timeout)
    client._ssh_recovery_deadline = deadline
    cancel = client.__dict__.get('ssh_recovery_cancel')
    delay, failures = 1, 0
    try:
        while True:
            if cancel is not None and cancel.is_set():
                raise SSHRecoveryCancelled(
                    f'Cancelled while waiting for SSH: {description}')
            if heartbeat is not None:
                heartbeat()
            try:
                result = operation()
            except Exception as error:
                if not is_transient(error):
                    raise
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise SSHRecoveryTimeout(
                        f'SSH recovery deadline exceeded during {description}; '
                        'existing jobs and recovery data are retained') from error
                failures += 1
                try:
                    client.close()
                except Exception:
                    logger.debug('Could not close stale SSH transport',
                                 exc_info=True)
                wait = min(delay, remaining)
                logger.warning(
                    'Waiting for SSH connection during %s (%s); retry in %.1fs, '
                    '%.1fs remaining. Existing job state is unchanged.',
                    description, type(error).__name__, wait, remaining)
                if cancel is None:
                    time.sleep(wait)
                else:
                    cancel.wait(wait)
                delay = min(delay * 2, 30)
                continue
            if failures:
                logger.info('SSH operation resumed: %s', description)
            return result
    finally:
        if previous is None:
            client.__dict__.pop('_ssh_recovery_deadline', None)
        else:
            client._ssh_recovery_deadline = previous


def download(client, remote, directory):
    """Publish a complete regular-file download without exposing partial data."""
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
                raise RuntimeError('Remote file changed or download is incomplete')
            os.replace(partial, destination)
            result.local = destination
            return result
        finally:
            if os.path.exists(partial):
                os.unlink(partial)

    return retry(client, attempt, 'download ' + remote.rsplit('/', 1)[-1])


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
    """Only replay commands protected by a remote lock and completion record."""
    from invoke.exceptions import UnexpectedExit

    def attempt():
        result = client.run_commands([command], env=env, warn=True)
        if result.exited == 200:
            raise RecoveryPending('Remote operation is pending reconciliation')
        if not result.ok:
            raise UnexpectedExit(result)
        return result

    return retry(client, attempt, description)


def locked_command(lock, body):
    """Reserve exit 200 for lock contention or an unresolved submission."""
    return (f'flock -w 5 -E 200 {shlex.quote(lock)} '
            f'sh -c {shlex.quote(body)}')


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
    state = posixpath.join(client.slurm_data_path, filename + '.biomero-unpacked')
    body = (f'if test -f {quote(state)}; then echo "Input already extracted"; '
            f'else ({client.get_unzip_command(filename)}) || exit 1; '
            f'printf "complete\\n" > {quote(state + ".tmp")} && '
            f'mv -f {quote(state + ".tmp")} {quote(state)} || exit 1; fi')
    return run_durable(client, locked_command(state + '.lock', body), env,
                       'extract input archive')


def submit(client, command, env, task_id):
    """Reconcile lost sbatch responses by task identity, never resubmit intent.

    A remote receipt bridges a process restart and the gap between Slurm's
    acceptance and recording the job in the local event store. Unresolved intent
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

    # squeue covers live jobs before sacct catches up; arrays reconcile to their
    # parent allocation. More than one match is ambiguous and never adopted.
    find_job = (
        'live=$(squeue -h -u "$(id -un)" -o "%A|%j") || exit 1; '
        f'recorded=$(sacct -n -X -u "$(id -un)" -S "$(cat {p(".intent")})" '
        '--format=JobID,JobName%100 -P) || exit 1; '
        'printf "%s\\n%s\\n" "$live" "$recorded" | '
        f'awk -F\'|\' -v token={quote(token)} '
        "'$2 == token {split($1, a, \"_\"); "
        "if (a[1] ~ /^[0-9]+$/) print a[1]}' | sort -u")
    body = (
        f'if test -e {p(".digest")} && '
        f'test "$(cat {p(".digest")})" != {quote(digest)}; then '
        'echo "Submission settings changed; manual reconciliation required" >&2; '
        'exit 1; fi; '
        f'if test -f {p(".failed")}; then cat {p(".stdout")}; '
        f'cat {p(".stderr")} >&2; exit 1; fi; '
        f'if test -s {p(".job")}; then job=$(cat {p(".job")}); '
        f'elif test -e {p(".intent")}; then job=$({find_job}) || exit 1; '
        'case "$job" in ""|*[!0-9]*) '
        'echo "Waiting for Slurm submission reconciliation" >&2; exit 200;; esac; '
        f'printf "%s\\n" "$job" > {p(".job.tmp")} && '
        f'mv -f {p(".job.tmp")} {p(".job")} || exit 1; '
        f'else printf "%s\\n" {quote(digest)} > {p(".digest")} && '
        f'date +%Y-%m-%dT%H:%M:%S > {p(".intent")} || exit 1; '
        f'({command}) > {p(".stdout")} 2> {p(".stderr")}; rc=$?; '
        f'if test "$rc" != 0; then printf "%s\\n" "$rc" > {p(".failed")}; '
        f'cat {p(".stdout")}; cat {p(".stderr")} >&2; exit 1; fi; '
        f'job=$(sed -n "s/^Submitted batch job \\([0-9][0-9]*\\)$/\\1/p" '
        f'{p(".stdout")} | tail -1); '
        'case "$job" in ""|*[!0-9]*) exit 200;; esac; '
        f'printf "%s\\n" "$job" > {p(".job.tmp")} && '
        f'mv -f {p(".job.tmp")} {p(".job")} || exit 1; fi; '
        'case "$job" in ""|*[!0-9]*) exit 1;; esac; '
        'printf "Submitted batch job %s\\n" "$job"')
    wrapped = (f'mkdir -p {quote(directory)} && ' +
               locked_command(state + '.lock', body))
    return run_durable(client, wrapped, env, 'submit/reconcile Slurm job')
