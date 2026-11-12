from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4
import shlex

import pytest

from biomero.slurm_client import SlurmClient


def test_default_is_disabled_without_remote_calls():
    client = SlurmClient(config_only=True)
    client.run_commands = MagicMock()
    assert client.shallow_results_on_slurm('/data', uuid4(), None) is None
    client.run_commands.assert_not_called()
    assert client.get_shallower_job_params() == ['--cpus-per-task=1']


def test_shallower_is_cpu_only_and_quotes_paths():
    client = SlurmClient(config_only=True, remote_shallow_zarr=True,
                         remote_shallower_image='registry/helper:0.1.0',
                         remote_shallower_partition='cpu',
                         slurm_global_job_params=[' --gres=gpu:1', ' --gpus=1', ' --mem=4G'])
    from biomero.remote_shallower import build_helper_command
    command = build_helper_command(client, '/scratch/path with spaces', '/sif/helper.sif',
                            '/state/input.json', str(uuid4()))
    assert '--gres' not in command
    assert '--gpus' not in command
    assert '--partition=cpu' in command
    assert '--mem=4G' in command
    assert '--containall' in command
    assert 'normalize-tree' in command


def test_latest_image_can_be_selected_in_configuration():
    from biomero.remote_shallower import image_spec
    client = SimpleNamespace(remote_shallower_image='registry/helper:latest',
                             slurm_converters_path='/images')
    spec = image_spec(client)
    assert spec['source'] == 'registry/helper'
    assert spec['version'] == 'latest'


def test_default_helper_image_is_latest():
    from biomero.remote_shallower import image_spec
    client = SlurmClient(config_only=True)
    assert client.remote_shallower_image == 'cellularimagingcf/biomero-shallower:latest'
    assert client.remote_shallower_version is None
    assert image_spec(client)['version'] == 'latest'


def test_setup_without_helper_configuration_acquires_default_helper():
    from biomero.remote_shallower import image_spec
    client = SlurmClient(config_only=True)
    client.validate = MagicMock(return_value=True)
    client.setup_directories = MagicMock()
    client.setup_job_scripts = MagicMock()
    client.prepare_converters = MagicMock(return_value=[{'kind': 'converter'}])
    client.setup_container_images = MagicMock(return_value=42)
    client.check_flock_available = MagicMock()
    assert client.setup_slurm() == 42
    client.setup_container_images.assert_called_once_with(
        extra_image_specs=[{'kind': 'converter'}, image_spec(client)])


def test_tool_version_is_read_from_installed_image_labels():
    import json
    from biomero.remote_shallower import installed_tool_version
    client = SimpleNamespace(run_commands=MagicMock(return_value=SimpleNamespace(
        ok=True, stdout=json.dumps({'data': {'attributes': {'labels': {
            'org.opencontainers.image.version': '9.2.1b3'}}}}))))
    assert installed_tool_version(client, '/images/helper image.sif') == '9.2.1b3'
    command = client.run_commands.call_args.args[0][0]
    assert 'inspect --json --labels' in command
    assert "'/images/helper image.sif'" in command


def test_runtime_capabilities_and_configured_version_are_enforced():
    import json
    from biomero.remote_shallower import validate_installed_tool
    labels = {
        'org.opencontainers.image.version': '0.1.0b4',
        'org.biomeroproject.shallower.capability-schema': '1',
        'org.biomeroproject.shallower.runtime-contracts': '1',
        'org.biomeroproject.shallower.manifest-schemas': '2',
        'org.biomeroproject.shallower.migrations': (
            'schema-1-to-2,schema-1-path-only-labels'),
    }
    client = SimpleNamespace(
        remote_shallower_version='0.1.0-beta.4',
        run_commands=MagicMock(return_value=SimpleNamespace(
            ok=True,
            stdout=json.dumps({'data': {'attributes': {'labels': labels}}}),
        )),
    )

    assert validate_installed_tool(client, '/images/helper.sif') == '0.1.0b4'


