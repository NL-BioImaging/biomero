import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from biomero_schema.zarr import CanonicalInputManifest
from biomero.result_normalizer import TASK_NAME, run, completed_receipts


def client_fixture(*, jobs=(123,), terminal=False):
    workflow_id, task_id = uuid4(), uuid4()
    # Contract tests cover nonempty inputs; orchestration only inspects inputs.
    canonical = SimpleNamespace(inputs=(1,), to_dict=lambda: {'inputs': [1]})
    batch = SimpleNamespace(receipts=(), to_dict=lambda: {'result': 'complete'})
    task = SimpleNamespace(id=task_id, task_name=TASK_NAME, input_data='/data',
                           task_version='0.1.0',
                           result_message='complete' if terminal else None,
                           job_ids=list(jobs), params={'image': 'helper:0.1.0'})
    workflow = SimpleNamespace(tasks=[task_id])
    tracker = MagicMock()
    tracker.repository.get.side_effect = lambda key: workflow if key == workflow_id else task
    tracker.add_task_to_workflow.return_value = task_id
    tracker.add_job_id.side_effect = lambda _task_id, job_id: task.job_ids.append(job_id)
    client = SimpleNamespace(remote_shallow_zarr=True, track_workflows=True,
                             workflowTracker=tracker, result_normalizer_image='helper:0.1.0',
                             result_normalizer_version='0.1.0', result_normalizer_workers=1,
                             result_normalizer_partition=None, slurm_global_job_params=[],
                             get_normalizer_job_params=lambda: ['--cpus-per-task=1'],
                             slurm_converters_path='/sifs', put=MagicMock(),
                             run_commands=MagicMock(side_effect=lambda commands, **kwargs:
                                 SimpleNamespace(ok=True, stdout=(
                                     json.dumps(canonical.to_dict())
                                     if 'canonical.json' in commands[0] else
                                     '' if commands[0].startswith('if test') else '{}'))))
    return client, workflow_id, canonical, batch


def test_resume_verifies_manifest_without_uploading_again():
    client, workflow_id, canonical, batch = client_fixture()
    original_run = client.run_commands.side_effect
    client.run_commands.side_effect = lambda commands, **kwargs: (
        SimpleNamespace(ok=True, stdout=json.dumps(canonical.to_dict()))
        if commands[0].startswith('if test -f') and 'canonical.json' in commands[0]
        else original_run(commands))
    with patch('biomero.result_normalizer._batch', return_value=batch), \
         patch('biomero.result_normalizer._wait', return_value='COMPLETED'):
        run(client, '/data', workflow_id, canonical)
    client.put.assert_not_called()


def test_resume_rejects_changed_manifest_before_polling():
    client, workflow_id, canonical, _ = client_fixture()
    client.run_commands.return_value = SimpleNamespace(ok=True, stdout='{"inputs": [2]}')
    client.run_commands.side_effect = None
    with patch('biomero.result_normalizer._wait') as wait:
        with pytest.raises(ValueError, match='manifest'):
            run(client, '/data', workflow_id, canonical)
    wait.assert_not_called()
    client.put.assert_not_called()


def test_resume_recovery_uses_recorded_image_after_configuration_change():
    client, workflow_id, canonical, batch = client_fixture()
    client.result_normalizer_image = 'new-helper:2.0.0'
    client.result_normalizer_version = '2.0.0'
    with patch('biomero.result_normalizer._batch', return_value=batch), \
         patch('biomero.result_normalizer._submit_once', return_value=124) as submit, \
         patch('biomero.result_normalizer._wait', side_effect=['FAILED', 'COMPLETED']):
        run(client, '/data', workflow_id, canonical)
    command = submit.call_args.args[1]
    assert '--image helper:0.1.0' in command
    assert 'new-helper' not in command
    assert client.result_normalizer_image == 'new-helper:2.0.0'


def test_resume_adopts_existing_job_without_submission():
    client, workflow_id, canonical, batch = client_fixture()
    with patch('biomero.result_normalizer._submit_once', side_effect=AssertionError('duplicate')), \
         patch('biomero.result_normalizer._wait', return_value='COMPLETED') as wait, \
         patch('biomero.result_normalizer._batch', return_value=batch):
        assert run(client, '/data', workflow_id, canonical) is batch
    wait.assert_called_once_with(client, 123, None)
    client.workflowTracker.complete_task.assert_called_once()


