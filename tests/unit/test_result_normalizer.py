from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from biomero.slurm_client import SlurmClient


def test_default_is_disabled_without_remote_calls():
    client = SlurmClient(config_only=True)
    client.run_commands = MagicMock()
    assert client.normalize_results_on_slurm('/data', uuid4(), None) is None
    client.run_commands.assert_not_called()


def test_normalizer_is_cpu_only_and_quotes_paths():
    client = SlurmClient(config_only=True, remote_shallow_zarr=True,
                         result_normalizer_image='registry/helper:0.1.0',
                         result_normalizer_partition='cpu',
                         slurm_global_job_params=[' --gres=gpu:1', ' --gpus=1', ' --mem=4G'])
    from biomero.result_normalizer import build_command
    command = build_command(client, '/scratch/path with spaces', '/sif/helper.sif',
                            '/state/input.json', str(uuid4()))
    assert '--gres' not in command
    assert '--gpus' not in command
    assert '--partition=cpu' in command
    assert '--mem=4G' in command
    assert '--containall' in command
    assert 'normalize-tree' in command


def test_unknown_image_version_rejected():
    from biomero.result_normalizer import image_spec
    client = SimpleNamespace(result_normalizer_image='registry/helper:latest',
                             slurm_converters_path='/images')
    with pytest.raises(ValueError, match='version'):
        image_spec(client)


@pytest.mark.parametrize('executable,expected', [('7z', "'-xr!*.biomero-lock'"),
                                               ('zip', "-x '*.biomero-lock'")])
def test_internal_locks_are_excluded_only_when_enabled(executable, expected):
    client = SlurmClient(config_only=True, slurm_zip_cmd=executable)
    assert 'biomero-lock' not in client.get_zip_command('/data', 'results')
    client.remote_shallow_zarr = True
    assert expected in client.get_zip_command('/data', 'results')
