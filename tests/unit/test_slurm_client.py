from biomero import SlurmClient
import pytest
import mock
from mock import patch, MagicMock
from paramiko import SSHException


@pytest.fixture
@patch('biomero.slurm_client.Connection.create_session')
@patch('biomero.slurm_client.Connection.open')
@patch('biomero.slurm_client.Connection.put')
@patch('biomero.slurm_client.SlurmClient.run')
def slurm_client(_mock_run,
                 _mock_put, _mock_open,
                 _mock_session):
    return SlurmClient("localhost", 8022, "slurm")


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
        f"mkdir {slurm_data_path}/{zipfile} \
                    {slurm_data_path}/{zipfile}/data \
                    {slurm_data_path}/{zipfile}/data/in \
                    {slurm_data_path}/{zipfile}/data/out \
                    {slurm_data_path}/{zipfile}/data/gt; \
                    7z x -y -o{slurm_data_path}/{zipfile}/data/in \
                    {slurm_data_path}/{zipfile}.zip {filter_filetypes}"
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
@patch.object(SlurmClient, 'run_commands', return_value=MagicMock(ok=True, stdout=""))
def test_zip_data_on_slurm_server(mock_run_commands, mock_logger, slurm_client):
    # GIVEN
    data_location = "/local/path/to/store"
    filename = "example_zip"

    # WHEN
    result = slurm_client.zip_data_on_slurm_server(data_location, filename)

    # THEN
    mock_run_commands.assert_called_once_with(
        [f"7z a -y {filename} -tzip {data_location}/data/out"], env=None)
    assert result.ok is True
    assert result.stdout == ""
    mock_logger.info.assert_called_with(
        f"Zipping {data_location} as {filename} on Slurm")


@patch('biomero.slurm_client.logger')
@patch.object(SlurmClient, 'get', return_value=MagicMock(ok=True, stdout=""))
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


@pytest.mark.parametrize("email, time_limit", [("", ""), ("user@example.com", "10:00:00")])
def test_get_workflow_command(slurm_client,
                              email,
                              time_limit):
    # GIVEN
    workflow = "example_workflow"
    workflow_version = "1.0"
    input_data = "input_data_folder"

    slurm_client.slurm_model_paths = {"example_workflow": "workflow_path"}
    slurm_client.slurm_model_jobs = {"example_workflow": "job_script.sh"}
    slurm_client.slurm_model_jobs_params = {
        "example_workflow": [" --param3=value3", " --param4=value4"]}
    slurm_client.slurm_model_images = {"example_workflow": "user/image"}
    slurm_client.slurm_data_path = "/path/to/slurm_data"
    slurm_client.slurm_converters_path = "/path/to/slurm_converters"
    slurm_client.slurm_images_path = "/path/to/slurm_images"
    slurm_client.slurm_script_path = "/path/to/slurm_script"

    expected_sbatch_cmd = f"sbatch --param3=value3 --param4=value4 --time={time_limit} --mail-user={email} --output=omero-%j.log \
            {slurm_client.slurm_script_path}/job_script.sh"
    expected_env = {
        "DATA_PATH": "/path/to/slurm_data/input_data_folder",
        "IMAGE_PATH": "/path/to/slurm_images/workflow_path",
        "IMAGE_VERSION": "1.0",
        "SINGULARITY_IMAGE": "image_1.0.sif",
        "CONVERSION_PATH": "/path/to/slurm_converters",
        "CONVERTER_IMAGE": "convert_zarr_to_tiff.sif",
        "DO_CONVERT": "true",
        "SCRIPT_PATH": "/path/to/slurm_script",
        "PARAM1": "value1",
        "PARAM2": "value2"
    }

    # WHEN
    sbatch_cmd, env = slurm_client.get_workflow_command(
        workflow, workflow_version, input_data, email, time_limit, param1="value1", param2="value2")

    # THEN
    assert sbatch_cmd == expected_sbatch_cmd
    assert env == expected_env


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
    mock_run_commands.return_value = mock.MagicMock(
        ok=False, stdout=expected_data_location)

    # WHEN
    with pytest.raises(SSHException):
        _ = slurm_client.extract_data_location_from_log(
            slurm_job_id=slurm_job_id, logfile=logfile)

    # THEN
    mock_run_commands.assert_called_with(
        [f"cat {logfile} | perl -wne '/Running [\\w-]+? Job w\\/ .+? \\| .+? \\| (.+?) \\|.*/i and print$1'"])


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_extract_data_location_from_log_2(mock_run_commands,
                                          slurm_client):
    # GIVEN
    slurm_job_id = "123"
    expected_data_location = '/path/to/data'
    mock_run_commands.return_value = mock.MagicMock(
        ok=True, stdout=expected_data_location)

    # WHEN
    data_location = slurm_client.extract_data_location_from_log(
        slurm_job_id=slurm_job_id, logfile=None)

    # THEN
    assert data_location == expected_data_location
    mock_run_commands.assert_called_with(
        [f"cat omero-{slurm_job_id}.log | perl -wne '/Running [\\w-]+? Job w\\/ .+? \\| .+? \\| (.+?) \\|.*/i and print$1'"])


