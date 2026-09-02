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
"""Queueing workflow runs for execution outside the requesting session.

A BIOMERO workflow run outlives the OMERO session that asks for it: data
transfer, conversion, Slurm execution and result import together take longer
than a browser tab or an OMERO session timeout can be relied on to last.

Detached mode splits that in two. The OMERO script validates the request,
starts a workflow in the tracker and records everything the run needs in a
*launcher task*, then returns. A supervisor elsewhere (the OMERO processor in
NL-BIOMERO) finds that launcher task and executes the workflow in a background
thread, driving the very same pipeline the script would have run inline.

This module holds the contract between those two halves: how a launcher task is
recognized, how it is claimed so that only one worker runs it, and how a run
that was interrupted can tell what it already did.
"""

import logging
import os
from typing import Dict, List, Optional, Tuple
from uuid import UUID

from biomero import constants

logger = logging.getLogger(__name__)

# Marker key in a launcher task's params. Its value is the version of the
# script that queued the run. Tasks without it are ordinary workflow tasks and
# must never be executed by a supervisor: the batched script, for instance,
# adds one task per batch under the same name as the launching script.
LAUNCHER_MARKER = "_biomero_detached_launcher"

# Claim state, kept as the launcher task's status so it survives a restart of
# the process that owns the worker.
CLAIMED = "CLAIMED"

# Scripts that can queue a detached run.
LAUNCHER_TASK_NAMES = (constants.RUN_WF_SCRIPT, constants.RUN_WF_BATCHED_SCRIPT)


# A workflow may accumulate a great many tasks. The launcher is registered
# immediately after the workflow is started, so only the first few tasks ever
# need looking at to decide whether a workflow was queued for a supervisor.
LAUNCHER_SCAN_LIMIT = 10


def workflow_tracker():
    """A handle on the workflow tracker's event store.

    The view applications each keep their own event store, so an aggregate the
    tracker created — here or in another process — can only be read through the
    tracker's own store. Use this where there is no SlurmClient to borrow one
    from.
    """
    from biomero.eventsourcing import WorkflowTracker
    return WorkflowTracker()


def detached_mode_enabled() -> bool:
    """Whether workflow scripts should queue runs instead of executing them.

    Opt-in through the BIOMERO_DETACHED_WORKFLOWS environment variable of the
    OMERO processor running the script, because it requires a supervisor to be
    present: without one, a queued run would never be executed.
    """
    return os.getenv("BIOMERO_DETACHED_WORKFLOWS", "false").lower() in (
        "1", "true", "yes", "on")


def register_detached_launcher(client, tracker, wf_id: UUID,
                              task_name: str, script_version: str,
                              selected_workflow_names: List[str],
                              extra_params: Dict) -> UUID:
    """Record everything a detached run needs, as a launcher task.

    The task's params are the hand-over to the supervisor: every script input
    (so the pipeline can read them back through a stand-in client) plus the
    values the script derived from them.

    Args:
        client: OMERO script client whose inputs are being recorded.
        tracker: Workflow tracker to record the task in.
        wf_id: Workflow the launcher belongs to.
        task_name: Name of the launching script, from LAUNCHER_TASK_NAMES.
        script_version: Version of the launching script.
        selected_workflow_names: Workflows the user actually chose.
        extra_params: Derived values the pipeline needs (zipfile, group, ...).

    Returns:
        UUID: The launcher task ID.
    """
    launcher_params = client.getInputs(unwrap=True) or {}
    launcher_params.update(extra_params)
    launcher_params["workflows"] = list(selected_workflow_names)
    launcher_params[LAUNCHER_MARKER] = script_version
    task_id = tracker.add_task_to_workflow(
        wf_id,
        task_name,
        script_version,
        launcher_params.get(constants.transfer.IDS),
        launcher_params
    )
    logger.info(f"Queued detached workflow {wf_id} (launcher task {task_id}) "
                f"for {list(selected_workflow_names)}")
    return task_id


def is_launcher_task(task) -> bool:
    """Whether a task aggregate is a launcher queued for a supervisor."""
    params = getattr(task, "params", None) or {}
    return (getattr(task, "task_name", None) in LAUNCHER_TASK_NAMES
            and LAUNCHER_MARKER in params)


