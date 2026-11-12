"""The durable remote scripts, executed in a real shell against fake Slurm."""
import os
import shlex
import time
from pathlib import Path
from threading import Timer
from uuid import uuid4

import pytest
from fabric import Result
from invoke.exceptions import UnexpectedExit

from biomero import remote_scripts, ssh_recovery
from biomero.slurm_client import SlurmClient
from biomero.constants import (
    REMOTE_BUSY_EXIT_CODE, SUBMISSION_REJECTED_EXIT_CODE,
    SUBMISSION_STATE_SUFFIXES)
from .remote_shell import POSIX_ONLY, make_remote, require_flock
from .test_ssh_recovery import recovery_clock  # noqa: F401

pytestmark = POSIX_ONLY


@pytest.fixture
def remote(tmp_path):
    return make_remote(tmp_path)


def state_files(remote, task_id):
    prefix = remote.client.get_submission_state_prefix(task_id)
    return {suffix for suffix in SUBMISSION_STATE_SUFFIXES
            if Path(prefix + suffix).exists()}


def submit(remote, task_id, command=None, **options):
    command = command or f'sbatch --job-name={name(task_id)} job.sh'
    return remote.client.submit_job_once(
        command, remote.client.get_submission_state_prefix(task_id),
        job_name=name(task_id), **options)


def name(task_id):
    return SlurmClient.get_submission_job_name(task_id)


