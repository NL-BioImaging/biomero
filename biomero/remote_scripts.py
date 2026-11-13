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
"""Shell scripts that BIOMERO runs durably on the cluster.

After a lost SSH connection a caller cannot know whether its remote command
ran. These builders make a command safe to ask again: it runs under a lock and
leaves a marker or receipt file, so that a second request finds the first
result instead of repeating the work (for example a second ``sbatch``).

The builders only create strings and know nothing about connections; the
caller (``SlurmClient``) runs them. Each script is built from small named
parts, so you can read what it does without tracing nested quotes.

Receipt protocol of ``submit_once_script``. All files share one prefix, the
*state prefix* (``<slurm_data_path>/.biomero-recovery/<task id>``):

* ``.lock``: the lock file; one submission per prefix runs at a time.
* ``.digest``: hash of the command, to notice a changed command on resume.
* ``.intent``: written BEFORE sbatch runs. It holds the submission time. An
  intent without a ``.job`` means the outcome of sbatch is unknown.
* ``.job``: the Slurm job id, published atomically once known.
* ``.failed``: sbatch refused the job (no job exists); keeps the output.
* ``.stdout`` / ``.stderr``: the output of the sbatch call.

Exit codes with a meaning to the caller: ``REMOTE_BUSY_EXIT_CODE`` (the lock
is held elsewhere, or the job is not visible in Slurm yet; ask again later)
and ``SUBMISSION_REJECTED_EXIT_CODE`` (sbatch refused the job).
"""
import shlex
from typing import Iterable, Optional

from biomero.constants import (
    LOCK_WAIT_SECONDS, REMOTE_BUSY_EXIT_CODE, SUBMISSION_REJECTED_EXIT_CODE)

# Receipt files of one submission: the shell variable that names each one in
# the generated script, and its suffix. This is the only place they are listed.
SUBMISSION_STATE_FILES = {
    "digest_file": ".digest",
    "intent_file": ".intent",
    "job_file": ".job",
    "job_tmp_file": ".job.tmp",
    "failed_file": ".failed",
    "stdout_file": ".stdout",
    "stderr_file": ".stderr",
}
SUBMISSION_STATE_SUFFIXES = (".lock", *SUBMISSION_STATE_FILES.values())

TIMESTAMP_COMMAND = "date +%Y-%m-%dT%H:%M:%S"
# Matches both "Submitted batch job 42" and the ``sbatch --parsable`` output
# "42" or "42;cluster".
EXTRACT_JOB_ID_COMMAND = (
    "sed -n -e 's/^Submitted batch job \\([0-9][0-9]*\\).*$/\\1/p' "
    "-e 's/^\\([0-9][0-9]*\\)\\(;.*\\)\\{0,1\\}$/\\1/p'")


def locked_script(lock_template: str, lock_file: str, script: str,
                  wait_seconds: int = LOCK_WAIT_SECONDS) -> str:
    """Run a script while holding a lock on the cluster.

    The lock command (``flock`` by default) takes an exclusive lock on the
    lock file and then runs the script, so concurrent callers run one at a
    time. A caller that waits longer than ``wait_seconds`` gets
    ``REMOTE_BUSY_EXIT_CODE`` instead of the script's own result.

    Args:
        lock_template (str): The lock command template.
        lock_file (str): Remote path of the lock file.
        script (str): The shell script to run under the lock.
        wait_seconds (int): How long to wait for the lock.

    Returns:
        str: The command that runs the script under the lock.
    """
    lock_prefix = lock_template.format(
        wait_seconds=wait_seconds, busy_exit_code=REMOTE_BUSY_EXIT_CODE,
        lock_file=shlex.quote(lock_file))
    return f"{lock_prefix} sh -c {shlex.quote(script)}"


def create_then_publish_script(create_command: str, partial_file: str,
                               final_file: str) -> str:
    """Create a file under a temporary name and rename it when complete.

    Args:
        create_command (str): The command that creates ``partial_file``.
        partial_file (str): The temporary remote path.
        final_file (str): The remote path to publish.

    Returns:
        str: A script that never shows ``final_file`` half written.
    """
    return (f"rm -f -- {shlex.quote(partial_file)}\n"
            f"({create_command}) || exit 1\n"
            f"mv -f -- {shlex.quote(partial_file)} {shlex.quote(final_file)} || exit 1")


