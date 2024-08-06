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
import os
from eventsourcing.domain import Aggregate, event
from eventsourcing.application import Application, AggregateNotFound
from eventsourcing.system import ProcessApplication
from eventsourcing.dispatch import singledispatchmethod
from uuid import NAMESPACE_URL, UUID, uuid5
from typing import Any, Dict, List
from fabric import Result
import logging
from sqlalchemy import create_engine, text, Column, Integer, String, URL
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import IntegrityError
from sqlalchemy.schema import CreateTable



# Create a logger for this module
logger = logging.getLogger(__name__)

# -------------------- DOMAIN MODEL -------------------- #


class ResultDict(dict):
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
    INITIAL_VERSION = 0

    class WorkflowInitiated(Aggregate.Created):
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
        logger.debug(f"Initializing WorkflowRun: name={name}, description={description}, user={user}, group={group}")

    class TaskAdded(Aggregate.Event):
        task_id: UUID

    @event(TaskAdded)
    def add_task(self, task_id: UUID):
        logger.debug(f"Adding task to WorkflowRun: task_id={task_id}")
        self.tasks.append(task_id)

    class WorkflowStarted(Aggregate.Event):
        pass

    @event(WorkflowStarted)
    def start_workflow(self):
        logger.debug(f"Starting workflow: id={self.id}")
        pass

    class WorkflowCompleted(Aggregate.Event):
        pass

    @event(WorkflowCompleted)
    def complete_workflow(self):
        logger.debug(f"Completing workflow: id={self.id}")
        pass

    class WorkflowFailed(Aggregate.Event):
        error_message: str

    @event(WorkflowFailed)
    def fail_workflow(self, error_message: str):
        logger.debug(f"Failing workflow: id={self.id}, error_message={error_message}")
        pass


class Task(Aggregate):
    INITIAL_VERSION = 0

    class TaskCreated(Aggregate.Created):
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
        logger.debug(f"Initializing Task: workflow_id={workflow_id}, task_name={task_name}, task_version={task_version}")

    class JobIdAdded(Aggregate.Event):
        job_id: str

    @event(JobIdAdded)
    def add_job_id(self, job_id):
        logger.debug(f"Adding job_id to Task: task_id={self.id}, job_id={job_id}")
        self.job_ids.append(job_id)

    class StatusUpdated(Aggregate.Event):
        status: str

    @event(StatusUpdated)
    def update_task_status(self, status):
        logger.debug(f"Adding status to Task: task_id={self.id}, status={status}")
        self.status = status

    class ResultAdded(Aggregate.Event):
        result: ResultDict

    def add_result(self, result: Result):
        logger.debug(f"Adding result to Task: task_id={self.id}, result={result}")
        result = ResultDict(result)
        self._add_result(result)

    @event(ResultAdded)
    def _add_result(self, result: ResultDict):
        logger.debug(f"Adding result to Task results: task_id={self.id}, result={result}")
        self.results.append(result)

    class TaskStarted(Aggregate.Event):
        pass

    @event(TaskStarted)
    def start_task(self):
        logger.debug(f"Starting task: id={self.id}")
        pass

    class TaskCompleted(Aggregate.Event):
        result: str

    @event(TaskCompleted)
    def complete_task(self, result: str):
        logger.debug(f"Completing task: id={self.id}, result={result}")
        self.result_message = result

    class TaskFailed(Aggregate.Event):
        error_message: str

    @event(TaskFailed)
    def fail_task(self, error_message: str):
        logger.debug(f"Failing task: id={self.id}, error_message={error_message}")
        pass


class JobAccount(Aggregate):
    INITIAL_VERSION = 0

    def __init__(self, user_id, group_id):
        self.user_id = user_id
        self.group_id = group_id
        self.jobs = []

    @classmethod
    def create_id(cls, user_id, group_id):
        return uuid5(NAMESPACE_URL, f'/jobaccount/{group_id}/{user_id}')

    @event('JobAdded')
    def add_job(self, job_id):
        logger.debug(f"Adding job: id={self.id}, job={job_id}, user={self.user_id}, group={self.group_id}")
        self.jobs.append(job_id)


# -------------------- APPLICATIONS -------------------- #


