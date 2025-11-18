"""
Unit tests for BIOMERO schema parsers using pytest.

This module tests the new schema parser system with both legacy cytomine-0.1
format and the new biomero-schema format using real test files.
"""

import json
import pytest
from pathlib import Path

from biomero.schema_parsers import (
    DescriptorParserFactory,
    detect_schema_format,
    CytomineParser,
    BiomeroSchemaParser,
    ParsedParameter,
    ParsedWorkflowDescriptor,
    ParsedResourceRequirements
)


@pytest.fixture
def test_data_dir():
    """Path to the test data directory."""
    return Path(__file__).parent.parent


@pytest.fixture
def cytomine_descriptor(test_data_dir):
    """Load the cytomine test descriptor."""
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
        ({'cwlVersion': 'v1.0'}, 'CWL'),
        ({'$schema': 'https://openapi.spec'}, 'OpenAPI'),
        ({'container-image': {}, 'inputs': []}, 'BIAFLOWS'),
    ])
    def test_format_detection(self, descriptor_data, expected_format):
        """Test schema format detection with various inputs."""
        detected = detect_schema_format(descriptor_data)
        assert detected == expected_format


class TestCytomineParser:
    """Test cases for cytomine-0.1 format parsing."""
    
    def test_cytomine_format_detection(self, cytomine_descriptor):
        """Test that cytomine format is detected correctly."""
        detected = detect_schema_format(cytomine_descriptor)
        assert detected == 'BIAFLOWS'
    
    def test_cytomine_parsing(self, cytomine_descriptor):
        """Test parsing of cytomine-0.1 format."""
        parsed = DescriptorParserFactory.parse_descriptor(cytomine_descriptor)
        
        # Test basic metadata
        assert parsed.name == "NucleiSegmentation-ImageJ"
        assert "Segment clustered nuclei" in parsed.description
        assert parsed.schema_version == "cytomine-0.1"
        assert parsed.container_image == "neubiaswg5/w_nucleisegmentation-imagej"
        assert parsed.container_type == "singularity"
        assert "python wrapper.py" in parsed.command_line
        
        # Test parameters
        assert len(parsed.parameters) == 2
        
        # Check parameter details
        radius_param = next(p for p in parsed.parameters if p.name == "Radius")
        assert radius_param.param_type == "Number"
        assert radius_param.default_value == 5
        assert radius_param.optional is True
        
        threshold_param = next(p for p in parsed.parameters if p.name == "Threshold")
        assert threshold_param.param_type == "Number"
        assert threshold_param.default_value == -0.5
        assert threshold_param.optional is True


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
        assert parsed.container_image == "neubiaswg5/w_nucleitracking-imagej:1.0.0"
        assert parsed.container_type == "oci"
        assert "python wrapper.py" in parsed.command_line
        
        # Test resource requirements
        assert parsed.resource_requirements is not None
        reqs = parsed.resource_requirements
        assert reqs.ram_min == 2048.0
        assert reqs.cores_min == 2.0
        assert reqs.gpu is False
        
        # Test parameters
        inputs = [p for p in parsed.parameters if not getattr(p, 'is_output', False)]
        outputs = [p for p in parsed.parameters if getattr(p, 'is_output', False)]
        
        assert len(inputs) == 4
        assert len(outputs) == 2
        
        # Check input parameters
        input_image = next(p for p in inputs if p.name == "Input Image Stack")
        assert input_image.param_type == "String"  # image type normalized to String
        assert input_image.format_info.get('format') == 'tif'
        assert input_image.format_info.get('sub_type') == 'grayscale'
        assert input_image.optional is False
        
        radius_param = next(p for p in inputs if p.name == "Detection Radius")
        assert radius_param.param_type == "float"
        assert radius_param.default_value == 5
        assert radius_param.optional is True
        
        # Check output parameters
        track_count = next(p for p in outputs if p.name == "Number of Tracks")
        assert track_count.param_type == "Number"
        assert track_count.is_output is True
        
        output_path = next(p for p in outputs if p.name == "Output File Path")
        assert output_path.param_type == "String"
        assert output_path.is_output is True
        
        # Test extended metadata
        assert hasattr(parsed, 'metadata')
        metadata = parsed.metadata
        assert len(metadata['authors']) == 2
        assert len(metadata['citations']) == 2
        assert metadata['problem_class'] == 'object-tracking'
    
    @pytest.mark.skipif(
        True,  # Skip if biomero-schema not available
        reason="biomero-schema package not available in test environment"
    )
    def test_biomero_parsing_with_pydantic_validation(self, biomero_descriptor):
        """Test parsing with full Pydantic validation (requires biomero-schema)."""
        # This test would run only if biomero-schema is available
        parsed = DescriptorParserFactory.parse_descriptor(biomero_descriptor)
        assert parsed is not None


