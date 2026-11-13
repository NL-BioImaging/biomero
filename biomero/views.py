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
from functools import wraps
from typing import Any, Callable, Dict, Iterable, Optional, Tuple
from uuid import UUID

from eventsourcing.application import AggregateNotFoundError
from eventsourcing.application import ProcessingEvent
from eventsourcing.system import ProcessApplication
from eventsourcing.dispatch import singledispatchmethod
import logging
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.sql import func
from biomero.eventsourcing import (
    WorkflowRun, Task, WorkflowTracker, is_launcher_task)
from biomero.database import (
    EngineManager, JobView, TaskExecution, JobProgressView,
    WorkflowProgressView, retry_on_database_conflict)
from biomero.constants import (
    CONVERSION_SCRIPT,
    CONVERT_TASK_PREFIX,
    DATABASE_CONFLICT_MAX_RETRIES,
    IMAGE_EXPORT_SCRIPT,
    IMAGE_IMPORT_SCRIPT,
    IMPORT_RESULTS_SCRIPT,
    INFRASTRUCTURE_TASK_PREFIXES,
    LEGACY_CLAIMED_STATUS,
    PROGRESS_CONVERTING,
    PROGRESS_DONE,
    PROGRESS_POSTPROCESSING,
    PROGRESS_RETRIEVING,
    PROGRESS_RUNNING,
    PROGRESS_STARTED,
    PROGRESS_TRANSFERRING,
    REMOTE_SHALLOWER_TASK_NAME,
    RUN_WF_BATCHED_SCRIPT,
    RUN_WF_SCRIPT,
    TASK_STATUS_CREATED,
    TASK_STATUS_UNKNOWN,
    UNKNOWN_TASK_NAME,
    workflow_status as wfs,
)

logger = logging.getLogger(__name__)

# What `import biomero` exposes from this module.
__all__ = ["JobAccounting", "JobProgress", "WorkflowProgress",
           "WorkflowAnalytics", "tracker_repository", "workflow_phase_for"]

# A view application only has its own event store, so an aggregate the tracker
# created is not in it. Since a workflow may now be run by a process that did
# not create it (a detached supervisor claims launchers, see
# WorkflowTracker.claim_launcher_task), the views have to be able to look those
# aggregates up rather than relying on what they saw go past in memory.
_tracker = None


def tracker_repository():
    """Read-only access to the workflow tracker's aggregates."""
    global _tracker
    if _tracker is None:
        _tracker = WorkflowTracker()
    return _tracker.repository


def get_tracker_aggregate(aggregate_id: UUID, kind: str) -> Optional[Any]:
    """Look an aggregate up in the workflow tracker.

    A missing aggregate and a database error are logged as a WARNING (with the
    traceback for the latter) and reported as None: projections run inside the
    producer's ``save()``, which would otherwise raise after its event is
    already stored (and a retry could then duplicate the work). Programming
    errors still propagate.

    Args:
        aggregate_id: ID of the workflow or task.
        kind: What the aggregate is, for the log message.

    Returns:
        The aggregate, or None if it could not be read.
    """
    try:
        return tracker_repository().get(aggregate_id)
    except AggregateNotFoundError:
        logger.warning(f"{kind} {aggregate_id} not found in the workflow "
                       f"tracker")
    except SQLAlchemyError:
        logger.warning(f"{kind} {aggregate_id} could not be read from the "
                       f"workflow tracker", exc_info=True)
    return None


def commit_after_event(handler: Callable) -> Callable:
    """Decorator for event handlers: commit the shared session afterwards.

    The projections write through the producer's scoped session; the handler
    ends the notification by committing it, whichever way it returns.
    """
    @wraps(handler)
    def wrapper(self, domain_event, process_event):
        handler(self, domain_event, process_event)
        EngineManager.commit()
    return wrapper


def write_view_row(description: str, write: Callable) -> None:
    """Write to a view table and commit.

    A conflicting write is rolled back and re-raised, so that the caller's
    ``retry_on_database_conflict`` can retry it.

    Args:
        description: What is written, for the log.
        write: Function that changes the view through the given session.
    """
    with EngineManager.get_session() as session:
        try:
            write(session)
            session.commit()
        except IntegrityError as error:
            session.rollback()
            logger.warning(f"Database conflict writing {description}: "
                           f"{error}")
            raise


def resolve_workflow_owner(workflows: Dict[UUID, dict],
                           wf_id: UUID) -> Optional[dict]:
    """Return a workflow's user and group, looked up in the tracker if unseen.

    The tracker is the source for every projection, so the result does not
    depend on whether another projection has processed the workflow yet.

    Args:
        workflows: The projection's cache of ``{"user": ..., "group": ...}``
            per workflow ID; filled in when the workflow is looked up.
        wf_id: ID of the workflow.

    Returns:
        The cached owner, or None if the workflow is unknown to the tracker.
    """
    if wf_id not in workflows:
        workflow = get_tracker_aggregate(wf_id, "Workflow")
        if workflow is not None:
            workflows[wf_id] = {"user": workflow.user,
                                "group": workflow.group}
    return workflows.get(wf_id)