@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_extract_data_location_from_log(mock_run_commands,
                                        slurm_client):
    # GIVEN
    slurm_job_id = "123"
    logfile = "path/to/logfile.txt"
    expected_data_location = '/path/to/data'
    mock_run_commands.return_value = mock.MagicMock(
        ok=True, stdout=expected_data_location)

    # WHEN
    data_location = slurm_client.extract_data_location_from_log(
        slurm_job_id=slurm_job_id, logfile=logfile)

    # THEN
    assert data_location == expected_data_location
    mock_run_commands.assert_called_with(
        [f"cat {logfile} | perl -wne '/Running [\\w-]+? Job w\\/ .+? \\| .+? \\| (.+?) \\|.*/i and print$1'"])


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
    mock_run_commands.return_value = mock.MagicMock(
        ok=True, stdout="12345 RUNNING\n67890 COMPLETED")

    # WHEN
    job_status_dict, _ = slurm_client.check_job_status([12345, 67890])

    # THEN
    mock_run_commands.assert_called_with(
        ['sacct -n -o JobId,State,End -X -j 12345 -j 67890'], env=None)
    assert job_status_dict == {12345: 'RUNNING', 67890: 'COMPLETED'}


@patch('biomero.slurm_client.logger')
@patch('biomero.slurm_client.SlurmClient.run_commands')
def test_check_job_status_exc(mock_run_commands,
                              mock_logger, slurm_client):
    # GIVEN
    return_mock = mock.MagicMock(
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
    mock_run_commands.return_value = mock.MagicMock(
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
    mock_put.return_value = MagicMock(ok=True)
    mock_run.return_value = MagicMock(ok=True)

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
    mock_run.assert_called_with("mkdir -p scriptpath")

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
    expected_result = {'PARAMS': '--param1 $PARAM1_NAME --param2 $PARAM2_NAME'}
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
    mock_run_commands.return_value = mock.MagicMock(
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
    mock_run_commands.return_value = mock.MagicMock(
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
    mock_run.return_value = mock.MagicMock(
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
    mock_run_commands.return_value = mock.MagicMock(
        ok=True, stdout=stdout_content)

    # WHEN
    result = slurm_client.get_active_job_progress(slurm_job_id, pattern)

    # THEN
    mock_get_recent_log_command.assert_called_once_with(
        log_file=slurm_client._LOGFILE.format(slurm_job_id=slurm_job_id))
    mock_run_commands.assert_called_once_with([log_cmd], env={})

    assert result == "Progress: 75%\n"


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

    mock_extract_data_location.return_value = data_location
    mock_run_commands.return_value = mock.MagicMock(ok=True)

    # WHEN
    result = slurm_client.cleanup_tmp_files(
        slurm_job_id, filename, data_location, logfile)

    # THEN
    mock_extract_data_location.assert_not_called()
    mock_run_commands.assert_called_once_with([
        f"rm {filename}.*",
        f"rm {logfile}",
        f"rm -rf {data_location} {data_location}.*"
    ])

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

    mock_extract_data_location.return_value = data_location
    mock_run_commands.return_value = mock.MagicMock(ok=True)

    # WHEN
    result = slurm_client.cleanup_tmp_files(
        slurm_job_id, filename, data_location, logfile)

    # THEN
    mock_extract_data_location.assert_called_once_with(logfile)
    mock_run_commands.assert_called_once_with([
        f"rm {filename}.*",
        f"rm {logfile}",
        f"rm -rf {data_location} {data_location}.*"
    ])

    assert result.ok is True


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

    # Create a MagicMock instance to represent the ConfigParser object
    mock_configparser_instance = mock.MagicMock()

    # Set the behavior or attributes of the mock_configparser_instance as needed
    mock_configparser_instance.read.return_value = None
    mv = "configvalue"
    mock_configparser_instance.get.return_value = mv
    mock_configparser_instance.getboolean.return_value = True
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

    mock_configparser_instance.items.return_value = {**model_dict, **repo_dict,
                                                     **job_dict, **jp_dict}

    # Configure the MagicMock to return the mock_configparser_instance when called
    mock_ConfigParser.return_value = mock_configparser_instance

    # WHEN
    # Call the class method that uses configparser
    SlurmClient.from_config(
        configfile=configfile, init_slurm=init_slurm)

    # THEN
    mock_configparser_instance.read.assert_called_once_with([
        SlurmClient._DEFAULT_CONFIG_PATH_1,
        SlurmClient._DEFAULT_CONFIG_PATH_2,
        configfile
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
        slurm_model_jobs=job_dict_out,  # expected slurm_model_jobs value,
        slurm_model_jobs_params=jp_dict_out,  # expected slurm_model_jobs_params value,
        slurm_script_path=mv,  # expected slurm_script_path value,
        slurm_script_repo=mv,  # expected slurm_script_repo value,
        init_slurm=init_slurm
    )


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
        f'mkdir -p {dpath} && mkdir -p {spath} && mkdir -p {ipath}', env={})


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
                     mock_stringio):
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
    modelpaths = 'path'
    mpaths = {'wf': modelpaths}
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
        [f'mkdir -p {dpath}', f'mkdir -p {spath}', f'mkdir -p {ipath}'])
    # 2 git
    mock_run.assert_any_call(
        ['git clone "$REPOSRC" "$LOCALREPO" 2> /dev/null || git -C "$LOCALREPO" pull'],
        {"REPOSRC": srepo, "LOCALREPO": spath})

    # 3 converters
    _mock_Connection_put.assert_called()
    # mock_run.assert_any_call(f"mkdir -p {cpath}")
    mock_run.assert_any_call(
        [f"singularity build -F {convert_name}.sif {convert_def} >> sing.log 2>&1 ; echo 'finished {convert_name}.sif' &"])

    # 4 images
    mock_run.assert_any_call([f"mkdir -p {modelpaths}"])
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
