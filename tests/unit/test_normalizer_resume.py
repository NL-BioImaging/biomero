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
                           result_message='complete' if terminal else None,
                           job_ids=list(jobs), params={'image': 'helper:0.1.0'})
    workflow = SimpleNamespace(tasks=[task_id])
    tracker = MagicMock()
    tracker.repository.get.side_effect = lambda key: workflow if key == workflow_id else task
    client = SimpleNamespace(remote_shallow_zarr=True, track_workflows=True,
                             workflowTracker=tracker, result_normalizer_image='helper:0.1.0',
                             result_normalizer_version='0.1.0', result_normalizer_workers=1,
                             result_normalizer_partition=None, slurm_global_job_params=[],
                             get_normalizer_job_params=lambda: ['--cpus-per-task=1'],
                             slurm_converters_path='/sifs', put=MagicMock(),
                             run_commands=MagicMock(side_effect=lambda commands:
                                 SimpleNamespace(ok=True, stdout='' if commands[0].startswith('if test') else '{}')))
    return client, workflow_id, canonical, batch


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
    client.workflowTracker.complete_task.assert_called_once()


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
