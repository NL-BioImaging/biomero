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
    convert_schema_type_to_omero_rtype,
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
    
    @pytest.mark.parametrize("schema_type,default_value,expected_class", [
        ('Number', 42, 'Int'),
        ('Number', 42.0, 'Float'),
        ('integer', 10, 'Int'),
        ('float', 3.14, 'Float'),
        ('Boolean', True, 'Bool'),
        ('boolean', False, 'Bool'),
        ('String', 'test', 'String'),
        ('string', 'test', 'String'),
        ('image', None, 'String'),
        ('file', None, 'String'),
    ])
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_scripts(
            self, mock_create_class, schema_type, default_value,
            expected_class):
        """Test schema type conversion to OMERO scripts."""
        mock_create_class.return_value = "mocked_result"
        
        result = convert_schema_type_to_omero(
            schema_type, default_value, 'param1',
            description='test', optional=True)
        
        mock_create_class.assert_called_once_with(
            'omero.scripts', expected_class, 'param1',
            description='test', optional=True)
        assert result == "mocked_result"
    
    @pytest.mark.parametrize(
        "schema_type,default_value,value,expected_class,expected_value", [
        ('Number', 42, '100', 'rint', 100),
        ('Number', 42.0, '100.5', 'rfloat', 100.5),
        ('integer', 10, '100', 'rint', 100),
        ('float', 3.14, '100.5', 'rfloat', 100.5),
        ('string', 'test', 'hello', 'rstring', 'hello'),
        ('String', 'test', 'hello', 'rstring', 'hello'),
        ('image', None, '/path/img.tif', 'rstring', '/path/img.tif'),
        ('file', None, '/path/data.csv', 'rstring', '/path/data.csv'),
    ])
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_rtypes(
            self, mock_create_class, schema_type, default_value, value,
            expected_class, expected_value):
        """Test schema type conversion to OMERO rtypes."""
        mock_create_class.return_value = "mocked_result"
        
        result = convert_schema_type_to_omero(
            schema_type, default_value, value, rtype=True)
        
        mock_create_class.assert_called_once_with(
            'omero.rtypes', expected_class, expected_value)
        assert result == "mocked_result"
    
    @pytest.mark.parametrize("value,expected_bool", [
        ('true', True), ('True', True), ('TRUE', True),
        ('1', True), ('yes', True), ('YES', True),
        ('on', True), ('ON', True),
        ('false', False), ('False', False), ('FALSE', False),
        ('0', False), ('no', False), ('NO', False),
        ('off', False), ('OFF', False),
        ('random', False), ('', False),
    ])
    @patch('biomero.schema_parsers.create_class_instance')
    def test_boolean_value_parsing(
            self, mock_create_class, value, expected_bool):
        """Test boolean value parsing edge cases."""
        mock_create_class.return_value = "mocked_rbool"
        
        convert_schema_type_to_omero('boolean', True, value, rtype=True)
        
        mock_create_class.assert_called_once_with(
            'omero.rtypes', 'rbool', expected_bool)
    
    @pytest.mark.parametrize("value,expected_int", [
        ('100', 100), ('100.0', 100), ('100.9', 100),
        ('-42', -42), ('-42.7', -42),
        ('0', 0), ('0.0', 0),
    ])
    @patch('biomero.schema_parsers.create_class_instance')
    def test_integer_value_parsing(
            self, mock_create_class, value, expected_int):
        """Test integer value parsing edge cases."""
        mock_create_class.return_value = "mocked_rint"
        
        convert_schema_type_to_omero('integer', 42, value, rtype=True)
        
        mock_create_class.assert_called_once_with(
            'omero.rtypes', 'rint', expected_int)
    
    @pytest.mark.parametrize("value,expected_float", [
        ('100', 100.0), ('100.5', 100.5), ('-42.7', -42.7),
        ('0', 0.0), ('0.0', 0.0), ('3.14159', 3.14159),
    ])
    @patch('biomero.schema_parsers.create_class_instance')
    def test_float_value_parsing(
            self, mock_create_class, value, expected_float):
        """Test float value parsing edge cases."""
        mock_create_class.return_value = "mocked_rfloat"
        
        convert_schema_type_to_omero('float', 3.14, value, rtype=True)
        
        mock_create_class.assert_called_once_with(
            'omero.rtypes', 'rfloat', expected_float)
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_convert_schema_type_to_omero_rtype_wrapper(
            self, mock_create_class):
        """Test that convert_schema_type_to_omero_rtype is a proper wrapper."""
        mock_create_class.return_value = "mocked_rfloat"
        
        result = convert_schema_type_to_omero_rtype('float', 42.0, '100')
        
        mock_create_class.assert_called_once_with(
            "omero.rtypes", "rfloat", 100.0)
        assert result == "mocked_rfloat"
    
    @patch('biomero.schema_parsers.create_class_instance')
    def test_omero_not_available(self, mock_create_class):
        """Test behavior when OMERO is not available."""
        mock_create_class.return_value = None
        
        result = convert_schema_type_to_omero('float', 42.0, '100.5', rtype=True)
        
        assert result is None
    
    def test_unsupported_schema_type(self):
        """Test that unsupported types raise ValueError."""
        with pytest.raises(ValueError, match="Unsupported schema type 'unknown'"):
            convert_schema_type_to_omero('unknown', 'test', 'param')
    
    def test_unsupported_schema_type_rtype(self):
        """Test that unsupported types raise ValueError for rtypes too."""
        with pytest.raises(ValueError, match="Unsupported schema type 'unknown'"):
            convert_schema_type_to_omero('unknown', 'test', 'param', rtype=True)


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
