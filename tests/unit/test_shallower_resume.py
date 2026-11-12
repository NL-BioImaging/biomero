import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest

from biomero.slurm_client import SlurmClient
from biomero_schema.zarr import CanonicalInputManifest
from biomero.constants import REMOTE_SHALLOWER_TASK_NAME as TASK_NAME
from biomero.remote_shallower import (
    POLLING_INTERVAL_SECONDS, shallow_workflow_results as run,
    completed_receipts)


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
    installed_labels = json.dumps({'data': {'attributes': {'labels': {
        'org.opencontainers.image.version': task.task_version,
        'org.biomeroproject.shallower.capability-schema': '1',
        'org.biomeroproject.shallower.runtime-contracts': '1',
        'org.biomeroproject.shallower.manifest-schemas': '2',
    }}}})
    client = SimpleNamespace(remote_shallow_zarr=True, track_workflows=True,
                             workflowTracker=tracker, remote_shallower_image='helper:0.1.0',
                             remote_shallower_version='0.1.0', remote_shallower_workers=1,
                             remote_shallower_partition=None, slurm_global_job_params=[],
                             get_shallower_job_params=lambda: ['--cpus-per-task=1'],
                             slurm_converters_path='/sifs', put=MagicMock(),
                             _partition_existing_images=MagicMock(return_value=([1], [])),
                             run_commands=MagicMock(side_effect=lambda commands, **kwargs:
                                 SimpleNamespace(ok=True, stdout=(
                                     json.dumps(canonical.to_dict())
                                     if 'canonical.json' in commands[0] else
                                     installed_labels
                                     if 'inspect --json --labels' in commands[0] else
                                     '' if commands[0].startswith('if test') else '{}'))))
    # Delegate the generic SlurmClient helpers to their real implementations.
    client.get_task = tracker.repository.get
    client.get_workflow = lambda workflow: tracker.repository.get(
        UUID(str(workflow)))
    client.get_workflow_tasks = lambda *args, **kwargs: (
        SlurmClient.get_workflow_tasks(client, *args, **kwargs))
    client.build_sbatch_wrap_command = lambda *args, **kwargs: (
        SlurmClient.build_sbatch_wrap_command(client, *args, **kwargs))
    client.remove_submission_receipts = MagicMock()
    return client, workflow_id, canonical, batch


def test_resume_verifies_manifest_without_uploading_again():
    client, workflow_id, canonical, batch = client_fixture()
    original_run = client.run_commands.side_effect
    client.run_commands.side_effect = lambda commands, **kwargs: (
        SimpleNamespace(ok=True, stdout=json.dumps(canonical.to_dict()))
        if commands[0].startswith('if test -f') and 'canonical.json' in commands[0]
        else original_run(commands))
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch), \
         patch('biomero.remote_shallower.wait_for_helper_job', return_value='COMPLETED'):
        run(client, '/data', workflow_id, canonical)
    client.put.assert_not_called()


def test_resume_rejects_changed_manifest_before_polling():
    client, workflow_id, canonical, _ = client_fixture()
    original_run = client.run_commands.side_effect
    client.run_commands.side_effect = lambda commands, **kwargs: (
        SimpleNamespace(ok=True, stdout='{"inputs": [2]}')
        if 'canonical.json' in commands[0]
        else original_run(commands)
    )
    with patch('biomero.remote_shallower.wait_for_helper_job') as wait:
        with pytest.raises(ValueError, match='manifest'):
            run(client, '/data', workflow_id, canonical)
    wait.assert_not_called()
    client.put.assert_not_called()


def test_resume_recovery_uses_recorded_image_after_configuration_change():
    client, workflow_id, canonical, batch = client_fixture()
    client.remote_shallower_image = 'new-helper:2.0.0'
    client.remote_shallower_version = '2.0.0'
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job', return_value=124) as submit, \
         patch('biomero.remote_shallower.wait_for_helper_job', side_effect=['FAILED', 'COMPLETED']):
        run(client, '/data', workflow_id, canonical)
    command = submit.call_args.args[1]
    assert '--image helper:0.1.0' in command
    assert 'new-helper' not in command
    assert client.remote_shallower_image == 'new-helper:2.0.0'


def test_resume_adopts_existing_job_without_submission():
    client, workflow_id, canonical, batch = client_fixture()
    with patch('biomero.remote_shallower._submit_helper_job', side_effect=AssertionError('duplicate')), \
         patch('biomero.remote_shallower.wait_for_helper_job', return_value='COMPLETED') as wait, \
         patch('biomero.remote_shallower.parse_batch_report', return_value=batch):
        assert run(client, '/data', workflow_id, canonical) is batch
    wait.assert_called_once_with(
        client, 123, None, POLLING_INTERVAL_SECONDS)
    client.workflowTracker.complete_task.assert_called_once()


