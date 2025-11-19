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
                input_param.get("type", "String")
            )

            # Build command line info
            param_id = input_param.get("id", "")
            cmd_flag = input_param.get("command-line-flag", f"--{param_id}")
            cmd_flag = cmd_flag.replace("@id", param_id)
            value_key = input_param.get("value-key", f"@{param_id.upper()}")

            input_param_obj = Parameter(
                id=param_id,
                name=input_param.get("name") or param_id,
                description=input_param.get("description", ""),
                type=param_type,
                default_value=input_param.get("default-value"),
                optional=input_param.get("optional", False),
                set_by_server=input_param.get("set-by-server", False),
                command_line_flag=cmd_flag,
                value_key=value_key
            )
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

    def _map_biaflows_type(self, biaflows_type: str) -> str:
        """Map BIAFLOWS parameter types to biomero-schema types."""
        type_mapping = {
            'String': 'string',
            'Number': 'float',  # BIAFLOWS Number is typically float
            'Boolean': 'boolean',
            'Integer': 'integer',  # Explicit integer type
            'Float': 'float',      # Explicit float type
            'Domain': 'string',  # Treat as string for now
            'ListDomain': 'string',  # Treat as string for now
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


# Backward compatibility aliases
DescriptorParserFactory = WorkflowDescriptorParser
# biomero-schema IS our internal representation
ParsedWorkflowDescriptor = WorkflowSchema
