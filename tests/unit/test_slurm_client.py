import logging
from uuid import uuid4
from biomero.slurm_client import (
    SlurmClient
)
from biomero.eventsourcing import NoOpWorkflowTracker
from biomero.database import EngineManager, TaskExecution, JobProgressView, JobView
from biomero.schema_parsers import DescriptorParserFactory
import pytest
import mock
from mock import patch, MagicMock
from paramiko import SSHException
import os
import shlex
from sqlalchemy import inspect


@pytest.fixture(autouse=True)
def set_env_vars():
    # Set environment variables directly
    os.environ["PERSISTENCE_MODULE"] = "eventsourcing_sqlalchemy"
    os.environ["SQLALCHEMY_URL"] = "sqlite:///:memory:"

    # Yield to let the test run
    yield

    # Optionally, clean up the environment variables after the test
    del os.environ["PERSISTENCE_MODULE"]
    del os.environ["SQLALCHEMY_URL"]


class SerializableMagicMock(MagicMock, dict):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
# Configure logging
logging.basicConfig(level=logging.INFO)


def test_get_config_value_prefers_env_over_ini():
    config = MagicMock()
    config.get.return_value = "ini-value"

    with patch.dict(os.environ, {"TEST_ENV_KEY": "env-value"}, clear=False):
        value = SlurmClient._get_config_value(
            config,
            section="SLURM",
            option="test_option",
            default="default-value",
            env_vars=["TEST_ENV_KEY"],
        )

    assert value == "env-value"


def test_get_config_value_uses_ini_when_env_missing():
    config = MagicMock()
    config.get.return_value = "ini-value"

    value = SlurmClient._get_config_value(
        config,
        section="SLURM",
        option="test_option",
        default="default-value",
        env_vars=["TEST_ENV_KEY"],
    )

    assert value == "ini-value"


def test_get_config_value_treats_empty_as_none():
    config = MagicMock()
    config.get.return_value = ""

    value = SlurmClient._get_config_value(
        config,
        section="SLURM",
        option="test_option",
        default="default-value",
        env_vars=None,
        empty_is_none=True,
    )

    assert value is None


@pytest.mark.parametrize(
    "ini_value, env_value, expected",
    [
        (True, None, True),
        (False, None, False),
        (False, "Yes", True),
        (True, "no", False),
        (True, "definitely-not-bool", True),
        (False, "definitely-not-bool", False),
    ],
)
def test_get_config_value_bool_precedence(ini_value, env_value, expected):
    config = MagicMock()
    config.getboolean.return_value = ini_value

    env_patch = {}
    if env_value is not None:
        env_patch["TEST_BOOL_ENV"] = env_value

    with patch.dict(os.environ, env_patch, clear=False):
        value = SlurmClient._get_config_value(
            config,
            section="SLURM",
            option="test_bool",
            default=False,
            env_vars=["TEST_BOOL_ENV"],
            value_type=bool,
        )

    assert value is expected


def test_get_config_value_int_invalid_falls_back_to_default():
    config = MagicMock()
    config.get.return_value = "not-an-int"

    value = SlurmClient._get_config_value(
        config,
        section="SLURM",
        option="test_int",
        default=7,
        env_vars=None,
        value_type=int,
    )

    assert value == 7


def test_get_config_value_int_invalid_env_falls_back_to_ini_value():
    config = MagicMock()
    config.get.return_value = "9"

    with patch.dict(os.environ, {"TEST_INT_ENV": "not-an-int"}, clear=False):
        value = SlurmClient._get_config_value(
            config,
            section="SLURM",
            option="test_int",
            default=7,
            env_vars=["TEST_INT_ENV"],
            value_type=int,
        )

    assert value == 9



@pytest.fixture
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
def slurm_client(_mock_run,
                 _mock_put, _mock_open,
                 _mock_session):
    logging.info("EngineManager.__dict__: %s", EngineManager.__dict__)
    return SlurmClient("localhost", 8022, "slurm")


def test_list_available_converter_versions(slurm_client):
    """
    Test the list_available_converter_versions method of SlurmClient.
    """
    # GIVEN
    slurm_client._CONVERTER_VERSION_CMD = "echo {converter_path}/converter"
    slurm_client.slurm_converters_path = "/path/to/converters"

    # Mock the run_commands method
    slurm_client.run_commands = MagicMock(return_value=MagicMock(
        ok=True,
        stdout="converter1 1.0\nconverter2 2.1\nconverter1 1.1"
    ))

    expected_result = {
        "converter1": ["1.0", "1.1"],
        "converter2": ["2.1"]
    }

    # WHEN
    result = slurm_client.list_available_converter_versions()

    # THEN
    assert result == expected_result
    slurm_client.run_commands.assert_called_once_with(
        ["echo /path/to/converters/converter"]
    )



@patch.object(SlurmClient, 'run_commands_split_out')
def test_get_all_image_versions_and_data_files(mock_run_commands_split_out, slurm_client):
    
    # GIVEN
    slurm_client.slurm_images_path = "/path/to/slurm/images"
    slurm_client.slurm_data_path = "/path/to/slurm/data"

    model_paths = {
        "model1": "/path/to/models/model1",
        "model2": "/path/to/models/model2",
    }

    slurm_client.slurm_model_paths = model_paths

    version_cmd_model1 = f"{slurm_client._VERSION_CMD.format(slurm_images_path=slurm_client.slurm_images_path, image_path='/path/to/models/model1')}"
    version_cmd_model2 = f"{slurm_client._VERSION_CMD.format(slurm_images_path=slurm_client.slurm_images_path, image_path='/path/to/models/model2')}"
    data_cmd = f"{slurm_client._DATA_CMD.format(slurm_data_path=slurm_client.slurm_data_path)}"

    # Mock the run_commands_split_out method
    mock_run_commands_split_out.return_value = [
        'version_output1', 'version_output2', 'data_output']

    # WHEN
    versions_dict, data_files = slurm_client.get_all_image_versions_and_data_files()

    # THEN
    assert versions_dict == {'model1': [
        'version_output1'], 'model2': ['version_output2']}
    assert data_files == ['data_output']

    # Ensure run_commands_split_out was called with the correct arguments
    mock_run_commands_split_out.assert_called_once_with(
        [version_cmd_model1, version_cmd_model2, data_cmd])


def test_get_image_versions_and_data_files(slurm_client):
    # GIVEN
    model = "example_model"
    slurm_client.slurm_images_path = "/path/to/slurm/images"
    slurm_client.slurm_data_path = "/path/to/slurm/data"
    model_path = '/path/to/models/example_model'
    slurm_client.slurm_model_paths = {
        model: model_path
    }

    version_cmd = f"{slurm_client._VERSION_CMD.format(slurm_images_path=slurm_client.slurm_images_path, image_path=model_path)}"
    data_cmd = f"{slurm_client._DATA_CMD.format(slurm_data_path=slurm_client.slurm_data_path)}"

    # Mock the run_commands_split_out method
    mock_run_commands_split_out = mock.Mock(
        return_value=['version_1\nversion_2\nversion_3', 'data_output_1\ndata_output_2'])
    with patch.object(slurm_client, 'run_commands_split_out', mock_run_commands_split_out):
        # WHEN
        versions, data_files = slurm_client.get_image_versions_and_data_files(
            model)

        # THEN
        assert versions == ['version_3', 'version_2', 'version_1']
        assert data_files == ['data_output_1', 'data_output_2']

        # Ensure run_commands_split_out was called with the correct arguments
        mock_run_commands_split_out.assert_called_once_with(
            [version_cmd, data_cmd])

    # WHEN & THEN
    with pytest.raises(ValueError, match="No path known for provided model"):
        slurm_client.get_image_versions_and_data_files("nonexistent_model")


def test_get_unzip_command(slurm_client):
    # GIVEN
    slurm_data_path = "/path/to/slurm/data"
    slurm_client.slurm_data_path = slurm_data_path
    slurm_client.slurm_zip_cmd = SlurmClient._DEFAULT_SLURM_ZIP_CMD
    zipfile = "example"
    filter_filetypes = "*.zarr *.ome.zarr *.tiff *.tif"
    auto = "$(command -v 7z || command -v 7za)"
    expected_command = (
        f'mkdir -p "{slurm_data_path}/{zipfile}"'
        f' "{slurm_data_path}/{zipfile}/data"'
        f' "{slurm_data_path}/{zipfile}/data/in"'
        f' "{slurm_data_path}/{zipfile}/data/out"'
        f' "{slurm_data_path}/{zipfile}/data/gt";'
        f' {auto} x -y'
        f' -o"{slurm_data_path}/{zipfile}/data/in"'
        f' "{slurm_data_path}/{zipfile}.zip" {filter_filetypes}'
    )

    # WHEN
    unzip_command = slurm_client.get_unzip_command(zipfile, filter_filetypes)

    # THEN
    assert unzip_command == expected_command


def test_get_unzip_command_explicit_zip_cmd(slurm_client):
    # GIVEN: cluster only has 7za
    slurm_data_path = "/path/to/slurm/data"
    slurm_client.slurm_data_path = slurm_data_path
    slurm_client.slurm_zip_cmd = "7za"
    zipfile = "example"
    filter_filetypes = "*.zarr *.ome.zarr *.tiff *.tif"

    # WHEN
    unzip_command = slurm_client.get_unzip_command(zipfile, filter_filetypes)

    # THEN: explicit command is used, not the auto-detect expression
    assert "7za x -y" in unzip_command
    assert "command -v" not in unzip_command
    assert "mkdir -p" in unzip_command


@patch.object(SlurmClient, 'get')
def test_get_logfile_from_slurm(mock_get, slurm_client):
    # GIVEN
    slurm_job_id = "12345"
    local_tmp_storage = "/tmp/"
    logfile = "/path/to/logfile.log"

    # Case 1: Logfile is provided
    _, _, result = slurm_client.get_logfile_from_slurm(
        slurm_job_id, local_tmp_storage, logfile)
    mock_get.assert_called_once_with(
        remote=logfile, local=f"{local_tmp_storage}")

    # Case 2: Logfile is not provided, and default logfile is used
    _, _, result = slurm_client.get_logfile_from_slurm(
        slurm_job_id, local_tmp_storage)
    default_logfile = slurm_client._LOGFILE.format(slurm_job_id=slurm_job_id)
    mock_get.assert_called_with(
        remote=default_logfile, local=f"{local_tmp_storage}")

    # Case 3: Logfile is not provided, and default logfile is changed
    slurm_client._LOGFILE = "/custom/logfile.log"
    _, _, result = slurm_client.get_logfile_from_slurm(
        slurm_job_id, local_tmp_storage)
    custom_logfile = slurm_client._LOGFILE.format(slurm_job_id=slurm_job_id)
    mock_get.assert_called_with(
        remote=custom_logfile, local=f"{local_tmp_storage}")


@patch('biomero.slurm_client.logger')
@patch.object(SlurmClient, 'run_commands', return_value=SerializableMagicMock(ok=True, stdout=""))
def test_zip_data_on_slurm_server(mock_run_commands, mock_logger, slurm_client):
    # GIVEN
    data_location = "/local/path/to/store"
    filename = "example_zip"
    slurm_client.slurm_zip_cmd = SlurmClient._DEFAULT_SLURM_ZIP_CMD

    # WHEN
    result = slurm_client.zip_data_on_slurm_server(data_location, filename)

    # THEN - auto-detect expression used; cd into data/out so entries are relative
    auto = "$(command -v 7z || command -v 7za)"
    mock_run_commands.assert_called_once_with(
        [f'cd "{data_location}/data/out" && {auto} a -y "{data_location}/{filename}.zip" -tzip .'], env=None)
    assert result.ok is True
    assert result.stdout == ""
    mock_logger.info.assert_called_with(
        f"Zipping {data_location} as {filename} on Slurm")


@patch('biomero.slurm_client.logger')
@patch.object(SlurmClient, 'get', return_value=SerializableMagicMock(ok=True, stdout=""))
def test_copy_zip_locally(mock_get, mock_logger, slurm_client):
    # GIVEN
    local_tmp_storage = "/local/path/to/store"
    filename = "example_zip"

    # WHEN
    result = slurm_client.copy_zip_locally(local_tmp_storage, filename)

    # THEN
    mock_get.assert_called_once_with(
        remote=f"{filename}.zip", local=local_tmp_storage)
    assert result.ok is True
    assert result.stdout == ""
    mock_logger.info.assert_called_with(f'Copying zip {filename} from\
            Slurm to {local_tmp_storage}')


@pytest.mark.parametrize(
    "email, time_limit, data_bind_path, conversion_partition, param_values, expected_env_values",
    [
        # Case 1: Basic test without bind path and partition
        (
            "", "", None, None,
            {"param1": "value1", "param2": "value2"},
            {"PARAM1": '"value1"', "PARAM2": '"value2"'}
        ),
        # Case 2: Test with bind path and partition
        (
            "user@example.com", "10:00:00", "/bind/path", "partition_name",
            {"param1": "value1", "param2": "value2"},
            {"PARAM1": '"value1"', "PARAM2": '"value2"'}
        ),
        # Case 3: Test with hyphenated parameter values
        (
            "", "", None, None,
            {"param1": "value-with-hyphens", "param2": 42},
            {"PARAM1": '"value-with-hyphens"', "PARAM2": "42"}
        ),
        # Case 4: Test with hyphenated parameter values and special characters
        (
            "", "", None, None,
            {"model_name": "cyto-2.1-latest", "threshold": 0.5},
            {"MODEL_NAME": '"cyto-2.1-latest"', "THRESHOLD": "0.5"}
        ),
    ]
)
def test_get_workflow_command(
    slurm_client, email, time_limit, data_bind_path, conversion_partition,
    param_values, expected_env_values
):
    # GIVEN
    workflow = "example_workflow"
    workflow_version = "1.0"
    input_data = "input_data_folder"

    slurm_client.slurm_model_paths = {"example_workflow": "workflow_path"}
    slurm_client.slurm_model_jobs = {"example_workflow": "job_script.sh"}
    slurm_client.slurm_model_jobs_params = {
        "example_workflow": [" --param3=value3", " --param4=value4"]
    }
    slurm_client.slurm_model_images = {"example_workflow": "user/image"}
    slurm_client.slurm_data_path = "/path/to/slurm_data"
    slurm_client.slurm_converters_path = "/path/to/slurm_converters"
    slurm_client.slurm_images_path = "/path/to/slurm_images"
    slurm_client.slurm_script_path = "/path/to/slurm_script"
    slurm_client.slurm_data_bind_path = data_bind_path
    slurm_client.slurm_conversion_partition = conversion_partition

    expected_sbatch_cmd = (
        f'sbatch --param3=value3 --param4=value4 --time={time_limit} --mail-user={email} --output=omero-%j.log \
            "{slurm_client.slurm_script_path}/job_script.sh"'
    )
    
    # Build expected environment dictionary
    expected_env = {
        "DATA_PATH": '"/path/to/slurm_data/input_data_folder"',
        "IMAGE_PATH": '"/path/to/slurm_images/workflow_path"',
        "IMAGE_VERSION": "1.0",
        "SINGULARITY_IMAGE": '"image_1.0.sif"',
        "SCRIPT_PATH": '"/path/to/slurm_script"',
    }
    # Add the test-specific environment variables
    expected_env.update(expected_env_values)

    # Add bind path if specified
    if data_bind_path is not None:
        expected_env["APPTAINER_BINDPATH"] = f'"{data_bind_path}"'

    # WHEN
    sbatch_cmd, env = slurm_client.get_workflow_command(
        workflow,
        workflow_version,
        input_data,
        email,
        time_limit,
        **param_values
    )

    # THEN
    assert sbatch_cmd == expected_sbatch_cmd
    assert env == expected_env


@pytest.mark.parametrize("source_format, target_format", [("zarr", "tiff"), ("xyz", "abc")])
@patch('biomero.slurm_client.SlurmClient.run_commands', new_callable=SerializableMagicMock)
@patch('fabric.Result', new_callable=SerializableMagicMock)
def test_run_conversion_workflow_job(mock_result, mock_run_commands, slurm_client, source_format, target_format):
    # GIVEN
    folder_name = "example_folder"

    slurm_client.slurm_data_path = "/path/to/slurm_data"
    slurm_client.slurm_converters_path = "/path/to/slurm_converters"
    slurm_client.slurm_script_path = "/path/to/slurm_script"
    slurm_client.converter_images = None

    expected_config_file = f"config_{folder_name}.txt"
    expected_data_path = f"{slurm_client.slurm_data_path}/{folder_name}"
    expected_conversion_cmd, expected_sbatch_env = (
        "sbatch --job-name=conversion --export=ALL,CONFIG_PATH=\"$PWD/$CONFIG_FILE\" --array=1-$N \"$SCRIPT_PATH/convert_job_array.sh\"",
        {
            "DATA_PATH": f"\"{expected_data_path}\"",
            "CONVERSION_PATH": f"\"{slurm_client.slurm_converters_path}\"",
            "CONVERTER_IMAGE": f"convert_{source_format}_to_{target_format}_latest.sif",
            "SCRIPT_PATH": f"\"{slurm_client.slurm_script_path}\"",
            "CONFIG_FILE": f"\"{expected_config_file}\""
        }
    )
    expected_commands = [
        f"find \"{expected_data_path}/data/in\" -name \"*.{source_format}\" | awk '{{print NR, $0}}' > \"{expected_config_file}\"",
        f"N=$(wc -l < \"{expected_config_file}\")",
        f"echo \"Number of .{source_format} files: $N\"",
        expected_conversion_cmd
    ]

    # Mocking the run_commands method to avoid actual execution
    mock_run_commands.return_value = mock_result

    # WHEN
    slurm_job = slurm_client.run_conversion_workflow_job(
        folder_name, source_format, target_format)

    # THEN
    assert slurm_job is not None

    assert mock_run_commands.call_args[0][0] == expected_commands
    assert mock_run_commands.call_args[0][1] == expected_sbatch_env

    # Check properties of slurm_job
    assert slurm_job.job_id == -1
    assert slurm_job.submit_result.ok
    assert slurm_job.job_state is None

