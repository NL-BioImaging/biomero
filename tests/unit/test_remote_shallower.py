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
    from biomero.remote_shallower import build_command
    command = build_command(client, '/scratch/path with spaces', '/sif/helper.sif',
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


def test_image_configuration_is_required_explicitly():
    from biomero.remote_shallower import image_spec
    client = SlurmClient(config_only=True)
    assert client.remote_shallower_image is None
    assert client.remote_shallower_version is None
    with pytest.raises(ValueError, match='remote_shallower_image'):
        image_spec(client)


def test_setup_without_helper_configuration_still_initializes_converters():
    client = SlurmClient(config_only=True)
    client.validate = MagicMock(return_value=True)
    client.setup_directories = MagicMock()
    client.setup_job_scripts = MagicMock()
    client.prepare_converters = MagicMock(return_value=[{'kind': 'converter'}])
    client.setup_container_images = MagicMock(return_value=42)
    assert client.setup_slurm() == 42
    client.setup_container_images.assert_called_once_with(
        extra_image_specs=[{'kind': 'converter'}])


def test_shallowing_and_recovery_have_distinct_job_names():
    from biomero.remote_shallower import build_command
    client = SlurmClient(config_only=True, remote_shallower_image='helper:latest')
    task_id = str(uuid4())
    names = []
    for recovery in (False, True):
        command = build_command(client, '/out', '/helper.sif', '/manifest',
                                task_id, recovery=recovery)
        names.append(next(arg for arg in shlex.split(command)
                          if arg.startswith('--job-name=')))
    assert names[0] == '--job-name=biomero-shallower-' + task_id
    assert names[1] == '--job-name=biomero-recovery-' + task_id


def test_submission_uses_explicit_reconciliation_identity():
    from biomero.remote_shallower import _submit_once
    client = SimpleNamespace(run_commands=MagicMock(
        return_value=SimpleNamespace(ok=True, stdout='123')))
    assert _submit_once(client, 'sbatch --parsable worker.sh', '/state/recovery',
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