@pytest.mark.parametrize(
    'removed,changed,error',
    [
        ('org.biomeroproject.shallower.capability-schema', None,
         'capability metadata'),
        ('org.biomeroproject.shallower.runtime-contracts', None,
         'does not declare'),
        (None, ('org.biomeroproject.shallower.runtime-contracts', '2'),
         'runtime contract 1'),
        (None, ('org.biomeroproject.shallower.manifest-schemas', '1'),
         'manifest schema 2'),
    ],
)
def test_runtime_capabilities_fail_closed(removed, changed, error):
    import json
    from biomero.remote_shallower import validate_installed_tool
    labels = {
        'org.opencontainers.image.version': '0.1.0b4',
        'org.biomeroproject.shallower.capability-schema': '1',
        'org.biomeroproject.shallower.runtime-contracts': '1',
        'org.biomeroproject.shallower.manifest-schemas': '2',
        'org.biomeroproject.shallower.migrations': 'schema-1-to-2',
    }
    if removed:
        labels.pop(removed)
    if changed:
        labels[changed[0]] = changed[1]
    client = SimpleNamespace(
        remote_shallower_version=None,
        run_commands=MagicMock(return_value=SimpleNamespace(
            ok=True,
            stdout=json.dumps({'data': {'attributes': {'labels': labels}}}),
        )),
    )

    with pytest.raises((ValueError, RuntimeError), match=error):
        validate_installed_tool(client, '/images/helper.sif')


def test_runtime_rejects_installed_version_mismatch():
    import json
    from biomero.remote_shallower import validate_installed_tool
    labels = {
        'org.opencontainers.image.version': '0.1.0b3',
        'org.biomeroproject.shallower.capability-schema': '1',
        'org.biomeroproject.shallower.runtime-contracts': '1',
        'org.biomeroproject.shallower.manifest-schemas': '2',
    }
    client = SimpleNamespace(
        remote_shallower_version='0.1.0b4',
        run_commands=MagicMock(return_value=SimpleNamespace(
            ok=True,
            stdout=json.dumps({'data': {'attributes': {'labels': labels}}}),
        )),
    )

    with pytest.raises(RuntimeError, match='does not match configured'):
        validate_installed_tool(client, '/images/helper.sif')


@pytest.mark.parametrize('output', ['{}', 'not json'])
def test_missing_tool_version_requires_explicit_configuration(output):
    from biomero.remote_shallower import installed_tool_version
    client = SimpleNamespace(run_commands=MagicMock(return_value=SimpleNamespace(
        ok=True, stdout=output)))
    with pytest.raises(ValueError, match='OCI labels'):
        installed_tool_version(client, '/images/helper.sif')


def test_shallowing_and_recovery_have_distinct_job_names():
    from biomero.remote_shallower import build_helper_command
    client = SlurmClient(config_only=True, remote_shallower_image='helper:latest')
    task_id = str(uuid4())
    names = []
    for recovery in (False, True):
        command = build_helper_command(client, '/out', '/helper.sif', '/manifest',
                                task_id, recovery=recovery)
        names.append(next(arg for arg in shlex.split(command)
                          if arg.startswith('--job-name=')))
    assert names[0] == '--job-name=biomero-shallower-' + task_id
    assert names[1] == '--job-name=biomero-recovery-' + task_id


def test_submission_uses_explicit_reconciliation_identity():
    from biomero.slurm_client import SlurmClient
    client = SimpleNamespace(run_commands=MagicMock(
        return_value=SimpleNamespace(ok=True, stdout='123')))
    assert SlurmClient.submit_job_once(client, 'sbatch --parsable worker.sh', '/state/recovery',
                        job_name='biomero-recovery-task') == 123
    script = shlex.split(client.run_commands.call_args.args[0][0])[-1]
    assert '--name=biomero-recovery-task' in script


@pytest.mark.parametrize('executable,expected', [('7z', "'-xr!*.biomero-lock'"),
                                               ('zip', "-x '*.biomero-lock'")])
def test_internal_locks_are_excluded_only_when_enabled(executable, expected):
    client = SlurmClient(config_only=True, slurm_zip_cmd=executable,
                         remote_shallow_zarr=False)
    assert 'biomero-lock' not in client.get_zip_command('/data', 'results')
    client.remote_shallow_zarr = True
    assert expected in client.get_zip_command('/data', 'results')


@pytest.mark.parametrize('enabled,image,level,expected', [
    (True, 'registry/helper:0.1.0', 'INFO',
     'Including remote shallower helper image'),
    (False, 'registry/helper:0.1.0', 'DEBUG',
     'Skipping remote shallower helper image'),
    (True, '', 'WARNING', 'remote_shallower_image is empty'),
])
def test_setup_slurm_logs_helper_image_decision(caplog, enabled, image, level,
                                                expected):
    client = SlurmClient(config_only=True, remote_shallow_zarr=enabled,
                         remote_shallower_image=image,
                         slurm_converters_path='/sifs')
    client.validate = MagicMock(return_value=True)
    client.setup_directories = MagicMock()
    client.setup_job_scripts = MagicMock()
    client.prepare_converters = MagicMock(return_value=[])
    client.setup_container_images = MagicMock(return_value=None)
    client.check_flock_available = MagicMock()
    with caplog.at_level('DEBUG', logger='biomero.slurm_client'):
        client.setup_slurm()
    assert [r.levelname for r in caplog.records if expected in r.getMessage()]         == [level]