def find_launcher_task(tracker, wf_id: UUID):
    """The launcher task of a workflow, if it has one.

    Args:
        tracker: Workflow tracker to read the aggregates from.
        wf_id: Workflow to inspect.

    Returns:
        The launcher Task aggregate, or None when this workflow was not queued
        for a supervisor (or its tasks cannot be read).
    """
    try:
        workflow = tracker.repository.get(wf_id)
    except Exception as e:
        logger.warning(f"Could not read workflow {wf_id}: {e}")
        return None
    for task_id in getattr(workflow, "tasks", [])[:LAUNCHER_SCAN_LIMIT]:
        try:
            task = tracker.repository.get(task_id)
        except Exception as e:
            logger.warning(f"Could not read task {task_id}: {e}")
            continue
        if is_launcher_task(task):
            return task
    return None


def claim_launcher_task(tracker, task_id: UUID) -> None:
    """Mark a launcher task as taken, so no second worker picks it up.

    The claim is the task's status, which is persisted in the task execution
    view. A launcher still marked CLAIMED while no worker is running it is
    therefore recognizable as a leftover from a previous process.
    """
    tracker.update_task_status(task_id, CLAIMED)


def is_claimed(task) -> bool:
    """Whether a launcher task was already claimed by a worker."""
    return getattr(task, "status", None) == CLAIMED


def load_task_history(tracker, wf_id: UUID) -> List[Dict]:
    """Summarize the tasks a workflow already has, to resume an interrupted run.

    Whether a task finished is read from the task execution view rather than
    from the aggregate: completing a task records a result message and an end
    time, but leaves ``Task.status`` on the last status it was given (which for
    a workflow task is a Slurm state).

    Args:
        tracker: Workflow tracker to read the aggregates from.
        wf_id: Workflow to inspect.

    Returns:
        list: One dict per task, with keys ``name``, ``task_id``, ``job_ids``,
            ``params``, ``completed`` and ``failed``. Empty when the history
            cannot be read, so that a resumed run simply starts over.
    """
    from biomero.database import EngineManager, TaskExecution

    history: List[Dict] = []
    try:
        workflow = tracker.repository.get(wf_id)
    except Exception as e:
        logger.warning(f"Could not read workflow {wf_id} for resume: {e}")
        return history
    task_ids = list(getattr(workflow, "tasks", []))
    rows = {}
    try:
        with EngineManager.get_session() as session:
            # Keyed by task: the execution view does not record which workflow
            # a task belongs to.
            for row in session.query(TaskExecution).filter(
                    TaskExecution.task_id.in_(task_ids)).all():
                rows[row.task_id] = row
    except Exception as e:
        logger.warning(f"Could not read task history of {wf_id}: {e}")
        return history
    for task_id in task_ids:
        try:
            task = tracker.repository.get(task_id)
        except Exception as e:
            logger.warning(f"Could not read task {task_id} for resume: {e}")
            continue
        row = rows.get(task_id)
        history.append({
            "name": task.task_name,
            "task_id": task_id,
            "job_ids": list(getattr(task, "job_ids", []) or []),
            "params": getattr(task, "params", {}) or {},
            "completed": bool(row is not None and row.end_time is not None
                              and not row.error_type),
            "failed": bool(row is not None and row.error_type),
        })
    return history


def task_completed(history: List[Dict], task_names) -> bool:
    """Whether one of the named tasks already completed for this workflow."""
    return any(entry["completed"] and entry["name"] in task_names
               for entry in history)


def submitted_job(history: List[Dict], wf_name: str,
                  import_task_names=()) -> Optional[Tuple[int, UUID]]:
    """The Slurm job already submitted for a workflow, if there is one.

    Args:
        history: Task history from load_task_history().
        wf_name: Workflow whose Slurm job is wanted.
        import_task_names: Names of the result-import scripts. A job whose
            results were already imported counts as not submitted, so that the
            caller skips the workflow instead of importing it twice.

    Returns:
        tuple: ``(slurm_job_id, task_id)``, or None when the workflow still
            has to be submitted.
    """
    for entry in history:
        if entry["name"] != wf_name or not entry["job_ids"]:
            continue
        job_id = entry["job_ids"][0]
        if any(other["completed"]
               and other["name"] in import_task_names
               and str(other["params"].get(
                   constants.results.OUTPUT_SLURM_JOB_ID)) == str(job_id)
               for other in history):
            logger.info(f"Results of Slurm job {job_id} were already "
                        f"imported; nothing left to do for {wf_name}.")
            continue
        return int(job_id), entry["task_id"]
    return None
