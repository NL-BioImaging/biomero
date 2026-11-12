"""Run the scripts BIOMERO sends to the cluster in a local POSIX shell.

The cluster is simulated with fake ``sbatch``, ``squeue`` and ``sacct``
programs in a temporary directory, so the scripts themselves execute for
real (lock, receipt files, ``sed``, ``awk``, ``find``), not just their text.
"""
import os
import shlex
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fabric import Result

from biomero.slurm_client import SlurmClient

POSIX_ONLY = pytest.mark.skipif(
    os.name == 'nt' or not shutil.which('sh'),
    reason='The remote scripts need a POSIX shell (Linux CI)')


def require_flock():
    """Skip where flock is missing, but fail on CI where it must exist."""
    if os.name == 'nt' or not shutil.which('flock'):
        if os.environ.get('CI'):
            pytest.fail('CI must provide flock for the lock tests')
        pytest.skip('flock is not available')


FAKE_SBATCH = """#!/bin/sh
echo call >> {attempts}
for arg in "$@"; do
  case "$arg" in --job-name=*) name=${{arg#--job-name=}};; esac
done
case " $* " in *" --reject "*) echo "sbatch: error: invalid partition" >&2
  exit 1;; esac
echo submitted >> {counter}
printf "42|%s\\n" "$name" >> {jobs}
case " $* " in
  *" --parsable "*) echo "42;cluster";;
  *) echo "Submitted batch job 42";;
esac
"""
# Both accounting commands print "JobID|JobName" rows. sacct honours --name.
FAKE_SQUEUE = """#!/bin/sh
cat {jobs} 2>/dev/null || true
"""
FAKE_SACCT = """#!/bin/sh
for arg in "$@"; do
  case "$arg" in --name=*) wanted=${{arg#--name=}};; esac
done
awk -F'|' -v wanted="$wanted" '$2 == wanted' {jobs} 2>/dev/null || true
"""


def make_remote(tmp_path, ssh_recovery_timeout=35):
    """Return a SlurmClient whose commands run locally against fake Slurm."""
    binaries = tmp_path / 'bin'
    binaries.mkdir()
    jobs = tmp_path / 'jobs'
    counter = tmp_path / 'submissions'
    attempts = tmp_path / 'sbatch-calls'
    quoted = {'jobs': shlex.quote(str(jobs)),
              'counter': shlex.quote(str(counter)),
              'attempts': shlex.quote(str(attempts))}
    for name, template in (('sbatch', FAKE_SBATCH), ('squeue', FAKE_SQUEUE),
                           ('sacct', FAKE_SACCT)):
        (binaries / name).write_text(template.format(**quoted))
        (binaries / name).chmod(0o755)
    environment = {**os.environ,
                   'PATH': str(binaries) + os.pathsep + os.environ['PATH']}
    client = SlurmClient(config_only=True,
                         ssh_recovery_timeout=ssh_recovery_timeout,
                         slurm_data_path=str(tmp_path))
    if not shutil.which('flock'):
        # No flock (local run): lock nothing. The lock tests need flock.
        client.slurm_lock_cmd = (
            'env LOCK_WAIT={wait_seconds} LOCK_BUSY={busy_exit_code} '
            'LOCK_FILE={lock_file}')
    client.close = Mock()
    commands = []

    def run_commands(cmdlist, env=None, warn=False, **kwargs):
        command = ' && '.join(cmdlist)
        commands.append(command)
        completed = subprocess.run(
            command, shell=True, executable='/bin/sh', capture_output=True,
            text=True, env={**environment, **(env or {})}, timeout=30)
        result = Result(connection=None, exited=completed.returncode,
                        stdout=completed.stdout, stderr=completed.stderr)
        if completed.returncode and not warn:
            from invoke.exceptions import UnexpectedExit
            raise UnexpectedExit(result)
        return result

    client.run_commands = run_commands
    return SimpleNamespace(
        client=client, root=tmp_path, jobs=jobs, counter=counter,
        commands=commands, environment=environment,
        sbatch_calls=lambda: (attempts.read_text().splitlines()
                              if attempts.exists() else []),
        submissions=lambda: (counter.read_text().splitlines()
                             if counter.exists() else []))
