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
"""This module defines constants for use with BIOMERO (scripts)"""

import re
from uuid import UUID

IMAGE_EXPORT_SCRIPT = "_SLURM_Image_Transfer.py"
IMAGE_IMPORT_SCRIPT = "SLURM_Get_Results.py"
CONVERSION_SCRIPT = "SLURM_Remote_Conversion.py"
FILE_TRANSFER_SCRIPT = "_SLURM_File_Transfer.py"
RUN_WF_SCRIPT = "SLURM_Run_Workflow.py"
RUN_WF_BATCHED_SCRIPT = "SLURM_Run_Workflow_Batched.py"
LABELS_TO_ROIS_SCRIPT = "Labels2Rois.py"

# Blueprint qualitative color scheme. Keep these values in Python because the
# OMERO scripts do not load the Blueprint JavaScript package.
# https://blueprintjs.com/docs/#core/colors.qualitative-color-schemes
QUALITATIVE_COLOR_SCHEME = (
    "#147EB3",  # cerulean3
    "#29A634",  # forest3
    "#D1980B",  # gold3
    "#D33D17",  # vermilion3
    "#9D3F9D",  # violet3
    "#00A396",  # turquoise3
    "#DB2C6F",  # rose3
    "#8EB125",  # lime3
    "#946638",  # sepia3
    "#7961DB",  # indigo3
)


def resolve_workflow_color(color_override, workflow_id):
    """Return an explicit color.

    Derive the automatic value deterministically from a workflow UUID.
    """
    color = str(color_override or "").strip().upper()
    if color:
        if not re.fullmatch(r"#[0-9A-F]{6}", color):
            raise ValueError(
                "Workflow color must be empty or use #RRGGBB format"
            )
        return color
    return QUALITATIVE_COLOR_SCHEME[
        UUID(str(workflow_id)).int % len(QUALITATIVE_COLOR_SCHEME)
    ]


# ------------------------------------------------------------
# Shared constants used across multiple scripts
# ------------------------------------------------------------

# Shared parameter names
CLEANUP = "Cleanup?"


class slurm_env:
    BIOMERO_SLURM_CONFIG_FILE = "BIOMERO_SLURM_CONFIG_FILE"
    BIOMERO_SHALLOW_ZARR = "BIOMERO_SHALLOW_ZARR"
    SQLALCHEMY_URL = "SQLALCHEMY_URL"
    BIOMERO_SACCT_START_TIME = "BIOMERO_SACCT_START_TIME"
    BIOMERO_SACCT_START_DAYS_AGO = "BIOMERO_SACCT_START_DAYS_AGO"
    BIOMERO_ENV_FILE_SUBMISSION = "BIOMERO_ENV_FILE_SUBMISSION"
    BIOMERO_INJECT_GPU_FLAG = "BIOMERO_INJECT_GPU_FLAG"
    BIOMERO_GPU_PARTITION = "BIOMERO_GPU_PARTITION"
    BIOMERO_GPU_GRES = "BIOMERO_GPU_GRES"
    BIOMERO_GPU_GPUS = "BIOMERO_GPU_GPUS"
    BIOMERO_DEFAULT_PARTITION = "BIOMERO_DEFAULT_PARTITION"
    BIOMERO_SLURM_ZIP_CMD = "BIOMERO_SLURM_ZIP_CMD"
    BIOMERO_IMAGE_PULL_VIA_SBATCH = "BIOMERO_IMAGE_PULL_VIA_SBATCH"
    BIOMERO_PULL_CPUS = "BIOMERO_PULL_CPUS"
    BIOMERO_PULL_MEM = "BIOMERO_PULL_MEM"
    BIOMERO_APPTAINER_TMPDIR = "BIOMERO_APPTAINER_TMPDIR"
    BIOMERO_APPTAINER_CACHEDIR = "BIOMERO_APPTAINER_CACHEDIR"
    BIOMERO_ANALYTICS_REBUILD_START_TIME = "BIOMERO_ANALYTICS_REBUILD_START_TIME"
    BIOMERO_ANALYTICS_REBUILD_DAYS_AGO = "BIOMERO_ANALYTICS_REBUILD_DAYS_AGO"
    GPU_PARTITION = "GPU_PARTITION"
    GPU_GRES = "GPU_GRES"
    GPU_GPUS = "GPU_GPUS"


