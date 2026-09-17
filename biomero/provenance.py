"""Versioned OMERO key/value views of immutable workflow history.

This module neither writes events nor changes OMERO objects. Full CSV export is
deliberately independent of this searchable, selectively reduced view.
"""
from dataclasses import dataclass
import re
from uuid import UUID


NAMESPACE = 'biomero/workflow'


@dataclass
class MetadataAnnotation:
    namespace: str
    values: dict


def _internal(task):
    return ('_biomero_detached_launcher' in (task.params or {}) or
            task.task_name == '_SLURM_Remote_Shallower')


def _parameters(task):
    params = dict(task.params or {})
    params.pop('output_settings', None)
    # Older batched orchestration tasks can contain all configured workflows.
    # Only filter workflow-qualified keys when selection is explicitly known.
    selected = params.get('workflows')
    configured = {k.split('_|_', 1)[0] for k in params if '_|_' in k}
    configured.update(k.split(' | ', 1)[0] for k in params if ' | ' in k)
    if selected is None and task.task_name in (
            'SLURM_Run_Workflow.py', 'SLURM_Run_Workflow_Batched.py'):
        selected = [name for name in configured if params.get(name) is True]
    if isinstance(selected, (list, tuple)):
        selected = set(selected)
        params = {k: v for k, v in params.items()
                  if not k.startswith(('wf_params_', 'wf_file_params_',
                                       '_biomero_'))
                  and (' | ' not in k or k.split(' | ', 1)[0] in selected)
                  and ('_|_' not in k or k.split('_|_', 1)[0] in selected)
                  and not (k in configured and k not in selected)
                  and not (k.endswith('_Version') and k[:-8] in configured
                           and k[:-8] not in selected)}
    return params


def render_workflow_metadata(tracker, workflow_id, *, view_version='v0',
                             aggregate_versions=None, revision_fields=True,
                             target_key=None):
    """Return ordered namespace/value pairs without writing to any datastore.

    ``v0`` is the only supported view and retains legacy task/job fields,
    including job command, environment and result-message fields. It omits
    detached coordination tasks and duplicated output settings. Scientific false/zero
    values are retained. ``Version`` still means the software version.

    An explicit ``aggregate_versions`` mapping pins the workflow and every
    retained task independently. Missing versions fail instead of mixing a
    historical workflow with current task state.
    """
    if view_version != 'v0':
        raise ValueError(f'Unknown metadata view: {view_version}')

    def get(ident):
        ident = UUID(str(ident))
        if aggregate_versions is None:
            return tracker.repository.get(ident)
        version = aggregate_versions[str(ident)]
        aggregate = tracker.repository.get(ident, version=version)
        if aggregate.version != version:
            raise ValueError(f'Aggregate version not found: {ident}/{version}')
        return aggregate

    wf = get(workflow_id)
    tasks = []
    for tid in wf.tasks:
        if aggregate_versions is not None and str(tid) not in aggregate_versions:
            # Task identity/launcher marker are immutable creation data.
            task = tracker.repository.get(UUID(str(tid)))
            if _internal(task):
                continue
            raise ValueError(f'Missing historical task version: {tid}')
        task = get(tid)
        if not _internal(task):
            tasks.append(task)

    rows = []

    def append(namespace, values, aggregate):
        if revision_fields:
            values.update(Metadata_View_Version=view_version,
                          Aggregate_Version=str(aggregate.version))
        rows.append(MetadataAnnotation(namespace, values))

    match = re.search(r'\d+\.\d+\.\d+', wf.description or '')
    append(NAMESPACE, {
        'Workflow_ID': str(workflow_id), 'Name': wf.name,
        'Version': match.group() if match else 'Unknown',
        'Created_On': wf._created_on.isoformat(),
        'Modified_On': wf._modified_on.isoformat(),
        'Task_IDs': ', '.join(str(t._id) for t in tasks),
    }, wf)
    for task in tasks:
        name = ('SLURM_Get_Results.py' if
                task.task_name == 'SLURM_Import_Results.py' else task.task_name)
        namespace = f'{NAMESPACE}/task/{name}'
        values = {
            'Task_ID': str(task._id), 'Workflow_ID': str(workflow_id),
            'Workflow_Name': wf.name, 'Name': task.task_name,
            'Version': task.task_version,
            'Created_On': task._created_on.isoformat(),
            'Modified_On': task._modified_on.isoformat(),
            'Status': task.status, 'Input_Data': task.input_data,
            'Job_IDs': ', '.join(str(jid) for jid in task.job_ids),
        }
        values.update({f'Param_{k}': str(v)
                       for k, v in _parameters(task).items()})
        evidence = getattr(task, 'storage_provenance', {}).get(target_key)
        if evidence and task.task_name in ('SLURM_Import_Results.py', 'SLURM_Get_Results.py'):
            values.update(storage_metadata_view(evidence, target_key))
        append(namespace, values, task)
        for jid in task.job_ids:
            values = {'Job_ID': str(jid), 'Task_ID': str(task._id),
                      'Workflow_ID': str(workflow_id)}
            values['Result_Message'] = task.result_message
            result = task.results[0] if task.results else {}
            if 'command' in result:
                values['Command'] = result['command']
            values.update({f'Env_{k}': str(v)
                           for k, v in result.get('env', {}).items()})
            append(namespace + '/job', values, task)
    return rows


