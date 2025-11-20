"""
Unit tests for BIOMERO schema parsers using pytest.

This module tests the new schema parser system with both legacy biaflows
format and the new biomero-schema format using real test files.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, Mock

from biomero.schema_parsers import (
    DescriptorParserFactory,
    detect_schema_format,
    convert_schema_type_to_omero,
    create_class_instance
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


class TestTypeConversion:
    """Test cases for schema type to OMERO type conversion."""
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_number_int(self, mock_create_class):
        """Test Number type with integer default converts to Int."""
        # GIVEN
        schema_type = 'Number'
        default_value = 42
        args = (1, 2, 3)
        kwargs = {'key': 'value'}

        # WHEN
        convert_schema_type_to_omero(schema_type, default_value, *args, **kwargs)
        
        # THEN
        mock_create_class.assert_called_once_with(
            "omero.scripts", "Int", *args, **kwargs)
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_number_float(
            self, mock_create_class):
        """Test Number type with float default converts to Float."""
        # GIVEN
        schema_type = 'Number'
        default_value = 42.0
        args = (1, 2, 3)
        kwargs = {'key': 'value'}

        # WHEN
        convert_schema_type_to_omero(schema_type, default_value, *args, **kwargs)
        
        # THEN
        mock_create_class.assert_called_once_with(
            "omero.scripts", "Float", *args, **kwargs)
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_integer(self, mock_create_class):
        """Test integer type converts to Int."""
        convert_schema_type_to_omero('integer', 10, 'test_param')
        
        mock_create_class.assert_called_once_with(
            "omero.scripts", "Int", 'test_param')
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_float(self, mock_create_class):
        """Test float type converts to Float."""
        convert_schema_type_to_omero('float', 3.14, 'test_param')
        
        mock_create_class.assert_called_once_with(
            "omero.scripts", "Float", 'test_param')
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_boolean(self, mock_create_class):
        """Test Boolean type converts to Bool."""
        # GIVEN
        schema_type = 'Boolean'
        default_value = "false"
        args = (1, 2, 3)
        kwargs = {'key': 'value'}

        # WHEN
        convert_schema_type_to_omero(schema_type, default_value, *args, **kwargs)
        
        # THEN
        mock_create_class.assert_called_once_with(
            "omero.scripts", "Bool", *args, **kwargs)
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_boolean_lowercase(
            self, mock_create_class):
        """Test boolean type converts to Bool."""
        convert_schema_type_to_omero('boolean', False, 'test_param')
        
        mock_create_class.assert_called_once_with(
            "omero.scripts", "Bool", 'test_param')
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_string(self, mock_create_class):
        """Test String type converts to String."""
        # GIVEN
        schema_type = 'String'
        default_value = "42 is the answer"
        args = (1, 2, 3)
        kwargs = {'key': 'value'}

        # WHEN
        convert_schema_type_to_omero(schema_type, default_value, *args, **kwargs)
        
        # THEN
        mock_create_class.assert_called_once_with(
            "omero.scripts", "String", *args, **kwargs)

    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_image(self, mock_create_class):
        """Test image type converts to String."""
        # GIVEN
        schema_type = 'image'
        default_value = None
        args = (1, 2, 3)
        kwargs = {'key': 'value'}

        # WHEN
        convert_schema_type_to_omero(schema_type, default_value, *args, **kwargs)
        
        # THEN
        mock_create_class.assert_called_once_with(
            "omero.scripts", "String", *args, **kwargs)

    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_file(self, mock_create_class):
        """Test file type converts to String."""
        # GIVEN
        schema_type = 'file'
        default_value = None
        args = (1, 2, 3)
        kwargs = {'key': 'value'}

        # WHEN
        convert_schema_type_to_omero(schema_type, default_value, *args, **kwargs)
        
        # THEN
        mock_create_class.assert_called_once_with(
            "omero.scripts", "String", *args, **kwargs)
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_string_lowercase(
            self, mock_create_class):
        """Test string type converts to String."""
        convert_schema_type_to_omero('string', 'test', 'test_param')
        
        mock_create_class.assert_called_once_with(
            "omero.scripts", "String", 'test_param')
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_with_kwargs(self, mock_create_class):
        """Test conversion with additional kwargs."""
        convert_schema_type_to_omero(
            'integer', 42, 'test_param',
            description='Test description',
            optional=True
        )
        
        mock_create_class.assert_called_once_with(
            "omero.scripts", "Int", 'test_param',
            description='Test description', optional=True)
    
    def test_convert_schema_type_to_omero_unsupported_type(self):
        """Test that unsupported types raise ValueError."""
        with pytest.raises(ValueError,
                           match="Unsupported schema type 'unknown'"):
            convert_schema_type_to_omero('unknown', 'test', 'test_param')


class TestClassInstantiation:
    """Test cases for dynamic class instantiation."""
    
    def test_create_class_instance_handles_missing_omero(self):
        """Test that missing OMERO modules are handled gracefully."""
        # This should return None when OMERO is not available
        result = create_class_instance('omero.scripts', 'Int', 'test_int')
        assert result is None
    
    def test_create_class_instance_invalid_module(self):
        """Test that invalid module names are handled gracefully."""
        result = create_class_instance('invalid.module', 'SomeClass', 'test')
        assert result is None
    
    def test_create_class_instance_invalid_class(self):
        """Test that invalid class names are handled gracefully."""
        # This will try to import a real module but invalid class
        result = create_class_instance('json', 'InvalidClass', 'test')
        assert result is None
    
    @patch('importlib.import_module')
    def test_create_class_instance_success(self, mock_import):
        """Test successful class instantiation when module is available."""
        # Mock the module and class
        mock_class = Mock()
        mock_instance = Mock()
        mock_class.return_value = mock_instance
        mock_module = Mock()
        mock_module.SomeClass = mock_class
        mock_import.return_value = mock_module
        
        result = create_class_instance(
            'some.module', 'SomeClass', 'arg1', kwarg1='value1'
        )
        
        assert result == mock_instance
        mock_import.assert_called_once_with('some.module')
        mock_class.assert_called_once_with('arg1', kwarg1='value1')
