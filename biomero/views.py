import os

from eventsourcing.system import ProcessApplication
from eventsourcing.dispatch import singledispatchmethod
from uuid import NAMESPACE_URL, UUID, uuid5
from typing import Any, Dict, List
import logging
from sqlalchemy import create_engine, text, Column, Integer, String, URL, DateTime, Float
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import IntegrityError
from sqlalchemy.sql import func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from biomero.eventsourcing import WorkflowRun, Task


logger = logging.getLogger(__name__)

# --------------------- VIEWS DB tables/classes ---------------------------- #

# Base class for declarative class definitions
Base = declarative_base()


class JobView(Base):
    __tablename__ = 'biomero_job_view'

    slurm_job_id = Column(Integer, primary_key=True)
    user = Column(Integer, nullable=False)
    group = Column(Integer, nullable=False)
    

class JobProgressView(Base):
    __tablename__ = 'biomero_job_progress_view'

    slurm_job_id = Column(Integer, primary_key=True)
    status = Column(String, nullable=False)
    progress = Column(String, nullable=True)
    

class TaskExecution(Base):
    __tablename__ = 'biomero_task_execution'

    task_id = Column(PGUUID(as_uuid=True), primary_key=True)
    task_name = Column(String, nullable=False)
    task_version = Column(String)
    user_id = Column(Integer, nullable=True)
    group_id = Column(Integer, nullable=True)
    status = Column(String, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=True)
    error_type = Column(String, nullable=True)
    
    
# ------------------- View Listener Applications ------------------ #


class BaseApplication:
    def __init__(self):
        # Read database configuration from environment variables
        persistence_mod = os.getenv('PERSISTENCE_MODULE')
        if 'postgres' in persistence_mod:
            logger.info("Using postgres database")
            database_url = URL.create(
                drivername="postgresql+psycopg2",
                username=os.getenv('POSTGRES_USER'),
                password=os.getenv('POSTGRES_PASSWORD'),
                host=os.getenv('POSTGRES_HOST', 'localhost'),
                port=os.getenv('POSTGRES_PORT', 5432),
                database=os.getenv('POSTGRES_DBNAME')
            )
        elif 'sqlite' in persistence_mod:
            logger.info("Using sqlite in-mem database")
            database_url = URL.create(
                drivername="sqlite",
                database=os.getenv('SQLITE_DBNAME')
            )
        else:
            raise NotImplementedError(f"Can't handle {persistence_mod}")

        # Set up SQLAlchemy engine and session
        self.engine = create_engine(database_url)
        self.SessionLocal = sessionmaker(bind=self.engine)
        
        # Create defined tables (subclasses of Base) if they don't exist
        Base.metadata.create_all(self.engine)


class JobAccounting(ProcessApplication, BaseApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)
        BaseApplication.__init__(self)
        
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
            
            
class JobProgress(ProcessApplication, BaseApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)
        BaseApplication.__init__(self)
        
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
        logger.debug(f"JobId added: job_id={job_id}, task_id={task_id}")
        
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
            
            logger.debug(f"Status updated: job_id={job_id}, status={status}")
            # Update view table
            self.update_view_table(job_id)
    
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
            
            logger.debug(f"Progress updated: job_id={job_id}, progress={progress}")
            # Update view table
            self.update_view_table(job_id)
        
    def update_view_table(self, job_id):
        """Update the view table with new job status and progress information."""
        with self.SessionLocal() as session:
            try:
                job_info = self.job_status[job_id]
                new_job_progress = JobProgressView(
                    slurm_job_id=job_id,
                    status=job_info["status"],
                    progress=job_info["progress"]
                )
                session.merge(new_job_progress)  # Use merge to insert or update
                session.commit()
                logger.debug(f"Inserted/Updated job progress in view table: job_id={job_id}, status={job_info['status']}, progress={job_info['progress']}")
            except IntegrityError:
                session.rollback()
                logger.error(f"Failed to insert/update job progress in view table: job_id={job_id}")


class WorkflowAnalytics(BaseApplication, ProcessApplication):
    def __init__(self, *args, **kwargs):
        ProcessApplication.__init__(self, *args, **kwargs)
        BaseApplication.__init__(self)

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
        logger.debug(f"Workflow initiated: wf_id={wf_id}, user={user}, group={group}")

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

        logger.debug(f"Task added: task_id={task_id}, wf_id={wf_id}")

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
                "start_time": timestamp_created
            })
        else:
            # Initialize task tracking if TaskAdded hasn't been processed yet
            self.tasks[task_id] = {
                "task_name": task_name,
                "task_version": task_version,
                "start_time": timestamp_created
            }

        logger.debug(f"Task created: task_id={task_id}, task_name={task_name}, timestamp={timestamp_created}")
        self.update_view_table(task_id)

    @policy.register(Task.StatusUpdated)
    def _(self, domain_event, process_event):
        """Handle StatusUpdated event"""
        task_id = domain_event.originator_id
        status = domain_event.status

        # Update task with status
        if task_id in self.tasks:
            self.tasks[task_id]["status"] = status
            logger.debug(f"Task status updated: task_id={task_id}, status={status}")
            self.update_view_table(task_id)

    @policy.register(Task.TaskCompleted)
    def _(self, domain_event, process_event):
        """Handle TaskCompleted event"""
        task_id = domain_event.originator_id
        timestamp_completed = domain_event.timestamp

        # Update task with end time
        if task_id in self.tasks:
            self.tasks[task_id]["end_time"] = timestamp_completed
            logger.debug(f"Task completed: task_id={task_id}, end_time={timestamp_completed}")
            self.update_view_table(task_id)

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
            logger.debug(f"Task failed: task_id={task_id}, end_time={timestamp_failed}, error={error_message}")
            self.update_view_table(task_id)

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

        with self.SessionLocal() as session:
            try:
                existing_task = session.query(TaskExecution).filter_by(task_id=task_id).first()
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
                logger.debug(f"Updated/Inserted task execution into view table: task_id={task_id}, task_name={task_info.get('task_name')}")
            except IntegrityError:
                session.rollback()
                logger.error(f"Failed to insert/update task execution into view table: task_id={task_id}")

    def get_task_counts(self, user=None, group=None):
        """Retrieve task execution counts grouped by task name and version.
        
        Parameters:
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary of task names and versions to counts.
        """
        with self.SessionLocal() as session:
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
            logger.debug(f"Retrieved task counts: {result}")
            return result

    def get_average_task_duration(self, user=None, group=None):
        """Retrieve the average task duration grouped by task name and version.
        
        Parameters:
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary of task names and versions to average duration (in seconds).
        """
        with self.SessionLocal() as session:
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
            logger.debug(f"Retrieved average task durations: {result}")
            return result

    def get_task_failures(self, user=None, group=None):
        """Retrieve tasks that failed, grouped by task name and version.
        
        Parameters:
        - user (int, optional): The user ID to filter by.
        - group (int, optional): The group ID to filter by.
        
        Returns:
        - Dictionary of task names and versions to lists of failure reasons.
        """
        with self.SessionLocal() as session:
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
            
            logger.debug(f"Retrieved task failures: {result}")
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
        with self.SessionLocal() as session:
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
            logger.debug(f"Retrieved task usage over time for {task_name}: {result}")
            return result