def workflow_phase_for(task_name: str, status: Optional[str] = None,
                       task_progress: Optional[str] = None) -> Tuple[str, str]:
    """Return the workflow status and progress that a running task implies.

    One table for both TaskStarted (no task status yet) and StatusUpdated, so
    the two cannot disagree about a phase.

    Args:
        task_name: Name of the task.
        status: The task's reported status; None when the task just started.
        task_progress: The workflow task's own progress (e.g. ``"43%"``), if
            reported; it moves the progress within the running phase.

    Returns:
        Tuple of the workflow status and the progress string.
    """
    name = task_name.lower()
    if name == IMAGE_EXPORT_SCRIPT.lower():
        return wfs.TRANSFERRING, PROGRESS_TRANSFERRING
    if name.startswith(CONVERT_TASK_PREFIX) or name == CONVERSION_SCRIPT.lower():
        return wfs.CONVERTING, PROGRESS_CONVERTING
    if name == IMAGE_IMPORT_SCRIPT.lower():
        if status == wfs.POSTPROCESSING:
            return wfs.POSTPROCESSING, PROGRESS_POSTPROCESSING
        return wfs.RETRIEVING, PROGRESS_RETRIEVING
    if name == IMPORT_RESULTS_SCRIPT.lower():
        progress = (PROGRESS_POSTPROCESSING if status == wfs.POSTPROCESSING
                    else PROGRESS_RETRIEVING)
        return status or wfs.IMPORTING, progress
    if name in (RUN_WF_SCRIPT.lower(), RUN_WF_BATCHED_SCRIPT.lower()):
        return wfs.RUNNING, PROGRESS_RUNNING
    # Unknown task types (the user's workflow): JOB_ prefix on the status.
    progress = (interpolate_task_progress(task_progress) if task_progress
                else PROGRESS_RUNNING)
    return wfs.JOB_STATUS + (status or wfs.RUNNING), progress


def interpolate_task_progress(task_progress: str) -> str:
    """Map a workflow task's own progress onto the running-to-retrieving span.

    Args:
        task_progress: The task's progress, e.g. ``"43%"``.

    Returns:
        The progress of the whole workflow, e.g. ``"71.2%"`` for 43%.
    """
    baseline = int(PROGRESS_RUNNING.strip("%"))
    upper_limit = int(PROGRESS_RETRIEVING.strip("%"))
    task_value = int(task_progress.strip("%"))
    return f"{baseline + (task_value / 100) * (upper_limit - baseline)}%"


def first_user_task_name(task_infos: Iterable[dict]) -> Optional[str]:
    """Return the first task that is not BIOMERO infrastructure, in order."""
    for task_info in task_infos:
        task_name = task_info.get("task_name", "")
        if not task_name.lower().startswith(INFRASTRUCTURE_TASK_PREFIXES):
            return task_name
    return None


# ------------------- View Listener Applications ------------------ #


