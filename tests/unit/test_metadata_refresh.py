from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from biomero.metadata_refresh import metadata_pairs, refresh_workflow_metadata
from biomero.provenance import MetadataAnnotation, MetadataChange


@pytest.fixture
def connection():
    conn = Mock()
    conn.isAdmin.return_value = True
    conn.SERVICE_OPTS.getOmeroGroup.return_value = '4'
    target = Mock()
    target.getDetails.return_value.group.id.val = 4
    ann = Mock()
    ann.getId.return_value = 12
    ann.getNs.return_value = 'biomero/workflow'
    ann.getValue.return_value = [['Workflow_ID', 'wf'], ['Name', 'run']]
    target.listAnnotations.return_value = [ann]
    conn.getObject.side_effect = lambda kind, ident: target if kind == 'Plate' else ann
    link = Mock()
    link.getParent.return_value.getId.return_value = 10
    link.getId.return_value = 123
    conn.getAnnotationLinks.side_effect = lambda kind, **kw: [link] if kind == 'Plate' else []
    return conn, ann


@pytest.fixture
def planner():
    before = MetadataAnnotation('biomero/workflow', {'Workflow_ID': 'wf', 'Name': 'run'})
    after = MetadataAnnotation(before.namespace, {**before.values, 'Metadata_View_Version': 'v0'})
    with patch('biomero.metadata_refresh.plan_metadata_refresh',
               return_value=[MetadataChange(before, after)]) as mock:
        yield mock


def test_default_is_read_only(connection, planner):
    conn, ann = connection
    result = refresh_workflow_metadata(conn, object(), 'Plate', 10, 'wf')
    assert result['annotations'][0]['action'] == 'update'
    ann.save.assert_not_called()
    ann.setValue.assert_not_called()
    conn.SERVICE_OPTS.setOmeroGroup.assert_called_with('4')


def test_apply_preserves_annotation_id_and_backs_up(connection, planner, tmp_path):
    conn, ann = connection
    backup = tmp_path / 'backup.json'
    refresh_workflow_metadata(conn, object(), 'Plate', 10, 'wf',
                              dry_run=False, backup_path=backup)
    assert '"annotation_id": 12' in backup.read_text()
    ann.save.assert_called_once()
    ann.setValue.assert_called_once_with([
        ['Workflow_ID', 'wf'], ['Name', 'run'], ['Metadata_View_Version', 'v0']])


def test_shared_maps_refused_before_backup_or_write(connection, planner, tmp_path):
    conn, ann = connection
    conn.getAnnotationLinks.side_effect = lambda *a, **kw: [Mock()]
    backup = tmp_path / 'backup.json'
    with pytest.raises(ValueError, match='Shared'):
        refresh_workflow_metadata(conn, object(), 'Plate', 10, 'wf',
                                  dry_run=False, backup_path=backup)
    assert not backup.exists()
    ann.save.assert_not_called()


def test_list_representation_matches_legacy_writer():
    assert metadata_pairs({'Input_Data': [2, 3], 'Status': 'DONE'}) == [
        ['Input_Data', '2'], ['Input_Data', '3'], ['Status', 'DONE']]


def test_no_overwrite_of_backup(connection, planner, tmp_path):
    conn, ann = connection
    backup = tmp_path / 'backup.json'
    backup.write_text('preserve')
    with pytest.raises(FileExistsError):
        refresh_workflow_metadata(conn, object(), 'Plate', 10, 'wf',
                                  dry_run=False, backup_path=backup)
    assert backup.read_text() == 'preserve'
    ann.save.assert_not_called()


def test_unlink_explicitly_excludes_map_annotations(connection, planner, tmp_path):
    conn, ann = connection
    planner.return_value[0].after = None
    delete = Mock()
    option = Mock()
    modules = {'omero.cmd': SimpleNamespace(Delete2=delete),
               'omero.cmd.graphs': SimpleNamespace(ChildOption=option)}
    with patch.dict('sys.modules', modules):
        refresh_workflow_metadata(conn, object(), 'Plate', 10, 'wf',
                                  dry_run=False, backup_path=tmp_path / 'backup.json')
    option.assert_called_once_with(excludeType=['MapAnnotation'])
    delete.assert_called_once_with(targetObjects={'PlateAnnotationLink': [123]},
                                   childOptions=[option.return_value])
    ann.save.assert_not_called()
