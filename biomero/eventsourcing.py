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
from eventsourcing.domain import Aggregate, event
from eventsourcing.application import Application
from uuid import NAMESPACE_URL, UUID, uuid5
from typing import Any, Dict, List
from fabric import Result
import logging
from biomero.database import EngineManager


# Create a logger for this module
logger = logging.getLogger(__name__)

# -------------------- DOMAIN MODEL -------------------- #


class ResultDict(dict):
    """
    A dictionary subclass that stores details from a Fabric Result object.

    Args:
        result (Result): The Fabric result object.

    Attributes:
        command (str): The command that was executed.
        env (dict): The environment variables during the command execution.
        stdout (str): The standard output from the command.
        stderr (str): The standard error from the command.
    """
    def __init__(self, result: Result):
        super().__init__()
        self['command'] = result.command
        self['env'] = result.env
        self['stdout'] = result.stdout
        self['stderr'] = result.stderr


# When updating Aggregate classes, take care of versioning for compatibility:
# Bump class_version and define @staticmethod upcast_vX_vY(state)
# for Aggregate (and Event(s)!)
# @See https://eventsourcing.readthedocs.io/en/stable/topics/domain.html#versioning


class WorkflowRun(Aggregate):
    """
    Represents a workflow run as an aggregate in the domain model.

    Attributes:
        name (str): The name of the workflow.
        description (str): A description of the workflow.
        user (int): The ID of the user who initiated the workflow.
        group (int): The ID of the group associated with the workflow.
        tasks (list): A list of task UUIDs associated with the workflow.
    """
    INITIAL_VERSION = 0

    class WorkflowInitiated(Aggregate.Created):
        """
        Event triggered when a new workflow is initiated.

        Attributes:
            name (str): The name of the workflow.
            description (str): A description of the workflow.
            user (int): The ID of the user who initiated the workflow.
            group (int): The group ID associated with the workflow.
        """
        name: str
        description: str
        user: int
        group: int

    @event(WorkflowInitiated)
    def __init__(self, name: str,
                 description: str,
                 user: int,
                 group: int):
        self.name = name
        self.description = description
        self.user = user
        self.group = group
        self.tasks = []
        # logger.debug(f"Initializing WorkflowRun: name={name}, description={description}, user={user}, group={group}")

    class TaskAdded(Aggregate.Event):
        """
        Event triggered when a task is added to the workflow.

        Attributes:
            task_id (UUID): The UUID of the task added to the workflow.
        """
        task_id: UUID

    @event(TaskAdded)
    def add_task(self, task_id: UUID):
        # logger.debug(f"Adding task to WorkflowRun: task_id={task_id}")
        self.tasks.append(task_id)

    class WorkflowStarted(Aggregate.Event):
        """
        Event triggered when the workflow starts.
        """
        pass

    @event(WorkflowStarted)
    def start_workflow(self):
        # logger.debug(f"Starting workflow: id={self.id}")
        pass

    class WorkflowCompleted(Aggregate.Event):
        """
        Event triggered when the workflow completes.
        """
        pass

    @event(WorkflowCompleted)
    def complete_workflow(self):
        # logger.debug(f"Completing workflow: id={self.id}")
        pass

    class WorkflowFailed(Aggregate.Event):
        """
        Event triggered when the workflow fails.

        Attributes:
            error_message (str): The error message indicating why the workflow failed.
        """
        error_message: str

    @event(WorkflowFailed)
    def fail_workflow(self, error_message: str):
        # logger.debug(f"Failing workflow: id={self.id}, error_message={error_message}")
        pass


