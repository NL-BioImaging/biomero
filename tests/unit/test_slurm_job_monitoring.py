from unittest.mock import MagicMock, patch

import pytest
from paramiko import SSHException

from biomero.slurm_client import REQUEUE_RECHECK_SECONDS, SlurmJob
from .test_slurm_client import slurm_client, set_env_vars  # noqa: F401


def test_heartbeat_runs_each_poll_and_failure_propagates_unchanged():
    client = MagicMock()
    client.check_job_status.return_value = ({7: 'PENDING'}, MagicMock(ok=True))
    failure = RuntimeError('caller heartbeat failed')
    heartbeat = MagicMock(side_effect=[None, failure])
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep'), pytest.raises(RuntimeError) as error:
        job.wait_for_completion(client, heartbeat=heartbeat,
                                track_progress=False, update_task=False)
    assert error.value is failure
    assert heartbeat.call_count == 2
    assert job.job_state == 'PENDING'


@pytest.mark.parametrize('call_style', ['positional', 'keyword'])
def test_deprecated_connection_argument_becomes_keep_alive_heartbeat(
        call_style):
    """2.8.0 callers pass the OMERO connection; its keepAlive stays called."""
    client, connection = MagicMock(), MagicMock()
    client.check_job_status.side_effect = [
        ({7: 'RUNNING'}, MagicMock(ok=True)),
        ({7: 'COMPLETED'}, MagicMock(ok=True))]
    client.get_active_job_progress.return_value = 'progress'
    job = SlurmJob.from_job_id(7, slurm_polling_interval=0)
    args = (client, connection) if call_style == 'positional' else (client,)
    kwargs = {} if call_style == 'positional' else {'omeroConn': connection}

    with pytest.warns(DeprecationWarning, match='heartbeat'):
        state = job.wait_for_completion(*args, **kwargs)

    assert state == 'COMPLETED'
    assert connection.keepAlive.call_count == 2


def test_heartbeat_takes_precedence_over_deprecated_connection():
    client, connection, heartbeat = MagicMock(), MagicMock(), MagicMock()
    client.check_job_status.return_value = ({7: 'COMPLETED'}, MagicMock(ok=True))
    job = SlurmJob.from_job_id(7, slurm_polling_interval=0)

    with pytest.warns(DeprecationWarning):
        job.wait_for_completion(client, connection, heartbeat=heartbeat)

    heartbeat.assert_called_once_with()
    connection.keepAlive.assert_not_called()


def test_public_shallower_has_no_connection_argument():
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True)
    with pytest.raises(TypeError, match='omero_conn'):
        client.shallow_results_on_slurm(
            '/data', 'workflow', None, helper_image='registry/helper:0.1.0',
            omero_conn=MagicMock())


def test_public_shallower_passes_callback_unchanged():
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True)
    heartbeat = MagicMock()
    with patch('biomero.remote_shallower.shallow_workflow_results') as run:
        client.shallow_results_on_slurm(
            '/data', 'workflow', None, helper_image='registry/helper:0.1.0',
            heartbeat=heartbeat)
    assert run.call_args.args[4] is heartbeat
    assert run.call_args.kwargs['image'] == 'registry/helper:0.1.0'


def test_adopted_job_calls_heartbeat_without_analysis_progress():
    client, heartbeat = MagicMock(), MagicMock()
    client.check_job_status.side_effect = [
        ({7: state}, MagicMock(ok=True))
        for state in ('PENDING', 'RUNNING', 'COMPLETED')]
    job = SlurmJob.from_job_id(7, slurm_polling_interval=15)
    with patch('biomero.slurm_client.timesleep.sleep') as sleep:
        assert job.wait_for_completion(
            client, heartbeat=heartbeat, track_progress=False, update_task=False,
            strict_status=True) == 'COMPLETED'
    assert job.completed()
    assert heartbeat.call_count == 3
    assert sleep.call_count == 2
    client.get_active_job_progress.assert_not_called()
    client.workflowTracker.update_task_status.assert_not_called()


@pytest.mark.parametrize('state', ['OUT_OF_MEMORY', 'NODE_FAIL', 'PREEMPTED',
                                 'BOOT_FAIL', 'DEADLINE', 'CANCELLED by 123'])