def resolve_metadata_versions(tracker, workflow_id, annotations):
    """Recover exact aggregate versions from existing KV snapshots.

    Legacy maps have no revision number: require a unique Modified_On match.
    Job maps inherit the paired task snapshot, never the current task version.
    Missing/inconsistent snapshots are errors; callers must not guess.
    """
    versions = {}
    for row in annotations:
        values = row.values
        if values.get('Workflow_ID') != str(workflow_id):
            continue
        if row.namespace != NAMESPACE and not row.namespace.startswith(
                NAMESPACE + '/task/'):
            continue
        if 'Job_ID' in values:
            continue
        ident = values.get('Task_ID', str(workflow_id))
        latest = tracker.repository.get(UUID(ident))
        timestamp = values.get('Modified_On')
        if 'Aggregate_Version' in values:
            version = int(values['Aggregate_Version'])
            aggregate = tracker.repository.get(UUID(ident), version=version)
            if (aggregate.version != version or
                    timestamp != aggregate._modified_on.isoformat()):
                raise ValueError(f'Inconsistent snapshot: {ident}')
        else:
            matches = []
            for version in range(latest.INITIAL_VERSION, latest.version + 1):
                aggregate = tracker.repository.get(UUID(ident), version=version)
                if timestamp == aggregate._modified_on.isoformat():
                    matches.append(version)
            if len(matches) != 1:
                raise ValueError(f'Missing or ambiguous snapshot: {ident}')
            version = matches[0]
        if ident in versions and versions[ident] != version:
            raise ValueError(f'Conflicting snapshots: {ident}')
        versions[ident] = version
    if str(workflow_id) not in versions:
        raise ValueError('Missing workflow snapshot')
    return versions


@dataclass
class MetadataChange:
    before: MetadataAnnotation
    after: MetadataAnnotation | None


def plan_metadata_refresh(tracker, workflow_id, annotations, *, view_version='v0',
                          target_key=None):
    """Plan a snapshot-preserving update. None means unlink, not global delete.

    Unknown namespaces and extra keys are preserved. Missing task snapshots,
    duplicate identities and changed namespaces fail closed. Large fields that
    were already replaced by CSV references remain reduced.
    """
    versions = resolve_metadata_versions(tracker, workflow_id, annotations)
    rendered = render_workflow_metadata(
        tracker, workflow_id, view_version=view_version,
        aggregate_versions=versions, target_key=target_key)

    def identity(row):
        return (row.namespace, row.values.get('Task_ID'), row.values.get('Job_ID'))

    expected = {identity(row): row for row in rendered}
    seen = set()
    changes = []
    for row in annotations:
        if (row.values.get('Workflow_ID') != str(workflow_id) or
                not (row.namespace == NAMESPACE or
                     row.namespace.startswith(NAMESPACE + '/task/'))):
            changes.append(MetadataChange(row, row))
            continue
        key = identity(row)
        if key in seen:
            raise ValueError(f'Duplicate annotation identity: {key}')
        seen.add(key)
        target = expected.get(key)
        if target is None:
            tid = row.values.get('Task_ID')
            if tid:
                task = tracker.repository.get(UUID(tid))
                if _internal(task):
                    changes.append(MetadataChange(row, None))
                    continue
            raise ValueError(f'Unrecognized annotation identity: {key}')
        values = dict(row.values)
        for name in list(values):
            excluded = name == 'Param_output_settings'
            if name.startswith('Param_') and name not in target.values:
                # Only remove keys actually belonging to this task's params.
                tid = row.values['Task_ID']
                task = tracker.repository.get(UUID(tid), version=versions[tid])
                excluded |= name[6:] in (task.params or {})
            if excluded:
                values.pop(name)
        for name, value in target.values.items():
            old = str(row.values.get(name, ''))
            if 'metadata_' in old and '.csv' in old and 'sha256' in old:
                continue
            values[name] = value
        changes.append(MetadataChange(row, MetadataAnnotation(row.namespace, values)))
    if not set(expected).issubset(seen):
        raise ValueError('Missing annotations; refusing an incomplete refresh')
    return changes


def storage_metadata_view(evidence, target_key):
    """Compact per-result storage facts; feature flags are not evidence."""
    storage = evidence.get('storage')
    if storage not in ('shallow-zarr', 'full-zarr'):
        return {}
    values = {'Storage_Target': target_key, 'Storage_Format': storage,
              'Storage_Shallow': str(storage == 'shallow-zarr').lower()}
    fields = {
        'location': 'Shallowing_Location', 'tool_version': 'Shallower_Tool_Version',
        'container': 'Shallower_Container', 'manifest': 'Shallow_Manifest',
        'manifest_sha256': 'Shallow_Manifest_SHA256',
        'report_sha256': 'Shallower_Report_SHA256',
        'task_id': 'Shallower_Task_ID', 'job_id': 'Shallower_Job_ID',
        'reason': 'Storage_Outcome_Reason',
    }
    for key, label in fields.items():
        if evidence.get(key) is not None:
            values[label] = str(evidence[key])
    codes = sorted(set(evidence.get('source_biocodes', [])))
    if codes:
        values['Canonical_Biocode_Count'] = str(len(codes))
        joined = ', '.join(codes)
        values['Canonical_Biocodes'] = (joined if len(joined.encode('utf-8')) <= 800
                                        else f'{len(codes)} codes; see Shallow_Manifest')
    return values