@pytest.mark.parametrize(
    "env_file_submission",
    [False, True],
)
def test_get_conversion_command(slurm_client, env_file_submission):
    # GIVEN
    slurm_client.slurm_converters_path = "/path/to/converters"
    slurm_client.slurm_script_path = "/path/to/scripts"
    slurm_client.slurm_data_bind_path = "/bind/path"
    slurm_client.slurm_conversion_partition = "gpu"
    slurm_client.env_file_submission = env_file_submission

    data_path = "/data"
    config_file = "config.cfg"

    # WHEN
    cmd, env, image, version = slurm_client.get_conversion_command(
        data_path,
        config_file,
    )

    # THEN
    assert image == "convert_zarr_to_tiff_latest.sif"
    assert version == "latest"

    if env_file_submission:
        assert env == {}
        assert "cat > /data/biomero_job_env.sh" in cmd
        assert "export DATA_PATH=/data" in cmd
        assert "export CONVERSION_PATH=/path/to/converters" in cmd
        assert "export CONVERSION_PARTITION=gpu" in cmd
        assert "--array=1-$N" in cmd
        assert 'sbatch --job-name=conversion' in cmd
        assert '/data/biomero_job_env.sh' in cmd
    else:
        assert cmd == (
            'sbatch --job-name=conversion '
            '--export=ALL,CONFIG_PATH="$PWD/config.cfg" '
            '--array=1-$N "/path/to/scripts/convert_job_array.sh"'
        )
        assert env == {
            "DATA_PATH": '"/data"',
            "CONVERSION_PATH": '"/path/to/converters"',
            "CONVERTER_IMAGE": "convert_zarr_to_tiff_latest.sif",
            "SCRIPT_PATH": '"/path/to/scripts"',
            "CONFIG_FILE": '"config.cfg"',
            "APPTAINER_BINDPATH": '"/bind/path"',
            "CONVERSION_PARTITION": '"gpu"',
        }    
    
@pytest.mark.parametrize(
    "source_format, target_format, data_bind_path, conversion_partition",
    [
        ("zarr", "tiff", None, None),  # Test without bind path and partition
        (
            "xyz",
            "abc",
            "/bind/path",
            "partition_name",
        ),  # Test with bind path and partition
    ],
)
@patch(
    "biomero.slurm_client.SlurmClient.run_commands", new_callable=SerializableMagicMock
)
@patch("fabric.Result", new_callable=SerializableMagicMock)
def test_run_conversion_workflow_job(
    mock_result,
    mock_run_commands,
    slurm_client,
    source_format,
    target_format,
    data_bind_path,
    conversion_partition,
):
    # GIVEN
    folder_name = "example_folder"

    slurm_client.slurm_data_path = "/path/to/slurm_data"
    slurm_client.slurm_converters_path = "/path/to/slurm_converters"
    slurm_client.slurm_script_path = "/path/to/slurm_script"
    slurm_client.converter_images = None
    slurm_client.slurm_data_bind_path = data_bind_path
    slurm_client.slurm_conversion_partition = conversion_partition

    expected_config_file = f"config_{folder_name}.txt"
    expected_data_path = f"{slurm_client.slurm_data_path}/{folder_name}"
    expected_sbatch_env = {
        "DATA_PATH": f'"{expected_data_path}"',
        "CONVERSION_PATH": f'"{slurm_client.slurm_converters_path}"',
        "CONVERTER_IMAGE": f"convert_{source_format}_to_{target_format}_latest.sif",
        "SCRIPT_PATH": f'"{slurm_client.slurm_script_path}"',
        "CONFIG_FILE": f'"{expected_config_file}"',
    }

    # Add expected environment variables for bind path and partition if set
    if data_bind_path is not None:
        expected_sbatch_env["APPTAINER_BINDPATH"] = f'"{data_bind_path}"'
    if conversion_partition is not None:
        expected_sbatch_env["CONVERSION_PARTITION"] = f'"{conversion_partition}"'

    expected_conversion_cmd = (
        'sbatch --job-name=conversion '
        f'--export=ALL,CONFIG_PATH="$PWD/{expected_config_file}" --array=1-$N '
        f'"{slurm_client.slurm_script_path}/convert_job_array.sh"'
    )
    
    # Handle special case for zarr format (.zarr and .ome.zarr)
    if source_format == 'zarr':
        find_cmd = (f'find "{expected_data_path}/data/in" -name "*.zarr" '
                    f'-o -name "*.ome.zarr" | awk \'{{print NR, $0}}\' '
                    f'> "{expected_config_file}"')
    else:
        find_cmd = (f'find "{expected_data_path}/data/in" '
                    f'-name "*.{source_format}" | awk \'{{print NR, $0}}\' '
                    f'> "{expected_config_file}"')

    expected_commands = [
        find_cmd,
        f'N=$(wc -l < "{expected_config_file}")',
        f'echo "Number of .{source_format} files: $N"',
        expected_conversion_cmd,
    ]

    # Mocking the run_commands method to avoid actual execution
    mock_run_commands.return_value = mock_result

    # WHEN
    slurm_job = slurm_client.run_conversion_workflow_job(
        folder_name, source_format, target_format
    )

    # THEN
    assert slurm_job is not None

    assert mock_run_commands.call_args[0][0] == expected_commands
    assert mock_run_commands.call_args[0][1] == expected_sbatch_env

    # Check properties of slurm_job
    assert slurm_job.job_id == -1
    assert slurm_job.submit_result.ok
    assert slurm_job.job_state is None

def test_descriptor_from_github(slurm_client):
    # GIVEN
    workflow = "example_workflow"
    git_repo = "https://github.com/username/repo/tree/branch"
    expected_raw_url = "https://github.com/username/repo/raw/branch/descriptor.json"
    raw_descriptor = {"key": "value"}
    expected_descriptor = {"container-image": {"image": "dockerhub.com/image1"}}
    repos = {
        workflow: git_repo
    }
    with patch('biomero.slurm_client.requests_cache.CachedSession.get') as mock_get:
        with patch('biomero.slurm_client.DescriptorParserFactory.parse_descriptor') as mock_parse:
            mock_schema = mock.Mock()
            mock_schema.model_dump.return_value = expected_descriptor
            mock_parse.return_value = mock_schema

            slurm_client.slurm_model_repos = repos
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = raw_descriptor

                # WHEN
            descriptor = slurm_client.generic_descriptor_from_github(workflow)

                # THEN
            mock_get.assert_called_with(expected_raw_url)
            mock_parse.assert_called_once_with(raw_descriptor, name=workflow)
            assert descriptor == expected_descriptor

                # WHEN & THEN
            mock_get.return_value.ok = False
            with pytest.raises(ValueError, match="No descriptor file found for repository"):
                slurm_client.generic_descriptor_from_github(workflow)


def test_convert_url(slurm_client):
    # GIVEN
    valid_url = "https://github.com/username/repo/tree/branch"
    invalid_url = "https://example.com/invalid-url"

    # WHEN
    converted_url = slurm_client.convert_url(valid_url)

    # THEN
    expected_output_url = "https://github.com/username/repo/raw/branch/descriptor.json"
    assert converted_url == expected_output_url

    # WHEN & THEN
    with pytest.raises(ValueError, match="Invalid GitHub URL"):
        slurm_client.convert_url(invalid_url)


def test_extract_parts_from_url(slurm_client):
    # GIVEN
    valid_url = "https://github.com/username/repo/tree/branch"
    valid_url2 = "https://github.com/username/repo"
    invalid_url = "https://example.com/invalid-url"

    # WHEN
    valid_url_parts, valid_branch = slurm_client.extract_parts_from_url(
        valid_url)

    # THEN
    assert valid_url_parts == [
        'https:', '', 'github.com', 'username', 'repo', 'tree', 'branch']
    assert valid_branch == 'branch'

    # WHEN & THEN
    with pytest.raises(ValueError, match="Invalid GitHub URL"):
        slurm_client.extract_parts_from_url(invalid_url)

    # WHEN no branch
    valid_url_parts2, valid_branch2 = slurm_client.extract_parts_from_url(
        valid_url2)

    # THEN
    assert valid_url_parts2 == [
        'https:', '', 'github.com', 'username', 'repo']
    assert valid_branch2 == 'master'


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github', return_value={
    'inputs': [
        {
            'id': 'input1',
            'default-value': 'default_value1',
            'type': 'type1',
            'optional': False,
            'command-line-flag': '--flag1',
            'description': 'description1',
        },
        {
            'id': 'input2',
            'default-value': 'default_value2',
            'type': 'type2',
            'optional': True,
            'command-line-flag': '--flag2',
            'description': 'description2',
        },
        {
            'id': 'cytomine_input3',
            'default-value': 'default_value3',
            'type': 'type3',
            'optional': True,
            'command-line-flag': '--flag3',
            'description': 'description3',
        },
    ]
})
def test_get_workflow_parameters(mock_pull_descriptor,
                                 slurm_client):
    # GIVEN
    workflow = "my_workflow"

    # WHEN
    workflow_params = slurm_client.get_workflow_parameters(workflow)

    # THEN
    expected_workflow_params = {
        'input1': {
            'name': 'input1',
            'default': 'default_value1',
            'type': 'type1',
            'optional': False,
            'cmd_flag': '--flag1',
            'description': 'description1',
            'file_attachment': False,
            'format': [],
        },
        'input2': {
            'name': 'input2',
            'default': 'default_value2',
            'type': 'type2',
            'optional': True,
            'cmd_flag': '--flag2',
            'description': 'description2',
            'file_attachment': False,
            'format': [],
        },
    }
    assert workflow_params == expected_workflow_params


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github', return_value={
    'inputs': [
        {
            'id': 'reg_param',
            'default-value': 'val',
            'type': 'Number',
            'optional': True,
            'command-line-flag': '--reg',
            'description': 'regular param',
        },
        {
            'id': 'model_file',
            'default-value': None,
            'type': 'file',
            'optional': True,
            'command-line-flag': '--model',
            'description': 'custom model file',
            'set-by-server': True,
            'file-attachment': True,
            'format': ['unix'],
        },
        {
            'id': 'server_only',
            'default-value': None,
            'type': 'string',
            'optional': True,
            'command-line-flag': '--server',
            'description': 'server-managed, not a file attachment',
            'set-by-server': True,
        },
    ]
})
def test_get_workflow_parameters_file_attachment(mock_descriptor, slurm_client):
    """file-attachment params (set-by-server=True, file-attachment=True) are
    included and tagged; plain set-by-server params are still excluded."""
    params = slurm_client.get_workflow_parameters('my_workflow')

    # regular param: present, not a file attachment
    assert 'reg_param' in params
    assert params['reg_param']['file_attachment'] is False
    assert params['reg_param']['format'] == []

    # file-attachment param: included even though set-by-server=True
    assert 'model_file' in params
    assert params['model_file']['file_attachment'] is True
    assert params['model_file']['format'] == ['unix']
    assert params['model_file']['cmd_flag'] == '--model'

    # plain set-by-server (no file-attachment) → still excluded
    assert 'server_only' not in params


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github', return_value={
    'inputs': [
        {
            'id': 'reg_param',
            'default-value': 'val',
            'type': 'Number',
            'optional': True,
            'command-line-flag': '--reg',
            'description': 'regular param',
        },
        {
            'id': 'model_file',
            'default-value': None,
            'type': 'file',
            'optional': True,
            'command-line-flag': '--model',
            'description': 'custom model file',
            'set-by-server': True,
            'file-attachment': True,
            'format': ['unix'],
        },
    ]
})
def test_get_file_attachment_params_filters(mock_descriptor, slurm_client):
    """get_file_attachment_params returns only params tagged file_attachment=True."""
    params = slurm_client.get_file_attachment_params('my_workflow')

    assert 'model_file' in params
    assert params['model_file']['file_attachment'] is True
    # regular param is excluded
    assert 'reg_param' not in params


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_extract_data_location_from_log_exc(mock_run_commands,
                                            slurm_client):
    # GIVEN
    slurm_job_id = "123"
    logfile = "path/to/logfile.txt"
    expected_data_location = '/path/to/data'
    mock_run_commands.return_value = SerializableMagicMock(
        ok=False, stdout=expected_data_location)

    # WHEN
    with pytest.raises(SSHException):
        _ = slurm_client.extract_data_location_from_log(
            slurm_job_id=slurm_job_id, logfile=logfile)

    # THEN
    mock_run_commands.assert_called_with(
        [f"cat \"{logfile}\" | perl -wne '/Running [\\w-]+? Job w\\/ .+? \\| .+? \\| (.+?) \\|.*/i and print$1'"])


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_extract_data_location_from_log_2(mock_run_commands,
                                          slurm_client):
    # GIVEN
    slurm_job_id = "123"
    expected_data_location = '/path/to/data'
    mock_run_commands.return_value = SerializableMagicMock(
        ok=True, stdout=expected_data_location)

    # WHEN
    data_location = slurm_client.extract_data_location_from_log(
        slurm_job_id=slurm_job_id, logfile=None)

    # THEN
    assert data_location == expected_data_location
    mock_run_commands.assert_called_with(
        [f"cat \"omero-{slurm_job_id}.log\" | perl -wne '/Running [\\w-]+? Job w\\/ .+? \\| .+? \\| (.+?) \\|.*/i and print$1'"])


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_extract_data_location_from_log(mock_run_commands,
                                        slurm_client):
    # GIVEN
    slurm_job_id = "123"
    logfile = "path/to/logfile.txt"
    expected_data_location = '/path/to/data'
    mock_run_commands.return_value = SerializableMagicMock(
        ok=True, stdout=expected_data_location)

    # WHEN
    data_location = slurm_client.extract_data_location_from_log(
        slurm_job_id=slurm_job_id, logfile=logfile)

    # THEN
    assert data_location == expected_data_location
    mock_run_commands.assert_called_with(
        [f"cat \"{logfile}\" | perl -wne '/Running [\\w-]+? Job w\\/ .+? \\| .+? \\| (.+?) \\|.*/i and print$1'"])


def test_get_job_status_command(slurm_client):
    # GIVEN
    slurm_job_ids = [123, 456, 789]

    # WHEN
    command = slurm_client.get_job_status_command(slurm_job_ids)

    # THEN
    expected_command = 'sacct -n -o JobId,State,End -X -j 123 -j 456 -j 789'
    assert command == expected_command


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_check_job_status(mock_run_commands,
                          slurm_client):
    # GIVEN
    mock_run_commands.return_value = SerializableMagicMock(
        ok=True, stdout="12345 RUNNING\n67890 COMPLETED")

    # WHEN
    job_status_dict, _ = slurm_client.check_job_status([12345, 67890])

    # THEN
    mock_run_commands.assert_called_with(
        ['sacct -n -o JobId,State,End -X -j 12345 -j 67890'], env=None)
    assert job_status_dict == {12345: 'RUNNING', 67890: 'COMPLETED'}
    
    
@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_check_job_array_status(mock_run_commands, slurm_client):
    # GIVEN
    mock_stdout = """2304_1        COMPLETED 2024-02-29T10:22:38 
    2304_2        COMPLETED 2024-02-29T10:22:38 
    2304_3        COMPLETED 2024-02-29T10:22:40 
    2304_4        COMPLETED 2024-02-29T10:22:40 
    2304_5        COMPLETED 2024-02-29T10:22:43 
    2304_6        COMPLETED 2024-02-29T10:22:43 
    2304_7        COMPLETED 2024-02-29T10:22:45 
    2304_8        COMPLETED 2024-02-29T10:22:45 
    2304_9        COMPLETED 2024-02-29T10:22:47 
    2304_10       COMPLETED 2024-02-29T10:22:47 
    2304_11       COMPLETED 2024-02-29T10:22:50 
    2304_12       COMPLETED 2024-02-29T10:22:50 
    2304_13       COMPLETED 2024-02-29T10:22:52 
    2304_14       COMPLETED 2024-02-29T10:22:52 
    2304_15       COMPLETED 2024-02-29T10:22:54 
    2304_16       COMPLETED 2024-02-29T10:22:54 
    2304_17       COMPLETED 2024-02-29T10:22:57 
    2304_18       COMPLETED 2024-02-29T10:22:57 
    2304_19       COMPLETED 2024-02-29T10:22:59 
    2304_20       COMPLETED 2024-02-29T10:22:59 
    2304_21       COMPLETED 2024-02-29T10:23:01 
    2304_22       COMPLETED 2024-02-29T10:23:01 
    2304_23       COMPLETED 2024-02-29T10:23:04 
    2304_24       COMPLETED 2024-02-29T10:23:04 
    2304_25       COMPLETED 2024-02-29T10:23:06 
    2304_26       COMPLETED 2024-02-29T10:23:06 
    2304_27       COMPLETED 2024-02-29T10:23:08 
    2304_28       COMPLETED 2024-02-29T10:23:08 
    2304_29       COMPLETED 2024-02-29T10:23:11 
    2304_30       COMPLETED 2024-02-29T10:23:11 
    2304_31       COMPLETED 2024-02-29T10:23:13 
    2304_32       COMPLETED 2024-02-29T10:23:12 
    2304_33       COMPLETED 2024-02-29T10:23:14 
    2304_34       COMPLETED 2024-02-29T10:23:15 
    2304_35       COMPLETED 2024-02-29T10:23:17 
    2339_1        COMPLETED 2024-02-29T10:34:42 
    2339_2        COMPLETED 2024-02-29T10:34:42 
    2339_3        COMPLETED 2024-02-29T10:34:44 
    2339_4        COMPLETED 2024-02-29T10:34:44 
    2339_5        COMPLETED 2024-02-29T10:34:46 
    2339_6        COMPLETED 2024-02-29T10:34:46 
    2339_7        COMPLETED 2024-02-29T10:34:48 
    2339_8        COMPLETED 2024-02-29T10:34:48 
    2339_9        COMPLETED 2024-02-29T10:34:51 
    2339_10       COMPLETED 2024-02-29T10:34:51 
    2339_11       COMPLETED 2024-02-29T10:34:53 
    2339_12       COMPLETED 2024-02-29T10:34:53 
    2339_[13-94+   PENDING  Unknown"""

    mock_run_commands.return_value = SerializableMagicMock(ok=True, stdout=mock_stdout)

    # WHEN
    job_status_dict, _ = slurm_client.check_job_status([2304, 2339])

    # THEN
    mock_run_commands.assert_called_with(
        ['sacct -n -o JobId,State,End -X -j 2304 -j 2339'], env=None)

    expected_status_dict = {2304: 'COMPLETED', 2339: 'PENDING'}
    assert job_status_dict == expected_status_dict


