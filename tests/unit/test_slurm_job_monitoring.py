from unittest.mock import MagicMock, patch

import pytest

from biomero.slurm_client import SlurmJob


def test_adopted_job_keeps_connection_alive_without_analysis_progress():
    client, conn = MagicMock(), MagicMock()
    client.check_job_status.side_effect = [
        ({7: state}, MagicMock(ok=True))
        for state in ('PENDING', 'RUNNING', 'COMPLETED')]
    job = SlurmJob.from_job_id(7, slurm_polling_interval=15)
    with patch('biomero.slurm_client.timesleep.sleep') as sleep:
        assert job.wait_for_completion(
            client, conn, track_progress=False, update_task=False,
            strict_status=True) == 'COMPLETED'
    assert job.completed()
    assert conn.keepAlive.call_count == 3
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
    assert job.wait_for_completion(client, MagicMock()) == 'FAILED'
    assert job.get_error() == 'lost'


def test_failed_keepalive_preserves_adopted_job():
    client, conn = MagicMock(), MagicMock()
    conn.keepAlive.return_value = False
    job = SlurmJob.from_job_id(7)
    with pytest.raises(RuntimeError, match='OMERO connection'):
        job.wait_for_completion(client, conn, strict_status=True)
    client.workflowTracker.update_task_status.assert_not_called()
    assert not job.completed()
