"""Which task a submission belongs to, and what is recorded about it."""
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fabric import Result

from biomero.eventsourcing import NoOpWorkflowTracker
from .test_slurm_client import slurm_client, set_env_vars  # noqa: F401

NAME = 'segment'
VERSION = 'v1'
INPUTS = 'inputs'
PARAMS = {'diameter': 30}


@pytest.fixture
def tracked(slurm_client):
    """A client with a real, in-memory workflow tracker and one workflow."""
    slurm_client.slurm_data_path = '/shared/data'
    slurm_client.run_commands = MagicMock(
        return_value=Result(connection=None, exited=0))
    workflow_id = slurm_client.workflowTracker.initiate_workflow(
        'workflow', VERSION, -1, -1)
    return slurm_client, workflow_id


def new_task(client, workflow_id, params=None):
    return client._submission_task(
        workflow_id, NAME, VERSION, INPUTS, PARAMS if params is None else params)


def test_a_first_submission_creates_a_task(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    assert client.get_task(task_id).task_name == NAME
    assert client.get_task(task_id).job_ids == []


def test_a_resumed_submission_continues_its_unfinished_task(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    client.workflowTracker.add_job_id(task_id, 42)
    client.workflowTracker.update_task_status(task_id, 'RUNNING')
    assert new_task(client, workflow_id) == task_id


def test_a_task_without_a_recorded_job_is_continued_too(tracked):
    """The reply and the record were both lost: the same task resumes."""
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    assert new_task(client, workflow_id) == task_id


@pytest.mark.parametrize('finish', [
    lambda tracker, task_id: tracker.update_task_status(task_id, 'FAILED'),
    lambda tracker, task_id: tracker.update_task_status(task_id, 'CANCELLED by 1000'),
    lambda tracker, task_id: tracker.update_task_status(task_id, 'TIMEOUT'),
    lambda tracker, task_id: tracker.complete_task(task_id, 'done'),
    lambda tracker, task_id: tracker.fail_task(task_id, 'failed'),
], ids=['job-failed', 'cancelled', 'timeout', 'task-completed', 'task-failed'])
def test_a_second_run_on_the_same_input_never_adopts_a_finished_task(
        tracked, finish):
    client, workflow_id = tracked
    first = new_task(client, workflow_id)
    client.workflowTracker.add_job_id(first, 42)
    finish(client.workflowTracker, first)
    second = new_task(client, workflow_id)
    assert second != first
    assert client.get_task(second).job_ids == []
    # And its receipts and job name are its own.
    assert client.get_submission_state_prefix(second) != \
        client.get_submission_state_prefix(first)
    assert client.get_submission_job_name(second) != \
        client.get_submission_job_name(first)


def test_a_crash_after_job_completion_adopts_the_completed_job(tracked):
    """No second analysis: the job COMPLETED but its outputs were not
    processed yet (the task is not completed), so resuming adopts it."""
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    client.workflowTracker.add_job_id(task_id, 42)
    client.workflowTracker.update_task_status(task_id, 'COMPLETED')
    assert new_task(client, workflow_id) == task_id
    result = client._submit_tracked('sbatch job.sh', {}, task_id)
    assert client.extract_job_id(result) == 42
    client.run_commands.assert_not_called()


def test_once_the_outputs_are_processed_the_next_run_is_a_new_task(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    client.workflowTracker.add_job_id(task_id, 42)
    client.workflowTracker.update_task_status(task_id, 'COMPLETED')
    client.workflowTracker.complete_task(task_id, 'imported')
    assert new_task(client, workflow_id) != task_id


def test_a_run_whose_submission_failed_starts_a_new_task_when_asked_again(
        tracked):
    client, workflow_id = tracked
    first = new_task(client, workflow_id)
    client.workflowTracker.add_job_id(first, -1)
    assert new_task(client, workflow_id) != first


def test_the_unfinished_task_is_chosen_among_finished_ones(tracked):
    client, workflow_id = tracked
    finished = new_task(client, workflow_id)
    client.workflowTracker.update_task_status(finished, 'FAILED')
    current = new_task(client, workflow_id)
    assert current != finished
    assert new_task(client, workflow_id) == current


def test_changed_parameters_on_resume_name_the_task(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    with pytest.raises(RuntimeError) as changed:
        new_task(client, workflow_id, params={'diameter': 99})
    assert str(task_id) in str(changed.value)
    assert 'changed on resume' in str(changed.value)


def test_several_unfinished_tasks_are_reported_with_their_ids(tracked):
    client, workflow_id = tracked
    ids = [client.workflowTracker.add_task_to_workflow(
        workflow_id, NAME, VERSION, INPUTS, PARAMS) for _ in range(2)]
    with pytest.raises(RuntimeError) as ambiguous:
        new_task(client, workflow_id)
    assert all(str(task_id) in str(ambiguous.value) for task_id in ids)


def test_without_tracking_there_is_no_task_to_resume(slurm_client):
    slurm_client.workflowTracker = NoOpWorkflowTracker()
    assert new_task(slurm_client, uuid4()) is None


def test_a_recorded_job_is_adopted_without_asking_the_cluster(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    client.workflowTracker.add_job_id(task_id, 42)
    result = client._submit_tracked('sbatch job.sh', {}, task_id)
    assert client.extract_job_id(result) == 42
    client.run_commands.assert_not_called()


def test_conflicting_recorded_jobs_are_not_guessed(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    client.workflowTracker.add_job_id(task_id, 41)
    client.workflowTracker.add_job_id(task_id, 42)
    with pytest.raises(RuntimeError, match=str(task_id)):
        client._submit_tracked('sbatch job.sh', {}, task_id)


def test_without_a_task_the_command_runs_once_and_unprotected(slurm_client):
    slurm_client.run_commands = MagicMock(
        return_value=Result(connection=None, exited=0))
    slurm_client._submit_tracked('sbatch job.sh', {'A': '1'}, None)
    slurm_client.run_commands.assert_called_once_with(['sbatch job.sh'], {'A': '1'})


def test_the_outcome_is_recorded_once_and_the_receipts_are_removed(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    result = Result(connection=None, exited=0, stdout='Submitted batch job 42\n')
    client._record_submission(task_id, 42, result)
    client._record_submission(task_id, 42, result)
    assert client.get_task(task_id).job_ids == [42]
    # Receipts are removed on every call, also when already recorded.
    assert client.run_commands.call_count == 2
    removal = client.run_commands.call_args.args[0][0]
    assert removal.startswith('rm -f ')
    assert f'/shared/data/.biomero-recovery/{task_id}.job' in removal


def test_a_failed_submission_is_recorded_and_its_receipts_removed(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    client._record_submission(task_id, -1, Result(connection=None, exited=1))
    assert client.get_task(task_id).job_ids == [-1]
    client.run_commands.assert_called_once()


def test_recording_without_a_task_does_nothing(slurm_client):
    slurm_client.run_commands = MagicMock()
    slurm_client._record_submission(None, -1, MagicMock())
    slurm_client.run_commands.assert_not_called()


def test_job_name_and_receipt_prefix_keep_the_layout_of_2_9_betas(
        slurm_client):
    """Runs started by earlier betas use these names; do not change them."""
    slurm_client.slurm_data_path = '/shared/data'
    task_id = uuid4()
    assert slurm_client.get_submission_job_name(task_id) == f'biomero-{task_id}'
    assert slurm_client.get_submission_state_prefix(task_id) == \
        f'/shared/data/.biomero-recovery/{task_id}'


def test_get_workflow_tasks_is_empty_when_tracking_is_off(slurm_client):
    slurm_client.workflowTracker = NoOpWorkflowTracker()
    assert slurm_client.get_workflow_tasks(uuid4()) == []
    assert slurm_client.get_workflow_tasks(str(uuid4()), task_name=NAME) == []


def test_get_workflow_tasks_accepts_a_uuid_or_a_string(tracked):
    client, workflow_id = tracked
    task_id = new_task(client, workflow_id)
    by_uuid = client.get_workflow_tasks(workflow_id)
    by_string = client.get_workflow_tasks(str(workflow_id), task_name=NAME,
                                          input_data=INPUTS)
    assert [task.id for task in by_uuid] == [task_id]
    assert [task.id for task in by_string] == [task_id]
    assert client.get_workflow_tasks(workflow_id, task_name='other') == []