class JobAccounting(ProcessApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)

        # State tracking
        self.workflows = {}  # {wf_id: {"user": user, "group": group}}
        self.tasks = {}      # {task_id: wf_id}
        self.jobs = {}       # {job_id: (task_id, user, group)}

    def resolve_workflow(self, wf_id):
        """Look a workflow up when we did not see it started ourselves."""
        return resolve_workflow_owner(self.workflows, wf_id)

    def resolve_task(self, task_id):
        """Look a task up when we did not see it created ourselves."""
        if task_id not in self.tasks:
            task_agg = get_tracker_aggregate(task_id, "Task")
            if task_agg is not None:
                self.tasks[task_id] = task_agg.workflow_id
                self.resolve_workflow(task_agg.workflow_id)
        return self.tasks.get(task_id)

    @singledispatchmethod
    def policy(self, domain_event: Any, process_event: ProcessingEvent) -> None:
        """Default policy"""

    @policy.register(WorkflowRun.WorkflowInitiated)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.WorkflowInitiated,
          process_event: ProcessingEvent) -> None:
        """Handle WorkflowInitiated event"""
        user = domain_event.user
        group = domain_event.group
        wf_id = domain_event.originator_id

        # Track workflow
        self.workflows[wf_id] = {"user": user, "group": group}
        logger.debug(f"Workflow initiated: wf_id={wf_id}, user={user}, group={group}")

        # Optionally, persist this state if needed
        # Optionally, add an event to do that, then save via collect
        # process_event.collect_events(jobaccount, wfView)

    @policy.register(WorkflowRun.TaskAdded)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.TaskAdded,
          process_event: ProcessingEvent) -> None:
        """Handle TaskAdded event"""
        task_id = domain_event.task_id
        wf_id = domain_event.originator_id

        # Track task
        self.tasks[task_id] = wf_id
        logger.debug(f"Task added: task_id={task_id}, wf_id={wf_id}")

        # Optionally, persist this state if needed
        # use .collect_events(agg) instead of .save(agg)
        # process_event.collect_events(taskView)

    @policy.register(Task.JobIdAdded)
    @commit_after_event
    def _(self, domain_event: Task.JobIdAdded,
          process_event: ProcessingEvent) -> None:
        """Handle JobIdAdded event"""
        # Grab event payload
        job_id = domain_event.job_id
        task_id = domain_event.originator_id

        # Find workflow and user/group for the task
        wf_id = self.resolve_task(task_id)
        if wf_id:
            workflow_info = self.resolve_workflow(wf_id)
            if workflow_info:
                user = workflow_info["user"]
                group = workflow_info["group"]

                # Track job
                self.jobs[job_id] = (task_id, user, group)
                logger.debug(f"Job added: job_id={job_id}, task_id={task_id}, user={user}, group={group}")

                # Update view table
                self.update_view_table(job_id, user, group, task_id)
        else:
            logger.debug(f"JobIdAdded event ignored: task_id={task_id} not found in tasks")

        # use .collect_events(agg) instead of .save(agg)
        # process_event.collect_events(jobaccount)

    @retry_on_database_conflict(max_retries=DATABASE_CONFLICT_MAX_RETRIES)
    def update_view_table(self, job_id: int, user: int, group: int,
                          task_id: UUID) -> None:
        """Update the view table with new job information.

        Uses merge (upsert) so that if SLURM recycles a job ID the new
        task_id/user/group overwrites the stale row instead of silently
        failing and leaving the old mapping in place.

        Args:
            job_id: The Slurm job ID.
            user: The ID of the user who owns the workflow.
            group: The group ID of the workflow.
            task_id: The task the job belongs to.
        """
        write_view_row(
            f"job {job_id}",
            lambda session: session.merge(JobView(
                slurm_job_id=job_id, user=user, group=group,
                task_id=task_id)))
        logger.debug(f"Upserted job into view table: job_id={job_id}, user={user}, group={group}, task_id={task_id}")

    def get_jobs(self, user=None, group=None):
        """Retrieve jobs for a specific user and/or group.

        Args:
            user (int, optional): The user ID to filter by.
            group (int, optional): The group ID to filter by.

        Returns:
            Dictionary of user IDs to lists of job IDs if no user is specified.
            Dictionary with a single user ID key and a list of job IDs if user is specified.

        Raises:
            ValueError: If neither user nor group is provided.
        """
        if user is None and group is None:
            # Retrieve all jobs grouped by user
            with EngineManager.get_session() as session:
                jobs = session.query(JobView.user, JobView.slurm_job_id).all()
                user_jobs = {}
                for user_id, job_id in jobs:
                    if user_id not in user_jobs:
                        user_jobs[user_id] = []
                    user_jobs[user_id].append(job_id)
                return user_jobs
        else:
            with EngineManager.get_session() as session:
                query = session.query(JobView.slurm_job_id)

                if user is not None:
                    query = query.filter_by(user=user)

                if group is not None:
                    query = query.filter_by(group=group)

                jobs = query.all()
                result = {user: [job.slurm_job_id for job in jobs]}
                logger.debug(f"Retrieved jobs for user={user} and group={group}: {result}")
                return result

    def get_task_id(self, job_id):
        """
        Retrieve the task ID associated with a given job ID.

        Args:
            job_id (int): The job ID (slurm_job_id) to look up.

        Returns:
            UUID: The task ID associated with the job ID, or None if not found.
        """
        with EngineManager.get_session() as session:
            try:
                task_id = session.query(JobView.task_id).filter(JobView.slurm_job_id == job_id).one_or_none()
                logger.debug(f"Retrieved task_id={task_id[0] if task_id else None} for job_id={job_id}")
                return task_id[0] if task_id else None
            except SQLAlchemyError:
                logger.error(f"Failed to retrieve task_id for job_id={job_id}", exc_info=True)
                return None


