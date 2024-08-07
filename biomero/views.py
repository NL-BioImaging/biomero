import os

from eventsourcing.system import ProcessApplication
from eventsourcing.dispatch import singledispatchmethod
from uuid import NAMESPACE_URL, UUID, uuid5
from typing import Any, Dict, List
import logging
from sqlalchemy import create_engine, text, Column, Integer, String, URL
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy.exc import IntegrityError
from biomero.eventsourcing import WorkflowRun, Task


logger = logging.getLogger(__name__)

# --------------------- VIEWS ---------------------------- #

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