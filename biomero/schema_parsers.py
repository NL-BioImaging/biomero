# -*- coding: utf-8 -*-
# Copyright 2024 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""
Workflow descriptor schema parsers for BIOMERO.

This module provides parsing of different workflow descriptor formats into
the biomero-schema format (our internal representation). The biomero-schema
is the primary format, with legacy formats adapted to fit this model.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List
import logging

# biomero-schema is our internal representation
from biomero_schema.models import (
    WorkflowSchema, Parameter, ContainerImage, Author, Institution, Citation
)

logger = logging.getLogger(__name__)


class WorkflowDescriptorAdapter(ABC):
    """Abstract adapter to convert legacy formats to biomero-schema format."""

    @abstractmethod
    def adapt_to_biomero_schema(
        self, descriptor_data: Dict[str, Any]
    ) -> WorkflowSchema:
        """Convert raw descriptor data to validated biomero-schema format."""
        pass

    @abstractmethod
    def get_supported_formats(self) -> List[str]:
        """Return list of supported schema format identifiers."""
        pass


class BiomeroSchemaAdapter(WorkflowDescriptorAdapter):
    """Direct adapter for biomero-schema format (no conversion needed)."""

    def get_supported_formats(self) -> List[str]:
        return ["biomero-schema", "biomero-0.1"]

    def adapt_to_biomero_schema(
        self, descriptor_data: Dict[str, Any]
    ) -> WorkflowSchema:
        """Parse and validate biomero-schema descriptor directly."""
        # Direct Pydantic validation - this IS our internal representation
        return WorkflowSchema.model_validate(descriptor_data)


class BiaflowsSchemaAdapter(WorkflowDescriptorAdapter):
    """Adapter to convert BIAFLOWS/cytomine-0.1 format to biomero-schema."""

    def get_supported_formats(self) -> List[str]:
        return ["BIAFLOWS", "cytomine-0.1"]

    def adapt_to_biomero_schema(
        self, descriptor_data: Dict[str, Any]
    ) -> WorkflowSchema:
        """Convert BIAFLOWS descriptor to biomero-schema format."""

        # Convert container info
        container_info = descriptor_data.get("container-image", {})
        container_image = ContainerImage(
            image=container_info.get("image", ""),
            type=container_info.get("type", "singularity")
        )

        # Convert input parameters, filtering out cytomine-specific ones
        inputs = []
        for input_param in descriptor_data.get("inputs", []):
            if input_param.get("id", "").startswith("cytomine"):
                continue

            # Map BIAFLOWS types to biomero-schema types
            param_type = self._map_biaflows_type(
                input_param.get("type", "String"),
                input_param.get("default-value")
            )

            # Build command line info
            param_id = input_param.get("id", "")
            cmd_flag = input_param.get("command-line-flag", f"--{param_id}")
            cmd_flag = cmd_flag.replace("@id", param_id)
            value_key = input_param.get("value-key", f"@{param_id.upper()}")

            # Build the parameter data with alias names for Pydantic validation
            param_data = {
                "id": param_id,
                "type": param_type,
                "name": input_param.get("name") or param_id,
                "description": input_param.get("description", ""),
                "value-key": value_key,  # Use alias name
                "command-line-flag": cmd_flag,  # Use alias name
                "default-value": input_param.get("default-value"),  # Alias
                "optional": input_param.get("optional", False),
                "set-by-server": input_param.get("set-by-server", False),
            }
            
            # Create Parameter using model_validate with alias names
            input_param_obj = Parameter.model_validate(param_data)
            inputs.append(input_param_obj)

        # BIAFLOWS doesn't have explicit outputs, create empty list
        outputs = []

        # Create minimal author info (BIAFLOWS doesn't have this)
        authors = [Author(name="Unknown", email=None)]
        institutions = []
        citations = [Citation(
            name="Unknown Tool",
            license="Unknown",
            description="No citation information available"
        )]

        # Build the biomero-schema object
        biomero_descriptor = WorkflowSchema(
            schema_version="1.0.0",  # Normalize to biomero-schema version
            name=descriptor_data.get("name", ""),
            description=descriptor_data.get("description"),
            command_line=descriptor_data.get("command-line"),
            container_image=container_image,
            inputs=inputs,
            outputs=outputs,
            authors=authors,
            institutions=institutions,
            citations=citations,
            problem_class=None,  # BIAFLOWS doesn't have this
            configuration=None   # BIAFLOWS doesn't have resource requirements
        )

        return biomero_descriptor

    def _map_biaflows_type(
        self, biaflows_type: str, default_value: Any = None
    ) -> str:
        """Map BIAFLOWS parameter types to biomero-schema types.
        
        BIAFLOWS Number type is converted to specific integer/float based on
        default value type. biomero-schema should never contain Number type.
        """
        if biaflows_type == 'Number':
            # Always convert Number type based on default value
            if default_value is not None and isinstance(default_value, float):
                return 'float'
            else:
                # Default to integer for Number type (most common case)
                return 'integer'
        
        # Direct mapping for other types
        type_mapping = {
            'String': 'string',
            'Boolean': 'boolean',
            'Integer': 'integer',
            'Float': 'float',
            'Domain': 'string',
            'ListDomain': 'string',
        }
        return type_mapping.get(biaflows_type, 'string')


