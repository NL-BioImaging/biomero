import logging
from uuid import uuid4
from biomero.slurm_client import SlurmClient
from biomero.eventsourcing import NoOpWorkflowTracker
from biomero.database import EngineManager, TaskExecution, JobProgressView, JobView
import pytest
import mock
from mock import patch, MagicMock
from paramiko import SSHException
import os
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
    zipfile = "example"
    filter_filetypes = "*.zarr *.tiff *.tif"
    expected_command = (
        f"mkdir \"{slurm_data_path}/{zipfile}\" \
                    \"{slurm_data_path}/{zipfile}/data\" \
                    \"{slurm_data_path}/{zipfile}/data/in\" \
                    \"{slurm_data_path}/{zipfile}/data/out\" \
                    \"{slurm_data_path}/{zipfile}/data/gt\"; \
                    7z x -y -o\"{slurm_data_path}/{zipfile}/data/in\" \
                    \"{slurm_data_path}/{zipfile}.zip\" {filter_filetypes}"
    )

    # WHEN
    unzip_command = slurm_client.get_unzip_command(
        zipfile, filter_filetypes)

    # THEN
    assert unzip_command == expected_command


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

    # WHEN
    result = slurm_client.zip_data_on_slurm_server(data_location, filename)

    # THEN
    mock_run_commands.assert_called_once_with(
        [f"7z a -y \"{filename}\" -tzip \"{data_location}/data/out\""], env=None)
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

    expected_conversion_cmd = 'sbatch --job-name=conversion --export=ALL,CONFIG_PATH="$PWD/$CONFIG_FILE" --array=1-$N "$SCRIPT_PATH/convert_job_array.sh"'
    expected_commands = [
        f'find "{expected_data_path}/data/in" -name "*.{source_format}" | awk \'{{print NR, $0}}\' > "{expected_config_file}"',
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

def test_pull_descriptor_from_github(slurm_client):
    # GIVEN
    workflow = "example_workflow"
    git_repo = "https://github.com/username/repo/tree/branch"
    expected_raw_url = "https://github.com/username/repo/raw/branch/descriptor.json"
    expected_json_descriptor = {"key": "value"}
    repos = {
        workflow: git_repo
    }
    with patch('biomero.slurm_client.requests_cache.CachedSession.get') as mock_get:
        slurm_client.slurm_model_repos = repos
        with patch.object(slurm_client, 'convert_url', return_value=expected_raw_url):
            mock_get.return_value.ok = True
            mock_get.return_value.json.return_value = expected_json_descriptor

            # WHEN
            json_descriptor = slurm_client.pull_descriptor_from_github(
                workflow)

            # THEN
            slurm_client.convert_url.assert_called_once_with(git_repo)
            mock_get.assert_called_with(expected_raw_url)
            assert json_descriptor == expected_json_descriptor

            # WHEN & THEN
            mock_get.return_value.ok = False
            with pytest.raises(ValueError, match="Error while pulling descriptor file"):
                slurm_client.pull_descriptor_from_github(workflow)


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


@patch('biomero.slurm_client.SlurmClient.str_to_class')
def test_convert_cytype_to_omtype_Number(mock_str_to_class,
                                         slurm_client):
    # GIVEN
    cytype = 'Number'
    _default = 42.0
    args = (1, 2, 3)
    kwargs = {'key': 'value'}

    # WHEN
    slurm_client.convert_cytype_to_omtype(cytype, _default, *args, **kwargs)

    # THEN
    mock_str_to_class.assert_called_once_with(
        "omero.scripts", "Float", *args, **kwargs)


@patch('biomero.slurm_client.SlurmClient.str_to_class')
def test_convert_cytype_to_omtype_Boolean(mock_str_to_class,
                                          slurm_client):
    # GIVEN
    cytype = 'Boolean'
    _default = "false"
    args = (1, 2, 3)
    kwargs = {'key': 'value'}

    # WHEN
    slurm_client.convert_cytype_to_omtype(cytype, _default, *args, **kwargs)

    # THEN
    mock_str_to_class.assert_called_once_with(
        "omero.scripts", "Bool", *args, **kwargs)


@patch('biomero.slurm_client.SlurmClient.str_to_class')
def test_convert_cytype_to_omtype_String(mock_str_to_class,
                                         slurm_client):
    # GIVEN
    cytype = 'String'
    _default = "42 is the answer"
    args = (1, 2, 3)
    kwargs = {'key': 'value'}

    # WHEN
    slurm_client.convert_cytype_to_omtype(cytype, _default, *args, **kwargs)

    # THEN
    mock_str_to_class.assert_called_once_with(
        "omero.scripts", "String", *args, **kwargs)


@patch('biomero.slurm_client.SlurmClient.pull_descriptor_from_github', return_value={
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
            'cytype': 'type1',
            'optional': False,
            'cmd_flag': '--flag1',
            'description': 'description1',
        },
        'input2': {
            'name': 'input2',
            'default': 'default_value2',
            'cytype': 'type2',
            'optional': True,
            'cmd_flag': '--flag2',
            'description': 'description2',
        },
    }
    assert workflow_params == expected_workflow_params


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
def test_update_slurm_scripts(mock_generate_job, mock_workflow_params_to_subs,
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

    # WHEN
    slurm_client.update_slurm_scripts(generate_jobs=True)

    # THEN
    # Assert that the workflow parameters are obtained
    mock_get_workflow_params.assert_called_once_with("workflow_name")

    # Assert that workflow parameters are converted to substitutions
    mock_workflow_params_to_subs.assert_called_once_with(
        {'param1': {'cmd_flag': '--param1', 'name': 'param1_name'}})

    # Assert that the job script is generated
    mock_generate_job.assert_called_once_with(
        "workflow_name", {'PARAMS': '--param1 $PARAM1_NAME'})

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
        env=env
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


@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
@patch('biomero.slurm_client.SlurmClient.__init__')
@patch('biomero.slurm_client.configparser.ConfigParser')
def test_from_config(mock_ConfigParser,
                     mock_SlurmClient,
                     _mock_run, _mock_put, _mock_open, _mock_session):
    """
    Test the creation of SlurmClient object from a configuration file.
    """
    # GIVEN
    configfile = 'test_config.ini'
    init_slurm = True
    mock_SlurmClient.return_value = None
    config_only = False

    # Create a MagicMock instance to represent the ConfigParser object
    mock_configparser_instance = MagicMock()

    # Set the behavior or attributes of the mock_configparser_instance as needed
    mock_configparser_instance.read.return_value = None
    mv = "configvalue"
    mock_configparser_instance.get.return_value = mv
    mock_configparser_instance.getboolean.side_effect = lambda section, option, fallback: {
        'track_workflows': True,
        'enable_job_accounting': True,
        'enable_job_progress': True,
        'enable_workflow_analytics': True
    }.get(option, fallback)
    
    # Set up mock for 'sqlalchemy_url' (new addition)
    mock_configparser_instance.get.side_effect = lambda section, option, fallback=None: {
        ('ANALYTICS', 'sqlalchemy_url'): 'sqlite:///test.db',
    }.get((section, option), mv)
    
    model_dict = {
        "m1": "v1"
    }
    repo_dict = {
        "m1_repo": "v2"
    }
    repo_dict_out = {
        "m1": "v2"
    }
    job_dict = {
        "m1_job": "v3"
    }
    job_dict_out = {
        "m1": "v3"
    }
    jp_dict = {
        "m1_job_param": "v4"
    }
    jp_dict_out = {
        "m1": [" --param=v4"]
    }
    conv_dict = {
        "zarr_to_tiff": "myconverter"
    }

    # Define a side effect function
    def items_side_effect(section):
        if section == "MODELS":
            return {**model_dict, **repo_dict,
                    **job_dict, **jp_dict}
        if section == "CONVERTERS":
            return conv_dict
        else:
            return {}.items()

    mock_configparser_instance.items.side_effect = items_side_effect

    # Configure the MagicMock to return the mock_configparser_instance when called
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
@patch('biomero.slurm_client.requests_cache.CachedSession')
def test_init_workflows(mock_CachedSession,
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
@patch('biomero.slurm_client.Connection.run')
@patch('biomero.slurm_client.Connection.put')
def test_init_workflows_force_update(_mock_Connection_put,
                                     _mock_Connection_run,
                                     mock_CachedSession):
    """
    Test the forced update of workflows in the SlurmClient.
    """
    # GIVEN
    wf_name = "workflow1"
    wf_repo = "https://github.com/example/workflow1"
    wf_image = "dockerhub.com/image1"
    json_descriptor = {"container-image": {"image": wf_image}}
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
                       match="Error while pulling descriptor file"):
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
