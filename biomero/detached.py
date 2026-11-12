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
"""The detached-mode setting, and aliases for names that moved.

A BIOMERO workflow run outlives the OMERO session that asks for it, so a
script can queue the run in a *launcher task* and a supervisor (the OMERO
processor in NL-BIOMERO) executes it. Core only provides the mechanisms:

* the launcher marker and rule: ``constants.LAUNCHER_MARKER`` and
  ``eventsourcing.is_launcher_task``;
* task lookup and the atomic claim: ``WorkflowTracker.find_launcher_task``,
  ``WorkflowTracker.claim_launcher_task`` and
  ``WorkflowTracker.release_launcher_task``;
* cancellation: ``WorkflowTracker.request_workflow_cancel`` and
  ``SlurmClient.cancel_slurm_jobs``.

The scripts decide what to record in a launcher task and how to resume a run;
the supervisor decides which runs to execute. This module holds the one reader
of the setting that switches the feature on, which the scripts and the
supervisor all need, plus aliases that keep the names of the 2.9 betas
working. The aliases are deprecated: use the names above.
"""

import logging
import os
import warnings
from uuid import UUID

from biomero import constants

logger = logging.getLogger(__name__)

# Owner recorded when a caller of the deprecated claim_launcher_task() does not
# identify itself.
LEGACY_CLAIM_OWNER = "legacy-supervisor"

# Names that moved out of core, and where they are now.
MOVED_TO_SCRIPTS = {
    "register_detached_launcher": "SLURM_Run_Workflow.py",
    "load_task_history": "SLURM_Run_Workflow.py",
    "task_completed": "SLURM_Run_Workflow.py",
    "submitted_job": "SLURM_Run_Workflow.py",
}

# Old constant names and the constants they now come from.
RENAMED_CONSTANTS = {
    "LAUNCHER_MARKER": constants.LAUNCHER_MARKER,
    "LAUNCHER_TASK_NAMES": constants.LAUNCHER_TASK_NAMES,
    "CLAIMED": constants.LEGACY_CLAIMED_STATUS,
}


def detached_mode_enabled() -> bool:
    """Whether workflow scripts should queue runs instead of executing them.

    Opt-in through the BIOMERO_DETACHED_WORKFLOWS environment variable of the
    OMERO processor running the script, because it requires a supervisor to be
    present: without one, a queued run would never be executed. The variable
    is declared in constants.slurm_env so that the processor forwards it to the
    script subprocesses it starts, which is where this is read. This is the
    only reader of the variable: callers use this function.

    Returns:
        bool: True when the variable has one of constants.TRUTHY_VALUES.
    """
    value = os.getenv(constants.slurm_env.BIOMERO_DETACHED_WORKFLOWS, "")
    enabled = value.strip().lower() in constants.TRUTHY_VALUES
    logger.debug(f"{constants.slurm_env.BIOMERO_DETACHED_WORKFLOWS}="
                 f"{value!r}: detached mode "
                 f"{'enabled' if enabled else 'disabled'}")
    return enabled


# -------------------- deprecated aliases -------------------- #

def _warn_deprecated(old: str, new: str) -> None:
    warnings.warn(f"biomero.detached.{old} is deprecated; use {new}",
                  DeprecationWarning, stacklevel=3)


def __getattr__(name: str):
    """Serve the renamed constants, and explain the names that moved away."""
    if name in RENAMED_CONSTANTS:
        _warn_deprecated(name, f"biomero.constants (value of {name})")
        return RENAMED_CONSTANTS[name]
    if name in MOVED_TO_SCRIPTS:
        raise AttributeError(
            f"biomero.detached.{name} moved to biomero-scripts "
            f"({MOVED_TO_SCRIPTS[name]}); core only keeps the mechanisms. "
            f"Update biomero-scripts to the version released with this "
            f"biomero.")
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def is_launcher_task(task) -> bool:
    """Deprecated: use biomero.eventsourcing.is_launcher_task."""
    from biomero.eventsourcing import is_launcher_task as launcher_rule
    _warn_deprecated("is_launcher_task", "eventsourcing.is_launcher_task")
    return launcher_rule(task)


def workflow_tracker():
    """Deprecated: use the SlurmClient's ``workflowTracker``."""
    from biomero.eventsourcing import WorkflowTracker
    _warn_deprecated("workflow_tracker", "SlurmClient.workflowTracker")
    return WorkflowTracker()


def find_launcher_task(tracker, wf_id: UUID):
    """Deprecated: use ``WorkflowTracker.find_launcher_task``.

    Unlike before, read errors are raised instead of reported as "no
    launcher".
    """
    _warn_deprecated("find_launcher_task",
                     "WorkflowTracker.find_launcher_task")
    return tracker.find_launcher_task(wf_id)


def claim_launcher_task(tracker, task_id: UUID) -> bool:
    """Deprecated: use ``WorkflowTracker.claim_launcher_task``.

    Claims the task as a fixed legacy owner, atomically.
    """
    _warn_deprecated("claim_launcher_task",
                     "WorkflowTracker.claim_launcher_task")
    return tracker.claim_launcher_task(task_id, LEGACY_CLAIM_OWNER)


def is_claimed(task) -> bool:
    """Deprecated: whether a launcher task is (or, in a 2.9 beta, was) claimed.

    A launcher whose status is the legacy CLAIMED, or that has a claim owner,
    counts as claimed.
    """
    _warn_deprecated("is_claimed", "Task.claimed_by")
    legacy_status = constants.LEGACY_CLAIMED_STATUS
    return (getattr(task, "claimed_by", None) is not None
            or getattr(task, "status", None) == legacy_status)