def run_once_script(work_command: str, done_marker: str, *,
                    stale_if_newer: Iterable[str] = (),
                    published_file: Optional[str] = None,
                    reuse_message: str = "Reusing the completed result"
                    ) -> str:
    """Run a command once and skip it when its marker shows it completed.

    The marker records a finished run. A finished run is reused only while it
    is still current: not when an input is newer than the marker (a zip that
    was uploaded again, results that changed), nor when the published file no
    longer has the recorded size.

    Args:
        work_command (str): The command to run when there is no current
            marker.
        done_marker (str): Remote path of the marker file.
        stale_if_newer (Iterable[str]): Remote paths; if any of them (or
            anything below a directory) is newer than the marker, the work
            runs again.
        published_file (Optional[str]): A file the work produces. Its size is
            stored in the marker and compared on reuse.
        reuse_message (str): Printed when the marker is reused.

    Returns:
        str: The shell script.
    """
    marker = shlex.quote(done_marker)
    marker_content = "complete"
    marker_is_current = f"test -f {marker}"
    if published_file:
        published = shlex.quote(published_file)
        marker_content = f'"$(wc -c < {published})"'
        marker_is_current += (
            f" && test -f {published}"
            f' && test "$(cat {marker})" = "$(wc -c < {published})"')
    newer_inputs = " ".join(shlex.quote(path) for path in stale_if_newer)
    if newer_inputs:
        marker_is_current += (
            f' && test -z "$(find {newer_inputs} -newer {marker} '
            '2>/dev/null | head -n 1)"')
    write_marker = (
        f"printf '%s\\n' {marker_content} > {shlex.quote(done_marker + '.tmp')}"
        f" && mv -f {shlex.quote(done_marker + '.tmp')} {marker} || exit 1")
    return (f"if {marker_is_current}; then\n"
            f"  echo {shlex.quote(reuse_message)}\n"
            "else\n"
            f"  ({work_command}) || exit 1\n"
            f"  {write_marker}\n"
            "fi")


def remove_state_files_command(state_prefix: str) -> str:
    """Build the command that removes the receipt files of one submission.

    Args:
        state_prefix (str): The state prefix of the submission.

    Returns:
        str: The ``rm -f`` command.
    """
    files = " ".join(
        shlex.quote(state_prefix + suffix)
        for suffix in SUBMISSION_STATE_SUFFIXES)
    return f"rm -f {files}"


def _define_state_files(state_prefix: str, command_digest: str) -> str:
    """Name the receipt files as shell variables for the other parts."""
    lines = [f"{variable}={shlex.quote(state_prefix + suffix)}"
             for variable, suffix in SUBMISSION_STATE_FILES.items()]
    lines.append(f"new_digest={shlex.quote(command_digest)}")
    return "\n".join(lines)


def _handle_changed_command() -> str:
    """Stop when the command differs from the recorded one.

    A recorded rejection created no job, so it is discarded and the changed
    command may submit. Otherwise a job may exist for the old command: stop.
    """
    return (
        'if test -e "$digest_file" && '
        'test "$(cat "$digest_file")" != "$new_digest"; then\n'
        '  if test -f "$failed_file"; then\n'
        '    echo "Discarding the rejected submission of a different '
        'command" >&2\n'
        '    rm -f "$digest_file" "$intent_file" "$failed_file" '
        '"$stdout_file" "$stderr_file"\n'
        '  else\n'
        '    echo "Submission settings changed; manual reconciliation '
        'required" >&2\n'
        '    exit 1\n'
        '  fi\n'
        'fi')


def _replay_recorded_rejection() -> str:
    """Repeat the output of an earlier sbatch rejection instead of asking again."""
    return (
        'if test -f "$failed_file"; then\n'
        '  echo "sbatch refused this job earlier (receipt $failed_file); '
        'delete the receipt files to submit again" >&2\n'
        '  cat "$stdout_file"; cat "$stderr_file" >&2\n'
        f'  exit {SUBMISSION_REJECTED_EXIT_CODE}\n'
        'fi')


def _define_publish_job(job_name: str) -> str:
    """Define the shell function ``publish_job``, used after a job id is known.

    It validates ``$job`` and publishes it to the ``.job`` file atomically.
    An empty ``$job`` means Slurm does not show the job yet, and several ids
    mean the name is not unique. Both ask the caller to wait, and the message
    tells an administrator how to resolve a submission that never resolves.
    """
    way_out = ('The submission started at $(cat "$intent_file") '
               '(intent file $intent_file). Check sacct and squeue for a job '
               'with this name; delete the receipt files with the same '
               'prefix only if no such job exists.')
    return (
        'publish_job() {\n'
        '  case "$job" in\n'
        f'    "") echo "Waiting for Slurm to show job {job_name}. {way_out}" '
        f'>&2; exit {REMOTE_BUSY_EXIT_CODE};;\n'
        f'    *[!0-9]*) echo "Several Slurm jobs are named {job_name}: $job. '
        f'{way_out}" >&2; exit {REMOTE_BUSY_EXIT_CODE};;\n'
        '  esac\n'
        '  printf "%s\\n" "$job" > "$job_tmp_file" && '
        'mv -f "$job_tmp_file" "$job_file" || exit 1\n'
        '}')


