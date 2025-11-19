"""
Unit tests for BIOMERO schema type handling using pytest.

This module tests the type mapping and conversion logic in the schema parsers.
"""

import pytest
from unittest.mock import patch, MagicMock

from biomero.schema_parsers import (
    DescriptorParserFactory, BiaflowsSchemaAdapter
)


class TestBiaflowsTypeMapping:
    """Test cases for BiaflowsSchemaAdapter type mappings."""
    
    @pytest.fixture
    def adapter(self):
        """Create a BiaflowsSchemaAdapter instance."""
        return BiaflowsSchemaAdapter()
    
    @pytest.mark.parametrize("input_type,expected_type", [
        ('String', 'string'),
        ('Number', 'float'),  # Default number mapping
        ('Boolean', 'boolean'),
        ('Integer', 'integer'),
        ('Float', 'float'),
    ])
    def test_type_mapping(self, adapter, input_type, expected_type):
        """Test that BiaflowsSchemaAdapter maps types correctly."""
        result = adapter._map_biaflows_type(input_type)
        assert result == expected_type


class TestOMEROTypeConversion:
    """Test cases for OMERO script type conversion logic."""
    
    @pytest.fixture
    def mock_slurm_client(self):
        """Create a mocked SlurmClient instance with mocked OMERO scripts."""
        from biomero.slurm_client import SlurmClient
        
        # Mock the OMERO scripts classes
        mock_int = MagicMock()
        mock_int.__class__.__name__ = 'Int'
        mock_float = MagicMock()
        mock_float.__class__.__name__ = 'Float'
        mock_bool = MagicMock()
        mock_bool.__class__.__name__ = 'Bool'
        mock_string = MagicMock()
        mock_string.__class__.__name__ = 'String'
        
        # Mock the str_to_class method to return appropriate mock objects
        def mock_str_to_class(module_name, class_name, *args, **kwargs):
            if class_name == 'Int':
                return mock_int
            elif class_name == 'Float':
                return mock_float
            elif class_name == 'Bool':
                return mock_bool
            elif class_name == 'String':
                return mock_string
            else:
                raise ValueError(f"Unknown class: {class_name}")
        
        with patch.object(SlurmClient, '__init__', lambda x: None):
            client = SlurmClient()
            client.str_to_class = MagicMock(side_effect=mock_str_to_class)
            return client
    
    @pytest.mark.parametrize("param_type,default_value,expected_class", [
        ('Number', 1, 'Int'),
        ('Number', 1.0, 'Float'),
        ('integer', None, 'Int'),
        ('float', None, 'Float'),
        ('boolean', None, 'Bool'),
        ('string', None, 'String'),
    ])
    def test_convert_cytype_to_omtype(self, mock_slurm_client, param_type,
                                      default_value, expected_class):
        """Test conversion from biaflows/biomero types to OMERO types."""
        # Test conversion using the actual method signature
        result = mock_slurm_client.convert_cytype_to_omtype(
            param_type, default_value, "test_param"
        )
        assert result.__class__.__name__ == expected_class
    
    def test_number_type_inference_from_default(self, mock_slurm_client):
        """Test that Number type is inferred from default value type."""
        # Integer default should create Int
        int_result = mock_slurm_client.convert_cytype_to_omtype(
            'Number', 42, "int_test"
        )
        assert int_result.__class__.__name__ == 'Int'

        # Float default should create Float
        float_result = mock_slurm_client.convert_cytype_to_omtype(
            'Number', 42.5, "float_test"
        )
        assert float_result.__class__.__name__ == 'Float'
    
    def test_explicit_types_override_default_inference(
            self, mock_slurm_client):
        """Test explicit integer/float types override default inference."""
        # Explicit integer type should create Int regardless of default
        int_result = mock_slurm_client.convert_cytype_to_omtype(
            'integer', 42.7, "int_test"
        )
        assert int_result.__class__.__name__ == 'Int'
        
        # Explicit float type should create Float regardless of default
        float_result = mock_slurm_client.convert_cytype_to_omtype(
            'float', 42, "float_test"
        )
        assert float_result.__class__.__name__ == 'Float'


