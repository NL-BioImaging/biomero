"""Metadata views are a versioned rendering of the event store, not the store."""
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from biomero.metadata_views import (
    MetadataAnnotation, format_large_field_reference, is_large_field_reference,
    plan_metadata_refresh, render_workflow_metadata, resolve_metadata_versions,
    storage_metadata_view,
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
        ('_SLURM_Remote_Shallower', {'internal': True}),
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
    tracker = SimpleNamespace(
        repository=SimpleNamespace(get=lambda key, **kwargs: aggregates[key]),
        events=SimpleNamespace(get=lambda key: events_of(aggregates[key])))
    return tracker, wf, tasks


def events_of(aggregate):
    """Fake event stream: every version of the aggregate has its timestamp."""
    return [SimpleNamespace(originator_version=version,
                            timestamp=aggregate._modified_on)
            for version in range(aggregate.INITIAL_VERSION,
                                 aggregate.version + 1)]


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


def test_import_storage_provenance_is_target_specific_and_compact(source):
    tracker, wf, tasks = source
    tasks[2].storage_provenance = {
        'Plate:10': {'storage': 'shallow-zarr', 'location': 'remote',
                     'tool_version': '0.1.0', 'container': 'helper@sha256:abc',
                     'manifest': 'import-mount-data/results/.biomero-shallow.json',
                     'source_biocodes': ['ISCC:AAA', 'ISCC:BBB']},
        'Plate:11': {'storage': 'full-zarr', 'location': 'importer'},
    }
    rows = render_workflow_metadata(tracker, wf._id, view_version='v0',
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
    changes = plan_metadata_refresh(tracker, wf._id, rows, target_key='Image:1', view_version='v0')
    assert changes[3].after.values['Storage_Format'] == 'shallow-zarr'


@pytest.fixture
def backfilled_source(source):
    tracker, wf, tasks = source
    tasks[2].job_ids = [15]
    rows = render_workflow_metadata(tracker, wf._id, target_key='Plate:10')
    historical = {item._id: deepcopy(item) for item in [wf, *tasks]}
    latest = deepcopy(historical)
    for item in latest.values():
        item.version += 1
        item._modified_on = datetime(2026, 1, 2, tzinfo=timezone.utc)
        item.status = 'DONE'
    latest[tasks[2]._id].storage_provenance = {
        'Plate:10': {'storage': 'shallow-zarr', 'location': 'remote',
                     'source_biocodes': ['ISCC:FIELD1', 'ISCC:FIELD0']}}

    def get(ident, version=None):
        if version is None or version == latest[ident].version:
            return latest[ident]
        if version == historical[ident].version:
            return historical[ident]
        raise ValueError(f'Unknown version: {ident}@{version}')

    tracker.repository.get = get
    return tracker, wf, tasks, rows, historical, latest


def test_default_refresh_does_not_pick_up_later_storage_evidence(backfilled_source):
    tracker, wf, _, rows, _, _ = backfilled_source
    changes = plan_metadata_refresh(tracker, wf._id, rows, target_key='Plate:10')
    assert all(change.before == change.after for change in changes)
    assert 'Storage_Format' not in changes[3].after.values


def test_explicit_storage_repair_advances_only_import_snapshot(backfilled_source):
    tracker, wf, tasks, rows, historical, latest = backfilled_source
    rows[3].values['User_Note'] = 'keep'
    changes = plan_metadata_refresh(
        tracker, wf._id, rows, target_key='Plate:10', refresh_storage=True)
    for change in changes[:3]:
        assert change.after == change.before  # Workflow and analysis retain their snapshots.
    values = changes[3].after.values
    assert values['Aggregate_Version'] == str(latest[tasks[2]._id].version)
    assert values['Modified_On'] == latest[tasks[2]._id]._modified_on.isoformat()
    assert values['Storage_Format'] == 'shallow-zarr'
    assert values['Canonical_Biocodes'] == 'ISCC:FIELD0, ISCC:FIELD1'
    assert values['Status'] == 'DONE'
    assert values['User_Note'] == 'keep'
    assert changes[4].after.values['Aggregate_Version'] == values['Aggregate_Version']
    assert rows[3].values['Status'] == 'IMPORTING'  # Caller and history remain untouched.
    assert not hasattr(historical[tasks[2]._id], 'storage_provenance')
    updated = [change.after for change in changes]
    again = plan_metadata_refresh(
        tracker, wf._id, updated, target_key='Plate:10', refresh_storage=True)
    assert all(change.before == change.after for change in again)


@pytest.mark.parametrize('target_key', [None, 'Plate:11'])
def test_explicit_storage_repair_requires_target_evidence(backfilled_source, target_key):
    tracker, wf, _, rows, _, _ = backfilled_source
    with pytest.raises(ValueError, match='storage|target'):
        plan_metadata_refresh(
            tracker, wf._id, rows, target_key=target_key, refresh_storage=True)


def test_storage_repair_refuses_to_replace_different_historical_evidence(backfilled_source):
    tracker, wf, tasks, rows, historical, _ = backfilled_source
    historical[tasks[2]._id].storage_provenance = {'Plate:10': {'storage': 'full-zarr'}}
    with pytest.raises(ValueError, match='different storage evidence'):
        plan_metadata_refresh(
            tracker, wf._id, rows, target_key='Plate:10', refresh_storage=True)


def test_storage_repair_does_not_advance_a_snapshot_that_already_has_the_evidence(backfilled_source):
    tracker, wf, tasks, rows, historical, latest = backfilled_source
    historical[tasks[2]._id].storage_provenance = deepcopy(
        latest[tasks[2]._id].storage_provenance)
    rows = render_workflow_metadata(
        tracker, wf._id, target_key='Plate:10',
        aggregate_versions={str(key): item.version for key, item in historical.items()})
    changes = plan_metadata_refresh(
        tracker, wf._id, rows, target_key='Plate:10', refresh_storage=True)
    assert all(change.before == change.after for change in changes)


@pytest.mark.parametrize('view', ['v1', 'latest'])
def test_unsupported_view_is_rejected_by_renderer_and_refresh(source, view):
    tracker, wf, _ = source
    rows = render_workflow_metadata(tracker, wf._id)
    with pytest.raises(ValueError, match='Unknown metadata view'):
        render_workflow_metadata(tracker, wf._id, view_version=view)
    with pytest.raises(ValueError, match='Unknown metadata view'):
        plan_metadata_refresh(tracker, wf._id, rows, view_version=view)


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
    plan = plan_metadata_refresh(tracker, wf._id, rows, view_version='v0')
    assert plan[1].after.values['User_Note'] == 'keep me'
    assert plan[2].after.values['Env_VALUE'] == '1'
    assert plan[2].after.values['Command'] == 'run'
    assert plan[2].after.values['Result_Message'] == 'ok'
    updated = [change.after for change in plan if change.after is not None]
    again = plan_metadata_refresh(tracker, wf._id, updated, view_version='v0')
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
    tracker.events.get = lambda ident: [
        SimpleNamespace(originator_version=0, timestamp=zero._modified_on),
        SimpleNamespace(originator_version=1, timestamp=latest._modified_on)]
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


# ------------------------------------------------------------------ #
# Contract with the scripts: how a reduced (large) field looks
# ------------------------------------------------------------------ #

# Written by SLURM_Get_Results.py / SLURM_Import_Results.py when OMERO rejects
# a key/value pair as too large for its index. Do not change this text without
# changing the scripts: refresh would then re-inflate such fields.
SCRIPT_REDUCED_VALUE = (
    'Large field; full value in metadata_8a1f.csv '
    '(utf8_bytes=2048; sha256=' + 'ab' * 32 + ')')


def test_reduced_value_text_written_by_scripts_is_recognised():
    assert is_large_field_reference(SCRIPT_REDUCED_VALUE)
    assert format_large_field_reference(
        'metadata_8a1f.csv', 2048, 'ab' * 32) == SCRIPT_REDUCED_VALUE


@pytest.mark.parametrize('value', [
    'metadata_1.csv sha256', 'see metadata_1.csv (sha256=ab)', '',
    'Large field; full value in other.csv (utf8_bytes=1; sha256=ab)',
    SCRIPT_REDUCED_VALUE + ' and more'])
def test_other_values_are_not_reduced_references(value):
    assert not is_large_field_reference(value)


def test_refresh_keeps_reduced_values_but_reinflates_look_alikes(source):
    tracker, wf, tasks = source
    rows = render_workflow_metadata(tracker, wf._id)
    rows[1].values['Param_diameter'] = SCRIPT_REDUCED_VALUE
    rows[1].values['Status'] = 'metadata_1.csv sha256'
    plan = plan_metadata_refresh(tracker, wf._id, rows)
    assert plan[1].after.values['Param_diameter'] == SCRIPT_REDUCED_VALUE
    assert plan[1].after.values['Status'] == 'IMPORTING'


# ------------------------------------------------------------------ #
# Contract with the scripts: namespaces, keys and limits
# ------------------------------------------------------------------ #

def test_namespaces_the_scripts_discover_results_by(source):
    tracker, wf, tasks = source
    namespaces = [row.namespace
                  for row in render_workflow_metadata(tracker, wf._id)]
    assert namespaces == [
        'biomero/workflow',
        'biomero/workflow/task/cisegmentation',
        'biomero/workflow/task/cisegmentation/job',
        'biomero/workflow/task/SLURM_Get_Results.py']


def test_workflow_row_keys(source):
    tracker, wf, _ = source
    values = render_workflow_metadata(tracker, wf._id)[0].values
    assert set(values) == {
        'Workflow_ID', 'Name', 'Version', 'Created_On', 'Modified_On',
        'Task_IDs', 'Metadata_View_Version', 'Aggregate_Version'}
    assert values['Version'] == '2.9.0'


def test_software_version_falls_back_to_unknown_and_logs(source, caplog):
    tracker, wf, _ = source
    wf.description = 'no number here'
    with caplog.at_level('DEBUG', logger='biomero.metadata_views'):
        values = render_workflow_metadata(tracker, wf._id)[0].values
    assert values['Version'] == 'Unknown'
    assert 'No software version in description' in caplog.text


@pytest.mark.parametrize('biocode_bytes, inline', [(800, True), (801, False)])
def test_canonical_biocodes_inline_limit_is_800_utf8_bytes(
        biocode_bytes, inline):
    code = 'x' * biocode_bytes
    values = storage_metadata_view(
        {'storage': 'shallow-zarr', 'source_biocodes': [code]}, 'Plate:1')
    assert (values['Canonical_Biocodes'] == code) is inline
    if not inline:
        assert values['Canonical_Biocodes'] == '1 codes; see Shallow_Manifest'


def test_storage_view_ignores_unknown_storage_formats():
    assert storage_metadata_view({'storage': 'tiff'}, 'Plate:1') == {}


def test_job_rows_repeat_the_first_result_of_their_task(source):
    """Legacy v0: all jobs of a task share its command, env and message."""
    tracker, wf, tasks = source
    tasks[1].job_ids = [42, 43]
    jobs = [row.values for row in render_workflow_metadata(tracker, wf._id)
            if row.namespace.endswith('/job')]
    assert [job['Job_ID'] for job in jobs] == ['42', '43']
    assert {job['Command'] for job in jobs} == {'run'}
    assert {job['Env_VALUE'] for job in jobs} == {'1'}


def test_workflow_qualified_parameters_with_spaced_separator(source):
    tracker, wf, tasks = source
    tasks[1].task_name = 'SLURM_Run_Workflow.py'
    tasks[1].params = {'chosen': True, 'other': False,
                       'chosen | diameter': 0, 'other | diameter': 3}
    values = render_workflow_metadata(tracker, wf._id)[1].values
    assert values['Param_chosen | diameter'] == '0'
    assert 'Param_other | diameter' not in values


# ------------------------------------------------------------------ #
# Refresh planning
# ------------------------------------------------------------------ #

def test_refresh_keeps_user_added_param_keys_and_drops_omitted_ones(source):
    tracker, wf, tasks = source
    # With a known selection the view omits workflow descriptor parameters.
    tasks[1].params = {'workflows': ['a'], 'wf_params_x': 'descriptor'}
    rows = render_workflow_metadata(tracker, wf._id)
    rows[0].values['Param_note'] = 'user key on the workflow row'
    rows[1].values['Param_my_note'] = 'user key'
    rows[1].values['Param_output_settings'] = 'duplicated'
    rows[1].values['Param_wf_params_x'] = 'descriptor'
    plan = plan_metadata_refresh(tracker, wf._id, rows)
    assert plan[0].after.values['Param_note'] == 'user key on the workflow row'
    assert plan[1].after.values['Param_my_note'] == 'user key'
    assert 'Param_output_settings' not in plan[1].after.values
    assert 'Param_wf_params_x' not in plan[1].after.values


def test_missing_annotation_error_names_workflow_and_identity(source):
    tracker, wf, _ = source
    rows = render_workflow_metadata(tracker, wf._id)
    missing_job = rows.pop(2)
    with pytest.raises(ValueError, match=str(wf._id)) as error:
        plan_metadata_refresh(tracker, wf._id, rows)
    assert missing_job.namespace in str(error.value)


def test_unrecognised_annotation_identity_fails_closed(source):
    tracker, wf, tasks = source
    rows = render_workflow_metadata(tracker, wf._id)
    # A job row of a visible task that the view does not render any more.
    rows.append(MetadataAnnotation(
        'biomero/workflow/task/cisegmentation/job',
        {'Workflow_ID': str(wf._id), 'Task_ID': str(tasks[1]._id),
         'Job_ID': '99'}))
    with pytest.raises(ValueError, match='Unrecognized annotation identity'):
        plan_metadata_refresh(tracker, wf._id, rows)


def test_duplicate_identities_fail_closed(source):
    tracker, wf, _ = source
    rows = render_workflow_metadata(tracker, wf._id)
    with pytest.raises(ValueError, match='Duplicate annotation identity'):
        plan_metadata_refresh(tracker, wf._id, rows + [rows[1]])


def test_unlinking_an_internal_task_row_is_logged(source, caplog):
    tracker, wf, tasks = source
    rows = render_workflow_metadata(tracker, wf._id)
    rows.append(MetadataAnnotation(
        'biomero/workflow/task/SLURM_Run_Workflow.py', {
            'Workflow_ID': str(wf._id), 'Task_ID': str(tasks[0]._id),
            'Modified_On': tasks[0]._modified_on.isoformat(),
            'Aggregate_Version': '3'}))
    with caplog.at_level('INFO', logger='biomero.metadata_views'):
        plan_metadata_refresh(tracker, wf._id, rows)
    assert (f'Unlinking annotation of internal task {tasks[0]._id}'
            in caplog.text)


def test_storage_repair_advance_is_logged(backfilled_source, caplog):
    tracker, wf, tasks, rows, _, latest = backfilled_source
    with caplog.at_level('INFO', logger='biomero.metadata_views'):
        plan_metadata_refresh(tracker, wf._id, rows, target_key='Plate:10',
                              refresh_storage=True)
    assert (f'Advancing import task {tasks[2]._id} to version '
            f'{latest[tasks[2]._id].version}') in caplog.text


# ------------------------------------------------------------------ #
# Against the real event store
# ------------------------------------------------------------------ #

@pytest.fixture
def real_tracker(monkeypatch):
    from biomero.database import EngineManager
    from biomero.eventsourcing import WorkflowTracker
    monkeypatch.setenv('PERSISTENCE_MODULE', 'eventsourcing_sqlalchemy')
    monkeypatch.setenv('SQLALCHEMY_URL', 'sqlite:///:memory:')
    EngineManager.create_scoped_session()
    yield WorkflowTracker()
    EngineManager.close_engine()


def test_legacy_rows_resolve_to_the_version_with_their_timestamp(real_tracker):
    tracker = real_tracker
    workflow_id = tracker.initiate_workflow('wf', 'BIOMERO 2.9.0', 1, 1)
    task_id = tracker.add_task_to_workflow(
        workflow_id, 'cisegmentation', '1.0', {'diameter': 5}, {})
    legacy_rows = render_workflow_metadata(
        tracker, workflow_id, revision_fields=False)
    tracker.update_task_status(task_id, 'DONE')
    tracker.update_task_status(task_id, 'RETRIEVING')
    task_version = tracker.repository.get(task_id).version
    versions = resolve_metadata_versions(tracker, workflow_id, legacy_rows)
    assert versions[str(task_id)] < task_version
    pinned = render_workflow_metadata(
        tracker, workflow_id, aggregate_versions=versions)
    assert pinned[1].values['Status'] == legacy_rows[1].values['Status']
    changes = plan_metadata_refresh(tracker, workflow_id, legacy_rows)
    assert changes[1].after.values['Status'] == legacy_rows[1].values['Status']


def test_deprecated_provenance_module_still_exports_the_api():
    import importlib
    import sys
    sys.modules.pop('biomero.provenance', None)
    with pytest.warns(DeprecationWarning, match='biomero.metadata_views'):
        provenance = importlib.import_module('biomero.provenance')
    from biomero import metadata_views
    assert (provenance.plan_metadata_refresh
            is metadata_views.plan_metadata_refresh)
    assert provenance.NAMESPACE == metadata_views.NAMESPACE