def test_additional_terminal_states_do_not_keep_polling(state):
    client = MagicMock()
    client.check_job_status.return_value = ({7: state}, MagicMock(ok=True))
    client.find_requeued_job_state.return_value = None
    job = SlurmJob(MagicMock(ok=True), 7, None, None)
    with patch('biomero.slurm_client.timesleep.sleep') as sleep:
        assert job.wait_for_completion(client, track_progress=False,
                                       update_task=False) == state
    sleep.assert_not_called()


@pytest.mark.parametrize('statuses,ok', [({}, True), ({7: 'UNKNOWN'}, True),
                                       ({7: 'COMPLETED'}, False)])
def test_unavailable_status_does_not_record_failure(statuses, ok):
    client = MagicMock()
    client.check_job_status.return_value = (statuses, MagicMock(ok=ok, stderr='lost'))
    job = SlurmJob(MagicMock(ok=True), 7, None, None)
    with pytest.raises(RuntimeError, match='unavailable'):
        job.wait_for_completion(client, strict_status=True)
    client.workflowTracker.update_task_status.assert_not_called()


def test_failed_poll_cannot_be_overwritten_by_success_status():
    client = MagicMock()
    client.check_job_status.return_value = (
        {7: 'COMPLETED'}, MagicMock(ok=False, stderr='lost'))
    job = SlurmJob(MagicMock(ok=True), 7, None, None)
    assert job.wait_for_completion(client, heartbeat=MagicMock()) == 'FAILED'
    assert job.get_error() == 'lost'


def test_failed_heartbeat_preserves_adopted_job():
    client = MagicMock()
    heartbeat = MagicMock(side_effect=RuntimeError('heartbeat failed'))
    job = SlurmJob.from_job_id(7)
    with pytest.raises(RuntimeError, match='heartbeat failed'):
        job.wait_for_completion(client, heartbeat=heartbeat, strict_status=True)
    client.workflowTracker.update_task_status.assert_not_called()
    assert not job.completed()


def test_deprecation_warning_points_at_the_caller():
    client, connection = MagicMock(), MagicMock()
    client.check_job_status.return_value = ({7: 'COMPLETED'}, MagicMock(ok=True))
    job = SlurmJob.from_job_id(7, slurm_polling_interval=0)

    with pytest.warns(DeprecationWarning) as record:
        job.wait_for_completion(client, connection)

    assert record[0].filename == __file__


def squeue_result(stdout, ok=True):
    """The result of ``squeue -h -j <id> -o %T``."""
    return MagicMock(ok=ok, stdout=stdout, stderr='' if ok else stdout)


@pytest.fixture
def node_failed_client(slurm_client):
    """A client whose job state answers are set per test."""
    slurm_client.check_job_status = MagicMock()
    slurm_client.run_commands_read_only = MagicMock()
    slurm_client.get_active_job_progress = MagicMock()
    return slurm_client


@pytest.mark.parametrize('ended_state,queue_state', [
    ('NODE_FAIL', 'PENDING'), ('PREEMPTED', 'PENDING'),
    ('NODE_FAIL', 'REQUEUE_HOLD')])
def test_a_job_requeued_after_it_ended_is_followed_to_completion(
        node_failed_client, ended_state, queue_state, caplog):
    """Slurm requeues the job under the same ID; sacct still shows the
    ended attempt, squeue the requeued one. No new job is submitted."""
    client = node_failed_client
    client.check_job_status.side_effect = [
        ({7: state}, MagicMock(ok=True))
        for state in (ended_state, 'RUNNING', 'COMPLETED')]
    client.run_commands_read_only.return_value = squeue_result(
        f'{queue_state}\n')
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep'), \
            caplog.at_level('INFO', logger='biomero.slurm_client'):
        state = job.wait_for_completion(client, track_progress=False,
                                        update_task=False)
    assert state == 'COMPLETED'
    assert client.check_job_status.call_count == 3
    client.run_commands_read_only.assert_called_once_with(
        ['squeue -h -j 7 -o %T'], warn=True)
    assert f'job 7 was requeued after {ended_state}; still waiting' in caplog.text


