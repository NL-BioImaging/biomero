from omero_slurm_client import SlurmClient
import pytest
import mock
from mock import patch


@patch('omero_slurm_client.slurm_client.Connection.run')
@patch('omero_slurm_client.slurm_client.Connection.put')
def test_slurm_client_connection(mconn_put, mconn_run):
    # GIVEN
    # Create a SlurmClient instance and assign the mock Connection instance to its super attribute
    slurm_client = SlurmClient("localhost", 8022, "slurm",
                               slurm_data_path="/path/to/data",
                               slurm_images_path="/path/to/images",
                               slurm_converters_path="/path/to/converters",
                               init_slurm=False)
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
@patch('omero_slurm_client.slurm_client.Connection.open')
@patch('omero_slurm_client.slurm_client.Connection.run')
@patch('omero_slurm_client.slurm_client.Connection.put')
@patch('omero_slurm_client.slurm_client.requests_cache.CachedSession')
def test_init_workflows(mock_CachedSession,
                        _mock_Connection_put,
                        _mock_Connection_run,
                        _mock_Connection_open,
                        slurm_model_repos, expected_slurm_model_images):
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


@patch('omero_slurm_client.slurm_client.requests_cache.CachedSession')
@patch('omero_slurm_client.slurm_client.Connection.run')
@patch('omero_slurm_client.slurm_client.Connection.put')
def test_init_workflows_force_update(_mock_Connection_put,
                                     _mock_Connection_run,
                                     mock_CachedSession):
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


def test_invalid_workflow_repo():
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


def test_invalid_workflow_url():
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
