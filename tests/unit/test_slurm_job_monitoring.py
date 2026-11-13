from unittest.mock import MagicMock, patch

import pytest

from biomero.slurm_client import SlurmJob


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
            '/data', 'workflow', None, omero_conn=MagicMock())


def test_public_shallower_passes_callback_unchanged():
    from biomero.slurm_client import SlurmClient
    client = SlurmClient(config_only=True)
    heartbeat = MagicMock()
    with patch('biomero.remote_shallower.shallow_workflow_results') as run:
        client.shallow_results_on_slurm('/data', 'workflow', None,
                                          heartbeat=heartbeat)
    assert run.call_args.args[4] is heartbeat


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