class conversion:
    # ------------------------------------------------------------
    # SLURM_Remote_Conversion script constants
    # ------------------------------------------------------------
    INPUT_DATA = "Input data"
    SOURCE_FORMAT = "Source format"
    TARGET_FORMAT = "Target format"
    PARENT_WORKFLOW_ID = "Parent_Workflow_ID"


class workflow_batched:
    # ------------------------------------------------------------
    # SLURM_Run_Workflow_Batched script constants
    # ------------------------------------------------------------
    BATCH_SIZE = "Batch_Size"


class file_output_targets:
    """Destination modes for individual non-image workflow outputs."""

    # Missing values from older clients preserve the historical behavior.
    LEGACY = "legacy_input_container"
    AUTO = "auto"
    RESULT_DESTINATION = "result_destination"
    INPUT_CONTAINER = "input_container"
    INPUT_PARENT = "input_parent"
    USER_VALUES = (
        AUTO,
        RESULT_DESTINATION,
        INPUT_CONTAINER,
        INPUT_PARENT,
    )


class workflow:
    # ------------------------------------------------------------
    # SLURM_Run_Workflow script constants
    # ------------------------------------------------------------
    EMAIL = "E-mail"
    SELECT_IMPORT = "Select how to import your results (one or more)"
    OUTPUT_RENAME = "3c) Rename the imported images"
    OUTPUT_PARENT = "1) Zip attachment to parent"
    OUTPUT_ATTACH = "2) Attach to original images"
    OUTPUT_NEW_DATASET = "3a) Import into NEW Dataset"
    OUTPUT_NEW_SCREEN = "3a) Import into NEW Screen"
    OUTPUT_DUPLICATES = "3b) Allow duplicate dataset (name)?"
    OUTPUT_CSV_TABLE = "4) Upload result CSVs as OMERO tables"
    OUTPUT_ATTACH_FILE_OUTPUTS = "5) Attach individual non-image output files"
    OUTPUT_ATTACH_FILE_OUTPUTS_TARGET = "5a) File annotation destination"
    OUTPUT_CREATE_ROIS = "6a) Create ROIs from label images"
    ROI_LABEL_PATTERN = "6b) Label image pattern"
    ROI_SHAPE = "6c) ROI shape"
    ROI_DELETE_LABEL_IMAGES = "6d) Delete label images after ROI creation"
    ROI_CLEAR_EXISTING = "6e) Clear existing ROIs on original images"
    ROI_CLEAR_FILTER = "6f) Clear existing ROI name filter"
    ROI_COLOR = "6g) ROI color override"
    NO = "--NO THANK YOU--"
    USE_ZARR_FORMAT = "Use_ZARR_Format"


