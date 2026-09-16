"""Explicit, backed-up refresh of one OMERO object's workflow KV view.

OMERO is optional: importing this module does not require its Python bindings.
"""
import json
from pathlib import Path

from .provenance import MetadataAnnotation, NAMESPACE, plan_metadata_refresh


def metadata_pairs(values):
    """Match ezomero's legacy representation of lists as repeated keys."""
    return [[str(key), str(item)] for key, value in values.items()
            for item in (value if isinstance(value, list) else [value])]


def _read_values(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            # Input_Data is historically list-valued, unlike identity fields.
            if key != 'Input_Data':
                raise ValueError(f'Duplicate non-list metadata key: {key}')
            if not isinstance(values[key], list):
                values[key] = [values[key]]
            values[key].append(value)
        else:
            values[key] = value
    return values


def refresh_workflow_metadata(conn, tracker, object_type, object_id, workflow_id,
                              *, view_version='v0', dry_run=True, backup_path=None):
    """Refresh existing maps in place; unlink obsolete internal-task maps.

    Default is a dry run. Applying requires a new backup file and administrator
    access so cross-group shared links can be checked. No CSV, canonical/shallow
    annotation, image data or event is modified. Shared annotations are refused.
    Writes are not one transaction: errors propagate and the backup remains.
    Quiesce metadata writers for the target during an administrative refresh.
    """
    if object_type not in ('Image', 'Plate'):
        raise ValueError('Only Image and Plate result targets are supported')
    if not conn.isAdmin():
        raise ValueError('Metadata refresh requires an administrator')
    original_group = conn.SERVICE_OPTS.getOmeroGroup()
    conn.SERVICE_OPTS.setOmeroGroup('-1')
    try:
        target = conn.getObject(object_type, int(object_id))
        if target is None:
            raise ValueError('Result target not found')
        records = []
        rows = []
        for ann in target.listAnnotations():
            ns = ann.getNs() or ''
            if not (ns == NAMESPACE or ns.startswith(NAMESPACE + '/task/')):
                continue
            pairs = ann.getValue()
            if ['Workflow_ID', str(workflow_id)] not in [list(p) for p in pairs]:
                continue
            values = _read_values(pairs)
            rows.append(MetadataAnnotation(ns, values))
            records.append({'annotation_id': ann.getId(), 'namespace': ns,
                            'pairs': [list(p) for p in pairs]})
        plan = plan_metadata_refresh(tracker, workflow_id, rows,
                                     view_version=view_version)
        actions = []
        for record, change in zip(records, plan):
            pairs = metadata_pairs(change.after.values) if change.after else None
            action = ('unlink' if pairs is None else
                      'unchanged' if pairs == record['pairs'] else 'update')
            actions.append({**record, 'action': action, 'new_pairs': pairs})
        summary = {'object_type': object_type, 'object_id': int(object_id),
                   'workflow_id': str(workflow_id), 'view_version': view_version,
                   'dry_run': dry_run,
                   'annotations': [{'id': a['annotation_id'], 'action': a['action'],
                                    'before': len(a['pairs']),
                                    'after': len(a['new_pairs'] or [])}
                                   for a in actions]}
        if dry_run:
            return summary
        if not backup_path:
            raise ValueError('An unused backup_path is required to apply')
        # Preflight all changes before writing any map. Across-group admin
        # visibility prevents accidentally modifying another target's view.
        links = {}
        for action in actions:
            if action['action'] == 'unchanged':
                continue
            aid = action['annotation_id']
            linked = []
            for kind in ('Project', 'Dataset', 'Image', 'Screen', 'Plate',
                         'Well', 'PlateAcquisition', 'Annotation'):
                linked.extend((kind, link) for link in conn.getAnnotationLinks(
                    kind, ann_ids=[aid]))
            if (len(linked) != 1 or linked[0][0] != object_type or
                    linked[0][1].getParent().getId() != int(object_id)):
                raise ValueError(f'Shared or unexpected annotation links: {aid}')
            links[aid] = linked[0][1].getId()
            current = conn.getObject('MapAnnotation', aid)
            if ([list(p) for p in current.getValue()] != action['pairs'] or
                    current.getNs() != action['namespace']):
                raise ValueError(f'Annotation changed during planning: {aid}')
        with Path(backup_path).open('x', encoding='utf-8') as stream:
            json.dump({**summary, 'actions': actions}, stream, indent=2)
        conn.SERVICE_OPTS.setOmeroGroup(str(target.getDetails().group.id.val))
        # Update retained maps before removing links. No global annotation delete.
        for action in actions:
            if action['action'] != 'update':
                continue
            ann = conn.getObject('MapAnnotation', action['annotation_id'])
            if [list(p) for p in ann.getValue()] != action['pairs']:
                raise ValueError('Annotation changed after preflight')
            ann.setValue(action['new_pairs'])
            ann.save()
        unlink_ids = [links[a['annotation_id']] for a in actions
                      if a['action'] == 'unlink']
        if unlink_ids:
            from omero.cmd import Delete2
            from omero.cmd.graphs import ChildOption
            request = Delete2(
                targetObjects={object_type + 'AnnotationLink': unlink_ids},
                childOptions=[ChildOption(excludeType=['MapAnnotation'])])
            handle = conn.c.sf.submit(request, conn.SERVICE_OPTS)
            try:
                conn._waitOnCmd(handle)
            finally:
                handle.close()
        return summary
    finally:
        conn.SERVICE_OPTS.setOmeroGroup(original_group)