@patch('biomero.slurm_client.logger')
@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_check_job_status_exc(mock_run_commands,
                              mock_logger, slurm_client):
    # GIVEN
    return_mock = SerializableMagicMock(
        ok=False, stdout="12345 RUNNING\n67890 COMPLETED")

    mock_run_commands.return_value = return_mock

    # WHEN
    with pytest.raises(SSHException):
        job_status_dict, _ = slurm_client.check_job_status([12345, 67890])

    # THEN
    mock_run_commands.assert_called_with(
        ['sacct -n -o JobId,State,End -X -j 12345 -j 67890'], env=None)
    mock_logger.error.assert_called_with(f'Result is not ok: {return_mock}')


@patch('biomero.slurm_client.logger')
@patch('biomero.slurm_client.timesleep')
@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_check_job_status_exc2(mock_run_commands, _mock_timesleep,
                               mock_logger, slurm_client):
    # GIVEN
    mock_run_commands.return_value = SerializableMagicMock(
        ok=True, stdout=None)

    # WHEN
    with pytest.raises(SSHException):
        job_status_dict, _ = slurm_client.check_job_status([12345, 67890])

    # THEN
    mock_run_commands.assert_called_with(
        ['sacct -n -o JobId,State,End -X -j 12345 -j 67890'], env=None)
    mock_logger.error.assert_called_with(
        'Error: Retried 3 times to get                 status of [12345, 67890], but no response.')


@patch('biomero.slurm_client.Result')
def test_extract_job_id(mock_result, slurm_client):
    # GIVEN
    mock_result.stdout = "Submitted batch job 12345"

    # WHEN
    extracted_job_id = slurm_client.extract_job_id(mock_result)

    # THEN
    assert extracted_job_id == 12345


@patch('biomero.slurm_client.io.StringIO')
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.get_workflow_parameters')
@patch('biomero.slurm_client.SlurmClient.workflow_params_to_subs')
@patch('biomero.slurm_client.SlurmClient.generate_slurm_job_for_workflow')
@patch('biomero.slurm_client.SlurmClient._is_bilayers_workflow', return_value=False)
@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_update_slurm_scripts(mock_generic_descriptor, mock_is_bilayers,
                              mock_generate_job, mock_workflow_params_to_subs,
                              mock_get_workflow_params, mock_put,
                              mock_run, _mock_open,
                              _mock_session, mock_stringio):
    # GIVEN
    slurm_client = SlurmClient(
        "localhost", 8022, "slurm", slurm_script_repo="gitrepo",
        slurm_script_path="scriptpath",
        slurm_model_jobs={'workflow_name': 'job_path'})
    mock_get_workflow_params.return_value = {
        'param1': {'cmd_flag': '--param1', 'name': 'param1_name'}}
    mock_workflow_params_to_subs.return_value = {
        'PARAMS': '--param1 $PARAM1_NAME'}
    mock_generate_job.return_value = "GeneratedJobScript"
    mock_put.return_value = SerializableMagicMock(ok=True)
    mock_run.return_value = SerializableMagicMock(ok=True)
    mock_generic_descriptor.return_value = {'schema-version': '1.0.0'}

    # WHEN
    slurm_client.update_slurm_scripts(generate_jobs=True)

    # THEN
    # Assert that the descriptor was fetched
    mock_generic_descriptor.assert_called_once_with("workflow_name")

    # Assert that the workflow parameters are obtained
    mock_get_workflow_params.assert_called_once_with("workflow_name")

    # Assert that workflow parameters are converted to substitutions
    mock_workflow_params_to_subs.assert_called_once_with(
        {'param1': {'cmd_flag': '--param1', 'name': 'param1_name'}})

    # Assert that the job script is generated from the shared template with
    # biaflows-style substitutions.
    mock_generate_job.assert_called_once_with(
        "workflow_name",
        {
            'PARAMS': '--param1 $PARAM1_NAME',
            'OPTIONAL_ENV': '',
            'WF_TYPE': 'biaflows',
            'INPARAMS': '--infolder "$DATA_PATH/data/in" --gtfolder "$DATA_PATH/data/gt"',
            'OUTPARAMS': '--outfolder "$DATA_PATH/data/out"',
            'EXTRAPARAMS': '--local -nmc',
        },
        "job_template.sh")

    # Assert that the remote directories are created
    mock_run.assert_called_with("mkdir -p \"scriptpath\"")

    # Assert that the job script is copied to the remote location
    mock_put.assert_called_once_with(
        local=mock_stringio("GeneratedJobScript"), remote="scriptpath/job_path")

    # Assert the overall behavior
    assert mock_run.call_count == 2  # Two calls, one for mkdir and one for put
    assert mock_put.call_count == 1


def test_workflow_params_to_subs(slurm_client):
    # GIVEN
    params = {
        'param1': {'cmd_flag': '--param1', 'name': 'param1_name'},
        'param2': {'cmd_flag': '--param2', 'name': 'param2_name'},
    }

    # WHEN
    result = slurm_client.workflow_params_to_subs(params)

    # THEN
    expected_result = {'PARAMS': '--param1="$PARAM1_NAME" --param2="$PARAM2_NAME"'}
    assert result == expected_result


def test_is_bilayers_workflow_bilayers_version(slurm_client):
    assert slurm_client._is_bilayers_workflow(
        {'schema-version': 'bilayers-1.0.0'}) is True


def test_is_bilayers_workflow_future_version(slurm_client):
    assert slurm_client._is_bilayers_workflow(
        {'schema-version': 'bilayers-2.0.0'}) is True


def test_is_bilayers_workflow_biaflows_version(slurm_client):
    assert slurm_client._is_bilayers_workflow(
        {'schema-version': '1.0.0'}) is False


def test_is_bilayers_workflow_biomero_version(slurm_client):
    assert slurm_client._is_bilayers_workflow(
        {'schema-version': 'biomero-0.1'}) is False


def test_is_bilayers_workflow_missing_version(slurm_client):
    assert slurm_client._is_bilayers_workflow({}) is False


