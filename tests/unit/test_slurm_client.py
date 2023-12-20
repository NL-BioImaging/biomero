from omero_slurm_client import SlurmClient
import pytest


def test_slurm_client_init():
    # Test default initialization
    slurm_client = SlurmClient()
    assert isinstance(slurm_client, SlurmClient)
    assert slurm_client.slurm_data_path == SlurmClient._DEFAULT_SLURM_DATA_PATH
    assert slurm_client.slurm_images_path == SlurmClient._DEFAULT_SLURM_IMAGES_PATH
    # Add more assertions for other default values

    # Test custom initialization
    slurm_client_custom = SlurmClient(
        host="custom_host",
        slurm_data_path="/custom/data/path",
        slurm_images_path="/custom/images/path",
        # Add more custom parameters
    )
    assert slurm_client_custom.host == "custom_host"
    assert slurm_client_custom.slurm_data_path == "/custom/data/path"
    assert slurm_client_custom.slurm_images_path == "/custom/images/path"
    # Add more assertions for other custom values