def test_terminal_task_skips_all_remote_work():
    client, workflow_id, canonical, batch = client_fixture(terminal=True)
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch):
        assert run(client, '/data', workflow_id, canonical) is batch
    client.run_commands.assert_not_called()
    client.put.assert_not_called()
    client._partition_existing_images.assert_not_called()


def test_submission_id_is_persisted_before_polling():
    client, workflow_id, canonical, batch = client_fixture(jobs=())
    def wait(_client, job, heartbeat=None, polling_interval=None):
        client.workflowTracker.add_job_id.assert_called_once()
        assert job == 123
        return 'COMPLETED'
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job', return_value=123), \
         patch('biomero.remote_shallower.wait_for_helper_job', side_effect=wait):
        run(client, '/data', workflow_id, canonical)


def test_failed_helper_runs_recovery_before_receipt_publication():
    client, workflow_id, canonical, batch = client_fixture()
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job', return_value=124) as submit, \
         patch('biomero.remote_shallower.wait_for_helper_job', side_effect=['FAILED', 'COMPLETED']):
        run(client, '/data', workflow_id, canonical)
    assert '--recover-only' in submit.call_args.args[1]
    assert submit.call_args.args[3].startswith('biomero-recovery-')
    client.workflowTracker.complete_task.assert_called_once()


def test_recorded_recovery_id_is_adopted_without_submission():
    client, workflow_id, canonical, batch = client_fixture(jobs=(123, 124))
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job') as submit, \
         patch('biomero.remote_shallower.wait_for_helper_job',
               side_effect=['FAILED', 'COMPLETED']) as wait:
        run(client, '/data', workflow_id, canonical)
    submit.assert_not_called()
    assert [call.args[1] for call in wait.call_args_list] == [123, 124]


def test_shared_monitor_keeps_connection_through_shallowing_and_recovery():
    client, workflow_id, canonical, batch = client_fixture()
    conn = MagicMock()
    client.check_job_status = MagicMock(side_effect=[
        ({123: 'RUNNING'}, SimpleNamespace(ok=True)),
        ({123: 'OUT_OF_MEMORY'}, SimpleNamespace(ok=True)),
        ({124: 'RUNNING'}, SimpleNamespace(ok=True)),
        ({124: 'COMPLETED+'}, SimpleNamespace(ok=True)),
    ])
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job', return_value=124), \
         patch('biomero.slurm_client.timesleep.sleep') as sleep:
        assert run(client, '/data', workflow_id, canonical, conn.keepAlive) is batch
    assert conn.keepAlive.call_count == 4
    assert sleep.call_count == 2
    client.workflowTracker.update_task_status.assert_not_called()
    client.workflowTracker.complete_task.assert_called_once()


def test_unknown_job_status_preserves_job_without_recovery_or_completion():
    client, workflow_id, canonical, _ = client_fixture()
    client.check_job_status = MagicMock(return_value=({}, SimpleNamespace(ok=True)))
    with patch('biomero.remote_shallower._submit_helper_job') as submit:
        with pytest.raises(RuntimeError, match='unavailable'):
            run(client, '/data', workflow_id, canonical, MagicMock())
    submit.assert_not_called()
    client.workflowTracker.complete_task.assert_not_called()


