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
from eventsourcing.system import ProcessApplication
from eventsourcing.dispatch import singledispatchmethod
import logging
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func
from biomero.eventsourcing import WorkflowRun, Task
from biomero.database import EngineManager, JobView, TaskExecution, JobProgressView, WorkflowProgressView
from biomero.constants import workflow_status as wfs

logger = logging.getLogger(__name__)

    
# ------------------- View Listener Applications ------------------ #


class JobAccounting(ProcessApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)
        
        # State tracking
        self.workflows = {}  # {wf_id: {"user": user, "group": group}}
        self.tasks = {}      # {task_id: wf_id}
        self.jobs = {}       # {job_id: (task_id, user, group)}   
                   
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
        EngineManager.commit()

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
        EngineManager.commit()

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
                self.update_view_table(job_id, user, group, task_id)
        else:
            logger.debug(f"JobIdAdded event ignored: task_id={task_id} not found in tasks")
            
        # use .collect_events(agg) instead of .save(agg)
        # process_event.collect_events(jobaccount)
        EngineManager.commit()
        
    def update_view_table(self, job_id, user, group, task_id):
        """Update the view table with new job information."""
        with EngineManager.get_session() as session:
            try:
                new_job = JobView(slurm_job_id=job_id, user=user, group=group, task_id=task_id)
                session.add(new_job)
                session.commit()
                logger.debug(f"Inserted job into view table: job_id={job_id}, user={user}, group={group}, task_id={task_id}")
            except IntegrityError as e:
                session.rollback()
                # Handle the case where the job already exists in the table if necessary
                logger.error(f"Failed to insert job into view table (already exists?): job_id={job_id}, user={user}, group={group}. Error {e}")

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

        Parameters:
        - job_id (int): The job ID (slurm_job_id) to look up.

        Returns:
        - UUID: The task ID associated with the job ID, or None if not found.
        """
        with EngineManager.get_session() as session:
            try:
                task_id = session.query(JobView.task_id).filter(JobView.slurm_job_id == job_id).one_or_none()
                logger.debug(f"Retrieved task_id={task_id[0] if task_id else None} for job_id={job_id}")
                return task_id[0] if task_id else None
            except Exception as e:
                logger.error(f"Failed to retrieve task_id for job_id={job_id}: {e}")
                return None
            
            
class WorkflowProgress(ProcessApplication):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # State tracking: {workflow_id: {"status": status, "progress": progress, "user": user, "group": group}}
        self.workflows = {}  
        self.tasks = {}  # {task_id: {"workflow_id": wf_id, "task_name": task_name}}

    @singledispatchmethod
    def policy(self, domain_event, process_event):
        """Default policy"""
        pass

    @policy.register(WorkflowRun.WorkflowInitiated)
    def _(self, domain_event, process_event):
        """Handle WorkflowInitiated event"""
        user = domain_event.user
        group = domain_event.group
        wf_id = domain_event.originator_id
        name = domain_event.name
        start_time = domain_event.timestamp

        # Track workflow with user, group, and INITIATED status
        self.workflows[wf_id] = {"status": wfs.INITIALIZING, 
                                       "progress": "0%", 
                                       "user": user, 
                                       "group": group,
                                       "name": name,
                                       "task": None,
                                       "start_time": start_time}
        logger.debug(f"[WFP] Workflow initiated: wf_id={wf_id}, name={name}, user={user}, group={group}, status={wfs.INITIALIZING} -- {domain_event.__dict__}")
        self.update_view_table(wf_id)
        EngineManager.commit()

    @policy.register(WorkflowRun.WorkflowCompleted)
    def _(self, domain_event, process_event):
        wf_id = domain_event.originator_id
        self.workflows[wf_id]["status"] = wfs.DONE
        self.workflows[wf_id]["progress"] = "100%"
        logger.debug(f"[WFP] Status updated: wf_id={wf_id}, status={wfs.DONE} -- {domain_event.__dict__}")
        self.update_view_table(wf_id)
        EngineManager.commit()
        
    @policy.register(WorkflowRun.WorkflowFailed)
    def _(self, domain_event, process_event):
        wf_id = domain_event.originator_id
        error = domain_event.error_message
        self.workflows[wf_id]["status"] = wfs.FAILED
        logger.debug(f"[WFP] Status updated: wf_id={wf_id}, status={wfs.FAILED} -- {domain_event.__dict__}")
        self.update_view_table(wf_id)
        EngineManager.commit()

    @policy.register(WorkflowRun.TaskAdded)
    def _(self, domain_event, process_event):
        """Handle TaskAdded event"""
        task_id = domain_event.task_id
        wf_id = domain_event.originator_id

        # Track task to workflow mapping
        if task_id in self.tasks:
            self.tasks[task_id]["workflow_id"] = wf_id
            if wf_id in self.workflows:
                self.workflows[wf_id]["task"] = self.tasks[task_id]["task_name"]
        logger.debug(f"[WFP] Task added: task_id={task_id}, wf_id={wf_id} -- {domain_event.__dict__}")
        EngineManager.commit()

    @policy.register(Task.TaskCreated)
    def _(self, domain_event, process_event):
        task_id = domain_event.originator_id
        task_name = domain_event.task_name
        
        # store task name
        self.tasks[task_id] = {
            "task_name": task_name,
            "workflow_id": None, 
            "progress": None 
            }
        logger.debug(f"[WFP] Task created: task_id={task_id}, task_name={task_name} -- {domain_event.__dict__}")
        EngineManager.commit()

    @policy.register(Task.StatusUpdated)
    def _(self, domain_event, process_event):
        """Handle Task StatusUpdated event"""
        task_id = domain_event.originator_id
        status = domain_event.status

        # Get the workflow ID and task name associated with this task
        task_info = self.tasks.get(task_id)
        if task_info:
            wf_id = task_info["workflow_id"]
            task_name = task_info["task_name"]

            if wf_id and wf_id in self.workflows:
                # Determine status based on task_name
                if task_name == '_SLURM_Image_Transfer.py':
                    workflow_status = wfs.TRANSFERRING
                    workflow_prog = "5%"
                elif task_name.startswith('convert_'):
                    workflow_status = wfs.CONVERTING
                    workflow_prog = "25%"
                elif task_name == 'SLURM_Get_Results.py':
                    workflow_status = wfs.RETRIEVING
                    workflow_prog = "90%"
                elif task_name == 'SLURM_Run_Workflow.py':
                    workflow_status = wfs.RUNNING
                    workflow_prog = "50%"
                else:
                    # Default to JOB_STATUS prefix for unknown task types
                    workflow_status = wfs.JOB_STATUS + status
                    workflow_prog = "50%"
                    if "task_progress" in self.workflows[wf_id]:
                        task_prog = self.workflows[wf_id]["task_progress"]
                        if task_prog:
                            # Initial string and baseline
                            upper_limit_str = "90%"  # Upper limit string
                            # Step 1: Extract integers from the strings
                            current_val = int(task_prog.strip('%'))
                            baseline_val = int(workflow_prog.strip('%'))
                            upper_limit_val = int(upper_limit_str.strip('%'))

                            # Step 2: Interpolation logic
                            # Map the current_val (43) between the range 50 and 90
                            # Formula for linear interpolation: new_val = baseline + (current_val / 100) * (upper_limit - baseline)
                            interpolated_val = baseline_val + ((current_val / 100) * (upper_limit_val - baseline_val))
                            workflow_prog = f"{interpolated_val}%"
                    

                # Update the workflow status
                self.workflows[wf_id]["status"] = workflow_status
                self.workflows[wf_id]["progress"] = workflow_prog
                logger.debug(f"[WFP] Status updated: wf_id={wf_id}, task_id={task_id}, status={workflow_status} -- {domain_event.__dict__}")
                self.update_view_table(wf_id)
        EngineManager.commit()

    @policy.register(Task.ProgressUpdated)
    def _(self, domain_event, process_event):
        """Handle ProgressUpdated event"""
        task_id = domain_event.originator_id
        progress = domain_event.progress
        
        if task_id in self.tasks:
            self.tasks[task_id]["progress"] = progress
            wf_id = self.tasks[task_id]["workflow_id"]
            if wf_id and wf_id in self.workflows:
                self.workflows[wf_id]["task_progress"] = progress
                logger.debug(f"[WFP] (Task) Progress updated: wf_id={wf_id}, progress={progress} -- {domain_event.__dict__}")
                self.update_view_table(wf_id)   
        EngineManager.commit() 
    
    def update_view_table(self, wf_id):
        """Update the view table with new workflow status, progress, user, and group."""
        with EngineManager.get_session() as session:
            workflow_info = self.workflows[wf_id]
            try:                
                new_workflow_progress = WorkflowProgressView(
                    workflow_id=wf_id,
                    status=workflow_info["status"],
                    progress=workflow_info["progress"],
                    user=workflow_info["user"],
                    group=workflow_info["group"],
                    name=workflow_info["name"],
                    task=workflow_info["task"],
                    start_time=workflow_info["start_time"]
                )
                session.merge(new_workflow_progress)
                session.commit()
                logger.debug(f"[WFP] Inserted wf progress in view table: wf_id={wf_id} wf_info={workflow_info}")
            except IntegrityError:
                session.rollback()
                logger.error(f"[WFP] Failed to insert/update wf progress in view table: wf_id={wf_id} wf_info={workflow_info}")


class JobProgress(ProcessApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)
        
        # State tracking
        self.task_to_job = {}  # {task_id: job_id}
        self.job_status = {}   # {job_id: {"status": status, "progress": progress}}
    
    @singledispatchmethod
    def policy(self, domain_event, process_event):
        """Default policy"""
    
    @policy.register(Task.JobIdAdded)
    def _(self, domain_event, process_event):
        """Handle JobIdAdded event"""
        job_id = domain_event.job_id
        task_id = domain_event.originator_id
        
        # Track task to job mapping
        self.task_to_job[task_id] = job_id
        logger.debug(f"[JP] JobId added: job_id={job_id}, task_id={task_id} -- {domain_event.__dict__}")
        EngineManager.commit()
        
    @policy.register(Task.StatusUpdated)
    def _(self, domain_event, process_event):
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
        EngineManager.commit()
    
    @policy.register(Task.ProgressUpdated)
    def _(self, domain_event, process_event):
        """Handle ProgressUpdated event"""
        task_id = domain_event.originator_id
        progress = domain_event.progress
        
        job_id = self.task_to_job.get(task_id)
        if job_id is not None:
            if job_id in self.job_status:
                self.job_status[job_id]["progress"] = progress
            else:
                self.job_status[job_id] = {"status": "UNKNOWN", "progress": progress}
            
            logger.debug(f"[JP] Progress updated: job_id={job_id}, progress={progress} -- {domain_event.__dict__}")
            # Update view table
            self.update_view_table(job_id)
        EngineManager.commit()
        
    def update_view_table(self, job_id):
        """Update the view table with new job status and progress information."""
        with EngineManager.get_session() as session:
            try:
                job_info = self.job_status[job_id]
                new_job_progress = JobProgressView(
                    slurm_job_id=job_id,
                    status=job_info["status"],
                    progress=job_info["progress"]
                )
                session.merge(new_job_progress)  # Use merge to insert or update
                session.commit()
                logger.debug(f"[JP] Inserted/Updated job progress in view table: job_id={job_id}, status={job_info['status']}, progress={job_info['progress']}")
            except IntegrityError:
                session.rollback()
                logger.error(f"[JP] Failed to insert/update job progress in view table: job_id={job_id}")


# @event.listens_for(Engine, "before_cursor_execute")
# def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
#     logger.debug(f"SQL: {statement}")
#     logger.debug(f"Parameters: {parameters}")


class WorkflowAnalytics(ProcessApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)

        # State tracking for workflows and tasks
        self.workflows = {}  # {wf_id: {"user": user, "group": group}}
        self.tasks = {}      # {task_id: {"wf_id": wf_id, "task_name": task_name, "task_version": task_version, "start_time": timestamp, "status": status, "end_time": timestamp, "error_type": error_type}}

    @singledispatchmethod
    def policy(self, domain_event, process_event):
        """Default policy"""
        pass

    @policy.register(WorkflowRun.WorkflowInitiated)
    def _(self, domain_event, process_event):
        """Handle WorkflowInitiated event"""
        user = domain_event.user
        group = domain_event.group
        wf_id = domain_event.originator_id

        # Track workflow
        self.workflows[wf_id] = {"user": user, "group": group}
        logger.debug(f"[WFA] Workflow initiated: wf_id={wf_id}, user={user}, group={group} -- {domain_event.__dict__}")
        EngineManager.commit()

    @policy.register(WorkflowRun.TaskAdded)
    def _(self, domain_event, process_event):
        """Handle TaskAdded event"""
        task_id = domain_event.task_id
        wf_id = domain_event.originator_id

        # Add workflow ID to the existing task information
        if task_id in self.tasks:
            self.tasks[task_id]["wf_id"] = wf_id
        else:
            # In case TaskAdded arrives before TaskCreated (unlikely but possible)
            self.tasks[task_id] = {"wf_id": wf_id}

        logger.debug(f"[WFA] Task added: task_id={task_id}, wf_id={wf_id} -- {domain_event.__dict__}")
        EngineManager.commit()

    @policy.register(Task.TaskCreated)
    def _(self, domain_event, process_event):
        """Handle TaskCreated event"""
        task_id = domain_event.originator_id
        task_name = domain_event.task_name
        task_version = domain_event.task_version
        timestamp_created = domain_event.timestamp

        # Track task creation details
        if task_id in self.tasks:
            self.tasks[task_id].update({
                "task_name": task_name,
                "task_version": task_version,
                "start_time": timestamp_created,
                "status": "CREATED"
            })
        else:
            # Initialize task tracking if TaskAdded hasn't been processed yet
            self.tasks[task_id] = {
                "task_name": task_name,
                "task_version": task_version,
                "start_time": timestamp_created,
                "status": "CREATED"
            }

        logger.debug(f"[WFA] Task created: task_id={task_id}, task_name={task_name}, timestamp={timestamp_created} -- {domain_event.__dict__}")
        self.update_view_table(task_id)
        EngineManager.commit()

    @policy.register(Task.StatusUpdated)
    def _(self, domain_event, process_event):
        """Handle StatusUpdated event"""
        task_id = domain_event.originator_id
        status = domain_event.status

        # Update task with status
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = status
            logger.debug(f"[WFA] Task status updated: task_id={task_id}, status={status} -- {domain_event.__dict__}")
            self.update_view_table(task_id)
        EngineManager.commit()

    @policy.register(Task.TaskCompleted)
    def _(self, domain_event, process_event):
        """Handle TaskCompleted event"""
        task_id = domain_event.originator_id
        timestamp_completed = domain_event.timestamp

        # Update task with end time
        if task_id in self.tasks:
            self.tasks[task_id]["end_time"] = timestamp_completed
            logger.debug(f"[WFA] Task completed: task_id={task_id}, end_time={timestamp_completed} -- {domain_event.__dict__}")
            self.update_view_table(task_id)
        EngineManager.commit()

    @policy.register(Task.TaskFailed)
    def _(self, domain_event, process_event):
        """Handle TaskFailed event"""
        task_id = domain_event.originator_id
        timestamp_failed = domain_event.timestamp
        error_message = domain_event.error_message

        # Update task with end time and error message
        if task_id in self.tasks:
            self.tasks[task_id]["end_time"] = timestamp_failed
            self.tasks[task_id]["error_type"] = error_message
            logger.debug(f"[WFA] Task failed: task_id={task_id}, end_time={timestamp_failed}, error={error_message} -- {domain_event.__dict__}")
            self.update_view_table(task_id)
        EngineManager.commit()

    def update_view_table(self, task_id):
        """Update the view table with new task execution information."""
        task_info = self.tasks.get(task_id)
        if not task_info:
            return  # Skip if task information is incomplete

        wf_id = task_info.get("wf_id")
        user_id = None
        group_id = None

        # Retrieve user and group from workflow
        if wf_id and wf_id in self.workflows:
            user_id = self.workflows[wf_id]["user"]
            group_id = self.workflows[wf_id]["group"]

        with EngineManager.get_session() as session:
            try:
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
                    new_task_execution = TaskExecution(
                        task_id=task_id,
                        task_name=task_info.get("task_name"),
                        task_version=task_info.get("task_version"),
                        user_id=user_id,
                        group_id=group_id,
                        status=task_info.get("status"),
                        start_time=task_info.get("start_time"),
                        end_time=task_info.get("end_time"),
                        error_type=task_info.get("error_type")
                    )
                    session.add(new_task_execution)
                
                session.commit()
                logger.debug(f"[WFA] Updated/Inserted task execution into view table: task_id={task_id}, task_name={task_info.get('task_name')}")
            except IntegrityError as e:
                session.rollback()
                logger.error(f"[WFA] Failed to insert/update task execution into view table: task_id={task_id}, error={str(e)}")
                logger.debug(f"[WFA] Task info: {task_info}")

    def get_task_counts(self, user=None, group=None):
        """Retrieve task execution counts grouped by task name and version.
        
        Parameters:
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary of task names and versions to counts.
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
        
        Parameters:
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary of task names and versions to average duration (in seconds).
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
        
        Parameters:
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary of task names and versions to lists of failure reasons.
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
        
        Parameters:
        - task_name (str): The name of the task to filter by.
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary mapping date to the count of task executions on that date.
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