def test_a_node_failed_job_that_is_not_requeued_ends_after_one_recheck(
        node_failed_client, caplog):
    client = node_failed_client
    client.check_job_status.return_value = ({7: 'NODE_FAIL'}, MagicMock(ok=True))
    # Before the job leaves the controller squeue shows its end, then fails.
    client.run_commands_read_only.side_effect = [
        squeue_result('NODE_FAIL\n'),
        squeue_result('slurm_load_jobs error: Invalid job id specified', ok=False)]
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep') as sleep, \
            caplog.at_level('INFO', logger='biomero.slurm_client'):
        state = job.wait_for_completion(client, track_progress=False,
                                        update_task=False)
    assert state == 'NODE_FAIL'
    assert client.check_job_status.call_count == 1
    assert client.run_commands_read_only.call_count == 2
    sleep.assert_called_once_with(REQUEUE_RECHECK_SECONDS)
    assert 'job 7 ended in NODE_FAIL and was not requeued' in caplog.text


def test_a_requeue_seen_on_the_recheck_keeps_waiting(node_failed_client):
    client = node_failed_client
    client.check_job_status.side_effect = [
        ({7: 'NODE_FAIL'}, MagicMock(ok=True)),
        ({7: 'COMPLETED'}, MagicMock(ok=True))]
    client.run_commands_read_only.side_effect = [
        squeue_result(''), squeue_result('REQUEUED\n')]
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep'):
        assert job.wait_for_completion(client, track_progress=False,
                                       update_task=False) == 'COMPLETED'


def test_a_requeued_job_can_still_be_cancelled(node_failed_client):
    """Cancelling targets the same job ID; its CANCELLED state is final."""
    client = node_failed_client
    client.check_job_status.side_effect = [
        ({7: 'NODE_FAIL'}, MagicMock(ok=True)),
        ({7: 'CANCELLED by 1000'}, MagicMock(ok=True))]
    client.run_commands_read_only.return_value = squeue_result('PENDING\n')
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep'):
        assert job.wait_for_completion(client, track_progress=False,
                                       update_task=False) == 'CANCELLED by 1000'
    client.run_commands_read_only.assert_called_once()


@pytest.mark.parametrize('state', ['FAILED', 'TIMEOUT', 'OUT_OF_MEMORY',
                                   'CANCELLED+', 'COMPLETED'])
def test_other_final_states_do_not_ask_for_a_requeue(node_failed_client, state):
    client = node_failed_client
    client.check_job_status.return_value = ({7: state}, MagicMock(ok=True))
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep') as sleep:
        assert job.wait_for_completion(client, track_progress=False,
                                       update_task=False) == state
    client.run_commands_read_only.assert_not_called()
    sleep.assert_not_called()


def test_a_stale_node_fail_on_every_poll_keeps_waiting(node_failed_client):
    """sacct may report the ended attempt for several polls."""
    client = node_failed_client
    client.check_job_status.side_effect = [
        ({7: state}, MagicMock(ok=True))
        for state in ('NODE_FAIL', 'NODE_FAIL', 'COMPLETED')]
    client.run_commands_read_only.return_value = squeue_result('RUNNING\n')
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep'):
        assert job.wait_for_completion(client, track_progress=False,
                                       update_task=False) == 'COMPLETED'
    assert client.run_commands_read_only.call_count == 2


def test_an_unreachable_controller_does_not_end_a_node_failed_job(
        node_failed_client):
    """A squeue error other than an unknown job says nothing about it."""
    client = node_failed_client
    client.check_job_status.return_value = ({7: 'NODE_FAIL'}, MagicMock(ok=True))
    client.run_commands_read_only.return_value = squeue_result(
        'slurm_load_jobs error: Unable to contact slurm controller', ok=False)
    job = SlurmJob.from_job_id(7)
    with patch('biomero.slurm_client.timesleep.sleep'), \
            pytest.raises(SSHException, match='Unable to contact'):
        job.wait_for_completion(client, track_progress=False,
                                update_task=False)
    assert job.job_state != 'NODE_FAIL'