def _indent(script: str) -> str:
    """Indent every line of a script by two spaces."""
    return "\n".join(f"  {line}" for line in script.splitlines())


def _find_job_by_name(job_name: str) -> str:
    """Find the ids of the jobs with this name, since the intent was written.

    Looks in the queue (``squeue``) and in the accounting (``sacct``, which
    must be enabled on the cluster). Array jobs ``42_7`` count as job 42.
    """
    name = shlex.quote(job_name)
    return (
        'live=$(squeue -h -u "$(id -un)" -o "%A|%j") || exit 1\n'
        f'recorded=$(sacct -n -X -u "$(id -un)" --name={name} '
        '-S "$(cat "$intent_file")" --format=JobID,JobName%100 -P) '
        '|| exit 1\n'
        'job=$(printf "%s\\n%s\\n" "$live" "$recorded" | '
        f"awk -F'|' -v name={name} "
        "'$2 == name {split($1, a, \"_\"); "
        "if (a[1] ~ /^[0-9]+$/) print a[1]}' | sort -u)")


def _submit_and_record(sbatch_command: str, job_name: str, *,
                       verify_command_unchanged: bool,
                       remember_rejection: bool) -> str:
    """Write the intent, run sbatch, and record the job id or the rejection."""
    write_digest = ('printf "%s\\n" "$new_digest" > "$digest_file" && '
                    if verify_command_unchanged else "")
    if remember_rejection:
        on_rejection = 'printf "%s\\n" "$rc" > "$failed_file"'
    else:
        # Forget the attempt, so that a later call may submit again.
        on_rejection = 'rm -f "$intent_file" "$digest_file"'
    return (
        f'{write_digest}{TIMESTAMP_COMMAND} > "$intent_file" || exit 1\n'
        f'(\n{sbatch_command}\n) > "$stdout_file" 2> "$stderr_file"; rc=$?\n'
        'if test "$rc" != 0; then\n'
        f'  {on_rejection}\n'
        '  cat "$stdout_file"; cat "$stderr_file" >&2\n'
        f'  exit {SUBMISSION_REJECTED_EXIT_CODE}\n'
        'fi\n'
        f'job=$({EXTRACT_JOB_ID_COMMAND} "$stdout_file" | tail -n 1)\n'
        'publish_job')


def submit_once_script(sbatch_command: str, state_prefix: str, job_name: str,
                       command_digest: str, *,
                       verify_command_unchanged: bool = True,
                       remember_rejection: bool = True) -> str:
    """Submit a Slurm job exactly once, however often the script is run.

    Run again after a lost connection, the script adopts the job recorded by
    the first run; when the first run died between sbatch and the record, it
    finds the job by its name in Slurm. It never runs sbatch a second time
    while an earlier attempt may have created a job.

    Args:
        sbatch_command (str): The submission command. Its job name must be
            ``job_name``, because that name is how a lost job is found again.
        state_prefix (str): Remote path prefix of the receipt files.
        job_name (str): The Slurm job name of this submission.
        command_digest (str): Hash of the command and its environment.
        verify_command_unchanged (bool): Stop when a recorded submission used
            a different command (default). Without it, no digest is kept.
        remember_rejection (bool): Keep a ``.failed`` receipt when sbatch
            refuses the job, and repeat that answer on later calls (default).
            Without it the attempt is forgotten, so a later call submits
            again.

    Returns:
        str: The shell script. It prints ``Submitted batch job <id>``.
    """
    parts = [_define_state_files(state_prefix, command_digest),
             _define_publish_job(job_name)]
    if verify_command_unchanged:
        parts.append(_handle_changed_command())
    if remember_rejection:
        parts.append(_replay_recorded_rejection())
    submit_step = _submit_and_record(
        sbatch_command, job_name,
        verify_command_unchanged=verify_command_unchanged,
        remember_rejection=remember_rejection)
    parts.append(
        'if test -s "$job_file"; then\n'
        '  job=$(cat "$job_file")\n'
        'elif test -e "$intent_file"; then\n'
        f'{_indent(_find_job_by_name(job_name))}\n'
        '  publish_job\n'
        'else\n'
        f'{submit_step}\n'
        'fi')
    parts.append('printf "Submitted batch job %s\\n" "$job"')
    return "\n".join(parts)