class results:
    # ------------------------------------------------------------
    # SLURM_Get_Results script constants
    # ------------------------------------------------------------
    OUTPUT_SLURM_JOB_ID = "SLURM Job Id"
    OUTPUT_COMPLETED_JOB = "Completed Job"
    WORKFLOW_UUID = "workflow_uuid"
    TASK_ID = "Task_ID"
    OUTPUT_ATTACH_PROJECT = "Output - Attach as zip to project?"
    OUTPUT_ATTACH_PROJECT_ID = "Project"
    OUTPUT_ATTACH_DATASET = "Output - Attach as zip to dataset?"
    OUTPUT_ATTACH_DATASET_ID = "Dataset_Attach"
    OUTPUT_ATTACH_PLATE = "Output - Attach as zip to plate?"
    OUTPUT_ATTACH_PLATE_ID = "Plate"
    OUTPUT_ATTACH_OG_IMAGES = "Output - Add as attachment to original images"
    OUTPUT_ATTACH_NEW_DATASET = "Output - Add as new images in NEW dataset"
    OUTPUT_ATTACH_NEW_DATASET_NAME = "New Dataset"
    OUTPUT_ATTACH_NEW_DATASET_DUPLICATE = "Allow duplicate?"
    OUTPUT_ATTACH_NEW_DATASET_RENAME = "Rename imported files?"
    OUTPUT_ATTACH_NEW_DATASET_RENAME_NAME = "Rename"
    OUTPUT_ATTACH_NEW_SCREEN = "Output - Add as new images in NEW screen"
    OUTPUT_ATTACH_NEW_SCREEN_NAME = "New Screen"
    OUTPUT_ATTACH_NEW_SCREEN_DUPLICATE = "Allow duplicate screen name?"
    OUTPUT_ATTACH_NEW_DATASET_ID = "Dataset_ID"
    OUTPUT_ATTACH_NEW_SCREEN_ID = "Screen_ID"
    OUTPUT_ATTACH_NEW_SCREEN_RENAME = "Rename imported screen files?"
    OUTPUT_ATTACH_NEW_SCREEN_RENAME_NAME = "Screen Rename"
    OUTPUT_ATTACH_TABLE = "Output - Add csv files as OMERO.table"
    OUTPUT_ATTACH_TABLE_DATASET = "Attach table to dataset"
    OUTPUT_ATTACH_TABLE_DATASET_ID = "Dataset for table"
    OUTPUT_ATTACH_TABLE_PLATE = "Attach table to plate"
    OUTPUT_ATTACH_TABLE_PLATE_ID = "Plate for table"
    OUTPUT_ATTACH_FILE_OUTPUTS_DATASET = "Attach file outputs to dataset"
    OUTPUT_ATTACH_FILE_OUTPUTS_DATASET_ID = "Dataset for file outputs"
    OUTPUT_ATTACH_FILE_OUTPUTS_PLATE = "Attach file outputs to plate"
    OUTPUT_ATTACH_FILE_OUTPUTS_PLATE_ID = "Plate for file outputs"
    IMPORT_LABEL_ZARRS = "Import_Label_Zarrs"
    IMPORT_ONLY_LABELS = "Import_Only_Labels"
    IMPORT_PLATE_LABEL_PREVIEW = "Import_Plate_Label_Preview"
    PLATE_LABEL_PREVIEW_NAME = "Plate_Label_Preview_Name"
    TEST_WRITE_PERMISSIONS_ONLY = "Test_Write_Permissions_Only"
    WORKFLOW_UUID_OUTPUT = "Workflow_UUID"
    OUTPUT_ATTACH_FILE_OUTPUTS = "Output - Attach non-image output files as annotations"
    OUTPUT_ATTACH_FILE_OUTPUTS_TARGET = "File output destination"
    OUTPUT_CREATE_ROIS = "Create_ROIs"
    ROI_LABEL_PATTERN = "ROI_Label_Pattern"
    ROI_SHAPE = "ROI_Shape"
    ROI_DELETE_LABEL_IMAGES = "ROI_Delete_Label_Images"
    ROI_NAME_PREFIX = "ROI_Name_Prefix"
    ROI_CLEAR_EXISTING = "ROI_Clear_Existing"
    ROI_CLEAR_FILTER = "ROI_Clear_Filter"
    ROI_COLOR = "ROI_Color"
    ROI_TARGET_IMAGE_IDS = "ROI_Target_Image_IDs"
    # Guaranteed container the SLURM job log is force-linked to when no richer
    # attachment target was resolved, so the log is always findable in OMERO
    # (unlinked annotations are effectively invisible to users). Forwarded by
    # SLURM_Run_Workflow as a "DataType:id" string (e.g. "Plate:123" or
    # "Dataset:45"); the import scripts also derive this from the workflow's
    # input objects when the parameter is absent (standalone manual runs).
    LOG_FALLBACK_TARGET = "Log_Fallback_Target"