class Task(Aggregate):
    """
    Represents a task in a workflow as an aggregate in the domain model.

    Attributes:
        workflow_id (UUID): The UUID of the associated workflow.
        task_name (str): The name of the task.
        task_version (str): The version of the task.
        input_data (dict): Input data for the task.
        params (dict): Additional parameters for the task.
        job_ids (list): List of job IDs associated with the task.
        results (list): List of results from the task execution.
        result_message (str): Message related to the result of the task.
        status (str): The current status of the task.
    """
    INITIAL_VERSION = 0

    class TaskCreated(Aggregate.Created):
        """
        Event triggered when a task is created.

        Attributes:
            workflow_id (UUID): The UUID of the associated workflow.
            task_name (str): The name of the task.
            task_version (str): The version of the task.
            input_data (dict): Input data for the task.
            params (dict): Additional parameters for the task.
        """
        workflow_id: UUID
        task_name: str
        task_version: str
        input_data: Dict[str, Any]
        params: Dict[str, Any]

    @event(TaskCreated)
    def __init__(self,
                 workflow_id: UUID,
                 task_name: str,
                 task_version: str,
                 input_data: Dict[str, Any],
                 params: Dict[str, Any]
                 ):
        self.workflow_id = workflow_id
        self.task_name = task_name
        self.task_version = task_version
        self.input_data = input_data
        self.params = params
        self.job_ids = []
        self.results = []
        self.result_message = None
        self.status = None
        # Not logging on aggregates, they get reconstructed so much
        # logger.debug(f"Initializing Task: workflow_id={workflow_id}, task_name={task_name}, task_version={task_version}")

    class JobIdAdded(Aggregate.Event):
        """
        Event triggered when a job ID is added to the task.

        Attributes:
            job_id (str): The job ID added to the task.
        """
        job_id: str

    @event(JobIdAdded)
    def add_job_id(self, job_id):
        # logger.debug(f"Adding job_id to Task: task_id={self.id}, job_id={job_id}")
        self.job_ids.append(job_id)

    class StatusUpdated(Aggregate.Event):
        """
        Event triggered when the task's status is updated.

        Attributes:
            status (str): The updated status of the task.
        """
        status: str

    @event(StatusUpdated)
    def update_task_status(self, status):
        # logger.debug(f"Adding status to Task: task_id={self.id}, status={status}")
        self.status = status
    
    class ProgressUpdated(Aggregate.Event):
        """
        Event triggered when the task's progress is updated.

        Attributes:
            progress (str): The updated progress of the task.
        """
        progress: str

    @event(ProgressUpdated)
    def update_task_progress(self, progress):
        # logger.debug(f"Adding progress to Task: task_id={self.id}, progress={progress}")
        self.progress = progress

    class ResultAdded(Aggregate.Event):
        """
        Event triggered when a result is added to the task.

        Attributes:
            result (ResultDict): The result dictionary added to the task.
        """
        result: ResultDict

    def add_result(self, result: Result):
        # logger.debug(f"Adding result to Task: task_id={self.id}, result={result}")
        result = ResultDict(result)
        self._add_result(result)

    @event(ResultAdded)
    def _add_result(self, result: ResultDict):
        # logger.debug(f"Adding result to Task results: task_id={self.id}, result={result}")
        self.results.append(result)

    class TaskStarted(Aggregate.Event):
        """
        Event triggered when the task starts.
        """
        pass

    @event(TaskStarted)
    def start_task(self):
        # logger.debug(f"Starting task: id={self.id}")
        pass

    class TaskCompleted(Aggregate.Event):
        """
        Event triggered when the task completes.

        Attributes:
            result (str): The result message of the task.
        """
        result: str

    @event(TaskCompleted)
    def complete_task(self, result: str):
        # logger.debug(f"Completing task: id={self.id}, result={result}")
        self.result_message = result

    class TaskFailed(Aggregate.Event):
        """
        Event triggered when the task fails.

        Attributes:
            error_message (str): The error message indicating why the task failed.
        """
        error_message: str

    @event(TaskFailed)
    def fail_task(self, error_message: str):
        # logger.debug(f"Failing task: id={self.id}, error_message={error_message}")
        self.result_message = error_message
        pass


# -------------------- APPLICATIONS -------------------- #

class NoOpWorkflowTracker:
    """
    A no-operation workflow tracker that makes all method calls no-op.

    All method calls to this class will return None and log the function name 
    and its parameters for debugging purposes.
    """
    def __getattr__(self, name):
        """
        Override attribute access to return a no-op function for undefined methods.

        Args:
            name (str): The name of the attribute or method being accessed.

        Returns:
            function: A no-op function that logs its name and arguments.
        """
        def no_op_function(*args, **kwargs):
            logger.debug(f"[No-op] Called function: {name} with args: {args}, kwargs: {kwargs}")
            return None

        return no_op_function