class WorkflowTracker(Application):

    def initiate_workflow(self,
                          name: str,
                          description: str,
                          user: int,
                          group: int) -> UUID:
        logger.debug(f"Initiating workflow: name={name}, description={description}, user={user}, group={group}")
        workflow = WorkflowRun(name, description, user, group)
        self.save(workflow)
        return workflow.id

    def add_task_to_workflow(self,
                             workflow_id: UUID,
                             task_name: str,
                             task_version: str,
                             input_data: Dict[str, Any],
                             kwargs: Dict[str, Any]
                             ) -> UUID:
        logger.debug(f"Adding task to workflow: workflow_id={workflow_id}, task_name={task_name}, task_version={task_version}")

        task = Task(workflow_id,
                    task_name,
                    task_version,
                    input_data,
                    kwargs)
        self.save(task)
        workflow = self.repository.get(workflow_id)
        workflow.add_task(task.id)
        self.save(workflow)
        return task.id

    def start_workflow(self, workflow_id: UUID):
        logger.debug(f"Starting workflow: workflow_id={workflow_id}")

        workflow = self.repository.get(workflow_id)
        workflow.start_workflow()
        self.save(workflow)

    def complete_workflow(self, workflow_id: UUID):
        logger.debug(f"Completing workflow: workflow_id={workflow_id}")

        workflow = self.repository.get(workflow_id)
        workflow.complete_workflow()
        self.save(workflow)

    def fail_workflow(self, workflow_id: UUID, error_message: str):
        logger.debug(f"Failing workflow: workflow_id={workflow_id}, error_message={error_message}")

        workflow = self.repository.get(workflow_id)
        workflow.fail_workflow(error_message)
        self.save(workflow)

    def start_task(self, task_id: UUID):
        logger.debug(f"Starting task: task_id={task_id}")

        task = self.repository.get(task_id)
        task.start_task()
        self.save(task)

    def complete_task(self, task_id: UUID, message: str):
        logger.debug(f"Completing task: task_id={task_id}, message={message}")

        task = self.repository.get(task_id)
        task.complete_task(message)
        self.save(task)

    def fail_task(self, task_id: UUID, error_message: str):
        logger.debug(f"Failing task: task_id={task_id}, error_message={error_message}")

        task = self.repository.get(task_id)
        task.fail_task(error_message)
        self.save(task)

    def add_job_id(self, task_id, slurm_job_id):
        logger.debug(f"Adding job_id to task: task_id={task_id}, slurm_job_id={slurm_job_id}")

        task = self.repository.get(task_id)
        task.add_job_id(slurm_job_id)
        self.save(task)

    def add_result(self, task_id, result):
        logger.debug(f"Adding result to task: task_id={task_id}, result={result}")

        task = self.repository.get(task_id)
        task.add_result(result)
        self.save(task)

    def update_task_status(self, task_id, status):
        logger.debug(f"Adding status to task: task_id={task_id}, status={status}")

        task = self.repository.get(task_id)
        task.update_task_status(status)
        self.save(task)


#--------------------- VIEWS ---------------------------- #

# Base class for declarative class definitions
Base = declarative_base()


class JobView(Base):
    __tablename__ = 'biomero_job_view'

    slurm_job_id = Column(Integer, primary_key=True)
    user = Column(Integer, nullable=False)
    group = Column(Integer, nullable=False)


class JobAccounting(ProcessApplication):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # Read database configuration from environment variables
        database_url = URL.create(
            drivername="postgresql+psycopg2",
            username=os.getenv('POSTGRES_USER'),
            password=os.getenv('POSTGRES_PASSWORD'),
            host=os.getenv('POSTGRES_HOST', 'localhost'),
            port=os.getenv('POSTGRES_PORT', 5432),
            database=os.getenv('POSTGRES_DBNAME')
        )
        
        # Set up SQLAlchemy engine and session
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # State tracking
        self.workflows = {}  # {wf_id: {"user": user, "group": group}}
        self.tasks = {}      # {task_id: wf_id}
        self.jobs = {}       # {job_id: (task_id, user, group)}    
        
        # Create defined tables (subclasses of Base) if they don't exist
        Base.metadata.create_all(self.engine)
    
    @singledispatchmethod
    def policy(self, domain_event, process_event):
        """Default policy"""
        
    @policy.register(WorkflowRun.WorkflowInitiated)
    def _(self, domain_event, process_event):
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
    def _(self, domain_event, process_event):
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
    def _(self, domain_event, process_event):
        """Handle JobIdAdded event"""
        # Grab event payload
        job_id = domain_event.job_id
        task_id = domain_event.originator_id
        
        # Find workflow and user/group for the task
        wf_id = self.tasks.get(task_id)
        if wf_id:
            workflow_info = self.workflows.get(wf_id)
            if workflow_info:
                user = workflow_info["user"]
                group = workflow_info["group"]
                
                # Track job
                self.jobs[job_id] = (task_id, user, group)
                logger.debug(f"Job added: job_id={job_id}, task_id={task_id}, user={user}, group={group}")
                
                
                # Update view table
                self.update_view_table(job_id, user, group)
        else:
            logger.debug(f"JobIdAdded event ignored: task_id={task_id} not found in tasks")
            
        # use .collect_events(agg) instead of .save(agg)
        # process_event.collect_events(jobaccount)
        
    def update_view_table(self, job_id, user, group):
        """Update the view table with new job information."""
        with self.SessionLocal() as session:
            try:
                new_job = JobView(slurm_job_id=job_id, user=user, group=group)
                session.add(new_job)
                session.commit()
                logger.debug(f"Inserted job into view table: job_id={job_id}, user={user}, group={group}")
            except IntegrityError:
                session.rollback()
                # Handle the case where the job already exists in the table if necessary
                logger.error(f"Failed to insert job into view table (already exists?): job_id={job_id}, user={user}, group={group}")

    def get_jobs(self, user=None, group=None):
        """Retrieve jobs for a specific user and/or group.
        
        Parameters:
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary of user IDs to lists of job IDs if no user is specified.
        - Dictionary with a single user ID key and a list of job IDs if user is specified.
        
        Raises:
        - ValueError: If neither user nor group is provided.
        """
        if user is None and group is None:
            # Retrieve all jobs grouped by user
            with self.SessionLocal() as session:
                jobs = session.query(JobView.user, JobView.slurm_job_id).all()
                user_jobs = {}
                for user_id, job_id in jobs:
                    if user_id not in user_jobs:
                        user_jobs[user_id] = []
                    user_jobs[user_id].append(job_id)
                return user_jobs
        else:
            with self.SessionLocal() as session:
                query = session.query(JobView.slurm_job_id)
                
                if user is not None:
                    query = query.filter_by(user=user)
                
                if group is not None:
                    query = query.filter_by(group=group)
                
                jobs = query.all()
                result = {user: [job.slurm_job_id for job in jobs]}
                logger.debug(f"Retrieved jobs for user={user} and group={group}: {result}")
                return result