def test_terminal_task_skips_all_remote_work():
    client, workflow_id, canonical, batch = client_fixture(terminal=True)
    with patch('biomero.result_normalizer._batch', return_value=batch):
        assert run(client, '/data', workflow_id, canonical) is batch
    client.run_commands.assert_not_called()
    client.put.assert_not_called()


def test_submission_id_is_persisted_before_polling():
    client, workflow_id, canonical, batch = client_fixture(jobs=())
    def wait(_client, job, conn=None):
        client.workflowTracker.add_job_id.assert_called_once()
        assert job == 123
        return 'COMPLETED'
    with patch('biomero.result_normalizer._batch', return_value=batch), \
         patch('biomero.result_normalizer._submit_once', return_value=123), \
         patch('biomero.result_normalizer._wait', side_effect=wait):
        run(client, '/data', workflow_id, canonical)


def test_failed_helper_runs_recovery_before_receipt_publication():
    client, workflow_id, canonical, batch = client_fixture()
    with patch('biomero.result_normalizer._batch', return_value=batch), \
         patch('biomero.result_normalizer._submit_once', return_value=124) as submit, \
         patch('biomero.result_normalizer._wait', side_effect=['FAILED', 'COMPLETED']):
        run(client, '/data', workflow_id, canonical)
    assert '--recover-only' in submit.call_args.args[1]
    assert submit.call_args.kwargs['job_name'].startswith('biomero-recovery-')
    client.workflowTracker.complete_task.assert_called_once()


def test_recorded_legacy_recovery_id_is_adopted_without_submission():
    client, workflow_id, canonical, batch = client_fixture(jobs=(123, 124))
    with patch('biomero.result_normalizer._batch', return_value=batch), \
         patch('biomero.result_normalizer._submit_once') as submit, \
         patch('biomero.result_normalizer._wait',
               side_effect=['FAILED', 'COMPLETED']) as wait:
        run(client, '/data', workflow_id, canonical)
    submit.assert_not_called()
    assert [call.args[1] for call in wait.call_args_list] == [123, 124]


def test_shared_monitor_keeps_connection_through_normalization_and_recovery():
    client, workflow_id, canonical, batch = client_fixture()
    conn = MagicMock()
    client.check_job_status = MagicMock(side_effect=[
        ({123: 'RUNNING'}, SimpleNamespace(ok=True)),
        ({123: 'OUT_OF_MEMORY'}, SimpleNamespace(ok=True)),
        ({124: 'RUNNING'}, SimpleNamespace(ok=True)),
        ({124: 'COMPLETED+'}, SimpleNamespace(ok=True)),
    ])
    with patch('biomero.result_normalizer._batch', return_value=batch), \
         patch('biomero.result_normalizer._submit_once', return_value=124), \
         patch('biomero.slurm_client.timesleep.sleep') as sleep:
        assert run(client, '/data', workflow_id, canonical, conn) is batch
    assert conn.keepAlive.call_count == 4
    assert sleep.call_count == 2
    client.workflowTracker.update_task_status.assert_not_called()
    client.workflowTracker.complete_task.assert_called_once()


def test_unknown_job_status_preserves_job_without_recovery_or_completion():
    client, workflow_id, canonical, _ = client_fixture()
    client.check_job_status = MagicMock(return_value=({}, SimpleNamespace(ok=True)))
    with patch('biomero.result_normalizer._submit_once') as submit:
        with pytest.raises(RuntimeError, match='unavailable'):
            run(client, '/data', workflow_id, canonical, MagicMock())
    submit.assert_not_called()
    client.workflowTracker.complete_task.assert_not_called()


def test_image_acquisition_also_keeps_connection_alive():
    client, workflow_id, canonical, batch = client_fixture(jobs=())
    workflow = client.workflowTracker.repository.get(workflow_id)
    workflow.tasks = []
    client._submit_image_pull_array = MagicMock(return_value=9)
    client._partition_existing_images = MagicMock(return_value=([1], []))
    client.check_job_status = MagicMock(side_effect=[
        ({9: 'RUNNING'}, SimpleNamespace(ok=True)),
        ({9: 'COMPLETED'}, SimpleNamespace(ok=True)),
        ({123: 'COMPLETED'}, SimpleNamespace(ok=True)),
    ])
    conn = MagicMock()
    with patch('biomero.result_normalizer._batch', return_value=batch), \
         patch('biomero.result_normalizer._submit_once', return_value=123), \
         patch('biomero.slurm_client.timesleep.sleep'):
        assert run(client, '/data', workflow_id, canonical, conn) is batch
    assert conn.keepAlive.call_count == 3
