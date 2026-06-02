"""
Unit tests for BIOMERO schema parsers using pytest.

This module tests the new schema parser system with both legacy biaflows
format and the new biomero-schema format using real test files.
"""

import json
import pytest
from pathlib import Path
from unittest.mock import patch, Mock
import yaml

from biomero.schema_parsers import (
    DescriptorParserFactory,
    detect_schema_format,
    convert_schema_type_to_omero,
    convert_schema_type_to_omero_rtype,
    create_class_instance
)
from biomero_schema import BIOMERO_SCHEMA_VERSION



class TestSchemaFormatDetection:
    """Test cases for schema format detection."""
    
    @pytest.mark.parametrize("descriptor_data,expected_format", [
        ({'schema-version': 'cytomine-0.1'}, 'BIAFLOWS'),
        ({'schema-version': 'biomero-0.1'}, 'biomero-schema'),
        ({'container-image': {}, 'inputs': []}, 'BIAFLOWS'),
        ({'docker_image': {}}, 'bilayers'),
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
        assert parsed.schema_version == BIOMERO_SCHEMA_VERSION  # normalized
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

    def test_biaflows_number_int_default_stays_integer(self, biaflows_descriptor):
        """Number param with int default parses as type 'integer' with int default_value."""
        parsed = DescriptorParserFactory.parse_descriptor(biaflows_descriptor)
        radius = next(p for p in parsed.inputs if p.id == 'ij_radius')
        assert radius.type == 'integer'
        assert isinstance(radius.default_value, int), (
            f"default_value must be int, got {type(radius.default_value)}: {radius.default_value!r}"
        )
        assert radius.default_value == 5

    def test_biaflows_number_float_default_stays_float(self, biaflows_descriptor):
        """Number param with float default parses as type 'float' with float default_value."""
        parsed = DescriptorParserFactory.parse_descriptor(biaflows_descriptor)
        threshold = next(p for p in parsed.inputs if p.id == 'ij_threshold')
        assert threshold.type == 'float'
        assert isinstance(threshold.default_value, float)
        assert threshold.default_value == -0.5


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
        assert parsed.schema_version == BIOMERO_SCHEMA_VERSION
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


class TestBilayersSchemaParser:
    """Test cases for bilayers format parsing."""

    def test_bilayers_format_detection(self, bilayers_descriptor):
        """Test that bilayers format is detected correctly."""
        detected = detect_schema_format(bilayers_descriptor)
        assert detected == 'bilayers'

    def test_bilayers_parsing(self, bilayers_descriptor):
        """Test parsing of bilayers format."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)

        # Test basic metadata
        assert parsed.name == "Cellpose"
        assert "Deep Learning algorithm for cell segmentation in microscopy images" in parsed.description
        assert parsed.schema_version == "bilayers-1.0.0"
        container_image = "cellprofiler/runcellpose_no_pretrained"
        assert parsed.container_image.image == container_image
        assert parsed.container_image.type == "docker"
        assert "python -m cellpose" in parsed.command_line

        # Test parameters
        assert len(parsed.inputs) >= 9
        assert len(parsed.outputs) >= 1

        # Check parameter names exist (bilayers uses label as name)
        param_names = [p.name for p in parsed.inputs]
        assert "Diameter" in param_names
        assert "PreTrained Model" in param_names

    def test_bilayers_mandatory_image_input_set_by_server(self, bilayers_descriptor):
        """Mandatory image inputs (type=image, optional=False) must be set-by-server."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        dir_param = next(p for p in parsed.inputs if p.id == 'dir')
        assert dir_param.set_by_server is True
        assert dir_param.optional is False

    def test_bilayers_optional_file_input_set_by_server(self, bilayers_descriptor):
        """Optional file inputs (type=file, optional=True) are set-by-server too
        (biomero skips them in INPARAMS because they are optional)."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        model_param = next(p for p in parsed.inputs if p.id == 'custom_model')
        assert model_param.set_by_server is True
        assert model_param.optional is True

    def test_bilayers_output_dir_set_param(self, bilayers_descriptor):
        """save_dir with output_dir_set=True must have output_dir_set=True
        and set-by-server=True in the parsed schema."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        save_dir = next(p for p in parsed.inputs if p.id == 'save_dir')
        assert save_dir.output_dir_set is True
        assert save_dir.set_by_server is True

    def test_bilayers_output_dir_set_survives_model_dump(self, bilayers_descriptor):
        """output-dir-set must survive model_dump(by_alias=True) for downstream use."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        dumped = parsed.model_dump(by_alias=True)
        save_dir = next(p for p in dumped['inputs'] if p['id'] == 'save_dir')
        assert save_dir['output-dir-set'] is True

    def test_bilayers_regular_param_not_set_by_server(self, bilayers_descriptor):
        """Regular user-facing parameters must NOT be set-by-server."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        diameter = next(p for p in parsed.inputs if p.id == 'diameter')
        assert not diameter.set_by_server
        assert not diameter.output_dir_set

    def test_bilayers_image_format_preserved_as_list(self, bilayers_descriptor):
        """format list from bilayers YAML must be passed through intact, not truncated to first element."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        dir_param = next(p for p in parsed.inputs if p.id == 'dir')
        assert dir_param.format == ["tiff", "ometiff", "omezarr"]

    def test_bilayers_image_subtype_preserved_as_list(self, bilayers_descriptor):
        """subtype list from bilayers YAML must be passed through intact, not truncated to first element."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        dir_param = next(p for p in parsed.inputs if p.id == 'dir')
        assert dir_param.sub_type == ["grayscale", "color", "binary"]

    def test_bilayers_requires_zarr_true_when_omezarr_in_format_list(self, bilayers_descriptor):
        """requires_zarr must be True when omezarr appears anywhere in a format list."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        assert parsed.requires_zarr is True

    def test_bilayers_requires_plate_false_when_no_plate_subtype(self, bilayers_descriptor):
        """requires_plate must be False when no input has plate in its subtype."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        assert parsed.requires_plate is False

    def test_bilayers_mode_beginner_preserved(self, bilayers_descriptor):
        """mode: beginner from YAML must be passed through to the parsed schema."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        dir_param = next(p for p in parsed.inputs if p.id == 'dir')
        assert dir_param.mode == "beginner"

    def test_bilayers_mode_advanced_preserved(self, bilayers_descriptor):
        """mode: advanced from YAML must be passed through to the parsed schema."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        custom_model = next(p for p in parsed.inputs if p.id == 'custom_model')
        assert custom_model.mode == "advanced"

    def test_bilayers_value_choices_labels_when_labels_differ_from_values(self, bilayers_descriptor):
        """value_choices_labels must be populated when option labels differ from values.
        pretrained_model has labels Cyto/Nuclei/Cyto2/Ignore vs values cyto/nuclei/cyto2/ignore.
        """
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        pretrained = next(p for p in parsed.inputs if p.id == 'pretrained_model')
        assert pretrained.value_choices == ["cyto", "nuclei", "cyto2", "ignore"]
        assert pretrained.value_choices_labels == ["Cyto", "Nuclei", "Cyto2", "Ignore"]

    def test_bilayers_value_choices_labels_none_when_labels_equal_values(self, bilayers_descriptor):
        """value_choices_labels must be None when all option labels equal their values.
        channel_axis has label 0 value 0 and label 2 value 2 — identical when stringified.
        """
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        channel_axis = next(p for p in parsed.inputs if p.id == 'channel_axis')
        assert channel_axis.value_choices == [0, 2]
        assert channel_axis.value_choices_labels is None

    def test_bilayers_integer_radio_default_is_int_not_float(self, bilayers_descriptor):
        """channel_axis default value is int, not float."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        channel_axis = next(p for p in parsed.inputs if p.id == 'channel_axis')
        assert isinstance(channel_axis.default_value, int), (
            f"default_value must be int, got {type(channel_axis.default_value)}: "
            f"{channel_axis.default_value!r}"
        )
        assert channel_axis.default_value == 0

    def test_bilayers_integer_radio_default_str_matches_string_choices(self, bilayers_descriptor):
        """str(default_value) is in [str(c) for c in value_choices]."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        channel_axis = next(p for p in parsed.inputs if p.id == 'channel_axis')
        str_choices = [str(c) for c in channel_axis.value_choices]
        assert str(channel_axis.default_value) in str_choices, (
            f"str({channel_axis.default_value!r}) not in {str_choices}"
        )

    def test_bilayers_value_choices_labels_survive_model_dump(self, bilayers_descriptor):
        """value-choices-labels survives model_dump(by_alias=True)."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        dumped = parsed.model_dump(by_alias=True)
        pretrained = next(p for p in dumped['inputs'] if p['id'] == 'pretrained_model')
        assert pretrained['value-choices-labels'] == ["Cyto", "Nuclei", "Cyto2", "Ignore"]



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


class TestBilayersSchemaVersionPreservation:
    """Test that bilayers descriptors get schema-version='bilayers-1.0.0'
    so downstream code can reliably detect the source format."""

    def test_bilayers_schema_version_in_parsed_object(self, bilayers_descriptor):
        """Parsed bilayers descriptor has schema_version starting with 'bilayers'."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        assert parsed.schema_version.startswith("bilayers")

    def test_bilayers_schema_version_in_model_dump(self, bilayers_descriptor):
        """schema-version survives model_dump(by_alias=True) as 'bilayers-1.0.0'."""
        parsed = DescriptorParserFactory.parse_descriptor(bilayers_descriptor)
        dumped = parsed.model_dump(by_alias=True)
        assert dumped["schema-version"] == "bilayers-1.0.0"

    def test_biaflows_schema_version_not_bilayers(self, biaflows_descriptor):
        """BIAFLOWS descriptor does NOT get a bilayers schema-version."""
        parsed = DescriptorParserFactory.parse_descriptor(biaflows_descriptor)
        assert not parsed.schema_version.startswith("bilayers")

    def test_biomero_schema_version_not_bilayers(self, biomero_descriptor):
        """biomero-schema descriptor does NOT get a bilayers schema-version."""
        parsed = DescriptorParserFactory.parse_descriptor(biomero_descriptor)
        assert not parsed.schema_version.startswith("bilayers")

    @pytest.mark.parametrize("schema_version,expected_is_bilayers", [
        ("bilayers-1.0.0", True),
        ("bilayers-2.0.0", True),
        ("1.0.0", False),
        ("biomero-0.1", False),
        ("cytomine-0.1", False),
        ("", False),
    ])
    def test_is_bilayers_detection_logic(self, schema_version, expected_is_bilayers):
        """Test the startswith('bilayers') check used in slurm_client._is_bilayers_workflow."""
        descriptor = {"schema-version": schema_version}
        result = descriptor.get("schema-version", "").startswith("bilayers")
        assert result == expected_is_bilayers
