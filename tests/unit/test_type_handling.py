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
        ('Number', 'integer'),  # Number maps to integer now (proper handling)
        ('Boolean', 'boolean'),
        ('Integer', 'integer'),
        ('Float', 'float'),
    ])
    def test_type_mapping(self, adapter, input_type, expected_type):
        """Test that BiaflowsSchemaAdapter maps types correctly."""
        result = adapter._map_biaflows_type(input_type)
        assert result == expected_type


# NOTE: Tests for convert_cytype_to_omtype and str_to_class methods have been
# moved to test_schema_parsers.py as these functions were moved to that module


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