class WorkflowProgress(ProcessApplication):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # State tracking: {workflow_id: {"status": status, "progress": progress, "user": user, "group": group}}
        self.workflows = {}
        self.tasks = {}  # {task_id: {"workflow_id": wf_id, "task_name": task_name}}

    def resolve_workflow(self, wf_id):
        """Reload a workflow from this projection's own table after a restart."""
        if wf_id not in self.workflows:
            # Borrow the producer's scoped session. Closing it here would
            # roll back the event currently notifying this cold listener.
            session = EngineManager.get_session()
            row = session.query(WorkflowProgressView).filter_by(workflow_id=wf_id).first()
            if row:
                self.workflows[wf_id] = {
                    "status": row.status,
                    "progress": row.progress,
                    "user": row.user,
                    "group": row.group,
                    "name": row.name,
                    "task": row.task,
                    "start_time": row.start_time
                }
        return self.workflows.get(wf_id)

    def resolve_task(self, task_id):
        """Look a task up when we did not see it created ourselves."""
        if task_id not in self.tasks:
            task_agg = get_tracker_aggregate(task_id, "Task")
            if task_agg is not None:
                self.tasks[task_id] = {
                    "task_name": task_agg.task_name,
                    "workflow_id": task_agg.workflow_id,
                    "is_launcher": is_launcher_task(task_agg),
                    # Progress is reported by events, not kept on the task.
                    "progress": None
                }
                self.resolve_workflow(task_agg.workflow_id)
        return self.tasks.get(task_id)

    def skip_remote_shallower(self, task_id, task_info, event_name):
        """Leave the analysis progress alone for a remote shallower helper task.

        Args:
            task_id: ID of the task the event belongs to.
            task_info: The projection's info for that task, or None.
            event_name: Name of the event, for the log.

        Returns:
            True if the event was for a helper task and has been handled.
        """
        if not task_info or task_info["task_name"] != REMOTE_SHALLOWER_TASK_NAME:
            return False
        logger.debug(f"[WFP] Ignoring {event_name} of remote shallower task "
                     f"{task_id}: it does not change the analysis progress")
        return True

    @singledispatchmethod
    def policy(self, domain_event: Any, process_event: ProcessingEvent) -> None:
        """Default policy"""
        pass

    @policy.register(WorkflowRun.WorkflowInitiated)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.WorkflowInitiated,
          process_event: ProcessingEvent) -> None:
        """Handle WorkflowInitiated event"""
        user = domain_event.user
        group = domain_event.group
        wf_id = domain_event.originator_id
        name = domain_event.name
        start_time = domain_event.timestamp

        # Track workflow with user, group, and INITIATED status
        self.workflows[wf_id] = {"status": wfs.INITIALIZING,
                                 "progress": PROGRESS_STARTED,
                                 "user": user,
                                 "group": group,
                                 "name": name,
                                 "task": None,
                                 "start_time": start_time}
        logger.debug(f"[WFP] Workflow initiated: wf_id={wf_id}, name={name}, user={user}, group={group}, "
                     f"status={wfs.INITIALIZING} -- {domain_event.__dict__}")
        self.update_view_table(wf_id)

    @policy.register(WorkflowRun.WorkflowCompleted)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.WorkflowCompleted,
          process_event: ProcessingEvent) -> None:
        wf_id = domain_event.originator_id
        # Defensive check: ensure workflow exists in our dictionary
        if not self.resolve_workflow(wf_id):
            logger.warning(f"[WFP] WorkflowCompleted event for unknown workflow: wf_id={wf_id}. Skipping status update.")
            return

        self.workflows[wf_id]["status"] = wfs.DONE
        self.workflows[wf_id]["progress"] = PROGRESS_DONE
        logger.debug(f"[WFP] Status updated: wf_id={wf_id}, status={wfs.DONE} -- {domain_event.__dict__}")
        self.update_view_table(wf_id)

    @policy.register(WorkflowRun.WorkflowFailed)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.WorkflowFailed,
          process_event: ProcessingEvent) -> None:
        wf_id = domain_event.originator_id
        # Defensive check: ensure workflow exists in our dictionary
        if not self.resolve_workflow(wf_id):
            logger.warning(f"[WFP] WorkflowFailed event for unknown workflow: wf_id={wf_id}. Skipping status update.")
            return

        self.workflows[wf_id]["status"] = wfs.FAILED
        logger.debug(f"[WFP] Status updated: wf_id={wf_id}, status={wfs.FAILED} -- {domain_event.__dict__}")
        self.update_view_table(wf_id)

    @policy.register(WorkflowRun.TaskAdded)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.TaskAdded,
          process_event: ProcessingEvent) -> None:
        """Handle TaskAdded event"""
        task_id = domain_event.task_id
        wf_id = domain_event.originator_id

        # Track task to workflow mapping
        task_info = self.resolve_task(task_id)
        if self.skip_remote_shallower(task_id, task_info, "TaskAdded"):
            return
        if task_info:
            task_info["workflow_id"] = wf_id
            workflow_info = self.resolve_workflow(wf_id)
            if workflow_info:
                workflow_info["task"] = task_info["task_name"]
        logger.debug(f"[WFP] Task added: task_id={task_id}, wf_id={wf_id} -- {domain_event.__dict__}")
        if self.resolve_workflow(wf_id):
            self.update_view_table(wf_id)

    @policy.register(Task.TaskCreated)
    @commit_after_event
    def _(self, domain_event: Task.TaskCreated,
          process_event: ProcessingEvent) -> None:
        task_id = domain_event.originator_id
        task_name = domain_event.task_name

        # store task name
        self.tasks[task_id] = {
            "task_name": task_name,
            "workflow_id": None,
            "is_launcher": is_launcher_task(domain_event),
            "progress": None
            }
        logger.debug(f"[WFP] Task created: task_id={task_id}, task_name={task_name} -- {domain_event.__dict__}")

    @policy.register(Task.TaskStarted)
    @commit_after_event
    def _(self, domain_event: Task.TaskStarted,
          process_event: ProcessingEvent) -> None:
        """Persist the current task and its initial workflow phase."""
        task_id = domain_event.originator_id
        task_info = self.resolve_task(task_id)
        if self.skip_remote_shallower(task_id, task_info, "TaskStarted"):
            return
        if task_info:
            wf_id = task_info["workflow_id"]
            workflow_info = self.resolve_workflow(wf_id)
            if workflow_info:
                task_name = task_info["task_name"]
                workflow_info["task"] = task_name
                workflow_info["status"], workflow_info["progress"] = (
                    workflow_phase_for(task_name))

                logger.debug(
                             f"[WFP] Task started: wf_id={wf_id}, task_id={task_id}, "
                             f"task_name={task_name} -- {domain_event.__dict__}"
                )
                self.update_view_table(wf_id)

    @policy.register(Task.StatusUpdated)
    @commit_after_event
    def _(self, domain_event: Task.StatusUpdated,
          process_event: ProcessingEvent) -> None:
        """Handle Task StatusUpdated event"""
        task_id = domain_event.originator_id
        status = domain_event.status

        # Get the workflow ID and task name associated with this task
        task_info = self.resolve_task(task_id)
        if self.skip_remote_shallower(task_id, task_info, "StatusUpdated"):
            return
        if task_info:
            wf_id = task_info["workflow_id"]
            task_name = task_info["task_name"]

            # A claim is its own event now, but 2.9 betas stored it as the
            # status CLAIMED. That is supervisor bookkeeping, not workflow
            # execution, and must stay out of the view. The
            # first real task's TaskStarted event will set the visible phase.
            if task_info.get("is_launcher") and status == LEGACY_CLAIMED_STATUS:
                logger.debug(
                             f"[WFP] Ignoring detached launcher claim: "
                             f"task_id={task_id}, wf_id={wf_id}"
                )
                return

            workflow_info = self.resolve_workflow(wf_id)
            if workflow_info:
                workflow_status, workflow_prog = workflow_phase_for(
                    task_name, status, workflow_info.get("task_progress"))
                # Update the workflow status
                workflow_info["status"] = workflow_status
                workflow_info["progress"] = workflow_prog
                logger.debug(f"[WFP] Status updated: wf_id={wf_id}, task_id={task_id}, "
                             f"status={workflow_status} -- {domain_event.__dict__}")
                self.update_view_table(wf_id)
            else:
                logger.warning(f"[WFP] Task.StatusUpdated event for unknown workflow: task_id={task_id}, "
                               f"wf_id={wf_id}. Skipping status update.")

    @policy.register(Task.ProgressUpdated)
    @commit_after_event
    def _(self, domain_event: Task.ProgressUpdated,
          process_event: ProcessingEvent) -> None:
        """Handle ProgressUpdated event"""
        task_id = domain_event.originator_id
        progress = domain_event.progress

        task_info = self.resolve_task(task_id)
        if self.skip_remote_shallower(task_id, task_info, "ProgressUpdated"):
            return
        if task_info:
            task_info["progress"] = progress
            wf_id = task_info["workflow_id"]
            workflow_info = self.resolve_workflow(wf_id)
            if workflow_info:
                workflow_info["task_progress"] = progress
                logger.debug(f"[WFP] (Task) Progress updated: wf_id={wf_id}, progress={progress} -- {domain_event.__dict__}")
                self.update_view_table(wf_id)
            else:
                logger.warning(f"[WFP] Task.ProgressUpdated event for unknown workflow: task_id={task_id}, "
                               f"wf_id={wf_id}. Skipping progress update.")

    @retry_on_database_conflict(max_retries=DATABASE_CONFLICT_MAX_RETRIES)
    def update_view_table(self, wf_id: UUID) -> None:
        """Update the view table with new workflow status, progress, user, and group."""
        # Defensive check: ensure workflow exists in our dictionary
        if wf_id not in self.workflows:
            logger.warning(f"[WFP] Cannot update view table for unknown workflow: wf_id={wf_id}")
            return

        workflow_info = self.workflows[wf_id]
        # Determine the main user-facing task name
        main_task_name = self._determine_main_task_name(wf_id)
        write_view_row(
            f"[WFP] workflow progress {wf_id}",
            lambda session: session.merge(WorkflowProgressView(
                workflow_id=wf_id,
                status=workflow_info["status"],
                progress=workflow_info["progress"],
                user=workflow_info["user"],
                group=workflow_info["group"],
                name=workflow_info["name"],
                task=workflow_info["task"],
                main_task_name=main_task_name,
                start_time=workflow_info["start_time"])))
        logger.debug(f"[WFP] Inserted wf progress in view table: wf_id={wf_id} wf_info={workflow_info}")

    def _determine_main_task_name(self, wf_id):
        """Determine the main user-facing task name for a workflow."""
        # Fast path for a listener that has observed the whole workflow.
        in_memory_tasks = [task_info for task_info in self.tasks.values()
                           if task_info.get("workflow_id") == wf_id]
        main_task = first_user_task_name(in_memory_tasks)
        if main_task:
            return main_task

        # A restarted projection does not have the earlier tasks in memory.
        # Rehydrate them from the persisted WorkflowRun task list so an import
        # or completion event cannot erase main_task_name from the view.
        workflow = get_tracker_aggregate(wf_id, "Workflow")
        if workflow is not None:
            persisted_tasks = [
                task_info for task_info in (
                    self.resolve_task(task_id) for task_id in workflow.tasks)
                if task_info]
            main_task = first_user_task_name(persisted_tasks)
            if main_task:
                return main_task

        # Fallback to current task if no main task found
        logger.debug(f"[WFP] No user task found for workflow {wf_id}; "
                     f"using its current task")
        return self.workflows[wf_id].get("task", UNKNOWN_TASK_NAME)