class WorkflowTracker(Application):
    """
    Application service class for managing workflow and task lifecycle operations.

    Methods:
        initiate_workflow: Creates a new workflow.
        add_task_to_workflow: Adds a new task to an existing workflow.
        start_workflow: Starts a workflow by its UUID.
        complete_workflow: Marks a workflow as completed.
        fail_workflow: Marks a workflow as failed with an error message.
        start_task: Starts a task by its UUID.
        complete_task: Marks a task as completed with a result message.
        fail_task: Marks a task as failed with an error message.
        add_job_id: Adds a job ID to a task.
        add_result: Adds a result to a task.
        update_task_status: Updates the status of a task.
        update_task_progress: Updates the progress of a task.
    """

    def initiate_workflow(self,
                          name: str,
                          description: str,
                          user: int,
                          group: int) -> UUID:
        """
        Initiates a new workflow.

        Args:
            name (str): The name of the workflow.
            description (str): A description of the workflow.
            user (int): The ID of the user initiating the workflow.
            group (int): The group ID associated with the workflow.

        Returns:
            UUID: The UUID of the newly initiated workflow.
        """
        logger.debug(f"[WFT] Initiating workflow: name={name}, description={description}, user={user}, group={group}")
        workflow = WorkflowRun(name, description, user, group)
        self.save(workflow)
        EngineManager.commit()
        return workflow.id

    def add_task_to_workflow(self,
                             workflow_id: UUID,
                             task_name: str,
                             task_version: str,
                             input_data: Dict[str, Any],
                             kwargs: Dict[str, Any]
                             ) -> UUID:
        """
        Adds a task to the specified workflow.

        Args:
            workflow_id (UUID): The UUID of the workflow.
            task_name (str): The name of the task.
            task_version (str): The version of the task.
            input_data (dict): Input data for the task.
            kwargs (dict): Additional parameters for the task.

        Returns:
            UUID: The UUID of the newly added task.
        """
        logger.debug(f"[WFT] Adding task to workflow: workflow_id={workflow_id}, task_name={task_name}, task_version={task_version}")

        task = Task(workflow_id,
                    task_name,
                    task_version,
                    input_data,
                    kwargs)
        self.save(task)
        EngineManager.commit()
        workflow: WorkflowRun = self.repository.get(workflow_id)
        workflow.add_task(task.id)
        self.save(workflow)
        EngineManager.commit()
        return task.id

    def start_workflow(self, workflow_id: UUID):
        """
        Starts the workflow with the given UUID.

        Args:
            workflow_id (UUID): The UUID of the workflow to start.
        """
        logger.debug(f"[WFT] Starting workflow: workflow_id={workflow_id}")

        workflow: WorkflowRun = self.repository.get(workflow_id)
        workflow.start_workflow()
        self.save(workflow)
        EngineManager.commit()

    def complete_workflow(self, workflow_id: UUID):
        """
        Marks the workflow with the given UUID as completed.

        Args:
            workflow_id (UUID): The UUID of the workflow to complete.
        """
        logger.debug(f"[WFT] Completing workflow: workflow_id={workflow_id}")

        workflow: WorkflowRun = self.repository.get(workflow_id)
        workflow.complete_workflow()
        self.save(workflow)
        EngineManager.commit()

    def fail_workflow(self, workflow_id: UUID, error_message: str):
        """
        Marks the workflow with the given UUID as failed with an error message.

        Args:
            workflow_id (UUID): The UUID of the workflow to fail.
            error_message (str): The error message describing the failure.
        """
        logger.debug(f"[WFT] Failing workflow: workflow_id={workflow_id}, error_message={error_message}")

        workflow: WorkflowRun = self.repository.get(workflow_id)
        workflow.fail_workflow(error_message)
        self.save(workflow)
        EngineManager.commit()

    def start_task(self, task_id: UUID):
        """
        Starts the task with the given UUID.

        Args:
            task_id (UUID): The UUID of the task to start.
        """
        logger.debug(f"[WFT] Starting task: task_id={task_id}")

        task: Task = self.repository.get(task_id)
        task.start_task()
        self.save(task)
        EngineManager.commit()

    def complete_task(self, task_id: UUID, message: str):
        """
        Marks the task with the given UUID as completed with a result message.

        Args:
            task_id (UUID): The UUID of the task to complete.
            message (str): The result message of the task.
        """
        logger.debug(f"[WFT] Completing task: task_id={task_id}, message={message}")

        task: Task = self.repository.get(task_id)
        task.complete_task(message)
        self.save(task)
        EngineManager.commit()

    def fail_task(self, task_id: UUID, error_message: str):
        """
        Marks the task with the given UUID as failed with an error message.

        Args:
            task_id (UUID): The UUID of the task to fail.
            error_message (str): The error message describing the failure.
        """
        logger.debug(f"[WFT] Failing task: task_id={task_id}, error_message={error_message}")

        task: Task = self.repository.get(task_id)
        task.fail_task(error_message)
        self.save(task)
        EngineManager.commit()

    def add_job_id(self, task_id, slurm_job_id):
        """
        Adds a Slurm job ID to the task with the given UUID.

        Args:
            task_id (UUID): The UUID of the task.
            slurm_job_id (str): The Slurm job ID to associate with the task.
        """
        logger.debug(f"[WFT] Adding job_id to task: task_id={task_id}, slurm_job_id={slurm_job_id}")

        task: Task = self.repository.get(task_id)
        task.add_job_id(slurm_job_id)
        self.save(task)
        EngineManager.commit()

    def add_result(self, task_id, result):
        """
        Adds a result to the task with the given UUID.

        Args:
            task_id (UUID): The UUID of the task.
            result (Result): The Fabric result object to add to the task.
        """
        logger.debug(f"[WFT] Adding result to task: task_id={task_id}, result={result}")

        task: Task = self.repository.get(task_id)
        task.add_result(result)
        self.save(task)
        EngineManager.commit()

    def update_task_status(self, task_id, status):
        """
        Updates the status of the task with the given UUID.

        Args:
            task_id (UUID): The UUID of the task.
            status (str): The new status of the task.
        """
        logger.debug(f"[WFT] Adding status to task: task_id={task_id}, status={status}")

        task: Task = self.repository.get(task_id)
        task.update_task_status(status)
        self.save(task)
        EngineManager.commit()
        
    def update_task_progress(self, task_id, progress):
        """
        Updates the progress of the task with the given UUID.

        Args:
            task_id (UUID): The UUID of the task.
            progress (str): The updated progress of the task.
        """
        logger.debug(f"[WFT] Adding progress to task: task_id={task_id}, progress={progress}")

        task: Task = self.repository.get(task_id)
        task.update_task_progress(progress)
        self.save(task)
        EngineManager.commit()



