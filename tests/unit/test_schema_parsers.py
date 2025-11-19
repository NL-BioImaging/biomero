"""
Unit tests for BIOMERO schema parsers using pytest.

This module tests the new schema parser system with both legacy biaflows
format and the new biomero-schema format using real test files.
"""

import json
import pytest
from pathlib import Path

from biomero.schema_parsers import (
    DescriptorParserFactory,
    detect_schema_format
)


@pytest.fixture
def test_data_dir():
    """Path to the test data directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def biaflows_descriptor(test_data_dir):
    """Load the biaflows test descriptor."""
    file_path = test_data_dir / "W_NucleiSegmentation-ImageJ.descriptor.json"
    with open(file_path, 'r') as f:
        return json.load(f)


@pytest.fixture
def biomero_descriptor(test_data_dir):
    """Load the biomero-schema test descriptor."""
    file_path = test_data_dir / "example_workflow.json"
    with open(file_path, 'r') as f:
        return json.load(f)


class TestSchemaFormatDetection:
    """Test cases for schema format detection."""
    
    @pytest.mark.parametrize("descriptor_data,expected_format", [
        ({'schema-version': 'cytomine-0.1'}, 'BIAFLOWS'),
        ({'schema-version': '1.0.0'}, 'biomero-schema'),
        ({'schema-version': 'biomero-0.1'}, 'biomero-schema'),
        ({'container-image': {}, 'inputs': []}, 'BIAFLOWS'),
    ])
    def test_format_detection(self, descriptor_data, expected_format):
        """Test schema format detection with various inputs."""
        detected = detect_schema_format(descriptor_data)
        assert detected == expected_format


class TestBiaflowsParser:
    """Test cases for biaflows format parsing."""
    
    def test_biaflows_format_detection(self, biaflows_descriptor):
        """Test that biaflows format is detected correctly."""
        detected = detect_schema_format(biaflows_descriptor)
        assert detected == 'BIAFLOWS'
    
    def test_biaflows_parsing(self, biaflows_descriptor):
        """Test parsing of biaflows format."""
        parsed = DescriptorParserFactory.parse_descriptor(biaflows_descriptor)
        
        # Test basic metadata
        assert parsed.name == "NucleiSegmentation-ImageJ"
        assert "Segment clustered nuclei" in parsed.description
        assert parsed.schema_version == "1.0.0"  # normalized
        expected_image = "neubiaswg5/w_nucleisegmentation-imagej"
        assert expected_image in parsed.container_image.image
        assert parsed.container_image.type == "singularity"
        assert "python wrapper.py" in parsed.command_line
        
        # Test parameters - should have inputs converted
        assert len(parsed.inputs) >= 2
        
        # Check parameter details (converted from biaflows format)
        param_names = [p.name for p in parsed.inputs]
        assert "Radius" in param_names
        assert "Threshold" in param_names


class TestBiomeroSchemaParser:
    """Test cases for biomero-schema format parsing."""
    
    def test_biomero_format_detection(self, biomero_descriptor):
        """Test that biomero-schema format is detected correctly."""
        detected = detect_schema_format(biomero_descriptor)
        assert detected == 'biomero-schema'
    
    def test_biomero_parsing(self, biomero_descriptor):
        """Test parsing of biomero-schema format."""
        parsed = DescriptorParserFactory.parse_descriptor(biomero_descriptor)
        
        # Test basic metadata
        assert parsed.name == "NucleiTracking-ImageJ"
        assert "ImageJ workflow for nuclei tracking" in parsed.description
        assert parsed.schema_version == "1.0.0"
        container_image = "neubiaswg5/w_nucleitracking-imagej:1.0.0"
        assert parsed.container_image.image == container_image
        assert parsed.container_image.type == "oci"
        assert "python wrapper.py" in parsed.command_line
        
        # Test resource requirements
        assert parsed.configuration is not None
        assert parsed.configuration.resources is not None
        reqs = parsed.configuration.resources
        assert reqs.ram_min == 2048.0
        assert reqs.cores_min == 2.0
        assert reqs.gpu is False
        
        # Test parameters
        assert len(parsed.inputs) >= 4
        
        # Check parameter names exist
        param_names = [p.name for p in parsed.inputs]
        assert "Input Image Stack" in param_names
        assert "Detection Radius" in param_names
        
        # Test extended metadata if present
        if hasattr(parsed, 'metadata') and parsed.metadata:
            metadata = parsed.metadata
            if 'authors' in metadata:
                assert len(metadata['authors']) >= 1
            if 'problem_class' in metadata:
                assert metadata['problem_class'] == 'object-tracking'


class TestDescriptorParserFactory:
    """Test cases for the descriptor parser factory."""
    
    def test_factory_parse_descriptor(self, biaflows_descriptor,
                                      biomero_descriptor):
        """Test end-to-end parsing through factory."""
        # Test biaflows parsing
        biaflows_parsed = DescriptorParserFactory.parse_descriptor(
            biaflows_descriptor
        )
        assert biaflows_parsed.name == "NucleiSegmentation-ImageJ"
        
        # Test biomero parsing
        biomero_parsed = DescriptorParserFactory.parse_descriptor(
            biomero_descriptor
        )
        assert biomero_parsed.name == "NucleiTracking-ImageJ"
    
    def test_unsupported_format_raises_error(self):
        """Test that unsupported formats raise appropriate errors."""
        with pytest.raises(ValueError, match="Unable to detect schema format"):
            DescriptorParserFactory.parse_descriptor(
                {'unsupported': 'format'}
            )
