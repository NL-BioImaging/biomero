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

This module provides a generic abstraction for parsing different workflow
descriptor formats (cytomine-0.1, biomero-schema, CWL, etc.) into a
standardized internal representation.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Union
import logging

logger = logging.getLogger(__name__)


class ParsedParameter:
    """Standardized parameter representation across schema formats."""
    
    def __init__(self):
        self.id: str = ""
        self.name: str = ""
        self.description: str = ""
        self.param_type: str = ""  # Normalized type (String, Number, Boolean, etc.)
        self.default_value: Any = None
        self.optional: bool = False
        self.command_flag: str = ""
        self.value_key: str = ""
        self.set_by_server: bool = False
        self.is_output: bool = False  # Flag to identify output parameters
        # Extended metadata for new schema formats
        self.ui_metadata: Dict[str, Any] = {}  # UI-specific properties
        self.format_info: Dict[str, Any] = {}  # Format-specific info (file extensions, etc.)


class ParsedResourceRequirements:
    """Resource requirements from workflow descriptor."""
    
    def __init__(self):
        self.networking: Optional[bool] = None
        self.ram_min: Optional[float] = None  # in MB
        self.cores_min: Optional[float] = None
        self.gpu: Optional[bool] = None
        self.cuda_requirements: Optional[Dict[str, Any]] = None
        self.cpu_avx: Optional[bool] = None
        self.cpu_avx2: Optional[bool] = None


class ParsedWorkflowDescriptor:
    """Standardized workflow descriptor representation."""
    
    def __init__(self):
        self.name: str = ""
        self.description: str = ""
        self.schema_version: str = ""
        self.container_image: str = ""
        self.container_type: str = "singularity"  # default
        self.command_line: str = ""
        self.parameters: List[ParsedParameter] = []
        self.output_parameters: List[ParsedParameter] = []
        self.resource_requirements: Optional[ParsedResourceRequirements] = None
        # Extended metadata for rich schema formats
        self.metadata: Dict[str, Any] = {}  # Schema-specific extras (authors, citations, etc.)


class WorkflowDescriptorParser(ABC):
    """Abstract base class for workflow descriptor parsers."""
    
    @abstractmethod
    def parse_descriptor(self, descriptor_data: Dict[str, Any]) -> ParsedWorkflowDescriptor:
        """Parse raw descriptor data into standardized format."""
        pass
    
    @abstractmethod
    def get_supported_formats(self) -> List[str]:
        """Return list of supported schema format identifiers."""
        pass


class CytomineParser(WorkflowDescriptorParser):
    """Parser for cytomine-0.1 format (legacy BIAFLOWS)."""
    
    def get_supported_formats(self) -> List[str]:
        return ["BIAFLOWS", "cytomine-0.1"]
    
    def parse_descriptor(self, descriptor_data: Dict[str, Any]) -> ParsedWorkflowDescriptor:
        """Parse cytomine-0.1 descriptor into standardized format."""
        descriptor = ParsedWorkflowDescriptor()
        
        # Basic metadata
        descriptor.name = descriptor_data.get("name", "")
        descriptor.description = descriptor_data.get("description", "")
        descriptor.schema_version = descriptor_data.get("schema-version", "cytomine-0.1")
        descriptor.command_line = descriptor_data.get("command-line", "")
        
        # Container info
        container_info = descriptor_data.get("container-image", {})
        descriptor.container_image = container_info.get("image", "")
        descriptor.container_type = container_info.get("type", "singularity")
        
        # Parse input parameters
        for input_param in descriptor_data.get("inputs", []):
            # Skip cytomine-specific parameters
            if input_param.get("id", "").startswith("cytomine"):
                continue
                
            param = ParsedParameter()
            param.id = input_param.get("id", "")
            param.name = input_param.get("name", param.id)
            param.description = input_param.get("description", "")
            param.param_type = input_param.get("type", "String")
            param.default_value = input_param.get("default-value")
            param.optional = input_param.get("optional", False)
            param.set_by_server = input_param.get("set-by-server", False)
            
            # Command line info
            cmd_flag = input_param.get("command-line-flag", f"--{param.id}")
            param.command_flag = cmd_flag.replace("@id", param.id)
            param.value_key = input_param.get("value-key", f"@{param.id.upper()}")
            
            descriptor.parameters.append(param)
        
        return descriptor