class JobProgress(ProcessApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)

        # State tracking
        self.task_to_job = {}  # {task_id: job_id}
        self.job_status = {}   # {job_id: {"status": status, "progress": progress}}

    @singledispatchmethod
    def policy(self, domain_event: Any, process_event: ProcessingEvent) -> None:
        """Default policy"""

    @policy.register(Task.JobIdAdded)
    @commit_after_event
    def _(self, domain_event: Task.JobIdAdded,
          process_event: ProcessingEvent) -> None:
        """Handle JobIdAdded event"""
        job_id = domain_event.job_id
        task_id = domain_event.originator_id

        # Track task to job mapping
        self.task_to_job[task_id] = job_id
        logger.debug(f"[JP] JobId added: job_id={job_id}, task_id={task_id} -- {domain_event.__dict__}")

    @policy.register(Task.StatusUpdated)
    @commit_after_event
    def _(self, domain_event: Task.StatusUpdated,
          process_event: ProcessingEvent) -> None:
        """Handle StatusUpdated event"""
        task_id = domain_event.originator_id
        status = domain_event.status

        job_id = self.task_to_job.get(task_id)
        if job_id is not None:
            if job_id in self.job_status:
                self.job_status[job_id]["status"] = status
            else:
                self.job_status[job_id] = {"status": status, "progress": None}

            logger.debug(f"[JP] Status updated: job_id={job_id}, status={status} -- {domain_event.__dict__}")
            # Update view table
            self.update_view_table(job_id)

    @policy.register(Task.ProgressUpdated)
    @commit_after_event
    def _(self, domain_event: Task.ProgressUpdated,
          process_event: ProcessingEvent) -> None:
        """Handle ProgressUpdated event"""
        task_id = domain_event.originator_id
        progress = domain_event.progress

        job_id = self.task_to_job.get(task_id)
        if job_id is not None:
            if job_id in self.job_status:
                self.job_status[job_id]["progress"] = progress
            else:
                self.job_status[job_id] = {"status": TASK_STATUS_UNKNOWN, "progress": progress}

            logger.debug(f"[JP] Progress updated: job_id={job_id}, progress={progress} -- {domain_event.__dict__}")
            # Update view table
            self.update_view_table(job_id)

    @retry_on_database_conflict(max_retries=DATABASE_CONFLICT_MAX_RETRIES)
    def update_view_table(self, job_id: int) -> None:
        """Update the view table with new job status and progress information."""
        job_info = self.job_status[job_id]
        write_view_row(
            f"[JP] job progress {job_id}",
            lambda session: session.merge(JobProgressView(
                slurm_job_id=job_id,
                status=job_info["status"],
                progress=job_info["progress"])))  # merge inserts or updates
        logger.debug(f"[JP] Inserted/Updated job progress in view table: job_id={job_id}, "
                     f"status={job_info['status']}, progress={job_info['progress']}")


