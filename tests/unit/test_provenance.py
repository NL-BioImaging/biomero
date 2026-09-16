"""Indexed provenance is a versioned view, not the event store."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from biomero.provenance import (
    MetadataAnnotation, plan_metadata_refresh, render_workflow_metadata,
    resolve_metadata_versions,
)


@pytest.fixture
def source():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    tasks = []
    for name, params in [
        ('SLURM_Run_Workflow.py', {'_biomero_detached_launcher': '2.9.0',
                                 'unused | diameter': 10}),
        ('cisegmentation', {'diameter': 0, 'enabled': False,
                            'output_settings': {'internal': True}}),
        ('SLURM_Import_Results.py', {'import': True}),
        ('_SLURM_Result_Normalizer', {'internal': True}),
    ]:
        tasks.append(SimpleNamespace(
            _id=uuid4(), version=3, INITIAL_VERSION=0,
            task_name=name, task_version='1.0',
            _created_on=now, _modified_on=now, status='IMPORTING',
            input_data='1551', job_ids=[42] if name == 'cisegmentation' else [],
            params=params, result_message='ok',
            results=[{'command': 'run', 'env': {'VALUE': '1'}}]))
    wf = SimpleNamespace(_id=uuid4(), version=5, INITIAL_VERSION=0,
                         name='Slurm Workflow',
                         description='BIOMERO 2.9.0', tasks=[t._id for t in tasks],
                         _created_on=now, _modified_on=now)
    aggregates = {a._id: a for a in [wf, *tasks]}
    tracker = SimpleNamespace(repository=SimpleNamespace(
        get=lambda key, **kwargs: aggregates[key]))
    return tracker, wf, tasks


def test_default_preserves_legacy_fields_and_excludes_coordination(source):
    tracker, wf, tasks = source
    before = deepcopy([wf, tasks])
    maps = render_workflow_metadata(tracker, wf._id)
    assert len(maps) == 4
    assert maps[0].values['Task_IDs'] == ', '.join(str(t._id) for t in tasks[1:3])
    assert maps[1].values['Param_diameter'] == '0'
    assert maps[1].values['Param_enabled'] == 'False'
    assert 'Param_output_settings' not in maps[1].values
    assert maps[2].values['Env_VALUE'] == '1'
    assert maps[2].values['Command'] == 'run'
    assert maps[3].namespace.endswith('/task/SLURM_Get_Results.py')
    assert maps[3].values['Name'] == 'SLURM_Import_Results.py'
    assert maps[3].values['Input_Data'] == '1551'
    assert maps[3].values['Status'] == 'IMPORTING'
    assert maps[1].values['Aggregate_Version'] == '3'
    assert maps[1].values['Metadata_View_Version'] == 'v0'
    assert [wf, tasks] == before


@pytest.mark.parametrize('view', ['v0', 'v1'])
def test_import_storage_provenance_is_target_specific_and_compact(source, view):
    tracker, wf, tasks = source
    tasks[2].storage_provenance = {
        'Plate:10': {'storage': 'shallow-zarr', 'location': 'remote',
                     'tool_version': '0.1.0', 'container': 'helper@sha256:abc',
                     'manifest': 'import-mount-data/results/.biomero-shallow.json',
                     'source_biocodes': ['ISCC:AAA', 'ISCC:BBB']},
        'Plate:11': {'storage': 'full-zarr', 'location': 'importer'},
    }
    rows = render_workflow_metadata(tracker, wf._id, view_version=view,
                                    target_key='Plate:10')
    values = rows[3].values
    assert values['Storage_Format'] == 'shallow-zarr'
    assert values['Storage_Shallow'] == 'true'
    assert values['Shallower_Container'] == 'helper@sha256:abc'
    assert values['Canonical_Biocodes'] == 'ISCC:AAA, ISCC:BBB'
    full = render_workflow_metadata(tracker, wf._id, target_key='Plate:11')[3].values
    assert full['Storage_Shallow'] == 'false'
    assert 'Shallower_Container' not in full
    assert 'Storage_Format' not in render_workflow_metadata(tracker, wf._id)[3].values
    tasks[2].storage_provenance['Plate:10']['source_biocodes'] = ['ISCC:' + str(i) for i in range(1000)]
    compact = render_workflow_metadata(tracker, wf._id, target_key='Plate:10')[3].values
    assert len(compact['Canonical_Biocodes']) < 200
    assert compact['Canonical_Biocode_Count'] == '1000'


def test_refresh_replays_storage_for_target(source):
    tracker, wf, tasks = source
    tasks[2].storage_provenance = {'Image:1': {'storage': 'shallow-zarr', 'location': 'importer'}}
    rows = render_workflow_metadata(tracker, wf._id, target_key='Image:1')
    changes = plan_metadata_refresh(tracker, wf._id, rows, target_key='Image:1', view_version='v1')
    assert changes[3].after.values['Storage_Format'] == 'shallow-zarr'


def test_slim_is_explicit_and_preserves_batch_discovery(source):
    tracker, wf, _ = source
    maps = render_workflow_metadata(tracker, wf._id, view_version='v1')
    assert 'Command' not in maps[2].values
    assert 'Result_Message' not in maps[2].values
    assert 'Env_VALUE' not in maps[2].values
    assert maps[3].values['Input_Data'] == '1551'
    assert 'Created_On' in maps[3].values
    assert maps[1].values['Param_enabled'] == 'False'
    with pytest.raises(ValueError):
        render_workflow_metadata(tracker, wf._id, view_version='latest')


def test_legacy_task_has_exact_original_keys_without_revision_fields(source):
    tracker, wf, _ = source
    row = render_workflow_metadata(tracker, wf._id, revision_fields=False)[3]
    assert set(row.values) == {
        'Task_ID', 'Workflow_ID', 'Workflow_Name', 'Name', 'Version',
        'Created_On', 'Modified_On', 'Status', 'Input_Data', 'Job_IDs', 'Param_import'}


def test_snapshot_roundtrip_uses_recorded_versions(source):
    tracker, wf, tasks = source
    rows = render_workflow_metadata(tracker, wf._id)
    versions = resolve_metadata_versions(tracker, wf._id, rows)
    assert versions == {str(wf._id): 5, str(tasks[1]._id): 3, str(tasks[2]._id): 3}


def test_legacy_timestamp_ambiguity_is_not_guessed(source):
    tracker, wf, _ = source
    rows = render_workflow_metadata(tracker, wf._id, revision_fields=False)
    with pytest.raises(ValueError, match='ambiguous'):
        resolve_metadata_versions(tracker, wf._id, rows)


def test_refresh_is_idempotent_and_preserves_unknown_keys(source):
    tracker, wf, _ = source
    rows = render_workflow_metadata(tracker, wf._id)
    rows[1].values['User_Note'] = 'keep me'
    plan = plan_metadata_refresh(tracker, wf._id, rows, view_version='v1')
    assert plan[1].after.values['User_Note'] == 'keep me'
    assert 'Env_VALUE' not in plan[2].after.values
    assert 'Command' not in plan[2].after.values
    updated = [change.after for change in plan if change.after is not None]
    again = plan_metadata_refresh(tracker, wf._id, updated, view_version='v1')
    assert all(change.before == change.after for change in again)


def test_refresh_drops_launcher_but_not_other_namespaces(source):
    tracker, wf, tasks = source
    rows = render_workflow_metadata(tracker, wf._id)
    rows.append(MetadataAnnotation('biomero/workflow/task/SLURM_Run_Workflow.py', {
        'Workflow_ID': str(wf._id), 'Task_ID': str(tasks[0]._id),
        'Modified_On': tasks[0]._modified_on.isoformat(), 'Aggregate_Version': '3',
    }))
    other = MetadataAnnotation('biomero/canonical', {'Workflow_ID': str(wf._id)})
    rows.append(other)
    plan = plan_metadata_refresh(tracker, wf._id, rows)
    assert plan[-2].after is None
    assert plan[-1].after == other


def test_missing_task_snapshot_fails_closed(source):
    tracker, wf, _ = source
    rows = render_workflow_metadata(tracker, wf._id)
    with pytest.raises(ValueError, match='Missing historical task'):
        plan_metadata_refresh(tracker, wf._id, rows[:1])


def test_legacy_timestamp_selects_version_zero_not_latest(source):
    tracker, wf, tasks = source
    zero = deepcopy(wf)
    zero.version = 0
    latest = deepcopy(wf)
    latest.version = 1
    latest._modified_on = datetime(2026, 1, 2, tzinfo=timezone.utc)
    tracker.repository.get = lambda ident, version=None: (
        zero if version == 0 else latest)
    rows = [MetadataAnnotation('biomero/workflow', {
        'Workflow_ID': str(wf._id), 'Modified_On': zero._modified_on.isoformat()})]
    assert resolve_metadata_versions(tracker, wf._id, rows) == {str(wf._id): 0}


def test_explicit_invalid_version_rejected(source):
    tracker, wf, _ = source
    rows = render_workflow_metadata(tracker, wf._id)
    rows[0].values['Aggregate_Version'] = '999'
    with pytest.raises(ValueError, match='Inconsistent snapshot'):
        resolve_metadata_versions(tracker, wf._id, rows)


def test_batched_selection_excludes_other_workflow_parameters(source):
    tracker, wf, tasks = source
    task = tasks[1]
    task.task_name = 'SLURM_Run_Workflow_Batched.py'
    task.params = {'chosen': True, 'other': False, 'chosen_|_diameter': 0,
                   'other_|_diameter': 3, 'other_Version': 'v1',
                   'wf_params_other': {'large': 'descriptor'}}
    values = render_workflow_metadata(tracker, wf._id)[1].values
    assert values['Param_chosen_|_diameter'] == '0'
    assert 'Param_other_|_diameter' not in values
    assert 'Param_other_Version' not in values
    assert 'Param_other' not in values
    assert 'Param_wf_params_other' not in values