class BiomeroSchemaParser(WorkflowDescriptorParser):
    """Parser for new biomero-schema format with Pydantic validation."""
    
    def get_supported_formats(self) -> List[str]:
        return ["biomero-schema", "biomero-0.1"]
    
    def parse_descriptor(self, descriptor_data: Dict[str, Any]) -> ParsedWorkflowDescriptor:
        """Parse biomero-schema descriptor using Pydantic validation."""
        # Import biomero-schema (now a hard dependency)
        from biomero_schema.models import WorkflowSchema
        
        # Validate with Pydantic
        workflow_schema = WorkflowSchema.model_validate(descriptor_data)
        
        # Convert to internal representation
        descriptor = ParsedWorkflowDescriptor()
        
        # Basic metadata
        descriptor.name = workflow_schema.name
        descriptor.description = workflow_schema.description or ""
        descriptor.schema_version = workflow_schema.schema_version
        descriptor.command_line = workflow_schema.command_line or ""
        
        # Container info
        descriptor.container_image = workflow_schema.container_image.image
        descriptor.container_type = workflow_schema.container_image.type
        
        # Resource requirements (new!)
        if workflow_schema.configuration and workflow_schema.configuration.resources:
            resources = ParsedResourceRequirements()
            res = workflow_schema.configuration.resources
            resources.networking = res.networking
            resources.ram_min = res.ram_min
            resources.cores_min = res.cores_min
            resources.gpu = res.gpu
            resources.cuda_requirements = res.cuda_requirements.model_dump() if res.cuda_requirements else None
            resources.cpu_avx = res.cpuAVX
            resources.cpu_avx2 = res.cpuAVX2
            descriptor.resource_requirements = resources
        
        # Parse input parameters
        for i, input_param in enumerate(workflow_schema.inputs):
            param = ParsedParameter()
            param.id = input_param.id
            param.name = input_param.name or input_param.id
            param.description = input_param.description or ""
            param.param_type = self._normalize_type(input_param.type)
            
            # Preserve original default value type for Number parameters
            # Pydantic converts int to float in Union[str, float, bool]
            if (param.param_type == "Number" and
                    i < len(descriptor_data.get("inputs", []))):
                original_default = descriptor_data["inputs"][i].get(
                    "default-value")
                param.default_value = original_default
            else:
                param.default_value = input_param.default_value
            
            param.optional = input_param.optional or False
            param.set_by_server = input_param.set_by_server or False
            
            # Command line info
            param.command_flag = input_param.command_line_flag or f"--{input_param.id}"
            param.value_key = input_param.value_key or f"[{input_param.id.upper()}]"
            
            # Store format-specific info
            if hasattr(input_param, 'format') and input_param.format:
                param.format_info['format'] = input_param.format
            if hasattr(input_param, 'sub_type') and input_param.sub_type:
                param.format_info['sub_type'] = input_param.sub_type
            
            descriptor.parameters.append(param)
        
        # Parse output parameters
        for output_param in workflow_schema.outputs:
            param = ParsedParameter()
            param.id = output_param.id
            param.name = output_param.name or output_param.id
            param.description = output_param.description or ""
            param.param_type = self._normalize_type(output_param.type)
            param.is_output = True  # Mark as output parameter
            
            # Command line info for outputs
            param.command_flag = getattr(output_param, 'command_line_flag', None) or f"--{output_param.id}"
            param.value_key = getattr(output_param, 'value_key', None) or f"[{output_param.id.upper()}]"
            
            # Store format-specific info
            if hasattr(output_param, 'format') and output_param.format:
                param.format_info['format'] = output_param.format
            if hasattr(output_param, 'sub_type') and output_param.sub_type:
                param.format_info['sub_type'] = output_param.sub_type
            
            descriptor.parameters.append(param)  # Add to main parameters list
        
        # Store rich metadata
        descriptor.metadata = {
            'authors': [author.model_dump() for author in workflow_schema.authors],
            'institutions': [inst.model_dump() for inst in workflow_schema.institutions],
            'citations': [cite.model_dump() for cite in workflow_schema.citations],
            'problem_class': workflow_schema.problem_class,
            'configuration': workflow_schema.configuration.model_dump() if workflow_schema.configuration else None
        }
        
        return descriptor
    
    def _normalize_type(self, param_type: str) -> str:
        """Normalize parameter types to consistent naming."""
        type_mapping = {
            'integer': 'integer',  # Keep as integer for proper UI handling
            'float': 'float',      # Keep as float for proper UI handling
            'string': 'String',
            'boolean': 'Boolean',
            'file': 'String',  # For now, treat as OMERO ID
            'image': 'String',  # For now, treat as OMERO ID
            'array': 'String',  # For now, treat as OMERO ID
        }
        return type_mapping.get(param_type.lower(), param_type)


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
        return "CWL"
    
    # Check for OpenAPI
    if "$schema" in descriptor_data and "openapi" in descriptor_data["$schema"]:
        return "OpenAPI"
    
    # Check schema-version field
    schema_version = descriptor_data.get("schema-version", "")
    if schema_version:
        if schema_version.startswith("cytomine"):
            return "BIAFLOWS"
        elif schema_version.startswith("biomero") or schema_version.startswith("1."):
            return "biomero-schema"
    
    # Fallback heuristics
    if "container-image" in descriptor_data and "inputs" in descriptor_data:
        # Looks like a workflow descriptor, assume cytomine format
        return "BIAFLOWS"
    
    raise ValueError(f"Unable to detect schema format from descriptor: {list(descriptor_data.keys())}")


class DescriptorParserFactory:
    """Factory for creating appropriate descriptor parsers."""
    
    _parsers = {
        "BIAFLOWS": CytomineParser,
        "cytomine-0.1": CytomineParser,
        "biomero-schema": BiomeroSchemaParser,
        "biomero-0.1": BiomeroSchemaParser,
    }
    
    @classmethod
    def create_parser(cls, schema_format: str) -> WorkflowDescriptorParser:
        """
        Create parser for given schema format.
        
        Args:
            schema_format: Format identifier
            
        Returns:
            Parser instance
            
        Raises:
            ValueError: If no parser available for format
        """
        parser_class = cls._parsers.get(schema_format)
        if not parser_class:
            available = list(cls._parsers.keys())
            raise ValueError(f"No parser available for schema format '{schema_format}'. Available: {available}")
        
        return parser_class()
    
    @classmethod
    def register_parser(cls, schema_format: str, parser_class: type):
        """Register a new parser for a schema format."""
        cls._parsers[schema_format] = parser_class
    
    @classmethod
    def parse_descriptor(cls, descriptor_data: Dict[str, Any]) -> ParsedWorkflowDescriptor:
        """
        Auto-detect format and parse descriptor.
        
        Args:
            descriptor_data: Raw descriptor dictionary
            
        Returns:
            Parsed workflow descriptor
        """
        schema_format = detect_schema_format(descriptor_data)
        parser = cls.create_parser(schema_format)
        
        logger.info(f"Parsing workflow descriptor with format: {schema_format}")
        return parser.parse_descriptor(descriptor_data)