# @event.listens_for(Engine, "before_cursor_execute")
# def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
#     logger.debug(f"SQL: {statement}")
#     logger.debug(f"Parameters: {parameters}")


class WorkflowAnalytics(ProcessApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)

        # State tracking for workflows and tasks
        self.workflows = {}  # {wf_id: {"user": user, "group": group}}
        # {task_id: {"wf_id", "task_name", "task_version", "start_time",
        #            "status", "end_time", "error_type"}}
        self.tasks = {}

    def resolve_workflow(self, wf_id):
        """Look a workflow's user and group up in the tracker when unseen."""
        return resolve_workflow_owner(self.workflows, wf_id)

    def resolve_task(self, task_id):
        """Look a task up when we did not see it created ourselves."""
        if task_id not in self.tasks:
            task_agg = get_tracker_aggregate(task_id, "Task")
            if task_agg is not None:
                start_time = None
                # Do not close or roll back the producer's event transaction.
                session = EngineManager.get_session()
                row = session.query(TaskExecution).filter_by(
                    task_id=task_id).first()
                if row:
                    start_time = row.start_time
                self.tasks[task_id] = {
                    "wf_id": task_agg.workflow_id,
                    "task_name": task_agg.task_name,
                    "task_version": task_agg.task_version,
                    "start_time": start_time or task_agg.created_on,
                    # Starting/completing a task does not set aggregate status.
                    # Preserve the projection's valid creation/previous state.
                    "status": task_agg.status or (
                        row.status if row else TASK_STATUS_CREATED)
                }
                if task_agg.workflow_id:
                    self.resolve_workflow(task_agg.workflow_id)
        return self.tasks.get(task_id)

    @singledispatchmethod
    def policy(self, domain_event: Any, process_event: ProcessingEvent) -> None:
        """Default policy"""
        pass

    @policy.register(WorkflowRun.WorkflowInitiated)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.WorkflowInitiated,
          process_event: ProcessingEvent) -> None:
        """Handle WorkflowInitiated event"""
        user = domain_event.user
        group = domain_event.group
        wf_id = domain_event.originator_id

        # Track workflow
        self.workflows[wf_id] = {"user": user, "group": group}
        logger.debug(f"[WFA] Workflow initiated: wf_id={wf_id}, user={user}, group={group} -- {domain_event.__dict__}")

    @policy.register(WorkflowRun.TaskAdded)
    @commit_after_event
    def _(self, domain_event: WorkflowRun.TaskAdded,
          process_event: ProcessingEvent) -> None:
        """Handle TaskAdded event"""
        task_id = domain_event.task_id
        wf_id = domain_event.originator_id

        # Add workflow ID to the existing task information
        task_info = self.resolve_task(task_id)
        if task_info:
            task_info["wf_id"] = wf_id
        logger.debug(f"[WFA] Task added: task_id={task_id}, wf_id={wf_id} -- {domain_event.__dict__}")
        if task_info:
            self.update_view_table(task_id)

    @policy.register(Task.TaskCreated)
    @commit_after_event
    def _(self, domain_event: Task.TaskCreated,
          process_event: ProcessingEvent) -> None:
        """Handle TaskCreated event"""
        task_id = domain_event.originator_id
        task_name = domain_event.task_name
        task_version = domain_event.task_version
        timestamp_created = domain_event.timestamp

        # Track task creation details
        if task_id not in self.tasks:
            self.tasks[task_id] = {}
        task_info = self.tasks[task_id]
        task_info.update({
            "task_name": task_name,
            "task_version": task_version,
            "start_time": timestamp_created,
            "status": TASK_STATUS_CREATED
        })
        logger.debug(f"[WFA] Task created: task_id={task_id}, task_name={task_name}, "
                     f"timestamp={timestamp_created} -- {domain_event.__dict__}")
        self.update_view_table(task_id)

    @policy.register(Task.TaskStarted)
    @commit_after_event
    def _(self, domain_event: Task.TaskStarted,
          process_event: ProcessingEvent) -> None:
        """Record the actual task start instead of only its creation time."""
        task_id = domain_event.originator_id
        task_info = self.resolve_task(task_id)
        if task_info:
            task_info["start_time"] = domain_event.timestamp
            logger.debug(
                         f"[WFA] Task started: task_id={task_id}, "
                         f"start_time={domain_event.timestamp} -- "
                         f"{domain_event.__dict__}"
            )
            self.update_view_table(task_id)

    @policy.register(Task.StatusUpdated)
    @commit_after_event
    def _(self, domain_event: Task.StatusUpdated,
          process_event: ProcessingEvent) -> None:
        """Handle StatusUpdated event"""
        task_id = domain_event.originator_id
        status = domain_event.status

        # Update task with status
        task_info = self.resolve_task(task_id)
        if task_info:
            task_info["status"] = status
            logger.debug(f"[WFA] Task status updated: task_id={task_id}, status={status} -- {domain_event.__dict__}")
            self.update_view_table(task_id)

    @policy.register(Task.TaskCompleted)
    @commit_after_event
    def _(self, domain_event: Task.TaskCompleted,
          process_event: ProcessingEvent) -> None:
        """Handle TaskCompleted event"""
        task_id = domain_event.originator_id
        timestamp_completed = domain_event.timestamp

        # Update task with end time
        task_info = self.resolve_task(task_id)
        if task_info:
            task_info["end_time"] = timestamp_completed
            logger.debug(f"[WFA] Task completed: task_id={task_id}, end_time={timestamp_completed} -- {domain_event.__dict__}")
            self.update_view_table(task_id)

    @policy.register(Task.TaskFailed)
    @commit_after_event
    def _(self, domain_event: Task.TaskFailed,
          process_event: ProcessingEvent) -> None:
        """Handle TaskFailed event"""
        task_id = domain_event.originator_id
        timestamp_failed = domain_event.timestamp
        error_message = domain_event.error_message

        # Update task with end time and error message
        task_info = self.resolve_task(task_id)
        if task_info:
            task_info["end_time"] = timestamp_failed
            task_info["error_type"] = error_message
            logger.debug(f"[WFA] Task failed: task_id={task_id}, end_time={timestamp_failed}, "
                         f"error={error_message} -- {domain_event.__dict__}")
            self.update_view_table(task_id)

    @retry_on_database_conflict(max_retries=DATABASE_CONFLICT_MAX_RETRIES)
    def update_view_table(self, task_id: UUID) -> None:
        """Update the view table with new task execution information."""
        task_info = self.resolve_task(task_id)
        if not task_info:
            return  # Skip if task information is incomplete

        wf_id = task_info.get("wf_id")
        user_id = None
        group_id = None

        # Retrieve user and group from workflow
        if wf_id:
            workflow_info = self.resolve_workflow(wf_id)
            if workflow_info:
                user_id = workflow_info["user"]
                group_id = workflow_info["group"]
            else:
                logger.warning(f"[WFA] Owner of workflow {wf_id} unknown; "
                               f"task {task_id} is stored without user/group")

        def write(session):
            existing_task = session.query(TaskExecution).filter_by(task_id=task_id).first()
            logger.debug(f"Existing: {existing_task}. vs info {task_info}")
            if existing_task:
                # Update existing task execution record
                existing_task.task_name = task_info.get("task_name", existing_task.task_name)
                existing_task.task_version = task_info.get("task_version", existing_task.task_version)
                existing_task.user_id = user_id
                existing_task.group_id = group_id
                existing_task.status = task_info.get("status", existing_task.status)
                existing_task.start_time = task_info.get("start_time", existing_task.start_time)
                existing_task.end_time = task_info.get("end_time", existing_task.end_time)
                existing_task.error_type = task_info.get("error_type", existing_task.error_type)
            else:
                # Create a new task execution record
                session.add(TaskExecution(
                    task_id=task_id,
                    task_name=task_info.get("task_name"),
                    task_version=task_info.get("task_version"),
                    user_id=user_id,
                    group_id=group_id,
                    status=task_info.get("status"),
                    start_time=task_info.get("start_time"),
                    end_time=task_info.get("end_time"),
                    error_type=task_info.get("error_type")))

        write_view_row(f"[WFA] task execution {task_id}", write)
        logger.debug(f"[WFA] Updated/Inserted task execution into view table: task_id={task_id}, "
                     f"task_name={task_info.get('task_name')}")

    def get_task_counts(self, user=None, group=None):
        """Retrieve task execution counts grouped by task name and version.

        Args:
            user (int, optional): The user ID to filter by.
            group (int, optional): The group ID to filter by.

        Returns:
            Dictionary of task names and versions to counts.
        """
        with EngineManager.get_session() as session:
            query = session.query(
                TaskExecution.task_name,
                TaskExecution.task_version,
                func.count(TaskExecution.task_name)
            ).group_by(TaskExecution.task_name, TaskExecution.task_version)

            if user is not None:
                query = query.filter_by(user_id=user)

            if group is not None:
                query = query.filter_by(group_id=group)

            task_counts = query.all()
            result = {
                (task_name, task_version): count
                for task_name, task_version, count in task_counts
            }
            logger.debug(f"[WFA] Retrieved task counts: {result}")
            return result

    def get_average_task_duration(self, user=None, group=None):
        """Retrieve the average task duration grouped by task name and version.

        Args:
            user (int, optional): The user ID to filter by.
            group (int, optional): The group ID to filter by.

        Returns:
            Dictionary of task names and versions to average duration (in seconds).
        """
        with EngineManager.get_session() as session:
            query = session.query(
                TaskExecution.task_name,
                TaskExecution.task_version,
                func.avg(
                    func.extract('epoch', TaskExecution.end_time) - func.extract('epoch', TaskExecution.start_time)
                ).label('avg_duration')
            ).filter(TaskExecution.end_time.isnot(None))  # Only include completed tasks
            query = query.group_by(TaskExecution.task_name, TaskExecution.task_version)

            if user is not None:
                query = query.filter_by(user_id=user)

            if group is not None:
                query = query.filter_by(group_id=group)

            task_durations = query.all()
            result = {
                (task_name, task_version): avg_duration
                for task_name, task_version, avg_duration in task_durations
            }
            logger.debug(f"[WFA] Retrieved average task durations: {result}")
            return result

    def get_task_failures(self, user=None, group=None):
        """Retrieve tasks that failed, grouped by task name and version.

        Args:
            user (int, optional): The user ID to filter by.
            group (int, optional): The group ID to filter by.

        Returns:
            Dictionary of task names and versions to lists of failure reasons.
        """
        with EngineManager.get_session() as session:
            query = session.query(
                TaskExecution.task_name,
                TaskExecution.task_version,
                TaskExecution.error_type
            ).filter(TaskExecution.error_type.isnot(None))  # Only include failed tasks
            query = query.group_by(TaskExecution.task_name, TaskExecution.task_version, TaskExecution.error_type)

            if user is not None:
                query = query.filter_by(user_id=user)

            if group is not None:
                query = query.filter_by(group_id=group)

            task_failures = query.all()
            result = {}
            for task_name, task_version, error_type in task_failures:
                key = (task_name, task_version)
                if key not in result:
                    result[key] = []
                result[key].append(error_type)

            logger.debug(f"[WFA] Retrieved task failures: {result}")
            return result

    def get_task_usage_over_time(self, task_name, user=None, group=None):
        """Retrieve task usage over time for a specific task.

        Args:
            task_name (str): The name of the task to filter by.
            user (int, optional): The user ID to filter by.
            group (int, optional): The group ID to filter by.

        Returns:
            Dictionary mapping date to the count of task executions on that date.
        """
        with EngineManager.get_session() as session:
            query = session.query(
                func.date(TaskExecution.start_time),
                func.count(TaskExecution.task_name)
            ).filter(TaskExecution.task_name == task_name)
            query = query.group_by(func.date(TaskExecution.start_time))

            if user is not None:
                query = query.filter_by(user_id=user)

            if group is not None:
                query = query.filter_by(group_id=group)

            usage_over_time = query.all()
            result = {
                date: count
                for date, count in usage_over_time
            }
            logger.debug(f"[WFA] Retrieved task usage over time for {task_name}: {result}")
            return result
