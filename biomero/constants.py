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

# ------------------------------------------------------------
# SLURM_Get_Results script
# ------------------------------------------------------------
# AFAIK these need to actually be unique strings (per script)
# as it is used as a key to lookup the values
RESULTS_OUTPUT_SLURM_JOB_ID = "SLURM Job Id"
RESULTS_OUTPUT_COMPLETED_JOB = "Completed Job"
RESULTS_OUTPUT_ATTACH_PROJECT = "Output - Attach as zip to project?"
RESULTS_OUTPUT_ATTACH_PROJECT_ID = "Project"
RESULTS_OUTPUT_ATTACH_PLATE = "Output - Attach as zip to plate?"
RESULTS_OUTPUT_ATTACH_PLATE_ID = "Plate"
RESULTS_OUTPUT_ATTACH_OG_IMAGES = "Output - Add as attachment to original images"
RESULTS_OUTPUT_ATTACH_NEW_DATASET = "Output - Add as new images in NEW dataset"
RESULTS_OUTPUT_ATTACH_NEW_DATASET_NAME = "New Dataset"
RESULTS_OUTPUT_ATTACH_NEW_DATASET_DUPLICATE = "Allow duplicate?"
RESULTS_OUTPUT_ATTACH_NEW_DATASET_RENAME = "Rename imported files?"
RESULTS_OUTPUT_ATTACH_NEW_DATASET_RENAME_NAME = "Rename"
RESULTS_OUTPUT_ATTACH_TABLE = "Output - Add csv files as OMERO.table"
RESULTS_OUTPUT_ATTACH_TABLE_DATASET = "Attach table to dataset"
RESULTS_OUTPUT_ATTACH_TABLE_DATASET_ID = "Dataset for table"
RESULTS_OUTPUT_ATTACH_TABLE_PLATE = "Attach table to plate"
RESULTS_OUTPUT_ATTACH_TABLE_PLATE_ID = "Plate for table"