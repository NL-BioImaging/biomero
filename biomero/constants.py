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

IMAGE_EXPORT_SCRIPT = "_SLURM_Image_Transfer.py"
IMAGE_IMPORT_SCRIPT = "SLURM_Get_Results.py"
CONVERSION_SCRIPT = "SLURM_Remote_Conversion.py"
RUN_WF_SCRIPT = "SLURM_Run_Workflow.py"


class workflow_batched:
    # ------------------------------------------------------------
    # SLURM_Run_Workflow_Batched script constants
    # ------------------------------------------------------------
    BATCH_SIZE = "Batch_Size"


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
    OUTPUT_DUPLICATES = "3b) Allow duplicate dataset (name)?"
    OUTPUT_CSV_TABLE = "4) Upload result CSVs as OMERO tables"
    NO = "--NO THANK YOU--"


class results:
    # ------------------------------------------------------------
    # SLURM_Get_Results script constants
    # ------------------------------------------------------------
    OUTPUT_SLURM_JOB_ID = "SLURM Job Id"
    OUTPUT_COMPLETED_JOB = "Completed Job"
    OUTPUT_ATTACH_PROJECT = "Output - Attach as zip to project?"
    OUTPUT_ATTACH_PROJECT_ID = "Project"
    OUTPUT_ATTACH_PLATE = "Output - Attach as zip to plate?"
    OUTPUT_ATTACH_PLATE_ID = "Plate"
    OUTPUT_ATTACH_OG_IMAGES = "Output - Add as attachment to original images"
    OUTPUT_ATTACH_NEW_DATASET = "Output - Add as new images in NEW dataset"
    OUTPUT_ATTACH_NEW_DATASET_NAME = "New Dataset"
    OUTPUT_ATTACH_NEW_DATASET_DUPLICATE = "Allow duplicate?"
    OUTPUT_ATTACH_NEW_DATASET_RENAME = "Rename imported files?"
    OUTPUT_ATTACH_NEW_DATASET_RENAME_NAME = "Rename"
    OUTPUT_ATTACH_TABLE = "Output - Add csv files as OMERO.table"
    OUTPUT_ATTACH_TABLE_DATASET = "Attach table to dataset"
    OUTPUT_ATTACH_TABLE_DATASET_ID = "Dataset for table"
    OUTPUT_ATTACH_TABLE_PLATE = "Attach table to plate"
    OUTPUT_ATTACH_TABLE_PLATE_ID = "Plate for table"


class transfer:
    # ------------------------------------------------------------
    # SLURM_Image_Transfer script constants
    # ------------------------------------------------------------
    DATA_TYPE = "Data_Type"
    DATA_TYPE_DATASET = 'Dataset'
    DATA_TYPE_IMAGE = 'Image'
    DATA_TYPE_PLATE = 'Plate'
    DATA_TYPE_PROJECT = 'Project'
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
    FORMAT_ZARR = 'ZARR'
    FOLDER = "Folder_Name"
    FOLDER_DEFAULT = 'SLURM_IMAGES_'
    

class workflow_status:
    INITIALIZING = "INITIALIZING"
    TRANSFERRING = "TRANSFERRING"
    CONVERTING = "CONVERTING"
    RETRIEVING = "RETRIEVING"
    DONE = "DONE"
    FAILED = "FAILED"
    RUNNING = "RUNNING"
    JOB_STATUS = "JOB_"