import json
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from biomero import remote_shallower as shallower
from biomero_schema.zarr import CanonicalInputManifest


def test_existing_manifest_is_reused_without_upload():
    canonical = CanonicalInputManifest(workflowId=uuid4(), exportTaskId=uuid4())
    client = SimpleNamespace(put=MagicMock(), run_commands=MagicMock(
        return_value=SimpleNamespace(ok=True, stdout=json.dumps(canonical.to_dict()))))
    shallower._prepare_manifest(client, '/state/canonical.json', canonical,
                                 submitted=True)
    client.put.assert_not_called()


def test_missing_manifest_for_submitted_job_is_not_recreated():
    client = SimpleNamespace(put=MagicMock(), run_commands=MagicMock(
        return_value=SimpleNamespace(ok=True, stdout='')))
    canonical = CanonicalInputManifest(workflowId=uuid4(), exportTaskId=uuid4())
    with pytest.raises(RuntimeError, match='manifest'):
        shallower._prepare_manifest(client, '/state/canonical.json', canonical,
                                     submitted=True)
    client.put.assert_not_called()


def test_batch_uses_persisted_image_and_version_not_current_config():
    ShallowBatchReport = pytest.importorskip(
        'biomero_schema.shallower').ShallowBatchReport
    canonical = CanonicalInputManifest(workflowId=uuid4(), exportTaskId=uuid4())
    task = SimpleNamespace(params={'image': 'helper:1.0'}, task_version='1.0')
    client = SimpleNamespace(remote_shallower_image='helper:2.0',
                             remote_shallower_version='2.0')
    batch = ShallowBatchReport(schema=1, canonicalInputs=canonical,
                               image='helper:1.0', toolVersion='1.0',
                               result='complete', receipts=())
    assert shallower._batch(client, json.dumps(batch.to_dict()), canonical,
                             task=task) == batch
    with pytest.raises(ValueError):
        shallower._batch(client, json.dumps(batch.to_dict()), canonical,
                          task=SimpleNamespace(params={'image': 'helper:2.0'},
                                               task_version='2.0'))


def test_persisted_receipts_remain_readable_after_config_change():
    schema = pytest.importorskip('biomero_schema.shallower')
    canonical = CanonicalInputManifest(workflowId=uuid4(), exportTaskId=uuid4())
    task_id = uuid4()
    receipt = schema.RemoteShallowReceipt(
        schema=1, image='helper:1.0', toolVersion='1.0',
        reportSha256='a' * 64, artifactPath='result.zarr',
        slurmJobId='123', taskId=task_id)
    batch = schema.ShallowBatchReport(
        schema=1, canonicalInputs=canonical, image='helper:1.0',
        toolVersion='1.0', result='complete', receipts=(receipt,))
    task = SimpleNamespace(
        id=task_id, task_name=shallower.TASK_NAME, task_version='1.0',
        params={'image': 'helper:1.0'}, job_ids=[123],
        result_message=json.dumps(batch.to_dict()))
    tracker = MagicMock()
    tracker.repository.get.side_effect = lambda key: (
        SimpleNamespace(tasks=[task_id]) if key == canonical.workflow_id else task)
    client = SimpleNamespace(
        remote_shallow_zarr=True, track_workflows=True, workflowTracker=tracker,
        remote_shallower_image='helper:2.0', remote_shallower_version='2.0')
    assert shallower.completed_receipts(
        client, canonical.workflow_id, canonical) == (receipt,)
    task.job_ids = [999]
    with pytest.raises(ValueError, match='different helper task/job'):
        shallower.completed_receipts(client, canonical.workflow_id, canonical)


def test_recorded_manifest_hash_rejects_changed_input():
    task = SimpleNamespace(params={
        'canonical_sha256': shallower._canonical_digest({'inputs': [1]})})
    with pytest.raises(ValueError, match='manifest'):
        shallower._check_canonical(
            task, SimpleNamespace(to_dict=lambda: {'inputs': [2]}))