class transfer:
    # ------------------------------------------------------------
    # SLURM_Image_Transfer script constants
    # ------------------------------------------------------------
    DATA_TYPE = "Data_Type"
    DATA_TYPE_DATASET = 'Dataset'
    DATA_TYPE_IMAGE = 'Image'
    DATA_TYPE_PLATE = 'Plate'
    DATA_TYPE_PROJECT = 'Project'
    DATA_TYPE_SCREEN = 'Screen'
    IDS = "IDs"
    SETTINGS = "Image settings (Required)"
    CHANNELS = "Export_Individual_Channels"
    CHANNELS_GREY = "Individual_Channels_Grey"
    CHANNELS_NAMES = "Channel_Names"
    MERGED = "Export_Merged_Image"
    Z = "Choose_Z_Section"
    Z_DEFAULT = 'Default-Z (last-viewed)'
    Z_ALL = 'ALL Z planes'
    Z_MAXPROJ = 'Max projection'
    Z_OTHER = 'Other (see below)'
    Z_IDX = "OR_specify_Z_index"
    Z_IDX_START = "OR_specify_Z_start_AND..."
    Z_IDX_END = "...specify_Z_end"
    T = "Choose_T_Section"
    T_DEFAULT = 'Default-T (last-viewed)'
    T_ALL = 'ALL T planes'
    T_OTHER = 'Other (see below)'
    T_IDX = "OR_specify_T_index"
    T_IDX_START = "OR_specify_T_start_AND..."
    T_IDX_END = "...specify_T_end"
    ZOOM = "Zoom"
    ZOOM_25 = "25%"
    ZOOM_50 = "50%"
    ZOOM_100 = "100%"
    ZOOM_200 = "200%"
    ZOOM_300 = "300%"
    ZOOM_400 = "400%"
    FORMAT = "Format"
    FORMAT_TIFF = 'TIFF'
    FORMAT_OMETIFF = 'OME-TIFF'
    FORMAT_OMEZARR = 'OME-ZARR'
    OME_VERSION = "OME-Zarr_version"
    RECONSTRUCT_SHALLOW_ZARR = "Reconstruct_Shallow_Zarr"
    OME_ZARR_VERSION_0_4 = '0.4'
    OME_ZARR_VERSION_0_5 = '0.5'
    OME_ZARR_VERSION_0_6 = '0.6'
    OME_ZARR_VERSION_1_0 = '1.0'
    FOLDER = "Folder_Name"
    FOLDER_DEFAULT = 'SLURM_IMAGES_'


class file_transfer:
    # ------------------------------------------------------------
    # _SLURM_File_Transfer script constants
    # ------------------------------------------------------------
    FILE_ANNOTATION_ID = "Annotation_ID"
    PARAM_SLOT = "Param_Slot"
    FOLDER = "Folder_Name"
    FORMAT = "Allowed_Formats"


class workflow_status:
    INITIALIZING = "INITIALIZING"
    TRANSFERRING = "TRANSFERRING"
    CONVERTING = "CONVERTING"
    RETRIEVING = "RETRIEVING"
    IMPORTING = "IMPORTING"
    IMPORTED = "IMPORTED"
    POSTPROCESSING = "POSTPROCESSING"
    DONE = "DONE"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    JOB_STATUS = "JOB_"


class schema_formats:
    # ------------------------------------------------------------
    # Workflow descriptor schema format identifiers
    # ------------------------------------------------------------
    BIAFLOWS = "BIAFLOWS"  # cytomine-0.1 format
    CYTOMINE = "cytomine-0.1"  # legacy name
    BIOMERO_SCHEMA = "biomero-schema"  # new Pydantic format
    BILAYERS = "BILAYERS"
    CWL = "CWL"  # Common Workflow Language
    OPENAPI = "OpenAPI"  # OpenAPI format
