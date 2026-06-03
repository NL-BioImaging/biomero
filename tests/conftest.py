"""Shared pytest fixtures for biomero tests."""
import json
import pytest
import yaml
from pathlib import Path


@pytest.fixture
def test_data_dir():
    """Path to the test data directory."""
    return Path(__file__).parent


@pytest.fixture
def biaflows_descriptor(test_data_dir):
    """Load the biaflows test descriptor."""
    with open(test_data_dir / "W_NucleiSegmentation-ImageJ.descriptor.json") as f:
        return json.load(f)


@pytest.fixture
def biomero_descriptor(test_data_dir):
    """Load the biomero-schema test descriptor."""
    with open(test_data_dir / "example_workflow.json") as f:
        return json.load(f)


@pytest.fixture
def bilayers_descriptor(test_data_dir):
    """Load the bilayers test descriptor."""
    with open(test_data_dir / "bilayers_example.yaml") as f:
        return yaml.safe_load(f)