def test_zip_exclusion_decision_is_logged(caplog):
    client = SlurmClient(config_only=True, slurm_zip_cmd='zip',
                         remote_shallow_zarr=False)
    with caplog.at_level('DEBUG', logger='biomero.slurm_client'):
        client.get_zip_command('/data', 'results')
    assert 'not applied' in caplog.text


def test_flock_check_warns_only_when_flock_is_missing(caplog):
    client = SlurmClient(config_only=True)
    client.run_commands = MagicMock(side_effect=[
        SimpleNamespace(ok=True, stdout='/usr/bin/flock'),
        SimpleNamespace(ok=False, stdout='')])
    with caplog.at_level('DEBUG', logger='biomero.slurm_client'):
        assert client.check_flock_available() is True
        assert 'flock was not found' not in caplog.text
        assert client.check_flock_available() is False
    assert 'flock was not found' in caplog.text


def test_setup_checks_flock_when_the_helper_image_is_included():
    client = SlurmClient(config_only=True, remote_shallow_zarr=True,
                         remote_shallower_image='registry/helper:0.1.0',
                         slurm_converters_path='/sifs')
    client.validate = MagicMock(return_value=True)
    client.setup_directories = MagicMock()
    client.setup_job_scripts = MagicMock()
    client.prepare_converters = MagicMock(return_value=[])
    client.setup_container_images = MagicMock(return_value=None)
    client.check_flock_available = MagicMock()
    client.setup_slurm()
    client.check_flock_available.assert_called_once_with()


def test_sbatch_wrap_command_applies_site_defaults():
    client = SlurmClient(config_only=True, slurm_default_partition='cpu',
                         slurm_global_job_params=['--account=lab'])
    command = shlex.split(client.build_sbatch_wrap_command(
        'echo "a b"', job_name='my job', output_path='/logs/%j.log'))
    assert command[:3] == ['sbatch', '--parsable', '--export=NONE']
    assert '--partition=cpu' in command
    assert '--account=lab' in command
    assert '--job-name=my job' in command
    assert '--output=/logs/%j.log' in command
    assert command[-1] == '--wrap=echo "a b"'


def test_sbatch_wrap_command_accepts_explicit_job_params():
    client = SlurmClient(config_only=True, slurm_default_partition='cpu')
    command = client.build_sbatch_wrap_command(
        'true', job_name='j', output_path='/o', job_params=['--time=1'])
    assert '--time=1' in command and '--partition' not in command


def test_workflow_tasks_are_read_once_and_filtered():
    client = SlurmClient(config_only=True)
    first = SimpleNamespace(task_name='a', input_data='/x')
    second = SimpleNamespace(task_name='b', input_data='/x')
    third = SimpleNamespace(task_name='b', input_data='/y')
    workflow = SimpleNamespace(tasks=[1, 2, 3])
    records = {'wf': workflow, 1: first, 2: second, 3: third}
    client.workflowTracker = MagicMock()
    client.workflowTracker.repository.get.side_effect = (
        lambda key: workflow if str(key) == str(workflow_id) else records[key])
    workflow_id = uuid4()
    assert client.get_workflow_tasks(workflow_id) == [first, second, third]
    assert client.get_workflow_tasks(workflow_id, task_name='b') == [second, third]
    assert client.get_workflow_tasks(
        workflow_id, task_name='b', input_data='/x') == [second]


@pytest.mark.parametrize('stdout,expected', [('NOT_SUBMITTED', None),
                                           ('77', 77)])
def test_submit_job_once_returns_job_id_or_none_when_rejected(stdout, expected):
    client = SimpleNamespace(run_commands=MagicMock(
        return_value=SimpleNamespace(ok=True, stdout=stdout)))
    assert SlurmClient.submit_job_once(
        client, 'sbatch --parsable job.sh', '/state/job',
        job_name='any-job') == expected


def test_submit_job_once_names_the_unresolved_job_and_exit_code():
    client = SimpleNamespace(run_commands=MagicMock(
        return_value=SimpleNamespace(ok=False, stdout='', exited=3)))
    with pytest.raises(RuntimeError, match=r'any-job.*exit 3'):
        SlurmClient.submit_job_once(
            client, 'sbatch --parsable job.sh', '/state/job',
            job_name='any-job')