class TestTypeHandlingIntegration:
    """Integration tests for type handling across the entire pipeline."""
    
    @pytest.mark.skip(
        reason="Requires biomero-schema with int support in default_value "
               "field. Test passes with local biomero-schema but fails with "
               "main branch version that converts integers to floats."
    )
    def test_integer_float_distinction_preserved(self):
        """Test that integer/float distinction is preserved in parsing."""
        # Test data with explicit integer and float types
        biomero_descriptor = {
            "name": "Type Test Workflow",
            "description": "Test integer/float type handling",
            "schema-version": "1.0.0",
            "container-image": {"image": "test:latest", "type": "oci"},
            "command-line": "python test.py",
            "authors": [{"name": "Test Author"}],
            "citations": [{"name": "Test Tool", "license": "MIT"}],
            "inputs": [
                {
                    "id": "int_param",
                    "type": "integer",
                    "name": "Integer Parameter",
                    "default-value": 5,
                    "optional": True
                },
                {
                    "id": "float_param",
                    "type": "float",
                    "name": "Float Parameter",
                    "default-value": 5.5,
                    "optional": True
                },
                {
                    "id": "number_int",
                    "type": "Number",
                    "name": "Number with Int Default",
                    "default-value": 10,
                    "optional": True
                },
                {
                    "id": "number_float",
                    "type": "Number",
                    "name": "Number with Float Default",
                    "default-value": 10.5,
                    "optional": True
                }
            ],
            "outputs": []
        }
        
        parsed = DescriptorParserFactory.parse_descriptor(biomero_descriptor)
        
        # Check that types are properly preserved
        int_param = next(p for p in parsed.inputs if p.id == "int_param")
        assert int_param.type == "integer"
        
        float_param = next(p for p in parsed.inputs if p.id == "float_param")
        assert float_param.type == "float"
        
        number_int = next(p for p in parsed.inputs if p.id == "number_int")
        assert number_int.type == "Number"
        assert number_int.default_value == 10
        
        number_float = next(p for p in parsed.inputs if p.id == "number_float")
        assert number_float.type == "Number"
        assert number_float.default_value == 10.5
        
        # Test OMERO type conversion
        from biomero.slurm_client import SlurmClient
        with patch.object(SlurmClient, '__init__', lambda x: None):
            client = SlurmClient()
            
            # Mock str_to_class like in the fixture
            mock_int = MagicMock()
            mock_int.__class__.__name__ = 'Int'
            mock_float = MagicMock()
            mock_float.__class__.__name__ = 'Float'
            
            def mock_str_to_class(module_name, class_name, *args, **kwargs):
                if class_name == 'Int':
                    return mock_int
                elif class_name == 'Float':
                    return mock_float
                else:
                    # Default behavior for unexpected calls
                    return None
            
            client.str_to_class = MagicMock(side_effect=mock_str_to_class)
            
            int_omero = client.convert_cytype_to_omtype(
                int_param.type, int_param.default_value, "int_param"
            )
            assert int_omero.__class__.__name__ == 'Int'
            
            float_omero = client.convert_cytype_to_omtype(
                float_param.type, float_param.default_value, "float_param"
            )
            assert float_omero.__class__.__name__ == 'Float'
            
            # Number types should infer from default value
            number_int_omero = client.convert_cytype_to_omtype(
                number_int.type, number_int.default_value, "number_int"
            )
            assert number_int_omero.__class__.__name__ == 'Int'
            
            number_float_omero = client.convert_cytype_to_omtype(
                number_float.type,
                number_float.default_value,
                "number_float"
            )
            assert number_float_omero.__class__.__name__ == 'Float'