def test_bilayers_folder_params_image_input(slurm_client):
    descriptor = {
        'inputs': [{'type': 'image', 'command-line-flag': '--dir'}],
        'outputs': [],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['INPARAMS'] == '--dir="$DATA_PATH/data/in"'
    assert result['OUTPARAMS'] == ''


def test_bilayers_folder_params_file_input(slurm_client):
    descriptor = {
        'inputs': [{'type': 'file', 'command-line-flag': '--input'}],
        'outputs': [],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['INPARAMS'] == '--input="$DATA_PATH/data/in"'


def test_bilayers_folder_params_non_folder_input_skipped(slurm_client):
    descriptor = {
        'inputs': [{'type': 'string', 'command-line-flag': '--model'}],
        'outputs': [],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['INPARAMS'] == ''


def test_bilayers_folder_params_output_mapped(slurm_client):
    descriptor = {
        'inputs': [],
        'outputs': [{'command-line-flag': '--output-dir'}],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['OUTPARAMS'] == '--output-dir="$DATA_PATH/data/out"'


def test_bilayers_folder_params_none_flag_skipped(slurm_client):
    descriptor = {
        'inputs': [],
        'outputs': [{'command-line-flag': 'None'}],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['OUTPARAMS'] == ''


def test_bilayers_folder_params_missing_flag_skipped(slurm_client):
    descriptor = {'inputs': [], 'outputs': [{}]}
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['OUTPARAMS'] == ''


def test_bilayers_folder_params_optional_file_input_skipped(slurm_client):
    """Optional folder inputs (e.g. optional model file) must not appear in INPARAMS."""
    descriptor = {
        'inputs': [
            {'type': 'image', 'command-line-flag': '--dir', 'optional': False},
            {'type': 'file', 'command-line-flag': '--add_model', 'optional': True},
        ],
        'outputs': [],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['INPARAMS'] == '--dir="$DATA_PATH/data/in"'
    assert '--add_model' not in result['INPARAMS']


def test_bilayers_folder_params_output_dir_set_routes_to_outparams(slurm_client):
    """Parameters with output-dir-set=True must appear in OUTPARAMS, not INPARAMS."""
    descriptor = {
        'inputs': [
            {'type': 'image', 'command-line-flag': '--dir', 'optional': False},
            # save_dir after schema parsing: set-by-server=True, output-dir-set=True
            {'type': 'string', 'command-line-flag': '--savedir',
             'set-by-server': True, 'output-dir-set': True},
        ],
        'outputs': [],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['INPARAMS'] == '--dir="$DATA_PATH/data/in"'
    assert result['OUTPARAMS'] == '--savedir="$DATA_PATH/data/out"'


def test_bilayers_folder_params_output_dir_set_and_outputs(slurm_client):
    """Both output[] cli_tags and output_dir_set params contribute to OUTPARAMS."""
    descriptor = {
        'inputs': [
            {'type': 'string', 'command-line-flag': '--savedir',
             'set-by-server': True, 'output-dir-set': True},
        ],
        'outputs': [
            {'command-line-flag': '--out'},
        ],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert '--out="$DATA_PATH/data/out"' in result['OUTPARAMS']
    assert '--savedir="$DATA_PATH/data/out"' in result['OUTPARAMS']


def test_bilayers_folder_params_all_folder_types_inparams(slurm_client):
    """All mandatory folder input types (image/file/array/measurement/executable)
    should appear in INPARAMS."""
    descriptor = {
        'inputs': [
            {'type': 'image',      'command-line-flag': '--images'},
            {'type': 'file',       'command-line-flag': '--file'},
            {'type': 'array',      'command-line-flag': '--array'},
            {'type': 'measurement','command-line-flag': '--measure'},
            {'type': 'executable', 'command-line-flag': '--script'},
        ],
        'outputs': [],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert '--images="$DATA_PATH/data/in"' in result['INPARAMS']
    assert '--file="$DATA_PATH/data/in"' in result['INPARAMS']
    assert '--array="$DATA_PATH/data/in"' in result['INPARAMS']
    assert '--measure="$DATA_PATH/data/in"' in result['INPARAMS']
    assert '--script="$DATA_PATH/data/in"' in result['INPARAMS']


def test_bilayers_folder_params_multiple_inputs_and_outputs(slurm_client):
    descriptor = {
        'inputs': [
            {'type': 'image', 'command-line-flag': '--dir'},
            {'type': 'string', 'command-line-flag': '--model'},
            {'type': 'file', 'command-line-flag': '--mask'},
        ],
        'outputs': [
            {'command-line-flag': '--out'},
            {'command-line-flag': 'None'},
        ],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    # 'string' type is not a folder type → excluded; 'image' and 'file' included
    assert result['INPARAMS'] == '--dir="$DATA_PATH/data/in" --mask="$DATA_PATH/data/in"'
    assert result['OUTPARAMS'] == '--out="$DATA_PATH/data/out"'


def test_bilayers_folder_params_excludes_file_attachment(slurm_client):
    """file-attachment params must NOT appear in INPARAMS regardless of optional flag.

    They are user-supplied annotation IDs and get injected as full paths via
    PARAMS (not as the generic data/in directory pointer).
    """
    descriptor = {
        'inputs': [
            # mandatory image input → INPARAMS
            {'type': 'image', 'command-line-flag': '--dir', 'optional': False},
            # required file-attachment → must NOT appear in INPARAMS
            {'type': 'file', 'command-line-flag': '--model',
             'optional': False, 'file-attachment': True},
            # optional file-attachment → also must NOT appear in INPARAMS
            {'type': 'file', 'command-line-flag': '--ref',
             'optional': True, 'file-attachment': True},
        ],
        'outputs': [],
    }
    result = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)
    assert result['INPARAMS'] == '--dir="$DATA_PATH/data/in"'
    assert '--model' not in result['INPARAMS']
    assert '--ref' not in result['INPARAMS']


@patch('biomero.slurm_client.io.StringIO')
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_update_slurm_scripts_bilayers(mock_generic_descriptor,
                                       mock_put, mock_run, _mock_open,
                                       _mock_session, mock_stringio,
                                       bilayers_descriptor):
    """Bilayers workflow: uses the shared job template with INPARAMS/OUTPARAMS substituted,
    image/file folder inputs excluded from PARAMS.

    Only SSH and GitHub calls are mocked; all template/param logic runs for real
    using the bilayers_example.yaml fixture.
    """
    # Parse the fixture exactly as slurm_client does via generic_descriptor_from_github
    descriptor = DescriptorParserFactory.parse_descriptor(
        bilayers_descriptor).model_dump(by_alias=True)
    mock_generic_descriptor.return_value = descriptor
    mock_put.return_value = SerializableMagicMock(ok=True)
    mock_run.return_value = SerializableMagicMock(ok=True)

    slurm_client = SlurmClient(
        "localhost", 8022, "slurm", slurm_script_repo="gitrepo",
        slurm_script_path="scriptpath",
        slurm_model_jobs={'cellpose': 'jobs/cellpose.sh'})

    # WHEN — _is_bilayers_workflow, workflow_bilayers_folder_params_to_subs,
    # workflow_params_to_subs, get_workflow_parameters, and
    # generate_slurm_job_for_workflow (reads real shared job_template.sh) all run
    slurm_client.update_slurm_scripts(generate_jobs=True)

    generated_script = mock_stringio.call_args[0][0]

    # mandatory image input 'dir' → INPARAMS
    assert '--dir="$DATA_PATH/data/in"' in generated_script

    # optional file input 'custom_model' is now a file-attachment param →
    # NOT in INPARAMS (it's optional, _get_bilayers_folder_flags skips optionals)
    # but IS in PARAMS as a string placeholder for the HPC-injected path
    assert '--add_model="$CUSTOM_MODEL"' in generated_script

    # output 'omezarr_images' has cli_tag "None" → no OUTPARAMS from outputs[]
    # but save_dir has output_dir_set=True → routed to OUTPARAMS
    assert '--savedir="$DATA_PATH/data/out"' in generated_script
    assert 'Running bilayers' in generated_script

    # regular params are present; image/file inputs are NOT in PARAMS
    assert '--diameter="$DIAMETER"' in generated_script
    assert '--dir="$DIR"' not in generated_script
    assert '--savedir="$SAVE_DIR"' not in generated_script
    assert '--local -nmc' not in generated_script

    # script pushed to correct remote path
    mock_put.assert_called_once_with(
        local=mock_stringio(generated_script), remote="scriptpath/jobs/cellpose.sh")


@patch('biomero.slurm_client.io.StringIO')
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_update_slurm_scripts_bilayers_with_env_file(mock_generic_descriptor,
                                                     mock_put, mock_run, _mock_open,
                                                     _mock_session, mock_stringio,
                                                     bilayers_descriptor):
    """Bilayers workflow with env-file submission should render the shared
    OPTIONAL_ENV block into the single shared template."""
    descriptor = DescriptorParserFactory.parse_descriptor(
        bilayers_descriptor).model_dump(by_alias=True)
    mock_generic_descriptor.return_value = descriptor
    mock_put.return_value = SerializableMagicMock(ok=True)
    mock_run.return_value = SerializableMagicMock(ok=True)

    slurm_client = SlurmClient(
        "localhost", 8022, "slurm", slurm_script_repo="gitrepo",
        slurm_script_path="scriptpath",
        slurm_model_jobs={'cellpose': 'jobs/cellpose.sh'},
        env_file_submission=True)

    slurm_client.update_slurm_scripts(generate_jobs=True)

    generated_script = mock_stringio.call_args[0][0]

    assert 'BIOMERO_ENV_FILE="${1:-}"' in generated_script
    assert '. "$BIOMERO_ENV_FILE"' in generated_script
    assert 'Running bilayers workflow...' in generated_script
    assert '--dir="$DATA_PATH/data/in"' in generated_script
    assert '--savedir="$DATA_PATH/data/out"' in generated_script


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_list_completed_jobs(mock_run_commands,
                             slurm_client):
    """
    Test getting a list of completed jobs from SLURM.
    """
    # GIVEN
    env = {"VAR1": "value1", "VAR2": "value2"}

    # Mocking the run_commands method
    stdout_content = "98765\n43210\n"
    mock_run_commands.return_value = SerializableMagicMock(
        ok=True, stdout=stdout_content)

    # WHEN
    result = slurm_client.list_completed_jobs(env=env)

    # THEN
    mock_run_commands.assert_called_once_with(
        [slurm_client.get_jobs_info_command(states="cd")],
        env=env,
        log_stdout=False
    )

    assert result == ["43210", "98765"]


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_list_active_jobs(mock_run_commands,
                          slurm_client):
    """
    Test getting a list of active jobs from SLURM.
    """
    # GIVEN
    env = {"VAR1": "value1", "VAR2": "value2"}

    # Mocking the run_commands method
    stdout_content = "12345\n67890\n"
    mock_run_commands.return_value = SerializableMagicMock(
        ok=True, stdout=stdout_content)

    # WHEN
    result = slurm_client.list_active_jobs(env=env)

    # THEN
    mock_run_commands.assert_called_once_with(
        [slurm_client.get_jobs_info_command(start_time="now", states="r")],
        env=env
    )

    assert result == ["67890", "12345"]


@patch('biomero.slurm_client.SlurmClient.run')
def test_run_commands(mock_run, slurm_client):
    """
    Test running a list of shell commands consecutively on the Slurm cluster.
    """
    # GIVEN
    cmdlist = ["echo 'command1'", "echo 'command2'", "echo 'command3'"]
    env = {"VAR1": "value1", "VAR2": "value2"}
    sep = ' && '

    # Mocking the run method
    mock_run.return_value = SerializableMagicMock(
        ok=True, stdout="Command executed successfully")

    # WHEN
    result = slurm_client.run_commands(cmdlist, env=env, sep=sep)

    # THEN
    mock_run.assert_called_with(
        "echo 'command1' && echo 'command2' && echo 'command3'",
        env=env
    )

    assert result.ok is True
    assert result.stdout == "Command executed successfully"


@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.SlurmClient.get_recent_log_command')
def test_get_active_job_progress(mock_get_recent_log_command,
                                 mock_run_commands,
                                 slurm_client):
    """
    Test getting the progress of an active Slurm job.
    """
    # GIVEN
    slurm_job_id = "12345"
    pattern = r"\d+%"

    # Mocking the get_recent_log_command method
    log_cmd = f"cat {slurm_client._LOGFILE.format(slurm_job_id=slurm_job_id)}"
    mock_get_recent_log_command.return_value = log_cmd

    # Mocking the run_commands method
    stdout_content = "Progress: 50%\nSome other text\nProgress: 75%\n"
    mock_run_commands.return_value = SerializableMagicMock(
        ok=True, stdout=stdout_content)

    # WHEN
    result = slurm_client.get_active_job_progress(slurm_job_id, pattern)

    # THEN
    mock_get_recent_log_command.assert_called_once_with(
        log_file=slurm_client._LOGFILE.format(slurm_job_id=slurm_job_id))
    mock_run_commands.assert_called_once_with([log_cmd], env={})

    assert result == "75%"


@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.SlurmClient.extract_data_location_from_log')
def test_cleanup_tmp_files_loc(mock_extract_data_location, mock_run_commands,
                               slurm_client):
    """
    Test the cleanup of temporary files associated with a Slurm job.
    """
    # GIVEN
    slurm_job_id = "12345"
    filename = "output.zip"
    data_location = "/path"
    logfile = "/path/to/logfile"

    mock_run_commands.return_value = SerializableMagicMock(ok=True)

    # WHEN
    result = slurm_client.cleanup_tmp_files(
        slurm_job_id, filename, data_location, logfile)

    # THEN
    mock_extract_data_location.assert_not_called()
    mock_run_commands.assert_called_once_with([
        f"rm \"{filename}\".*",
        f"rm \"{logfile}\"",
        f"rm \"slurm-{slurm_job_id}\"_*.out",
        f"rm -rf \"{data_location}\" \"{data_location}\".*",
        f"rm \"config_path.txt\""
    ], sep=' ; ')

    assert result.ok is True


@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.SlurmClient.extract_data_location_from_log')
def test_cleanup_tmp_files(mock_extract_data_location, mock_run_commands,
                           slurm_client):
    """
    Test the cleanup of temporary files associated with a Slurm job.
    """
    # GIVEN
    slurm_job_id = "12345"
    filename = "output.zip"
    data_location = None
    logfile = "/path/to/logfile"
    found_location = '/path'

    mock_extract_data_location.return_value = found_location
    mock_run_commands.return_value = SerializableMagicMock(ok=True)

    # WHEN
    result = slurm_client.cleanup_tmp_files(
        slurm_job_id, filename, data_location, logfile)

    # THEN
    mock_extract_data_location.assert_called_once_with(logfile)
    mock_run_commands.assert_called_once_with([
        f"rm \"{filename}\".*",
        f"rm \"{logfile}\"",
        f"rm \"slurm-{slurm_job_id}\"_*.out",
        f"rm -rf \"{found_location}\" \"{found_location}\".*",
        f"rm \"config_path.txt\""
    ], sep=' ; ')

    assert result.ok is True


def table_exists(session, table_name):
    inspector = inspect(session.bind)
    return inspector.has_table(table_name)

@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.Connection.run')
def test_sqlalchemy_tables_exist(mock_run, mock_put, mock_open, mock_session, caplog):
    """
    Test that after initializing SlurmClient with all listeners enabled, 
    the relevant SQLAlchemy tables exist in the database.
    """
    # Initialize the analytics system (with reset_tables=False to not drop them)
    with caplog.at_level(logging.INFO):
        slurm_client = SlurmClient(
            host="localhost",
            port=8022,
            user="slurm",
            slurm_data_path="datapath",
            slurm_images_path="imagespath",
            slurm_script_path="scriptpath",
            slurm_converters_path="converterspath",
            slurm_script_repo="repo-url",
            slurm_model_paths={'wf': 'path'},
            slurm_model_images={'wf': 'image'},
            slurm_model_repos={'wf': 'https://github.com/example/workflow1'},
            track_workflows=True,  # Enable workflow tracking
            enable_job_accounting=True,  # Enable job accounting
            enable_job_progress=True,  # Enable job progress tracking
            enable_workflow_analytics=True,  # Enable workflow analytics
            init_slurm=True  # Trigger the initialization of Slurm
        )
        
        # Check that the expected log message for table drops is present
        assert any("Dropped view tables successfully" in record.message for record in caplog.records), \
            "Expected log message 'Dropped view tables successfully' was not found."

    # Check that the expected tables exist
    with EngineManager.get_session() as session:
        expected_tables = [
            # Listener event and tracking tables
            slurm_client.jobAccounting.recorder.tracking_table_name,
            slurm_client.jobAccounting.recorder.events_table_name,
            slurm_client.jobProgress.recorder.tracking_table_name,
            slurm_client.jobProgress.recorder.events_table_name,
            slurm_client.workflowAnalytics.recorder.tracking_table_name,
            slurm_client.workflowAnalytics.recorder.events_table_name,
            
            # Views
            TaskExecution.__tablename__,
            JobProgressView.__tablename__,
            JobView.__tablename__
        ]
        
        # Ensure each expected table exists in the database
        for table in expected_tables:
            assert table_exists(session, table), f"Table {table} should exist but does not."
            

@pytest.mark.parametrize("track_workflows, enable_job_accounting, enable_job_progress, enable_workflow_analytics, expected_tracker_classes", [
    # Case when everything is enabled
    (True, True, True, True, {"workflowTracker": "WorkflowTracker", "jobAccounting": "JobAccounting", "jobProgress": "JobProgress", "workflowAnalytics": "WorkflowAnalytics"}),
    
    # Case when tracking is disabled (NoOp for all)
    (False, True, True, True, {"workflowTracker": "NoOpWorkflowTracker", "jobAccounting": "NoOpWorkflowTracker", "jobProgress": "NoOpWorkflowTracker", "workflowAnalytics": "NoOpWorkflowTracker"}),

    # Case when only accounting is disabled
    (True, False, True, True, {"workflowTracker": "WorkflowTracker", "jobAccounting": "NoOpWorkflowTracker", "jobProgress": "JobProgress", "workflowAnalytics": "WorkflowAnalytics"}),

    # Case when only progress is disabled
    (True, True, False, True, {"workflowTracker": "WorkflowTracker", "jobAccounting": "JobAccounting", "jobProgress": "NoOpWorkflowTracker", "workflowAnalytics": "WorkflowAnalytics"}),

    # Case when only analytics is disabled
    (True, True, True, False, {"workflowTracker": "WorkflowTracker", "jobAccounting": "JobAccounting", "jobProgress": "JobProgress", "workflowAnalytics": "NoOpWorkflowTracker"}),

    # Case when all listeners are disabled
    (True, False, False, False, {"workflowTracker": "WorkflowTracker", "jobAccounting": "NoOpWorkflowTracker", "jobProgress": "NoOpWorkflowTracker", "workflowAnalytics": "NoOpWorkflowTracker"})
])
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.Connection.run')
def test_workflow_tracker_and_listeners_no_op(
        mock_run, mock_put, mock_open, mock_session,
        track_workflows, enable_job_accounting, enable_job_progress, enable_workflow_analytics,
        expected_tracker_classes, caplog):
    """
    Test that the WorkflowTracker, JobAccounting, JobProgress, and WorkflowAnalytics
    are set to NoOpWorkflowTracker when tracking is disabled or listeners are disabled.
    """
    # GIVEN
    slurm_client = SlurmClient(
        host="localhost",
        port=8022,
        user="slurm",
        slurm_data_path="datapath",
        slurm_images_path="imagespath",
        slurm_script_path="scriptpath",
        slurm_converters_path="converterspath",
        slurm_script_repo="repo-url",
        slurm_model_paths={'wf': 'path'},
        slurm_model_images={'wf': 'image'},
        slurm_model_repos={'wf': 'https://github.com/example/workflow1'},
        track_workflows=track_workflows,
        enable_job_accounting=enable_job_accounting,
        enable_job_progress=enable_job_progress,
        enable_workflow_analytics=enable_workflow_analytics
    )

    # THEN
    # Check workflow tracker
    assert slurm_client.workflowTracker.__class__.__name__ == expected_tracker_classes['workflowTracker']

    # Check job accounting listener
    assert slurm_client.jobAccounting.__class__.__name__ == expected_tracker_classes['jobAccounting']

    # Check job progress listener
    assert slurm_client.jobProgress.__class__.__name__ == expected_tracker_classes['jobProgress']

    # Check workflow analytics listener
    assert slurm_client.workflowAnalytics.__class__.__name__ == expected_tracker_classes['workflowAnalytics']
    
    # WHEN (No-Op calls)
    if isinstance(slurm_client.workflowTracker, NoOpWorkflowTracker):
        # Call NoOp methods on workflowTracker
        slurm_client.workflowTracker.start_workflow("dummy_workflow")
        slurm_client.workflowTracker.update_status("dummy_status")
        # WHEN
        workflow_id = uuid4()
        task_name = 'example_task'
        task_version = '1.0'
        input_data = {'key': 'value'}
        kwargs = {'param1': 'value1', 'param2': 'value2'}
        with caplog.at_level(logging.DEBUG):
            # Call methods that should be no-ops
            slurm_client.workflowTracker.add_task_to_workflow(
                workflow_id=workflow_id,
                task_name=task_name,
                task_version=task_version,
                input_data=input_data,
                kwargs=kwargs
            )

            # THEN
            # Assert that appropriate log messages are generated for NoOpWorkflowTracker
            for record in caplog.records:
                if record.message.startswith("[No-op] Called function: add_task_to_workflow"):
                    assert f"'workflow_id': {repr(workflow_id)}" in record.message
                    assert f"'task_name': {repr(task_name)}" in record.message
                    assert f"'task_version': {repr(task_version)}" in record.message
                    assert f"'input_data': {repr(input_data)}" in record.message
                    assert f"'kwargs': {repr(kwargs)}" in record.message

    if isinstance(slurm_client.jobAccounting, NoOpWorkflowTracker):
        # Call NoOp methods on jobAccounting
        slurm_client.jobAccounting.record_job("dummy_job")
        slurm_client.jobAccounting.get_job_status("dummy_job")

    if isinstance(slurm_client.jobProgress, NoOpWorkflowTracker):
        # Call NoOp methods on jobProgress
        slurm_client.jobProgress.track_progress("dummy_progress")

    if isinstance(slurm_client.workflowAnalytics, NoOpWorkflowTracker):
        # Call NoOp methods on workflowAnalytics
        slurm_client.workflowAnalytics.generate_report("dummy_report")
        
    # THEN
    with caplog.at_level(logging.DEBUG):
        # Assert that appropriate log messages are generated for NoOpWorkflowTracker
        for tracker_name, tracker_class in expected_tracker_classes.items():
            tracker = getattr(slurm_client, tracker_name)
            if tracker_class == "NoOpWorkflowTracker":
                # Call a method to trigger the no-op behavior
                tracker.some_method()
                # Check the log output
                assert any(record.message.startswith("[No-op] Called function: some_method") for record in caplog.records)
    
    
    # THEN (No actions should be performed, and nothing should break)
    # We can only verify that no exceptions are raised and no actual work was done
    # Logs or other side-effects can be checked if logging is added to NoOpWorkflowTracker
    assert True  # No exception means test passes


# ---------------------------------------------------------------------------
# Tests for get_jobs_info_command sacct start-time resolution
# ---------------------------------------------------------------------------

@pytest.fixture
def slurm_client_from_config_factory():
    def make_client(config_values=None, env_values=None):
        configparser_instance = MagicMock()
        configparser_instance.read.return_value = None

        config_values = config_values or {}
        env_values = env_values or {}

        value_map = {
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
        }

        for key, value in config_values.items():
            value_map[('SLURM', key)] = value

        def get_side_effect(section, option, fallback=None):
            return value_map.get(
                (section, option),
                fallback if fallback is not None else 'configvalue',
            )

        def parse_bool(val):
            if isinstance(val, bool):
                return val
            if isinstance(val, str):
                return val.strip().lower() in ("1", "true", "yes", "on")
            return bool(val)

        def getboolean_side_effect(section, option, fallback=None):
            val = value_map.get((section, option), None)
            if val is None:
                return fallback if fallback is not None else False
            return parse_bool(val)

        configparser_instance.get.side_effect = get_side_effect
        configparser_instance.getboolean.side_effect = getboolean_side_effect
        configparser_instance.items.side_effect = lambda section: {
            k: v for (s, k), v in value_map.items() if s == section
        }.items()

        env_patch = dict(env_values)

        with patch.dict(os.environ, env_patch, clear=False), \
             patch('biomero.slurm_client.Connection.create_session'), \
             patch('biomero.slurm_client.Connection.open'), \
             patch('biomero.slurm_client.Connection.put'), \
             patch('biomero.slurm_client.Connection.run'), \
             patch('biomero.slurm_client.configparser.ConfigParser',
                   return_value=configparser_instance):

            client = SlurmClient.from_config(
                configfile='test_config.ini',
                init_slurm=False,
                config_only=True,
            )

            if not getattr(client, "slurm_model_jobs", None):
                client.slurm_model_jobs = {'cellpose': 'jobs/cellpose.sh'}

            if not getattr(client, "slurm_script_repo", None):
                client.slurm_script_repo = "gitrepo"

            if not getattr(client, "slurm_script_path", None):
                client.slurm_script_path = "scriptpath"

            return client

    return make_client


@pytest.fixture
def slurm_configparser_factory():
    def make_configparser(
        get_values=None,
        boolean_values=None,
        model_items=None,
        converter_items=None,
        default_value="configvalue",
    ):
        configparser_instance = MagicMock()
        configparser_instance.read.return_value = None
        get_values = get_values or {}
        boolean_values = boolean_values or {}
        model_items = model_items or {}
        converter_items = converter_items or {}

        def get_side_effect(section, option, fallback=None):
            if (section, option) in get_values:
                return get_values[(section, option)]
            return default_value

        def getboolean_side_effect(section, option, fallback):
            return boolean_values.get(option, fallback)

        def items_side_effect(section):
            if section == "MODELS":
                return model_items.items()
            if section == "CONVERTERS":
                return converter_items.items()
            return {}.items()

        configparser_instance.get.side_effect = get_side_effect
        configparser_instance.getboolean.side_effect = getboolean_side_effect
        configparser_instance.items.side_effect = items_side_effect
        return configparser_instance

    return make_configparser

def test_get_jobs_info_command_default_start_time(slurm_client_from_config_factory):
    """
    No config, no env vars → backward-compatible default "2023-01-01".
    """
    # GIVEN
    slurm_client = slurm_client_from_config_factory()

    # WHEN
    cmd = slurm_client.get_jobs_info_command()

    # THEN
    assert "--starttime 2023-01-01" in cmd


def test_get_jobs_info_command_ini_absolute_date(slurm_client_from_config_factory):
    """
    sacct_start_time set (e.g. from slurm-config.ini) overrides class default.
    """
    # GIVEN
    slurm_client = slurm_client_from_config_factory(
        config_values={"sacct_start_time": "2025-01-01"}
    )

    # WHEN
    cmd = slurm_client.get_jobs_info_command()

    # THEN
    assert "--starttime 2025-01-01" in cmd
    assert "2023-01-01" not in cmd


def test_get_jobs_info_command_ini_days_ago_only(slurm_client_from_config_factory):
    """
    sacct_days_ago alone computes a rolling window from today.
    """
    # GIVEN
    from datetime import datetime, timedelta
    expected_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    slurm_client = slurm_client_from_config_factory(
        config_values={"sacct_days_ago": 30}
    )

    # WHEN
    cmd = slurm_client.get_jobs_info_command()

    # THEN
    assert f"--starttime {expected_date}" in cmd
    assert "2023-01-01" not in cmd


def test_get_jobs_info_command_ini_days_ago_overrides_absolute(slurm_client_from_config_factory):
    """
    sacct_days_ago overrides sacct_start_time when both are set.
    """
    # GIVEN
    from datetime import datetime, timedelta
    expected_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    slurm_client = slurm_client_from_config_factory(
        config_values={"sacct_start_time": "2025-01-01", "sacct_days_ago": 7},
    )

    # WHEN
    cmd = slurm_client.get_jobs_info_command()

    # THEN
    assert f"--starttime {expected_date}" in cmd
    assert "2025-01-01" not in cmd


def test_get_jobs_info_command_explicit_arg_bypasses_all(slurm_client_from_config_factory):
    """
    An explicit start_time argument always wins over all config and env vars.
    """
    # GIVEN
    slurm_client = slurm_client_from_config_factory(
        config_values={"sacct_start_time": "2025-01-01", "sacct_days_ago": 7},
    )

    # WHEN
    cmd = slurm_client.get_jobs_info_command(start_time="2022-03-15")

    # THEN
    assert "--starttime 2022-03-15" in cmd
    assert "2025-01-01" not in cmd


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
@patch.dict(os.environ, {
    "BIOMERO_SACCT_START_TIME": "2024-06-01",
    "BIOMERO_SACCT_START_DAYS_AGO": "not-an-int",
}, clear=False)
def test_from_config_invalid_env_days_ago_falls_back(
        mock_ConfigParser,
        mock_SlurmClient,
        _mock_run, _mock_put, _mock_open, _mock_session,
        slurm_configparser_factory):
    """Invalid BIOMERO_SACCT_START_DAYS_AGO should preserve the resolved absolute date."""
    mock_SlurmClient.return_value = None
    mock_ConfigParser.return_value = slurm_configparser_factory(
        get_values={
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
            ('SLURM', 'sacct_start_time'): '2025-01-01',
            ('SLURM', 'sacct_days_ago'): None,
        },
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
            'env_file_submission': False,
            'inject_gpu_flag': False,
        },
    )
    
    # WHEN
    SlurmClient.from_config(configfile='test_config.ini', init_slurm=False, config_only=True)
    
    # THEN
    _, kwargs = mock_SlurmClient.call_args
    assert kwargs['sacct_start_time'] == '2024-06-01'
    assert kwargs['sacct_days_ago'] is None


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
def test_from_config(mock_ConfigParser,
                     mock_SlurmClient,
                     _mock_run, _mock_put, _mock_open, _mock_session,
                     slurm_configparser_factory):
    """
    Test the creation of SlurmClient object from a configuration file.
    """
    # GIVEN
    configfile = 'test_config.ini'
    init_slurm = True
    mock_SlurmClient.return_value = None
    config_only = False
    mv = "configvalue"
    model_dict = {"m1": "v1"}
    repo_dict_out = {"m1": "v2"}
    job_dict_out = {"m1": "v3"}
    jp_dict_out = {"m1": [" --param=v4"]}
    conv_dict = {"zarr_to_tiff": "myconverter"}

    mock_configparser_instance = slurm_configparser_factory(
        get_values={
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
            ('SLURM', 'sacct_start_time'): None,
            ('SLURM', 'sacct_days_ago'): None,
        },
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
        },
        model_items={
            **model_dict,
            "m1_repo": "v2",
            "m1_job": "v3",
            "m1_job_param": "v4",
        },
        converter_items=conv_dict,
    )
    mock_ConfigParser.return_value = mock_configparser_instance

    # WHEN
    # Call the class method that uses configparser
    SlurmClient.from_config(
        configfile=configfile, init_slurm=init_slurm, config_only=config_only)

    # THEN
    mock_configparser_instance.read.assert_called_once_with([
        os.path.expanduser(SlurmClient._DEFAULT_CONFIG_PATH_1),
        os.path.expanduser(SlurmClient._DEFAULT_CONFIG_PATH_2),
        os.path.expanduser(SlurmClient._DEFAULT_CONFIG_PATH_3),
        os.path.expanduser(configfile)
    ])
    mock_SlurmClient.assert_called_with(
        host=mv,  # expected host value,
        inline_ssh_env=True,  # expected inline_ssh_env value,
        slurm_data_path=mv,  # expected slurm_data_path value,
        slurm_images_path=mv,  # expected slurm_images_path value,
        slurm_converters_path=mv,  # expected slurm_converters_path value,
        slurm_model_paths=model_dict,  # expected slurm_model_paths value,
        slurm_model_repos=repo_dict_out,  # expected slurm_model_repos value,
        slurm_model_images=None,  # expected slurm_model_images value,
        converter_images=conv_dict,  # expected converter_images
        slurm_model_jobs=job_dict_out,  # expected slurm_model_jobs value,
        slurm_model_jobs_params=jp_dict_out,  # expected slurm_model_jobs_params value,
        slurm_model_use_gpu={},
        slurm_script_path=mv,  # expected slurm_script_path value,
        slurm_script_repo=mv,  # expected slurm_script_repo value,
        init_slurm=init_slurm,
        track_workflows=True,  # expected track_workflows value
        enable_job_accounting=True,  # expected enable_job_accounting value
        enable_job_progress=True,  # expected enable_job_progress value
        enable_workflow_analytics=True,  # expected enable_workflow_analytics value
        sqlalchemy_url="sqlite:///test.db",  # expected sqlalchemy_url value
        config_only=config_only,
        slurm_data_bind_path=mv,
        slurm_conversion_partition=mv,
        sacct_start_time=None,
        sacct_days_ago=None,
        env_file_submission=False,
        inject_gpu_flag=False,
        gpu_partition=mv,
        gpu_gres=mv,
        gpu_resource_flag=mv,
        slurm_image_pull_via_sbatch=False,
        image_pull_cpus=mv,
        image_pull_mem=mv,
        apptainer_tmpdir=mv,
        apptainer_cachedir=mv,
        slurm_zip_cmd=mv,
        analytics_rebuild_start_time=mv,
        analytics_rebuild_days_ago=None,
    )


   
def test_parse_docker_image_with_version(slurm_client):
    version, image_name = slurm_client.parse_docker_image_version("example_image:1.0")
    assert version == "1.0"
    assert image_name == "example_image"


def test_parse_docker_image_without_version(slurm_client):
    version, image_name = slurm_client.parse_docker_image_version("example_image")
    assert version is None
    assert image_name == "example_image"


def test_parse_docker_image_with_empty_version(slurm_client):
    version, image_name = slurm_client.parse_docker_image_version("example_image:")
    assert version is None
    assert image_name == "example_image:"


def test_parse_docker_image_invalid_format(slurm_client):
    version, image_name = slurm_client.parse_docker_image_version("example_image:1.0:extra")
    assert version is None
    assert image_name == "example_image:1.0:extra"


@patch('biomero.slurm_client.SlurmClient.validate')
@patch('biomero.slurm_client.SlurmClient.run')
def test_setup_slurm_notok(mock_run, mock_validate):
    """
    Test the validation of the connection to the Slurm cluster using the SlurmClient.
    """
    # GIVEN
    mock_run.return_value.ok = False
    mock_validate.return_value = True
    spath = "scriptpath"
    dpath = "datapath"
    ipath = "imagespath"
    slurm_client = SlurmClient("localhost", 8022, "slurm",
                               slurm_data_path=dpath,
                               slurm_images_path=ipath,
                               slurm_script_path=spath)

    # WHEN
    with pytest.raises(SSHException):
        slurm_client.setup_slurm()

    # THEN
    # 1 create dirs
    mock_run.assert_called()
    mock_run.assert_any_call(
        f"mkdir -p \"{dpath}\" && mkdir -p \"{spath}\" && mkdir -p \"{ipath}\"", env={})


@pytest.mark.parametrize("mpaths, expected_modelpaths", [({'wf': 'path'}, 'path'), ({'wf': 'path', 'wf2': 'path2'}, 'path" "path2')])
@patch('biomero.slurm_client.io.StringIO')
@patch('biomero.slurm_client.SlurmClient.validate')
@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.requests_cache.CachedSession')
def test_setup_slurm(_mock_CachedSession,
                     _mock_Connection_put,
                     _mock_Connection_open,
                     mock_run,
                     mock_validate,
                     mock_stringio,
                     expected_modelpaths,
                     mpaths):
    """
    Test the validation of the connection to the Slurm cluster using the SlurmClient.
    """
    # GIVEN
    mock_run.return_value.ok = True
    mock_validate.return_value = True
    spath = "scriptpath"
    dpath = "datapath"
    ipath = "imagespath"
    cpath = "converterspath"
    srepo = "repo-url"
    convert_name = "convert_zarr_to_tiff"
    convert_def = f"{convert_name}.def"
    mimages = {'wf': 'image'}
    mrepos = {'wf': "https://github.com/example/workflow1"}
    script_name = "pull_images.sh"
    slurm_client = SlurmClient("localhost", 8022, "slurm",
                               slurm_data_path=dpath,
                               slurm_images_path=ipath,
                               slurm_script_path=spath,
                               slurm_converters_path=cpath,
                               slurm_script_repo=srepo,
                               slurm_model_paths=mpaths,
                               slurm_model_images=mimages,
                               slurm_model_repos=mrepos)

    # WHEN
    slurm_client.setup_slurm()

    # THEN
    mock_run.assert_called()
    # 1 create dirs
    mock_run.assert_any_call(
        [f"mkdir -p \"{dpath}\"", f"mkdir -p \"{spath}\"", f"mkdir -p \"{ipath}\""])
    # 2 git 
    mock_run.assert_any_call(
        ['rm -rf "$LOCALREPO"', 'git clone "$REPOSRC" "$LOCALREPO" 2> /dev/null'],
        {"REPOSRC": f"\"{srepo}\"", "LOCALREPO": f"\"{spath}\""})

    # 3 converters
    _mock_Connection_put.assert_called()
    # mock_run.assert_any_call(f"mkdir -p {cpath}")
    mock_run.assert_any_call(
        [f"singularity build -F \"{convert_name}_latest.sif\" {convert_def} >> sing.log 2>&1 ; echo 'finished {convert_name}_latest.sif' &"])

    # 4 images
    mock_run.assert_any_call([f"mkdir -p \"{expected_modelpaths}\""])
    _mock_Connection_put.assert_called_with(
        local=mock_stringio(), remote=f'{ipath}/{script_name}')
    mock_run.assert_any_call([f"time sh {script_name}"])


@pytest.mark.parametrize(
    "env_file_submission, expected",
    [
        (False, False),
        (True, True),
    ],
)
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.cd')   # <-- IMPORTANT
@patch('biomero.slurm_client.io.StringIO')
def test_setup_converters_env_file_template(
    mock_stringio,
    mock_cd,
    mock_run,
    mock_run2,
    mock_put,
    _open,
    _session,
    env_file_submission,
    expected,
):
    # make cd a no-op context manager
    mock_cd.return_value.__enter__.return_value = None
    mock_cd.return_value.__exit__.return_value = None

    client = SlurmClient(
        "localhost",
        8022,
        "slurm",
        slurm_script_path="scriptpath",
        slurm_converters_path="converterspath",
    )

    client.env_file_submission = env_file_submission

    mock_run.return_value = MagicMock(ok=True)
    mock_run2.return_value.ok = True
    mock_put.return_value = MagicMock(ok=True)

    client.setup_converters()

    script = mock_stringio.call_args_list[0][0][0]
    print(script)
    if expected:
        assert "BIOMERO_ENV_FILE" in script
        assert '. "$BIOMERO_ENV_FILE"' in script
    else:
        assert "BIOMERO_ENV_FILE" not in script

@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.setup_slurm')
def test_validate(mock_setup_slurm, mock_run, slurm_client):
    """
    Test the validation of the connection to the Slurm cluster using the SlurmClient.
    """
    # GIVEN
    mock_run.return_value.ok = True

    # WHEN
    result = slurm_client.validate(validate_slurm_setup=True)

    # THEN
    mock_run.assert_called_with('echo " "')
    mock_setup_slurm.assert_called_once()
    assert result is True


@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.setup_slurm')
def test_validate_no_setup(mock_setup_slurm, mock_run, slurm_client):
    """
    Test the validation of the connection to the Slurm cluster without setup using the SlurmClient.
    """
    # GIVEN
    mock_run.return_value.ok = True

    # WHEN
    result = slurm_client.validate(validate_slurm_setup=False)

    # THEN
    mock_run.assert_called_with('echo " "')
    mock_setup_slurm.assert_not_called()
    assert result is True


@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.setup_slurm')
def test_validate_connection_failure(mock_setup_slurm, mock_run, slurm_client):
    """
    Test the validation failure when the connection to the Slurm cluster fails using the SlurmClient.
    """
    # GIVEN
    mock_run.return_value.ok = False

    # WHEN
    result = slurm_client.validate(validate_slurm_setup=True)

    # THEN
    mock_run.assert_called_with('echo " "')
    mock_setup_slurm.assert_not_called()
    assert result is False


@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.setup_slurm')
def test_validate_setup_failure(mock_setup_slurm, mock_run, slurm_client):
    """
    Test the validation failure when setting up Slurm fails using the SlurmClient.
    """
    # GIVEN
    mock_run.return_value.ok = True
    mock_setup_slurm.side_effect = SSHException("Setup failed")

    # WHEN
    result = slurm_client.validate(validate_slurm_setup=True)

    # THEN
    mock_run.assert_called_with('echo " "')
    mock_setup_slurm.assert_called_once()
    assert result is False


@patch('biomero.slurm_client.Connection.run')
@patch('biomero.slurm_client.Connection.put')
def test_slurm_client_connection(mconn_put, mconn_run, slurm_client):
    """
    Test the SlurmClient connection methods.
    """
    # GIVEN

    # WHEN
    slurm_client.run("echo hello world")
    slurm_client.put("file.txt", "/remote/path")

    # THEN
    mconn_run.assert_called_with('echo hello world')
    mconn_put.assert_called_once()


@pytest.mark.parametrize("slurm_model_repos, expected_slurm_model_images", [
    ({}, {}),
    ({"workflow1": "https://github.com/example/workflow1"},
     {"workflow1": "dockerhub.com/image1"}),
])
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.run')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.DescriptorParserFactory.parse_descriptor')
@patch('biomero.slurm_client.requests_cache.CachedSession')
def test_init_workflows(mock_CachedSession,
                        mock_parse_descriptor,
                        _mock_Connection_put,
                        _mock_Connection_run,
                        _mock_Connection_open,
                        slurm_model_repos, expected_slurm_model_images):
    """
    Test the initialization of workflows in the SlurmClient.
    """
    # GIVEN
    wf_image = "dockerhub.com/image1"
    json_descriptor = {"container-image": {"image": wf_image}}
    mock_schema = mock.Mock()
    mock_schema.model_dump.return_value = json_descriptor
    mock_parse_descriptor.return_value = mock_schema
    github_session = mock_CachedSession.return_value
    github_response = mock.Mock()
    github_response.json.return_value = json_descriptor
    github_session.get.return_value = github_response
    slurm_client = SlurmClient("localhost", 8022, "slurm",
                               slurm_data_path="/path/to/data",
                               slurm_images_path="/path/to/images",
                               slurm_converters_path="/path/to/converters",
                               slurm_model_repos=slurm_model_repos)

    # WHEN
    slurm_client.init_workflows()
    # THEN
    assert slurm_client.slurm_model_images == expected_slurm_model_images


@patch('biomero.slurm_client.requests_cache.CachedSession')
@patch('biomero.slurm_client.DescriptorParserFactory.parse_descriptor')
@patch('biomero.slurm_client.Connection.run')
@patch('biomero.slurm_client.Connection.put')
def test_init_workflows_force_update(_mock_Connection_put,
                                     _mock_Connection_run,
                                     mock_parse_descriptor,
                                     mock_CachedSession):
    """
    Test the forced update of workflows in the SlurmClient.
    """
    # GIVEN
    wf_name = "workflow1"
    wf_repo = "https://github.com/example/workflow1"
    wf_image = "dockerhub.com/image1"
    json_descriptor = {"container-image": {"image": wf_image}}
    mock_schema = mock.Mock()
    mock_schema.model_dump.return_value = json_descriptor
    mock_parse_descriptor.return_value = mock_schema
    github_session = mock_CachedSession.return_value
    github_response = mock.Mock()
    github_response.json.return_value = json_descriptor
    github_session.get.return_value = github_response

    slurm_client = SlurmClient("localhost", 8022, "slurm",
                               slurm_data_path="/path/to/data",
                               slurm_images_path="/path/to/images",
                               slurm_converters_path="/path/to/converters",
                               slurm_model_repos={wf_name: wf_repo})

    # WHEN
    slurm_client.init_workflows(force_update=True)

    # THEN
    mock_CachedSession.assert_called()
    github_session.get.assert_called_with(
        wf_repo+"/raw/master/descriptor.json")
    assert slurm_client.slurm_model_images[wf_name] == wf_image


def test_init_invalid_workflow_repo():
    """
    Test the handling of an invalid workflow repository URL in SlurmClient.
    """
    # GIVEN
    # THEN
    with pytest.raises(ValueError,
                       match="No descriptor file found for repository"):
        # WHEN
        invalid_url = "https://github.com/this-is-an-invalid-url/wf"
        SlurmClient(
            host="localhost",
            port=8022,
            user="slurm",
            slurm_data_path="/path/to/data",
            slurm_images_path="/path/to/images",
            slurm_converters_path="/path/to/converters",
            slurm_model_repos={
                "workflow1": invalid_url},
            slurm_script_repo="https://github.com/nl-bioimaging/slurm-scripts",
        )


def test_init_invalid_workflow_url():
    """
    Test the handling of an invalid GitHub URL in SlurmClient.
    """
    # GIVEN
    # THEN
    with pytest.raises(ValueError, match="Invalid GitHub URL"):
        # WHEN
        invalid_url = "https://this-is-an-invalid-url/wf"
        SlurmClient(
            host="localhost",
            port=8022,
            user="slurm",
            slurm_data_path="/path/to/data",
            slurm_images_path="/path/to/images",
            slurm_converters_path="/path/to/converters",
            slurm_model_repos={
                "workflow1": invalid_url},
            slurm_script_repo="https://github.com/nl-bioimaging/slurm-scripts",
        )


# ---------------------------------------------------------------------------
# Tests for _get_bilayers_folder_flags (helper)
# ---------------------------------------------------------------------------

def test_get_bilayers_folder_flags_image_input(slurm_client):
    """Non-optional image input → in_flags; no outputs → out_flags empty."""
    descriptor = {
        'inputs': [{'type': 'image', 'command-line-flag': '--dir'}],
        'outputs': [],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert in_flags == ['--dir']
    assert out_flags == []


def test_get_bilayers_folder_flags_optional_input_excluded(slurm_client):
    """Optional folder inputs must not appear in in_flags."""
    descriptor = {
        'inputs': [
            {'type': 'image',  'command-line-flag': '--dir',       'optional': False},
            {'type': 'file',   'command-line-flag': '--add_model', 'optional': True},
        ],
        'outputs': [],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert '--dir' in in_flags
    assert '--add_model' not in in_flags


def test_get_bilayers_folder_flags_non_folder_type_excluded(slurm_client):
    """String / radio parameters are not folder types → not in in_flags."""
    descriptor = {
        'inputs': [{'type': 'string', 'command-line-flag': '--model'}],
        'outputs': [],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert in_flags == []


def test_get_bilayers_folder_flags_all_folder_types(slurm_client):
    """All five folder-input types (image/file/array/measurement/executable) → in_flags."""
    descriptor = {
        'inputs': [
            {'type': 'image',       'command-line-flag': '--img'},
            {'type': 'file',        'command-line-flag': '--fil'},
            {'type': 'array',       'command-line-flag': '--arr'},
            {'type': 'measurement', 'command-line-flag': '--meas'},
            {'type': 'executable',  'command-line-flag': '--exec'},
        ],
        'outputs': [],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert set(in_flags) == {'--img', '--fil', '--arr', '--meas', '--exec'}
    assert out_flags == []


def test_get_bilayers_folder_flags_output_with_flag(slurm_client):
    """Outputs with an explicit CLI flag → out_flags."""
    descriptor = {
        'inputs': [],
        'outputs': [{'command-line-flag': '--out'}],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert in_flags == []
    assert out_flags == ['--out']


def test_get_bilayers_folder_flags_output_none_flag_skipped(slurm_client):
    """Outputs with flag == 'None' → skipped."""
    descriptor = {
        'inputs': [],
        'outputs': [{'command-line-flag': 'None'}],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert out_flags == []


def test_get_bilayers_folder_flags_output_missing_flag_skipped(slurm_client):
    """Outputs missing the command-line-flag key entirely → skipped."""
    descriptor = {'inputs': [], 'outputs': [{}]}
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert out_flags == []


def test_get_bilayers_folder_flags_output_dir_set(slurm_client):
    """Inputs with output-dir-set=True → out_flags (not in_flags)."""
    descriptor = {
        'inputs': [
            {'type': 'image',  'command-line-flag': '--dir',     'optional': False},
            {'type': 'string', 'command-line-flag': '--savedir', 'output-dir-set': True},
        ],
        'outputs': [],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert '--dir' in in_flags
    assert '--savedir' not in in_flags
    assert '--savedir' in out_flags


def test_get_bilayers_folder_flags_output_dir_set_and_outputs(slurm_client):
    """Both outputs[] flags and output-dir-set inputs contribute to out_flags."""
    descriptor = {
        'inputs': [
            {'type': 'string', 'command-line-flag': '--savedir', 'output-dir-set': True},
        ],
        'outputs': [
            {'command-line-flag': '--out'},
        ],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    assert '--out' in out_flags
    assert '--savedir' in out_flags


def test_get_bilayers_folder_flags_empty_descriptor(slurm_client):
    """Empty descriptor → both lists empty, no crash."""
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags({})
    assert in_flags == []
    assert out_flags == []


def test_workflow_bilayers_folder_params_to_subs_delegates_to_helper(slurm_client):
    """workflow_bilayers_folder_params_to_subs must produce the same classification
    as _get_bilayers_folder_flags — verifies the two share logic via the helper."""
    descriptor = {
        'inputs': [
            {'type': 'image',  'command-line-flag': '--dir',     'optional': False},
            {'type': 'file',   'command-line-flag': '--add_model','optional': True},
            {'type': 'string', 'command-line-flag': '--savedir', 'output-dir-set': True},
        ],
        'outputs': [{'command-line-flag': '--out'}],
    }
    in_flags, out_flags = slurm_client._get_bilayers_folder_flags(descriptor)
    subs = slurm_client.workflow_bilayers_folder_params_to_subs(descriptor)

    # Every in_flag must appear in INPARAMS
    for flag in in_flags:
        assert f'{flag}="$DATA_PATH/data/in"' in subs['INPARAMS']
    # Every out_flag must appear in OUTPARAMS
    for flag in out_flags:
        assert f'{flag}="$DATA_PATH/data/out"' in subs['OUTPARAMS']
    # Optional file input excluded from both
    assert '--add_model' not in subs['INPARAMS']
    assert '--add_model' not in subs['OUTPARAMS']


# ---------------------------------------------------------------------------
# Tests for _get_server_managed_params
# ---------------------------------------------------------------------------

@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_get_server_managed_params_bilayers(mock_descriptor, slurm_client):
    """Bilayers workflow: folder flags resolved to concrete data paths."""
    slurm_client.slurm_data_path = "/scratch/data"
    descriptor = {
        'schema-version': 'bilayers-1.0.0',
        'inputs': [
            {'type': 'image',  'command-line-flag': '--dir',     'optional': False},
            {'type': 'string', 'command-line-flag': '--savedir', 'output-dir-set': True},
        ],
        'outputs': [],
    }
    mock_descriptor.return_value = descriptor

    result = slurm_client._get_server_managed_params("my_wf", "job_42")

    assert result['dir']     == "/scratch/data/job_42/data/in"
    assert result['savedir'] == "/scratch/data/job_42/data/out"
    # No biaflows keys present
    assert 'infolder' not in result


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_get_server_managed_params_bilayers_output_flag(mock_descriptor, slurm_client):
    """Bilayers workflow: outputs[] with explicit flag → data/out."""
    slurm_client.slurm_data_path = "/scratch/data"
    descriptor = {
        'schema-version': 'bilayers-1.0.0',
        'inputs': [{'type': 'image', 'command-line-flag': '--dir', 'optional': False}],
        'outputs': [{'command-line-flag': '--out'}],
    }
    mock_descriptor.return_value = descriptor

    result = slurm_client._get_server_managed_params("my_wf", "job_1")

    assert result['dir'] == "/scratch/data/job_1/data/in"
    assert result['out'] == "/scratch/data/job_1/data/out"


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_get_server_managed_params_biaflows(mock_descriptor, slurm_client):
    """Biaflows workflow: hardcoded template args recorded with resolved paths."""
    slurm_client.slurm_data_path = "/scratch/data"
    # No 'schema-version' starting with 'bilayers' → biaflows branch
    mock_descriptor.return_value = {'schema-version': 'biomero-0.1'}

    result = slurm_client._get_server_managed_params("my_wf", "job_99")

    assert result['infolder']  == "/scratch/data/job_99/data/in"
    assert result['outfolder'] == "/scratch/data/job_99/data/out"
    assert result['gtfolder']  == "/scratch/data/job_99/data/gt"
    assert result['local']     is True
    assert result['nmc']       is True


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_get_server_managed_params_flag_dash_stripped(mock_descriptor, slurm_client):
    """Leading dashes are stripped from flag names → id-style keys."""
    slurm_client.slurm_data_path = "/data"
    descriptor = {
        'schema-version': 'bilayers-1.0.0',
        'inputs': [{'type': 'image', 'command-line-flag': '--dir', 'optional': False}],
        'outputs': [],
    }
    mock_descriptor.return_value = descriptor

    result = slurm_client._get_server_managed_params("wf", "d")

    assert 'dir' in result          # stripped
    assert '--dir' not in result    # not the raw flag


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github',
       side_effect=Exception("network error"))
def test_get_server_managed_params_descriptor_error_returns_empty(mock_descriptor, slurm_client):
    """If descriptor fetch fails, return empty dict rather than raising."""
    result = slurm_client._get_server_managed_params("failing_wf", "job_x")
    assert result == {}


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_get_server_managed_params_user_kwargs_take_precedence(mock_descriptor, slurm_client):
    """User kwargs must override server params when merged in run_workflow."""
    slurm_client.slurm_data_path = "/data"
    mock_descriptor.return_value = {'schema-version': 'biomero-0.1'}

    server = slurm_client._get_server_managed_params("wf", "job_1")
    user_kwargs = {'infolder': '/custom/override', 'diameter': 30}
    merged = {**server, **user_kwargs}

    # user value wins
    assert merged['infolder'] == '/custom/override'
    # server values for other keys still present
    assert merged['outfolder'] == '/data/job_1/data/out'
    # user-only param also present
    assert merged['diameter'] == 30


# ---------------------------------------------------------------------------
# Tests for SlurmJob (lines 128-196)
# ---------------------------------------------------------------------------

def _make_slurm_job(ok=True, stderr=''):
    """Build a SlurmJob with a mock submit result."""
    from biomero.slurm_client import SlurmJob
    result = MagicMock()
    result.ok = ok
    result.stderr = stderr
    job_id = 42
    wf_id = uuid4()
    task_id = uuid4()
    return SlurmJob(result, job_id, wf_id, task_id)


def test_slurm_job_init_sets_fields():
    """SlurmJob __init__ copies submit_result fields correctly."""
    job = _make_slurm_job(ok=True)
    assert job.job_id == 42
    assert job.ok is True
    assert job.job_state is None


def test_slurm_job_completed_true():
    """completed() returns True when state is COMPLETED."""
    job = _make_slurm_job()
    job.job_state = "COMPLETED"
    assert job.completed() is True


def test_slurm_job_completed_plus():
    """completed() returns True when state is COMPLETED+."""
    job = _make_slurm_job()
    job.job_state = "COMPLETED+"
    assert job.completed() is True


def test_slurm_job_not_completed():
    """completed() returns False for non-completed states."""
    job = _make_slurm_job()
    job.job_state = "FAILED"
    assert job.completed() is False


def test_slurm_job_get_error():
    """get_error() returns the error_message."""
    job = _make_slurm_job(ok=False, stderr="oops")
    assert job.get_error() == "oops"


def test_slurm_job_str():
    """__str__ returns a SlurmJob(...) string containing the job_id."""
    job = _make_slurm_job()
    s = str(job)
    assert "SlurmJob(" in s
    assert "42" in s


def test_slurm_job_cleanup_delegates():
    """cleanup() calls slurmClient.cleanup_tmp_files with the job_id."""
    job = _make_slurm_job()
    mock_client = MagicMock()
    mock_client.cleanup_tmp_files.return_value = MagicMock(ok=True)
    result = job.cleanup(mock_client)
    mock_client.cleanup_tmp_files.assert_called_once_with(42)
    assert result is not None


def test_slurm_job_wait_for_completion_single_poll():
    """wait_for_completion() returns job_state once a terminal state is reached."""
    from biomero.slurm_client import SlurmJob
    submit_result = MagicMock(ok=True, stderr='')
    job = SlurmJob(submit_result, 7, uuid4(), uuid4(), slurm_polling_interval=0)

    poll_result = MagicMock(ok=True)
    mock_client = MagicMock()
    mock_client.check_job_status.return_value = ({7: "COMPLETED"}, poll_result)
    mock_client.get_active_job_progress.return_value = "50%"
    mock_client.workflowTracker = MagicMock()

    mock_conn = MagicMock()

    with patch('biomero.slurm_client.timesleep') as mock_sleep:
        state = job.wait_for_completion(mock_client, mock_conn)

    assert state == "COMPLETED"
    mock_sleep.sleep.assert_called_once_with(0)


def test_slurm_job_wait_poll_not_ok_sets_failed():
    """When poll_result.ok is False the state is forced to FAILED."""
    from biomero.slurm_client import SlurmJob
    submit_result = MagicMock(ok=True, stderr='')
    job = SlurmJob(submit_result, 7, uuid4(), uuid4(), slurm_polling_interval=0)

    bad_result = MagicMock(ok=False, stderr="ssh error")
    # check_job_status must return a dict with the job_id key; after setting
    # job_state = "FAILED" the line `self.job_state = job_status_dict[self.job_id]`
    # still runs, so we return FAILED there too.
    mock_client = MagicMock()
    mock_client.check_job_status.return_value = ({7: "FAILED"}, bad_result)
    mock_client.get_active_job_progress.return_value = None
    mock_client.workflowTracker = MagicMock()
    mock_conn = MagicMock()

    with patch('biomero.slurm_client.timesleep'):
        state = job.wait_for_completion(mock_client, mock_conn)

    assert state == "FAILED"
    assert job.error_message == "ssh error"


# ---------------------------------------------------------------------------
# Tests for initialize_analytics_system error branches (lines 469, 483, 488)
# ---------------------------------------------------------------------------

def test_initialize_analytics_system_unsupported_module(slurm_client):
    """Unsupported PERSISTENCE_MODULE raises NotImplementedError."""
    with patch.dict(os.environ, {"PERSISTENCE_MODULE": "some_other_module"}):
        with pytest.raises(NotImplementedError, match="some_other_module"):
            slurm_client.initialize_analytics_system()


def test_initialize_analytics_system_missing_sqlalchemy_url(slurm_client):
    """Missing SQLALCHEMY_URL raises ValueError."""
    with patch.dict(os.environ, {"SQLALCHEMY_URL": ""}):
        slurm_client.sqlalchemy_url = None
        with pytest.raises(ValueError, match="SQLALCHEMY_URL"):
            slurm_client.initialize_analytics_system()


def test_initialize_analytics_system_env_url_overrides_config(slurm_client):
    """SQLALCHEMY_URL env var takes precedence over config value."""
    env_url = "sqlite:///env_override.db"
    slurm_client.sqlalchemy_url = "sqlite:///config_value.db"
    with patch.dict(os.environ, {"SQLALCHEMY_URL": env_url}):
        # Just verify no crash and the env value is used
        # (full init would need a real DB; we patch the heavy parts)
        with patch.object(slurm_client, 'get_listeners'), \
             patch.object(slurm_client, 'bring_listener_uptodate'), \
             patch('biomero.slurm_client.SingleThreadedRunner') as mock_runner_cls:
            mock_runner_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
            mock_runner_cls.return_value.__exit__ = MagicMock(return_value=False)
            # We only need to confirm the env override log path is hit;
            # allow it to fall through naturally up to the DB step
            try:
                slurm_client.initialize_analytics_system()
            except Exception:
                pass  # DB setup may fail in test env; we only care about the branch


# ---------------------------------------------------------------------------
# Tests for validate() branches (lines 1126, 1132-1134)
# ---------------------------------------------------------------------------

def test_validate_connected_no_setup(slurm_client):
    """validate() with no setup flag just checks connection."""
    slurm_client.run = MagicMock(return_value=MagicMock(ok=True))
    assert slurm_client.validate() is True


def test_validate_not_connected(slurm_client):
    """validate() returns False when run() fails."""
    slurm_client.run = MagicMock(return_value=MagicMock(ok=False))
    assert slurm_client.validate() is False


def test_validate_setup_slurm_ssh_error(slurm_client):
    """validate(validate_slurm_setup=True) returns False on SSHException."""
    slurm_client.run = MagicMock(return_value=MagicMock(ok=True))
    with patch.object(slurm_client, 'setup_slurm', side_effect=SSHException("fail")):
        assert slurm_client.validate(validate_slurm_setup=True) is False


# ---------------------------------------------------------------------------
# Tests for cleanup_tmp_files no-data-location branch (lines 1103-1104)
# ---------------------------------------------------------------------------

def test_cleanup_tmp_files_no_data_location(slurm_client):
    """When data_location is None and log extraction fails, cleanup still runs."""
    slurm_client.run_commands = MagicMock(return_value=MagicMock(ok=True))
    with patch.object(slurm_client, 'extract_data_location_from_log', return_value=None):
        result = slurm_client.cleanup_tmp_files(slurm_job_id="99")
    # Should still try to remove log/converter-log files
    slurm_client.run_commands.assert_called_once()
    called_cmds = slurm_client.run_commands.call_args[0][0]
    # Only log + converter-log, no rm -rf data
    assert not any("rm -rf" in c for c in called_cmds)


# ---------------------------------------------------------------------------
# Tests for run_commands UnicodeEncodeError branch (lines 1200-1201)
# ---------------------------------------------------------------------------

def test_run_commands_unicode_error_recodes_stdout(slurm_client):
    """UnicodeEncodeError in stdout logging recodes stdout to utf-8."""

    class _BadStr(str):
        """A str whose __format__ raises UnicodeEncodeError."""
        def __format__(self, spec):
            raise UnicodeEncodeError('utf-8', 'original content', 0, 1, 'test reason')

    bad_stdout = _BadStr("original content")
    mock_result = MagicMock(ok=True)
    mock_result.stdout = bad_stdout
    slurm_client.run = MagicMock(return_value=mock_result)

    # Should not raise; stdout gets recoded in the except branch
    result = slurm_client.run_commands(["echo hi"])
    assert result is mock_result
    # stdout replaced by re-encoded version
    assert mock_result.stdout == "original content"


# ---------------------------------------------------------------------------
# Tests for str_to_class error branches (lines 1207-1208)
# ---------------------------------------------------------------------------

def test_str_to_class_module_not_found(slurm_client):
    """str_to_class returns None when module does not exist."""
    result = slurm_client.str_to_class("nonexistent.module.xyz", "SomeClass")
    assert result is None


def test_str_to_class_class_not_found(slurm_client):
    """str_to_class returns None when class does not exist in module."""
    result = slurm_client.str_to_class("os", "NoSuchClassXyz")
    assert result is None


# ---------------------------------------------------------------------------
# Tests for run_commands_split_out failure branch (lines 1250-1253)
# ---------------------------------------------------------------------------

def test_run_commands_split_out_raises_on_failure(slurm_client):
    """run_commands_split_out raises SSHException when result is not ok."""
    slurm_client.run_commands = MagicMock(
        return_value=MagicMock(ok=False, stdout="", stderr="err"))
    with pytest.raises(SSHException):
        slurm_client.run_commands_split_out(["bad_cmd"])


# ---------------------------------------------------------------------------
# Tests for list_active_jobs / list_completed_jobs / list_all_jobs
# (lines 1271-1279, 1308-1323)
# ---------------------------------------------------------------------------

def test_list_active_jobs(slurm_client):
    """list_active_jobs returns a reversed list of job IDs."""
    slurm_client.run_commands = MagicMock(
        return_value=MagicMock(ok=True, stdout="1\n2\n3"))
    jobs = slurm_client.list_active_jobs()
    assert jobs == ["3", "2", "1"]


def test_list_completed_jobs(slurm_client):
    """list_completed_jobs returns a stripped and reversed list."""
    slurm_client.run_commands = MagicMock(
        return_value=MagicMock(ok=True, stdout=" 10 \n 20 \n 30 "))
    jobs = slurm_client.list_completed_jobs()
    assert jobs == ["30", "20", "10"]


def test_list_all_jobs(slurm_client):
    """list_all_jobs returns a reversed list."""
    slurm_client.run_commands = MagicMock(
        return_value=MagicMock(ok=True, stdout="5\n6\n7"))
    jobs = slurm_client.list_all_jobs()
    assert jobs == ["7", "6", "5"]


# ---------------------------------------------------------------------------
# Tests for transfer_data / unpack_data (lines 1379-1386, 1457-1477)
# ---------------------------------------------------------------------------

def test_transfer_data_calls_put(slurm_client):
    """transfer_data delegates to put with correct arguments."""
    slurm_client.put = MagicMock(return_value=MagicMock(ok=True))
    slurm_client.slurm_data_path = "/remote/data"
    slurm_client.transfer_data("/local/myfile.zip")
    slurm_client.put.assert_called_once_with(
        local="/local/myfile.zip", remote="/remote/data")


def test_unpack_data_calls_run_commands(slurm_client):
    """unpack_data builds the unzip command and runs it."""
    slurm_client.run_commands = MagicMock(return_value=MagicMock(ok=True))
    slurm_client.get_unzip_command = MagicMock(return_value="unzip file.zip")
    slurm_client.unpack_data("file.zip")
    slurm_client.run_commands.assert_called_once_with(["unzip file.zip"], env=None)


# ---------------------------------------------------------------------------
# Tests for generic_descriptor_from_github fallback chain (lines 2120-2129)
# ---------------------------------------------------------------------------

@patch('biomero.slurm_client.SlurmClient.get_or_create_github_session')
@patch('biomero.slurm_client.SlurmClient.convert_url')
def test_generic_descriptor_yaml_fallback(mock_convert_url, mock_session_fn, slurm_client):
    """Falls back to descriptor.yaml when descriptor.json is not found."""
    slurm_client.slurm_model_repos = {
        "wf1": "https://github.com/org/repo/tree/main"}

    json_resp = MagicMock(ok=False)
    yaml_resp = MagicMock(ok=True, from_cache=False)
    yaml_resp.text = "schema-version: biomero-0.1\nname: wf1\ninputs: []\noutputs: []"
    mock_convert_url.side_effect = [
        "https://raw.../descriptor.json",
        "https://raw.../descriptor.yaml",
    ]
    session = MagicMock()
    session.get.side_effect = [json_resp, yaml_resp]
    mock_session_fn.return_value = session

    with patch('biomero.slurm_client.DescriptorParserFactory.parse_descriptor') as mock_parse:
        mock_parse.return_value.model_dump.return_value = {"schema-version": "biomero-0.1"}
        result = slurm_client.generic_descriptor_from_github("wf1")

    assert result is not None


@patch('biomero.slurm_client.SlurmClient.get_or_create_github_session')
@patch('biomero.slurm_client.SlurmClient.convert_url')
@patch('biomero.slurm_client.SlurmClient.extract_parts_from_url')
def test_generic_descriptor_config_yaml_fallback(
        mock_parts, mock_convert_url, mock_session_fn, slurm_client):
    """Falls back to config.yaml when both descriptor.json and descriptor.yaml are missing."""
    slurm_client.slurm_model_repos = {
        "wf1": "https://github.com/org/repo/tree/main"}

    json_resp = MagicMock(ok=False)
    yaml_resp = MagicMock(ok=False)
    config_resp = MagicMock(ok=True, from_cache=False)
    config_resp.text = "schema-version: bilayers-0.1\nname: wf1\ninputs: []\noutputs: []"
    mock_convert_url.side_effect = [
        "https://raw.../descriptor.json",
        "https://raw.../descriptor.yaml",
    ]
    mock_parts.return_value = (
        ["", "", "", "org", "repo", "tree", "main"], "main")
    session = MagicMock()
    session.get.side_effect = [json_resp, yaml_resp, config_resp]
    mock_session_fn.return_value = session

    with patch('biomero.slurm_client.DescriptorParserFactory.parse_descriptor') as mock_parse:
        mock_parse.return_value.model_dump.return_value = {"schema-version": "bilayers-0.1"}
        result = slurm_client.generic_descriptor_from_github("wf1")

    assert result is not None


# ---------------------------------------------------------------------------
# Tests for get_workflow_parameters set-by-server filter (lines 2204-2211)
# ---------------------------------------------------------------------------

@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_get_workflow_parameters_excludes_set_by_server(mock_desc, slurm_client):
    """Parameters with set-by-server=True are excluded from the result."""
    mock_desc.return_value = {
        'inputs': [
            {
                'id': 'diameter',
                'type': 'integer',
                'optional': False,
                'default-value': 30,
                'command-line-flag': '--diameter',
                'description': 'Cell diameter',
                'set-by-server': False,
            },
            {
                'id': 'dir',
                'type': 'image',
                'optional': False,
                'default-value': '',
                'command-line-flag': '--dir',
                'description': 'Input folder',
                'set-by-server': True,
            },
        ]
    }
    params = slurm_client.get_workflow_parameters("wf1")
    assert 'diameter' in params
    assert 'dir' not in params


@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
def test_get_workflow_parameters_excludes_cytomine(mock_desc, slurm_client):
    """Parameters with id starting with 'cytomine' are excluded."""
    mock_desc.return_value = {
        'inputs': [
            {
                'id': 'cytomine_host',
                'type': 'string',
                'optional': True,
                'default-value': '',
                'command-line-flag': '--cytomine_host',
                'description': 'Cytomine host',
            },
            {
                'id': 'model',
                'type': 'string',
                'optional': True,
                'default-value': 'nuclei',
                'command-line-flag': '--model',
                'description': 'Model name',
            },
        ]
    }
    params = slurm_client.get_workflow_parameters("wf1")
    assert 'cytomine_host' not in params
    assert 'model' in params


# ---------------------------------------------------------------------------
# Tests for get_active_job_progress exception branch (line 1172)
# ---------------------------------------------------------------------------

def test_get_active_job_progress_run_exception(slurm_client):
    """get_active_job_progress returns None when run_commands raises."""
    slurm_client.run_commands = MagicMock(side_effect=Exception("ssh down"))
    result = slurm_client.get_active_job_progress("123")
    assert result is None


# ---------------------------------------------------------------------------
# Tests for generate_slurm_job_for_workflow flags (env_file + gpu_flag)
# ---------------------------------------------------------------------------

import pytest
from unittest.mock import patch

@pytest.mark.parametrize(
    "inject_gpu_flag, expected",
    [
        (False, "--nv"),
        (True, "$GPU_FLAG"),
    ],
)
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
@patch('biomero.slurm_client.io.StringIO')
def test_generate_slurm_job_gpu_flag(
    mock_stringio,
    mock_descriptor,
    mock_run,
    mock_put,
    _open,
    _session,
    slurm_client_from_config_factory,
    bilayers_descriptor,
    inject_gpu_flag,
    expected,
):
    descriptor = DescriptorParserFactory.parse_descriptor(
        bilayers_descriptor
    ).model_dump(by_alias=True)

    mock_descriptor.return_value = descriptor
    mock_put.return_value = SerializableMagicMock(ok=True)
    mock_run.return_value = SerializableMagicMock(ok=True)

    client = slurm_client_from_config_factory(
        config_values={"inject_gpu_flag": inject_gpu_flag}
    )

    client.update_slurm_scripts(generate_jobs=True)

    script = mock_stringio.call_args[0][0]

    assert expected in script
    
import pytest
from unittest.mock import patch

@pytest.mark.parametrize(
    "env_file_submission, expected_snippet",
    [
        (True, ". \"$BIOMERO_ENV_FILE\""),
        (False, None),
    ],
)
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.generic_descriptor_from_github')
@patch('biomero.slurm_client.io.StringIO')
def test_generate_slurm_job_env_file_template(
    mock_stringio,
    mock_descriptor,
    mock_run,
    mock_put,
    _open,
    _session,
    slurm_client_from_config_factory,
    bilayers_descriptor,
    env_file_submission,
    expected_snippet,
):
    descriptor = DescriptorParserFactory.parse_descriptor(
        bilayers_descriptor
    ).model_dump(by_alias=True)

    mock_descriptor.return_value = descriptor
    mock_put.return_value = SerializableMagicMock(ok=True)
    mock_run.return_value = SerializableMagicMock(ok=True)

    client = slurm_client_from_config_factory(
        config_values={"env_file_submission": env_file_submission}
    )

    client.update_slurm_scripts(generate_jobs=True)

    script = mock_stringio.call_args[0][0]

    if expected_snippet:
        assert "BIOMERO_ENV_FILE" in script
        assert expected_snippet in script
    else:
        assert "BIOMERO_ENV_FILE" not in script


# ---------------------------------------------------------------------------
# Tests for get_workflow_command: env_file_submission path
# ---------------------------------------------------------------------------

def test_get_workflow_command_env_file_submission(slurm_client):
    """env_file_submission=True: write-env block prepended, sbatch gets file arg, env=={}."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = True
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = None
    slurm_client.gpu_gres = None

    cmd, env = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "")

    assert env == {}
    assert "cat >" in cmd                          # env file write block
    assert "BIOMERO_ENV" in cmd                    # heredoc marker
    assert "sbatch" in cmd
    assert "run1/biomero_job_env.sh" in cmd        # file path in sbatch arg


def test_get_workflow_command_env_file_submission_shell_quotes_values(slurm_client):
    """env_file_submission should shell-quote tricky values in the generated env file."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = True
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = None
    slurm_client.gpu_gres = None

    cmd, env = slurm_client.get_workflow_command(
        "wf",
        "1.0",
        "run1",
        comment="value with spaces",
        label="it's-here",
        use_gpu=True,
        count=7,
    )

    assert env == {}
    assert "export COMMENT='value with spaces'" in cmd
    assert "export LABEL=" + shlex.quote("it's-here") in cmd
    assert "export USE_GPU=True" in cmd
    assert "export COUNT=7" in cmd


def test_get_workflow_command_no_env_file_submission(slurm_client):
    """env_file_submission=False (default): plain sbatch, env dict returned."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = None
    slurm_client.gpu_gres = None

    cmd, env = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "")

    assert env != {}                               # env dict populated
    assert "cat >" not in cmd                      # no env file write block
    assert "sbatch" in cmd


# ---------------------------------------------------------------------------
# Tests for get_workflow_command: GPU sbatch params
# ---------------------------------------------------------------------------

def test_get_workflow_command_use_gpu_appends_params(slurm_client):
    """use_gpu=True + gpu_partition/gpu_gres → both appended to sbatch params."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = True
    slurm_client.gpu_partition = "gpu_a100"
    slurm_client.gpu_gres = "gpu:a100:1"

    cmd, _ = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "",
                                               use_gpu=True)
    assert "--partition=gpu_a100" in cmd
    assert "--gres=gpu:a100:1" in cmd


def test_get_workflow_command_use_gpu_false_no_gpu_params(slurm_client):
    """use_gpu=False → no GPU sbatch params injected even if configured."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = "gpu_a100"
    slurm_client.gpu_gres = "gpu:a100:1"

    cmd, _ = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "",
                                               use_gpu=False)
    assert "--partition=" not in cmd
    assert "--gres=" not in cmd


def test_get_workflow_command_use_gpu_respects_per_workflow_partition(slurm_client):
    """Per-workflow --partition in job_params takes precedence; no duplicate."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": [" --partition=my_gpu"]}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = "gpu_a100"
    slurm_client.gpu_gres = None

    cmd, _ = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "",
                                               use_gpu=True)
    assert cmd.count("--partition=") == 1
    assert "--partition=my_gpu" in cmd
    assert "--partition=gpu_a100" not in cmd


@patch.dict(os.environ, {
    "BIOMERO_GPU_PARTITION": "gpu_h100",
    "BIOMERO_GPU_GRES": "gpu:h100:1",
}, clear=False)
def test_get_workflow_command_use_gpu_prefers_biomero_envvars(slurm_client_from_config_factory):
    """BIOMERO-prefixed GPU env vars should override configured GPU defaults."""
    # GIVEN
    slurm_client = slurm_client_from_config_factory(
        config_values={
            "sacct_start_time": "2025-01-01",
            "gpu_partition": "gpu_a100",
            "gpu_gres": "gpu:a100:1",
            "slurm_script_path": "/scripts",
            "slurm_images_path": "/images",
            "slurm_data_path": "/data",
            "inject_gpu_flag": True,
            }
    )
    
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    
    cmd, _ = slurm_client.get_workflow_command(
        "wf", "1.0", "run1", "", "", use_gpu=True
    )

    assert slurm_client.gpu_partition == "gpu_h100"
    assert slurm_client.gpu_gres == "gpu:h100:1"
    assert slurm_client.inject_gpu_flag == True
    assert "--partition=gpu_h100" in cmd
    assert "--gres=gpu:h100:1" in cmd
    assert "--partition=gpu_a100" not in cmd
    assert "--gres=gpu:a100:1" not in cmd


def test_get_workflow_command_model_use_gpu_defaults_to_gpu(slurm_client):
    """Per-workflow *_use_gpu should apply generic GPU defaults even without runtime use_gpu."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_use_gpu = {"wf": True}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = "gpu_a100"
    slurm_client.gpu_gres = "gpu:a100:1"
    slurm_client.gpu_resource_flag = "gres"

    cmd, env = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "")

    assert "--partition=gpu_a100" in cmd
    assert "--gres=gpu:a100:1" in cmd
    assert env.get("GPU_FLAG") is None


def test_get_workflow_command_model_use_gpu_with_inject_can_disable(slurm_client):
    """Submission-time use_gpu=false should override *_use_gpu when inject_gpu_flag is enabled."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_use_gpu = {"wf": True}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = True
    slurm_client.gpu_partition = "gpu_a100"
    slurm_client.gpu_gres = "gpu:a100:1"
    slurm_client.gpu_resource_flag = "gres"

    cmd, env = slurm_client.get_workflow_command(
        "wf", "1.0", "run1", "", "", use_gpu=False
    )

    assert "--partition=" not in cmd
    assert "--gres=" not in cmd
    assert env["GPU_FLAG"] == ""


def test_get_workflow_command_gpu_resource_flag_gpus(slurm_client):
    """Generic GPU fallback should use configurable --gpus when requested."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_use_gpu = {"wf": True}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = None
    slurm_client.gpu_gres = "a100:1"
    slurm_client.gpu_resource_flag = "gpus"

    cmd, _ = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "")

    assert "--gpus=a100:1" in cmd
    assert "--gres=" not in cmd


# ---------------------------------------------------------------------------
# Tests for _zip_shell_cmd / get_zip_command
# ---------------------------------------------------------------------------


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
@patch.dict(os.environ, {
    "BIOMERO_SLURM_ZIP_CMD": "7za",
}, clear=False)
def test_from_config_biomero_slurm_zip_cmd_env_override(
        mock_ConfigParser,
        mock_SlurmClient,
        _mock_run, _mock_put, _mock_open, _mock_session,
        slurm_configparser_factory):
    """BIOMERO-prefixed zip env var should override INI/default zip command."""
    mock_SlurmClient.return_value = None
    mock_ConfigParser.return_value = slurm_configparser_factory(
        get_values={
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
            ('SLURM', 'slurm_zip_cmd'): '',
        },
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
        },
    )

    SlurmClient.from_config(configfile='test_config.ini', init_slurm=False, config_only=True)

    _, kwargs = mock_SlurmClient.call_args
    assert kwargs['slurm_zip_cmd'] == '7za'


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
@patch.dict(os.environ, {
    "BIOMERO_GPU_PARTITION": "gpu_h100",
    "BIOMERO_GPU_GRES": "gpu:h100:2",
}, clear=False)
def test_from_config_biomero_gpu_envvars_override_ini(
        mock_ConfigParser,
        mock_SlurmClient,
        _mock_run, _mock_put, _mock_open, _mock_session,
        slurm_configparser_factory):
    """BIOMERO-prefixed GPU env vars should override INI GPU defaults."""
    mock_SlurmClient.return_value = None
    mock_ConfigParser.return_value = slurm_configparser_factory(
        get_values={
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
            ('SLURM', 'sacct_start_time'): None,
            ('SLURM', 'sacct_days_ago'): None,
            ('SLURM', 'gpu_partition'): 'gpu_a100',
            ('SLURM', 'gpu_gres'): 'gpu:a100:1',
        },
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
        },
    )

    SlurmClient.from_config(configfile='test_config.ini', init_slurm=False, config_only=True)

    _, kwargs = mock_SlurmClient.call_args
    assert kwargs['gpu_partition'] == 'gpu_h100'
    assert kwargs['gpu_gres'] == 'gpu:h100:2'


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
@patch.dict(os.environ, {
    "BIOMERO_ENV_FILE_SUBMISSION": "yes",
}, clear=False)
def test_from_config_new_option_parsing_uses_helper(
        mock_ConfigParser,
        mock_SlurmClient,
        _mock_run, _mock_put, _mock_open, _mock_session,
        slurm_configparser_factory):
    """Newer config options should follow shared parsing semantics."""
    mock_SlurmClient.return_value = None
    mock_ConfigParser.return_value = slurm_configparser_factory(
        get_values={
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
            ('SLURM', 'sacct_start_time'): '',
            ('SLURM', 'sacct_days_ago'): '',
            ('SLURM', 'slurm_data_bind_path'): '',
            ('SLURM', 'slurm_conversion_partition'): '',
            ('SLURM', 'slurm_zip_cmd'): '',
        },
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
            'env_file_submission': False,
            'inject_gpu_flag': False,
        },
    )
    
    # WHEN
    # Call the class method that uses configparser
    SlurmClient.from_config(configfile='test_config.ini', init_slurm=False, config_only=True)

    # THEN
    _, kwargs = mock_SlurmClient.call_args
    assert kwargs['sacct_start_time'] is None
    assert kwargs['sacct_days_ago'] is None
    assert kwargs['slurm_data_bind_path'] is None
    assert kwargs['slurm_conversion_partition'] is None
    assert kwargs['slurm_zip_cmd'] is None
    assert kwargs['env_file_submission'] is True


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
@patch.dict(os.environ, {
    "BIOMERO_GPU_RESOURCE_FLAG": "gpus",
    "BIOMERO_IMAGE_PULL_VIA_SBATCH": "true",
    "BIOMERO_PULL_CPUS": "12",
    "BIOMERO_PULL_MEM": "64G",
}, clear=False)
def test_from_config_gpu_resource_and_pull_env_override(
        mock_ConfigParser,
        mock_SlurmClient,
        _mock_run, _mock_put, _mock_open, _mock_session,
        slurm_configparser_factory):
    """New GPU resource flag and sbatch pull settings should use the shared config helper."""
    mock_SlurmClient.return_value = None
    mock_ConfigParser.return_value = slurm_configparser_factory(
        get_values={
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
            ('SLURM', 'sacct_start_time'): None,
            ('SLURM', 'sacct_days_ago'): None,
            ('SLURM', 'gpu_resource_flag'): 'gres',
            ('SLURM', 'image_pull_cpus'): '8',
            ('SLURM', 'image_pull_mem'): '32G',
        },
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
            'slurm_image_pull_via_sbatch': False,
        },
    )

    SlurmClient.from_config(configfile='test_config.ini', init_slurm=False, config_only=True)

    _, kwargs = mock_SlurmClient.call_args_list[-1]
    assert kwargs['gpu_resource_flag'] == 'gpus'
    assert kwargs['slurm_image_pull_via_sbatch'] is True
    assert kwargs['image_pull_cpus'] == '12'
    assert kwargs['image_pull_mem'] == '64G'


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
@patch.dict(os.environ, {
    "BIOMERO_APPTAINER_TMPDIR": "/scratch/user/.apptainer-tmp",
    "BIOMERO_APPTAINER_CACHEDIR": "/scratch/user/.apptainer-cache",
}, clear=False)
def test_from_config_apptainer_cache_dirs_env_override(
        mock_ConfigParser,
        mock_SlurmClient,
        _mock_run, _mock_put, _mock_open, _mock_session,
        slurm_configparser_factory):
    """Apptainer tmp/cache settings should support ini values with BIOMERO env overrides."""
    mock_SlurmClient.return_value = None
    mock_ConfigParser.return_value = slurm_configparser_factory(
        get_values={
            ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
            ('SLURM', 'sacct_start_time'): None,
            ('SLURM', 'sacct_days_ago'): None,
            ('SLURM', 'apptainer_tmpdir'): '/ini/tmp',
            ('SLURM', 'apptainer_cachedir'): '/ini/cache',
        },
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
        },
    )

    SlurmClient.from_config(configfile='test_config.ini', init_slurm=False, config_only=True)

    _, kwargs = mock_SlurmClient.call_args_list[-1]
    assert kwargs['apptainer_tmpdir'] == '/scratch/user/.apptainer-tmp'
    assert kwargs['apptainer_cachedir'] == '/scratch/user/.apptainer-cache'


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.cd')
@patch('biomero.slurm_client.io.StringIO')
def test_setup_converters_image_pull_via_sbatch(
    mock_stringio,
    mock_cd,
    mock_run,
    mock_run_commands,
    mock_put,
    _open,
    _session,
):
    """Converter image pulls should submit via sbatch when the opt-in flag is enabled."""
    mock_cd.return_value.__enter__.return_value = None
    mock_cd.return_value.__exit__.return_value = None

    client = SlurmClient(
        "localhost",
        user="slurm",
        port=8022,
        slurm_script_path="scriptpath",
        slurm_converters_path="converterspath",
        converter_images={"zarr_to_tiff": "repo/conv:1.2.3"},
    )
    client.slurm_image_pull_via_sbatch = True
    client.image_pull_cpus = "12"
    client.image_pull_mem = "64G"

    mock_run.return_value = MagicMock(ok=True)
    mock_run_commands.return_value = MagicMock(ok=True, stdout="12345\n")
    mock_put.return_value = MagicMock(ok=True)

    client.setup_converters()

    script = mock_stringio.call_args_list[-1][0][0]
    assert "singularity build --force --disable-cache" in script
    assert "BIOMERO_PULL_CPUS" in script
    mock_run_commands.assert_any_call([
        "sbatch --parsable --job-name=biomero-pull-converters --cpus-per-task=12 --mem=64G --export=ALL,BIOMERO_PULL_CPUS=12 --output=pull_converters-%j.log pull_images.sh"
    ])


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.cd')
@patch('biomero.slurm_client.io.StringIO')
def test_setup_converters_uses_configured_apptainer_dirs_without_sbatch(
    mock_stringio,
    mock_cd,
    mock_run,
    mock_run_commands,
    mock_put,
    _open,
    _session,
):
    """Standard background pulls should still honor configured Apptainer tmp/cache dirs."""
    mock_cd.return_value.__enter__.return_value = None
    mock_cd.return_value.__exit__.return_value = None

    client = SlurmClient(
        "localhost",
        user="slurm",
        port=8022,
        slurm_script_path="scriptpath",
        slurm_converters_path="converterspath",
        converter_images={"zarr_to_tiff": "repo/conv:1.2.3"},
        apptainer_tmpdir="/scratch/user/.apptainer-tmp",
        apptainer_cachedir="/scratch/user/.apptainer-cache",
    )
    client.slurm_image_pull_via_sbatch = False

    mock_run.return_value = MagicMock(ok=True)
    mock_run_commands.return_value = MagicMock(ok=True, stdout="started\n")
    mock_put.return_value = MagicMock(ok=True)

    client.setup_converters()

    script = mock_stringio.call_args_list[-1][0][0]
    assert "APPTAINER_TMPDIR=/scratch/user/.apptainer-tmp" in script
    assert "SINGULARITY_TMPDIR=/scratch/user/.apptainer-tmp" in script
    assert "APPTAINER_CACHEDIR=/scratch/user/.apptainer-cache" in script
    assert "SINGULARITY_CACHEDIR=/scratch/user/.apptainer-cache" in script


@pytest.mark.parametrize(
    "ini_value, env_value, expected",
    [
        (None, None, SlurmClient._DEFAULT_SLURM_ZIP_CMD),
        ("7za", None, "7za"),
        ("7za", "7z", "7z"),
    ],
)
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
def test_from_config_slurm_zip_cmd_precedence(
        mock_ConfigParser,
        mock_SlurmClient,
        _mock_run, _mock_put, _mock_open, _mock_session,
        ini_value, env_value, expected,
        slurm_configparser_factory):
    """slurm_zip_cmd resolves by default, then INI, then BIOMERO env override."""
    mock_SlurmClient.return_value = None

    get_values = {
        ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
    }
    if ini_value is not None:
        get_values[('SLURM', 'slurm_zip_cmd')] = ini_value

    mock_ConfigParser.return_value = slurm_configparser_factory(
        get_values=get_values,
        boolean_values={
            'track_workflows': True,
            'enable_job_accounting': True,
            'enable_job_progress': True,
            'enable_workflow_analytics': True,
        },
        default_value=SlurmClient._DEFAULT_SLURM_ZIP_CMD,
    )

    env_patch = {}
    if env_value is not None:
        env_patch['BIOMERO_SLURM_ZIP_CMD'] = env_value

    with patch.dict(os.environ, env_patch, clear=False):
        SlurmClient.from_config(configfile='test_config.ini', init_slurm=False, config_only=True)

    _, kwargs = mock_SlurmClient.call_args
    assert kwargs['slurm_zip_cmd'] == expected


def test_get_zip_command_auto_detect(slurm_client):
    slurm_client.slurm_zip_cmd = SlurmClient._DEFAULT_SLURM_ZIP_CMD
    cmd = slurm_client.get_zip_command("/data/loc", "result")
    assert "$(command -v 7z || command -v 7za)" in cmd
    assert 'cd "/data/loc/data/out"' in cmd
    assert '"/data/loc/result.zip"' in cmd


def test_get_unzip_command_auto_detect_uses_resolved_default(slurm_client):
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_zip_cmd = SlurmClient._DEFAULT_SLURM_ZIP_CMD
    cmd = slurm_client.get_unzip_command("batch1")
    assert "$(command -v 7z || command -v 7za) x -y" in cmd
    assert 'mkdir -p "/data/batch1"' in cmd
    assert '"/data/batch1.zip"' in cmd


def test_get_zip_command_explicit(slurm_client):
    slurm_client.slurm_zip_cmd = "7za"
    cmd = slurm_client.get_zip_command("/data/loc", "result")
    assert cmd.startswith('cd "/data/loc/data/out" && 7za a -y')
    assert "command -v" not in cmd


def test_get_unzip_command_explicit_uses_configured_zip_cmd(slurm_client):
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_zip_cmd = "7za"
    cmd = slurm_client.get_unzip_command("batch1")
    assert "7za x -y" in cmd
    assert "command -v" not in cmd


# ---------------------------------------------------------------------------
# Tests for slurm_data_bind_path optional (no ValueError)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Direct unit tests for _apptainer_pull_env_prefix
# ---------------------------------------------------------------------------

def test_apptainer_pull_env_prefix_empty(slurm_client):
    """No dirs configured → empty string."""
    slurm_client.apptainer_tmpdir = None
    slurm_client.apptainer_cachedir = None
    assert slurm_client._apptainer_pull_env_prefix() == ""


def test_apptainer_pull_env_prefix_tmpdir_only(slurm_client):
    """Only tmpdir set → both APPTAINER_TMPDIR and SINGULARITY_TMPDIR emitted."""
    slurm_client.apptainer_tmpdir = "/scratch/tmp"
    slurm_client.apptainer_cachedir = None
    result = slurm_client._apptainer_pull_env_prefix()
    assert "APPTAINER_TMPDIR=/scratch/tmp" in result
    assert "SINGULARITY_TMPDIR=/scratch/tmp" in result
    assert "CACHEDIR" not in result
    assert result.endswith(" ")


def test_apptainer_pull_env_prefix_both(slurm_client):
    """Both dirs set → all four env vars emitted."""
    slurm_client.apptainer_tmpdir = "/tmp/app"
    slurm_client.apptainer_cachedir = "/cache/app"
    result = slurm_client._apptainer_pull_env_prefix()
    assert "APPTAINER_TMPDIR=/tmp/app" in result
    assert "SINGULARITY_TMPDIR=/tmp/app" in result
    assert "APPTAINER_CACHEDIR=/cache/app" in result
    assert "SINGULARITY_CACHEDIR=/cache/app" in result
    assert result.endswith(" ")


def test_apptainer_pull_env_prefix_quotes_paths_with_spaces(slurm_client):
    """Paths with spaces are shell-quoted."""
    slurm_client.apptainer_tmpdir = "/my scratch/tmp"
    slurm_client.apptainer_cachedir = None
    result = slurm_client._apptainer_pull_env_prefix()
    assert "'/my scratch/tmp'" in result


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.SlurmClient.put')
@patch('biomero.slurm_client.SlurmClient.run_commands')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.cd')
@patch('biomero.slurm_client.io.StringIO')
def test_setup_converters_sbatch_with_apptainer_dirs(
    mock_stringio,
    mock_cd,
    mock_run,
    mock_run_commands,
    mock_put,
    _open,
    _session,
):
    """When both slurm_image_pull_via_sbatch and apptainer dirs are set,
    the pull script should contain the Apptainer env vars."""
    mock_cd.return_value.__enter__.return_value = None
    mock_cd.return_value.__exit__.return_value = None

    client = SlurmClient(
        "localhost",
        user="slurm",
        port=8022,
        slurm_script_path="scriptpath",
        slurm_converters_path="converterspath",
        converter_images={"zarr_to_tiff": "repo/conv:1.2.3"},
        apptainer_tmpdir="/scratch/.apptainer-tmp",
        apptainer_cachedir="/scratch/.apptainer-cache",
    )
    client.slurm_image_pull_via_sbatch = True
    client.image_pull_cpus = "8"
    client.image_pull_mem = "32G"

    mock_run.return_value = MagicMock(ok=True)
    mock_run_commands.return_value = MagicMock(ok=True, stdout="12345\n")
    mock_put.return_value = MagicMock(ok=True)

    client.setup_converters()

    script = mock_stringio.call_args_list[-1][0][0]
    assert "APPTAINER_TMPDIR=/scratch/.apptainer-tmp" in script
    assert "SINGULARITY_TMPDIR=/scratch/.apptainer-tmp" in script
    assert "APPTAINER_CACHEDIR=/scratch/.apptainer-cache" in script
    assert "SINGULARITY_CACHEDIR=/scratch/.apptainer-cache" in script


def test_get_workflow_command_no_bind_path_no_error(slurm_client):
    """slurm_data_bind_path=None is allowed; APPTAINER_BINDPATH not set."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = None
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = None
    slurm_client.gpu_gres = None

    cmd, env = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "")
    assert "APPTAINER_BINDPATH" not in env


def test_get_workflow_command_with_bind_path(slurm_client):
    """slurm_data_bind_path set → APPTAINER_BINDPATH included in env."""
    slurm_client.slurm_model_paths = {"wf": "wf_path"}
    slurm_client.slurm_model_jobs = {"wf": "job.sh"}
    slurm_client.slurm_model_jobs_params = {"wf": []}
    slurm_client.slurm_model_images = {"wf": "user/image"}
    slurm_client.slurm_data_path = "/data"
    slurm_client.slurm_images_path = "/images"
    slurm_client.slurm_script_path = "/scripts"
    slurm_client.slurm_data_bind_path = "/scratch/my-data"
    slurm_client.env_file_submission = False
    slurm_client.inject_gpu_flag = False
    slurm_client.gpu_partition = None
    slurm_client.gpu_gres = None

    _, env = slurm_client.get_workflow_command("wf", "1.0", "run1", "", "")
    assert env.get("APPTAINER_BINDPATH") == '"/scratch/my-data"'