def lose_first_response(remote, *, lose_receipt=False, task_id=None):
    """The command runs, but its answer never arrives (exit status -1)."""
    original = remote.client.run_commands
    calls = []

    def run_commands(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            assert result.ok, result.stderr
            if lose_receipt:
                prefix = remote.client.get_submission_state_prefix(task_id)
                Path(prefix + '.job').unlink()
            return Result(connection=None, exited=-1)
        return result

    remote.client.run_commands = run_commands
    return calls


@pytest.mark.parametrize('lose_receipt', [False, True])
def test_lost_acknowledgement_never_submits_twice(
        remote, recovery_clock, lose_receipt):
    task_id = uuid4()
    calls = lose_first_response(remote, lose_receipt=lose_receipt,
                                task_id=task_id)
    result = submit(remote, task_id)
    assert result.stdout == 'Submitted batch job 42\n'
    assert remote.submissions() == ['submitted']
    assert len(calls) == 2


def test_unresolved_intent_waits_and_never_resubmits(remote, recovery_clock):
    task_id = uuid4()
    prefix = Path(remote.client.get_submission_state_prefix(task_id))
    prefix.parent.mkdir()
    Path(str(prefix) + '.intent').write_text('2026-10-30T00:00:00')
    with pytest.raises(ssh_recovery.RemoteOperationStillPending):
        submit(remote, task_id)
    assert remote.submissions() == []


def test_a_job_with_another_name_is_not_adopted(remote, recovery_clock):
    task_id = uuid4()
    prefix = Path(remote.client.get_submission_state_prefix(task_id))
    prefix.parent.mkdir()
    Path(str(prefix) + '.intent').write_text('2026-10-30T00:00:00')
    remote.jobs.write_text('41|someone-elses-job\n')
    with pytest.raises(ssh_recovery.RemoteOperationStillPending):
        submit(remote, task_id)
    assert not Path(str(prefix) + '.job').exists()


def test_the_job_with_its_own_name_is_found_after_a_restart(
        remote, recovery_clock):
    task_id = uuid4()
    prefix = Path(remote.client.get_submission_state_prefix(task_id))
    prefix.parent.mkdir()
    Path(str(prefix) + '.intent').write_text('2026-10-30T00:00:00')
    remote.jobs.write_text(f'41|other\n77_3|{name(task_id)}\n'
                           f'77_4|{name(task_id)}\n')
    result = submit(remote, task_id)
    assert result.stdout == 'Submitted batch job 77\n'
    assert Path(str(prefix) + '.job').read_text().strip() == '77'
    assert remote.submissions() == []


def test_several_jobs_with_the_same_name_are_not_guessed(
        remote, recovery_clock, caplog):
    task_id = uuid4()
    prefix = Path(remote.client.get_submission_state_prefix(task_id))
    prefix.parent.mkdir()
    Path(str(prefix) + '.intent').write_text('2026-10-30T00:00:00')
    remote.jobs.write_text(f'41|{name(task_id)}\n42|{name(task_id)}\n')
    with pytest.raises(ssh_recovery.RemoteOperationStillPending):
        submit(remote, task_id)
    assert remote.submissions() == []


def test_receipt_survives_a_new_client(remote, recovery_clock):
    task_id = uuid4()
    submit(remote, task_id)
    replacement = SlurmClient(config_only=True, ssh_recovery_timeout=35,
                              slurm_data_path=str(remote.root))
    replacement.run_commands = remote.client.run_commands
    replacement.submit_job_once(
        f'sbatch --job-name={name(task_id)} job.sh',
        replacement.get_submission_state_prefix(task_id),
        job_name=name(task_id))
    assert remote.submissions() == ['submitted']


def test_changed_command_is_rejected_without_a_new_job(remote, recovery_clock):
    task_id = uuid4()
    submit(remote, task_id, 'sbatch job.sh')
    with pytest.raises(UnexpectedExit) as rejected:
        submit(remote, task_id, 'sbatch different.sh')
    assert 'Submission settings changed' in rejected.value.result.stderr
    assert remote.submissions() == ['submitted']


def test_the_environment_is_part_of_the_command_identity(
        remote, recovery_clock):
    task_id = uuid4()
    submit(remote, task_id, 'sbatch job.sh', env={'PARAM': '1'})
    with pytest.raises(UnexpectedExit):
        submit(remote, task_id, 'sbatch job.sh', env={'PARAM': '2'})
    assert remote.submissions() == ['submitted']


def test_parsable_output_is_accepted(remote, recovery_clock):
    task_id = uuid4()
    result = submit(remote, task_id,
                    f'sbatch --parsable --job-name={name(task_id)} job.sh')
    assert result.stdout == 'Submitted batch job 42\n'
    assert remote.client.extract_job_id(result) == 42


def test_rejection_is_reported_and_replayed_without_asking_slurm_again(
        remote, recovery_clock):
    task_id = uuid4()
    command = f'sbatch --reject --job-name={name(task_id)} job.sh'
    with pytest.raises(UnexpectedExit) as first:
        submit(remote, task_id, command)
    assert first.value.result.exited == SUBMISSION_REJECTED_EXIT_CODE
    assert 'invalid partition' in first.value.result.stderr
    with pytest.raises(UnexpectedExit) as replay:
        submit(remote, task_id, command)
    assert 'invalid partition' in replay.value.result.stderr
    # The replay did not ask Slurm again.
    assert remote.sbatch_calls() == ['call']
    assert remote.submissions() == []
    assert '.failed' in state_files(remote, task_id)


def test_corrected_command_replaces_a_recorded_rejection(
        remote, recovery_clock):
    task_id = uuid4()
    with pytest.raises(UnexpectedExit):
        submit(remote, task_id, f'sbatch --reject --job-name={name(task_id)} a')
    result = submit(remote, task_id)
    assert result.stdout == 'Submitted batch job 42\n'
    assert remote.submissions() == ['submitted']
    assert '.failed' not in state_files(remote, task_id)


def test_tolerated_rejection_is_forgotten_so_a_later_call_submits(
        remote, recovery_clock):
    task_id = uuid4()
    rejected = submit(remote, task_id,
                      f'sbatch --reject --job-name={name(task_id)} job.sh',
                      raise_on_rejection=False)
    assert rejected.exited == SUBMISSION_REJECTED_EXIT_CODE
    assert not state_files(remote, task_id) - {'.lock', '.stdout', '.stderr'}
    accepted = submit(remote, task_id, raise_on_rejection=False)
    assert remote.client.extract_job_id(accepted) == 42
    assert remote.submissions() == ['submitted']


def test_without_digest_a_changed_command_adopts_the_recorded_job(
        remote, recovery_clock):
    task_id = uuid4()
    submit(remote, task_id, 'sbatch job.sh', verify_command_unchanged=False)
    assert '.digest' not in state_files(remote, task_id)
    result = submit(remote, task_id, 'sbatch other.sh',
                    verify_command_unchanged=False)
    assert remote.client.extract_job_id(result) == 42
    assert remote.submissions() == ['submitted']


def test_two_runs_have_separate_receipts(remote, recovery_clock):
    first, second = uuid4(), uuid4()
    submit(remote, first)
    submit(remote, second)
    assert remote.submissions() == ['submitted', 'submitted']


def test_recorded_outcome_is_cleaned_up_from_the_cluster(remote):
    task_id = uuid4()
    submit(remote, task_id)
    assert {'.job', '.intent', '.digest'} <= state_files(remote, task_id)
    remote.client.remove_submission_receipts(
        remote.client.get_submission_state_prefix(task_id))
    assert state_files(remote, task_id) == set()


def test_failed_cleanup_is_only_a_warning(remote, recovery_clock, caplog):
    remote.client.run_commands = lambda *a, **k: (_ for _ in ()).throw(
        PermissionError('read-only filesystem'))
    remote.client.remove_submission_receipts('/shared/.biomero-recovery/x')
    assert any(record.levelname == 'WARNING'
               and 'Could not remove the submission receipts'
               in record.getMessage() for record in caplog.records)


# --- unpacking and archiving ------------------------------------------------

def unpack_client(remote):
    extractions = remote.root / 'extractions'
    remote.client.get_unzip_command = lambda zip_name: (
        f'echo extracted >> {shlex.quote(str(extractions))}')
    zip_file = remote.root / 'inputs.zip'
    zip_file.write_text('zip')
    old = time.time() - 100
    os.utime(zip_file, (old, old))
    return extractions, zip_file


def test_unpack_runs_once_even_when_asked_again(remote, recovery_clock):
    extractions, _ = unpack_client(remote)
    original = remote.client.run_commands
    calls = []

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            return Result(connection=None, exited=-1)
        return result

    remote.client.run_commands = lose_response
    remote.client.unpack_data('inputs')
    assert extractions.read_text().splitlines() == ['extracted']


def test_unpack_runs_again_for_a_zip_uploaded_after_the_extraction(remote):
    extractions, zip_file = unpack_client(remote)
    remote.client.unpack_data('inputs')
    remote.client.unpack_data('inputs')
    assert extractions.read_text().splitlines() == ['extracted']
    newer = time.time() + 100
    os.utime(zip_file, (newer, newer))
    remote.client.unpack_data('inputs')
    assert extractions.read_text().splitlines() == ['extracted', 'extracted']


def archive_client(remote):
    creations = remote.root / 'creations'
    (remote.root / 'data' / 'out').mkdir(parents=True)
    result_file = remote.root / 'data' / 'out' / 'result.txt'
    result_file.write_text('result')
    old = time.time() - 100
    os.utime(result_file, (old, old))
    os.utime(result_file.parent, (old, old))
    remote.client.get_zip_command = lambda location, zip_name: (
        f'echo created >> {shlex.quote(str(creations))}; '
        f'printf archive > {shlex.quote(str(Path(location) / (zip_name + ".zip")))}')
    return creations, result_file


def test_archive_response_loss_reuses_the_complete_archive(
        remote, recovery_clock):
    creations, _ = archive_client(remote)
    original = remote.client.run_commands
    calls = []

    def lose_response(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            return Result(connection=None, exited=-1)
        return result

    remote.client.run_commands = lose_response
    remote.client.zip_data_on_slurm_server(str(remote.root), '42_out')
    assert creations.read_text().splitlines() == ['created']
    assert (remote.root / '42_out.zip').read_text() == 'archive'
    assert not list(remote.root.glob('*.biomero-partial*'))


def test_archive_is_made_again_when_results_changed_afterwards(remote):
    creations, result_file = archive_client(remote)
    remote.client.zip_data_on_slurm_server(str(remote.root), '42_out')
    remote.client.zip_data_on_slurm_server(str(remote.root), '42_out')
    assert creations.read_text().splitlines() == ['created']
    newer = time.time() + 100
    os.utime(result_file, (newer, newer))
    remote.client.zip_data_on_slurm_server(str(remote.root), '42_out')
    assert creations.read_text().splitlines() == ['created', 'created']


def test_archive_is_made_again_when_the_published_file_changed_size(remote):
    creations, _ = archive_client(remote)
    remote.client.zip_data_on_slurm_server(str(remote.root), '42_out')
    (remote.root / '42_out.zip').write_text('truncated by something else')
    remote.client.zip_data_on_slurm_server(str(remote.root), '42_out')
    assert creations.read_text().splitlines() == ['created', 'created']


# --- the lock -----------------------------------------------------------------

def test_a_held_lock_makes_the_operation_wait_then_run_once(
        remote, recovery_clock):
    require_flock()
    import fcntl
    extractions, _ = unpack_client(remote)
    lock_path = remote.root / 'inputs.biomero-unpacked.lock'
    lock = open(lock_path, 'w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    original = remote.client.run_commands

    def short_lock_wait(commands, **kwargs):
        shortened = [c.replace('flock -w 5 ', 'flock -w 0.05 ')
                     for c in commands]
        return original(shortened, **kwargs)

    remote.client.run_commands = short_lock_wait
    release = Timer(0.3, lambda: fcntl.flock(lock, fcntl.LOCK_UN))
    release.start()
    try:
        remote.client.unpack_data('inputs')
    finally:
        release.join()
        lock.close()
    assert extractions.read_text().splitlines() == ['extracted']


def test_a_lock_held_for_good_ends_as_pending_not_as_an_ssh_outage(
        remote, recovery_clock):
    require_flock()
    import fcntl
    unpack_client(remote)
    lock = open(remote.root / 'inputs.biomero-unpacked.lock', 'w')
    fcntl.flock(lock, fcntl.LOCK_EX)
    original = remote.client.run_commands
    remote.client.run_commands = lambda commands, **kwargs: original(
        [c.replace('flock -w 5 ', 'flock -w 0.05 ') for c in commands],
        **kwargs)
    try:
        with pytest.raises(ssh_recovery.RemoteOperationStillPending):
            remote.client.unpack_data('inputs')
    finally:
        lock.close()


def test_the_configured_lock_command_is_used(remote, tmp_path):
    """slurm_lock_cmd is a template: any lock tool can replace flock."""
    log = remote.root / 'lock-calls'
    tool = remote.root / 'bin' / 'mylock'
    tool.write_text(
        '#!/bin/sh\n'
        f'echo "$@" >> {shlex.quote(str(log))}\n'
        'wait_seconds=$1; busy=$2; lock=$3; shift 3\n'
        'exec "$@"\n')
    tool.chmod(0o755)
    remote.client.slurm_lock_cmd = (
        'mylock {wait_seconds} {busy_exit_code} {lock_file}')
    unpack_client(remote)
    remote.client.unpack_data('inputs')
    arguments = log.read_text().split()
    assert arguments[:3] == ['5', str(REMOTE_BUSY_EXIT_CODE),
                             str(remote.root / 'inputs.biomero-unpacked.lock')]


# --- builders without a shell ---------------------------------------------------

def test_lock_template_placeholders_are_filled_and_the_script_quoted():
    command = remote_scripts.locked_script(
        'flock -w {wait_seconds} -E {busy_exit_code} {lock_file}',
        '/data/my file.lock', 'echo "it\'s"', wait_seconds=7)
    assert command.startswith(
        f"flock -w 7 -E {REMOTE_BUSY_EXIT_CODE} '/data/my file.lock' sh -c ")
    assert shlex.split(command)[-1] == 'echo "it\'s"'


@pytest.mark.parametrize('template', [
    'flock -w {wait_seconds} -E {busy_exit_code} {lock_file}',
    'lockf -t {wait_seconds} {lock_file}',
    'lock-only {lock_file}',
])
def test_valid_lock_templates_are_accepted(template):
    remote_scripts.validate_lock_template(template)


@pytest.mark.parametrize('template', ['', '   ', 'flock {unknown}',
                                      'flock {0}', 'flock {lock_file'])
def test_invalid_lock_templates_are_rejected(template):
    with pytest.raises(ValueError, match='slurm_lock_cmd'):
        remote_scripts.validate_lock_template(template)


def test_the_lock_executable_is_the_first_word():
    assert remote_scripts.lock_executable(
        '/opt/bin/flock -w {wait_seconds} {lock_file}') == '/opt/bin/flock'