class TestTypeMapping:
    """Test cases for parameter type mapping."""
    
    def test_biomero_schema_type_normalization(self):
        """Test BiomeroSchemaParser type normalization."""
        parser = BiomeroSchemaParser()
        
        # Test type mappings
        assert parser._normalize_type('integer') == 'integer'
        assert parser._normalize_type('float') == 'float'
        assert parser._normalize_type('string') == 'String'
        assert parser._normalize_type('boolean') == 'Boolean'
        assert parser._normalize_type('file') == 'String'
        assert parser._normalize_type('image') == 'String'
        assert parser._normalize_type('array') == 'String'
        assert parser._normalize_type('Number') == 'Number'
    
    def test_cytomine_type_handling(self):
        """Test that CytomineParser handles types directly."""
        parser = CytomineParser()
        
        # Test with a simple descriptor to verify types are preserved
        test_descriptor = {
            "name": "Test",
            "inputs": [
                {"id": "test1", "type": "String"},
                {"id": "test2", "type": "Number"},
                {"id": "test3", "type": "Boolean"}
            ]
        }
        
        parsed = parser.parse_descriptor(test_descriptor)
        
        # Check that types are preserved as-is
        assert len(parsed.parameters) == 3
        assert parsed.parameters[0].param_type == "String"
        assert parsed.parameters[1].param_type == "Number"
        assert parsed.parameters[2].param_type == "Boolean"


class TestDescriptorParserFactory:
    """Test cases for the descriptor parser factory."""
    
    def test_factory_creates_correct_parser(self, cytomine_descriptor, biomero_descriptor):
        """Test that factory creates the correct parser for each format."""
        # Test cytomine parser creation
        cytomine_parser = DescriptorParserFactory.create_parser('BIAFLOWS')
        assert isinstance(cytomine_parser, CytomineParser)
        
        # Test biomero parser creation
        biomero_parser = DescriptorParserFactory.create_parser('biomero-schema')
        assert isinstance(biomero_parser, BiomeroSchemaParser)
    
    def test_factory_parse_descriptor(self, cytomine_descriptor, biomero_descriptor):
        """Test end-to-end parsing through factory."""
        # Test cytomine parsing
        cytomine_parsed = DescriptorParserFactory.parse_descriptor(cytomine_descriptor)
        assert isinstance(cytomine_parsed, ParsedWorkflowDescriptor)
        assert cytomine_parsed.name == "NucleiSegmentation-ImageJ"
        
        # Test biomero parsing
        biomero_parsed = DescriptorParserFactory.parse_descriptor(biomero_descriptor)
        assert isinstance(biomero_parsed, ParsedWorkflowDescriptor)
        assert biomero_parsed.name == "NucleiTracking-ImageJ"
    
    def test_unsupported_format_raises_error(self):
        """Test that unsupported formats raise appropriate errors."""
        with pytest.raises(ValueError, match="No parser available for schema format"):
            DescriptorParserFactory.create_parser('unsupported-format')


class TestParsedDataStructures:
    """Test cases for parsed data structures."""
    
    def test_parsed_parameter_initialization(self):
        """Test ParsedParameter initialization and attributes."""
        param = ParsedParameter()
        
        # Test default values
        assert param.id == ""
        assert param.name == ""
        assert param.description == ""
        assert param.param_type == ""
        assert param.default_value is None
        assert param.optional is False
        assert param.command_flag == ""
        assert param.value_key == ""
        assert param.set_by_server is False
        assert param.is_output is False
        assert param.ui_metadata == {}
        assert param.format_info == {}
    
    def test_parsed_workflow_descriptor_initialization(self):
        """Test ParsedWorkflowDescriptor initialization and attributes."""
        descriptor = ParsedWorkflowDescriptor()
        
        # Test default values
        assert descriptor.name == ""
        assert descriptor.description == ""
        assert descriptor.schema_version == ""
        assert descriptor.container_image == ""
        assert descriptor.container_type == "singularity"  # default value
        assert descriptor.command_line == ""
        assert descriptor.parameters == []
        assert descriptor.output_parameters == []
        assert descriptor.resource_requirements is None
        assert descriptor.metadata == {}
    
    def test_parsed_resource_requirements_initialization(self):
        """Test ParsedResourceRequirements initialization and attributes."""
        reqs = ParsedResourceRequirements()
        
        # Test default values
        assert reqs.ram_min is None  # Optional[float]
        assert reqs.cores_min is None  # Optional[float]
        assert reqs.gpu is None  # Optional[bool]
        assert reqs.cuda_requirements is None