def detect_schema_format(descriptor_data: Dict[str, Any]) -> str:
    """
    Auto-detect schema format from descriptor data.

    Args:
        descriptor_data: Raw descriptor dictionary

    Returns:
        Format identifier string

    Raises:
        ValueError: If format cannot be detected
    """
    # Check for CWL
    if "cwlVersion" in descriptor_data:
        raise ValueError("CWL format not yet supported")

    # Check for OpenAPI
    if ("$schema" in descriptor_data and
            "openapi" in descriptor_data["$schema"]):
        raise ValueError("OpenAPI format not yet supported")

    # Check schema-version field
    schema_version = descriptor_data.get("schema-version", "")
    if schema_version:
        if schema_version.startswith("cytomine"):
            return "BIAFLOWS"
        elif (schema_version.startswith("biomero") or
              schema_version.startswith("1.")):
            return "biomero-schema"

    # Fallback heuristics
    if "container-image" in descriptor_data and "inputs" in descriptor_data:
        # Looks like a workflow descriptor, assume cytomine format
        return "BIAFLOWS"

    keys = list(descriptor_data.keys())
    raise ValueError(
        f"Unable to detect schema format from descriptor: {keys}"
    )


class WorkflowDescriptorParser:
    """Main parser that converts any supported format to biomero-schema."""

    _adapters = {
        "BIAFLOWS": BiaflowsSchemaAdapter,
        "cytomine-0.1": BiaflowsSchemaAdapter,
        "biomero-schema": BiomeroSchemaAdapter,
        "biomero-0.1": BiomeroSchemaAdapter,
    }

    @classmethod
    def parse_descriptor(
        cls, descriptor_data: Dict[str, Any]
    ) -> WorkflowSchema:
        """
        Auto-detect format and parse descriptor to biomero-schema.

        Args:
            descriptor_data: Raw descriptor dictionary

        Returns:
            Validated WorkflowSchema (biomero-schema format)

        Raises:
            ValueError: If format not supported or validation fails
        """
        schema_format = detect_schema_format(descriptor_data)

        adapter_class = cls._adapters.get(schema_format)
        if not adapter_class:
            available = list(cls._adapters.keys())
            raise ValueError(
                f"No adapter available for schema format '{schema_format}'. "
                f"Available: {available}"
            )

        adapter = adapter_class()
        logger.info(
            f"Parsing workflow descriptor with format: {schema_format}"
        )

        # Convert to biomero-schema and validate with Pydantic
        return adapter.adapt_to_biomero_schema(descriptor_data)

    @classmethod
    def register_adapter(cls, schema_format: str, adapter_class: type):
        """Register a new adapter for a schema format."""
        cls._adapters[schema_format] = adapter_class


def create_class_instance(module_name: str, class_name: str, *args, **kwargs):
    """
    Create a class instance from a string reference.
    
    Args:
        module_name (str): The name of the module.
        class_name (str): The name of the class.
        *args: Additional positional arguments for the class constructor.
        **kwargs: Additional keyword arguments for the class constructor.
    
    Returns:
        object: An instance of the specified class, or None if the class or
            module does not exist.
    """
    import importlib
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        module_ = importlib.import_module(module_name)
        try:
            class_ = getattr(module_, class_name)(*args, **kwargs)
        except AttributeError:
            logger.error('Class does not exist')
            return None
    except ImportError:
        logger.error('Module does not exist')
        return None
    return class_


def convert_schema_type_to_omero(
        schema_type: str, default_value, *args, **kwargs):
    """
    Convert a schema type (BIAFLOWS/biomero-schema) to an OMERO type.
    
    Args:
        schema_type (str): The schema type to convert (Number, String,
            Boolean, integer, float, etc.)
        default_value: The default value. Used to distinguish between float
            and int for Number type.
        *args: Additional positional arguments.
        **kwargs: Additional keyword arguments.
    
    Returns:
        Any: The converted OMERO type class instance or None if errors
            occurred.
    """
    if schema_type == 'Number':
        if isinstance(default_value, float):
            return create_class_instance(
                "omero.scripts", "Float", *args, **kwargs)
        else:
            return create_class_instance(
                "omero.scripts", "Int", *args, **kwargs)
    elif schema_type == 'integer':
        return create_class_instance(
            "omero.scripts", "Int", *args, **kwargs)
    elif schema_type == 'float':
        return create_class_instance(
            "omero.scripts", "Float", *args, **kwargs)
    elif schema_type == 'Boolean':
        return create_class_instance(
            "omero.scripts", "Bool", *args, **kwargs)
    elif schema_type == 'boolean':
        return create_class_instance(
            "omero.scripts", "Bool", *args, **kwargs)
    elif schema_type == 'String':
        return create_class_instance(
            "omero.scripts", "String", *args, **kwargs)
    elif schema_type == 'string':
        return create_class_instance(
            "omero.scripts", "String", *args, **kwargs)
    elif schema_type == 'image':
        # Image type - for OMERO, this is typically a String parameter
        # that accepts image IDs or paths
        return create_class_instance(
            "omero.scripts", "String", *args, **kwargs)
    elif schema_type == 'file':
        # File type - for OMERO, this is typically a String parameter
        # that accepts file paths
        return create_class_instance(
            "omero.scripts", "String", *args, **kwargs)
    else:
        raise ValueError(f"Unsupported schema type '{schema_type}'")


# Backward compatibility aliases
DescriptorParserFactory = WorkflowDescriptorParser
# biomero-schema IS our internal representation
ParsedWorkflowDescriptor = WorkflowSchema