def test_runtime_uses_ready_image_without_pulling():
    client, workflow_id, canonical, batch = client_fixture(jobs=())
    workflow = client.workflowTracker.repository.get(workflow_id)
    workflow.tasks = []
    client._submit_image_pull_array = MagicMock(return_value=9)
    client._partition_existing_images = MagicMock(return_value=([1], []))
    client.check_job_status = MagicMock(side_effect=[
        ({123: 'COMPLETED'}, SimpleNamespace(ok=True)),
    ])
    conn = MagicMock()
    with patch('biomero.remote_shallower.parse_batch_report', return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job', return_value=123), \
         patch('biomero.slurm_client.timesleep.sleep'):
        assert run(client, '/data', workflow_id, canonical, conn.keepAlive) is batch
    assert conn.keepAlive.call_count == 1
    client._submit_image_pull_array.assert_not_called()


def test_missing_image_requires_initialization_without_pulling():
    client, workflow_id, canonical, _ = client_fixture(jobs=())
    client.workflowTracker.repository.get(workflow_id).tasks = []
    client._submit_image_pull_array = MagicMock(return_value=9)
    client._partition_existing_images = MagicMock(return_value=([], [1]))
    with pytest.raises(RuntimeError, match='SLURM_Init_environment'):
        run(client, '/data', workflow_id, canonical)
    client._submit_image_pull_array.assert_not_called()
    client.workflowTracker.add_task_to_workflow.assert_not_called()


def test_incompatible_installed_tool_stops_before_task_or_submission():
    client, workflow_id, canonical, _ = client_fixture(jobs=())
    client.workflowTracker.repository.get(workflow_id).tasks = []
    incompatible_labels = {
        'org.opencontainers.image.version': '0.1.0b3',
        'org.biomeroproject.shallower.capability-schema': '1',
        'org.biomeroproject.shallower.runtime-contracts': '1',
        'org.biomeroproject.shallower.manifest-schemas': '2',
    }

    with patch(
        'biomero.remote_shallower._installed_labels',
        return_value=incompatible_labels,
    ), patch('biomero.remote_shallower._submit_helper_job') as submit:
        with pytest.raises(RuntimeError, match='does not match configured'):
            run(client, '/data', workflow_id, canonical)

    submit.assert_not_called()
    client.workflowTracker.add_task_to_workflow.assert_not_called()
    client.workflowTracker.start_task.assert_not_called()
    client.put.assert_not_called()


def test_new_task_records_discovered_receipt_version_before_submission():
    client, workflow_id, canonical, _ = client_fixture(jobs=())
    client.workflowTracker.repository.get(workflow_id).tasks = []
    client.remote_shallower_version = None
    with patch('biomero.remote_shallower.validate_installed_tool', return_value='9.2.1'), \
         patch('biomero.remote_shallower.publish_or_verify_manifest'), \
         patch('biomero.remote_shallower._submit_helper_job', side_effect=RuntimeError('stop before submit')):
        with pytest.raises(RuntimeError, match='stop before submit'):
            run(client, '/data', workflow_id, canonical)
    assert client.workflowTracker.add_task_to_workflow.call_args.args[2] == '9.2.1'


def test_missing_discovered_version_does_not_create_task():
    client, workflow_id, canonical, _ = client_fixture(jobs=())
    client.workflowTracker.repository.get(workflow_id).tasks = []
    client.remote_shallower_version = None
    with patch('biomero.remote_shallower.validate_installed_tool', side_effect=ValueError('missing version')), \
         pytest.raises(ValueError, match='missing version'):
        run(client, '/data', workflow_id, canonical)
    client.workflowTracker.add_task_to_workflow.assert_not_called()


def test_image_validation_errors_propagate_unchanged():
    client, workflow_id, canonical, _ = client_fixture(jobs=())
    client.workflowTracker.repository.get(workflow_id).tasks = []
    failure = RuntimeError('SSH connection lost')
    client._partition_existing_images = MagicMock(side_effect=failure)
    client._submit_image_pull_array = MagicMock()
    with pytest.raises(RuntimeError) as error:
        run(client, '/data', workflow_id, canonical)
    assert error.value is failure
    client._submit_image_pull_array.assert_not_called()


@pytest.mark.parametrize('during_recovery', [False, True])
def test_heartbeat_failure_preserves_shallowing_or_recovery_job(during_recovery):
    client, workflow_id, canonical, _ = client_fixture()
    client.check_job_status = MagicMock(return_value=(
        {123: 'FAILED'}, SimpleNamespace(ok=True)))
    failure = RuntimeError('caller heartbeat failed')
    heartbeat = MagicMock(side_effect=([None, failure] if during_recovery else [failure]))
    with patch('biomero.remote_shallower._submit_helper_job', return_value=124) as submit:
        with pytest.raises(RuntimeError) as error:
            run(client, '/data', workflow_id, canonical, heartbeat=heartbeat)
    assert error.value is failure
    assert submit.call_count == int(during_recovery)
    client.workflowTracker.complete_task.assert_not_called()
    client.workflowTracker.fail_task.assert_not_called()


def test_disabled_shallowing_logs_why_it_is_skipped(caplog):
    client, workflow_id, canonical, _ = client_fixture()
    client.remote_shallow_zarr = False
    with caplog.at_level('DEBUG', logger='biomero.remote_shallower'):
        assert run(client, '/data', workflow_id, canonical) is None
    assert 'remote_shallow_zarr is disabled' in caplog.text


def test_untracked_workflows_warn_that_shallowing_is_skipped(caplog):
    client, workflow_id, canonical, _ = client_fixture()
    client.track_workflows = False
    with caplog.at_level('DEBUG', logger='biomero.remote_shallower'):
        assert run(client, '/data', workflow_id, canonical) is None
    assert [r.levelname for r in caplog.records
            if 'track_workflows is disabled' in r.getMessage()] == ['WARNING']


def test_non_completed_helper_job_warns_before_recovery(caplog):
    client, workflow_id, canonical, batch = client_fixture()
    with patch('biomero.remote_shallower.parse_batch_report',
               return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job', return_value=124), \
         patch('biomero.remote_shallower.wait_for_helper_job',
               side_effect=['FAILED', 'COMPLETED']), \
         caplog.at_level('DEBUG', logger='biomero.remote_shallower'):
        run(client, '/data', workflow_id, canonical)
    recovery_warnings = [
        record.levelname for record in caplog.records
        if 'ended as FAILED; running recovery' in record.getMessage()]
    assert recovery_warnings == ['WARNING']


def test_rejected_submission_completes_task_with_empty_report(caplog):
    client, workflow_id, canonical, _ = client_fixture(jobs=())
    reports = []

    class FakeReport:
        def __init__(self, **fields):
            self.fields = fields
            reports.append(self)

        def to_dict(self):
            return {'result': self.fields['result']}

    with patch('biomero.remote_shallower._submit_helper_job', return_value=None), \
         patch('biomero_schema.shallower.ShallowBatchReport', FakeReport), \
         caplog.at_level('DEBUG', logger='biomero.remote_shallower'):
        returned = run(client, '/data', workflow_id, canonical)
    assert returned is reports[0]
    assert reports[0].fields['receipts'] == ()
    assert reports[0].fields['result'] == 'complete'
    assert reports[0].fields['canonicalInputs'] is canonical
    task_id = client.get_workflow(workflow_id).tasks[0]
    client.workflowTracker.complete_task.assert_called_once_with(
        task_id, json.dumps({'result': 'complete'}))
    client.workflowTracker.add_job_id.assert_not_called()
    warnings = [record.getMessage() for record in caplog.records
                if record.levelname == 'WARNING']
    assert any('retaining full results' in message for message in warnings)


def test_empty_canonical_inputs_skip_at_debug(caplog):
    client, workflow_id, _, _ = client_fixture()
    canonical = SimpleNamespace(inputs=(), to_dict=lambda: {'inputs': []})
    with caplog.at_level('DEBUG', logger='biomero.remote_shallower'):
        assert run(client, '/data', workflow_id, canonical) is None
    skips = [(record.levelname, record.getMessage())
             for record in caplog.records if 'no canonical inputs'
             in record.getMessage()]
    assert [level for level, _ in skips] == ['DEBUG']
    client.run_commands.assert_not_called()


def test_resumed_task_and_completion_are_logged_at_info(caplog):
    client, workflow_id, canonical, batch = client_fixture(jobs=())
    with patch('biomero.remote_shallower.parse_batch_report',
               return_value=batch), \
         patch('biomero.remote_shallower._submit_helper_job', return_value=123), \
         patch('biomero.remote_shallower.wait_for_helper_job',
               return_value='COMPLETED'), \
         caplog.at_level('INFO', logger='biomero.remote_shallower'):
        run(client, '/data', workflow_id, canonical)
    info = [record.getMessage() for record in caplog.records
            if record.levelname == 'INFO']
    assert any('Resuming remote shallower task' in m for m in info)
    assert any('completed with 0 receipts' in m for m in info)


def test_rejected_existing_report_warns_and_polls_the_recorded_job(caplog):
    client, workflow_id, canonical, _ = client_fixture()
    client.run_commands = MagicMock(return_value=SimpleNamespace(
        ok=True, stdout='{"stale": true}'))
    with patch('biomero.remote_shallower.parse_batch_report',
               side_effect=ValueError('stale')), \
         caplog.at_level('DEBUG', logger='biomero.remote_shallower'):
        from biomero.remote_shallower import _adopt_report_of_finished_job
        task = client.get_task(client.get_workflow(workflow_id).tasks[0])
        assert _adopt_report_of_finished_job(
            client, task, '/data', canonical) is None
    levels = [record.levelname for record in caplog.records
              if 'rejected as partial or stale' in record.getMessage()]
    assert levels == ['WARNING']
    client.workflowTracker.complete_task.assert_not_called()
