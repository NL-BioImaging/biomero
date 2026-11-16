# -*- coding: utf-8 -*-
# Copyright 2024 Torec Luik
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.  See the License for the specific language governing
# permissions and limitations under the License.
"""Settings for where BIOMERO stores its files.

The folder that holds imported workflow results is a deployment setting, like
the importer's PROCESSED_DATA_FOLDER: an optional environment variable with a
strong default.
"""

import logging
import os

from biomero import constants

logger = logging.getLogger(__name__)


def read_analyzed_data_folder() -> str:
    """Read the name of the folder that holds imported workflow results.

    Results are stored under ``<group folder>/<name>/<workflow>/<timestamp>``.
    The environment is read on every call, so a changed value applies to new
    runs only. This is the only reader of the variable: callers use this
    function. The name must be a single folder name, because anything else
    would put results outside BIOMERO's own folder. Surrounding whitespace is
    removed; an empty or whitespace-only value counts as unset.

    Returns:
        str: The value of ANALYZED_DATA_FOLDER when it is exactly one folder
            name, otherwise constants.results.DEFAULT_ANALYZED_DATA_FOLDER
            (a WARNING is logged for an invalid value).
    """
    variable = constants.slurm_env.ANALYZED_DATA_FOLDER
    default = constants.results.DEFAULT_ANALYZED_DATA_FOLDER
    value = os.getenv(variable, "").strip()
    if not value:
        logger.debug(f"{variable} is not set: using {default!r}")
        return default
    if not _is_single_folder_name(value):
        logger.warning(f"Ignoring invalid {variable}={value!r}: it must be "
                       f"one folder name; using {default!r}")
        return default
    logger.debug(f"{variable}={value!r}")
    return value


def _is_single_folder_name(value: str) -> bool:
    """Whether a value is one folder name, with no path around it.

    Args:
        value (str): The candidate name.

    Returns:
        bool: False when the value contains ``/`` or a backslash (which also
            excludes absolute paths), or is ``.`` or ``..``. Any other value
            is a legal folder name, including ``C:`` on Linux.
    """
    has_separator = "/" in value or "\\" in value
    is_relative_marker = value in {".", ".."}
    return not (has_separator or is_relative_marker)
