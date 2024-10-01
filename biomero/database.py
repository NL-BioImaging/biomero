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
from eventsourcing.utils import get_topic, clear_topic_cache
import logging
from sqlalchemy import create_engine, text, Column, Integer, String, URL, DateTime, Float
from sqlalchemy.orm import sessionmaker, declarative_base, scoped_session
from sqlalchemy.dialects.postgresql import UUID as PGUUID
import os

logger = logging.getLogger(__name__)

# --------------------- VIEWS DB tables/classes ---------------------------- #

# Base class for declarative class definitions
Base = declarative_base()


class JobView(Base):
    """
    SQLAlchemy model for the 'biomero_job_view' table.

    Attributes:
        slurm_job_id (Integer): The unique identifier for the Slurm job.
        user (Integer): The ID of the user who submitted the job.
        group (Integer): The group ID associated with the job.
        task_id (UUID): The unique identifier for the biomero task
    """
    __tablename__ = 'biomero_job_view'

    slurm_job_id = Column(Integer, primary_key=True)
    user = Column(Integer, nullable=False)
    group = Column(Integer, nullable=False)
    task_id = Column(PGUUID(as_uuid=True))
    

class JobProgressView(Base):
    """
    SQLAlchemy model for the 'biomero_job_progress_view' table.

    Attributes:
        slurm_job_id (Integer): The unique identifier for the Slurm job.
        status (String): The current status of the Slurm job.
        progress (String, optional): The progress status of the Slurm job.
    """
    __tablename__ = 'biomero_job_progress_view'

    slurm_job_id = Column(Integer, primary_key=True)
    status = Column(String, nullable=False)
    progress = Column(String, nullable=True)


class WorkflowProgressView(Base):
    """
    SQLAlchemy model for the 'workflow_progress_view' table.

    Attributes:
        workflow_id (PGUUID): The unique identifier for the workflow (primary key).
        status (String, optional): The current status of the workflow.
        progress (String, optional): The progress status of the workflow.
        user (String, optional): The user who initiated the workflow.
        group (String, optional): The group associated with the workflow.
        name (String, optional): The name of the workflow
    """
    __tablename__ = 'biomero_workflow_progress_view'

    workflow_id = Column(PGUUID(as_uuid=True), primary_key=True)
    status = Column(String, nullable=True)
    progress = Column(String, nullable=True)
    user = Column(Integer, nullable=True)
    group = Column(Integer, nullable=True)
    name = Column(String, nullable=True)
    task = Column(String, nullable=True)
    start_time = Column(DateTime, nullable=False)
    
    
class TaskExecution(Base):
    """
    SQLAlchemy model for the 'biomero_task_execution' table.

    Attributes:
        task_id (PGUUID): The unique identifier for the task.
        task_name (String): The name of the task.
        task_version (String): The version of the task.
        user_id (Integer, optional): The ID of the user who initiated the task.
        group_id (Integer, optional): The group ID associated with the task.
        status (String): The current status of the task.
        start_time (DateTime): The time when the task started.
        end_time (DateTime, optional): The time when the task ended.
        error_type (String, optional): Type of error encountered during execution, if any.
    """
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
    

class EngineManager:
    """
    Manages the SQLAlchemy engine and session lifecycle.

    Class Attributes:
        _engine: The SQLAlchemy engine used to connect to the database.
        _scoped_session_topic: The topic of the scoped session.
        _session: The scoped session used for database operations.
    """
    _engine = None
    _scoped_session_topic = None
    _session = None
        
    @classmethod
    def create_scoped_session(cls, sqlalchemy_url: str = None):
        """
        Creates and returns a scoped session for interacting with the database.

        If the engine doesn't already exist, it initializes the SQLAlchemy engine 
        and sets up the scoped session.

        Args:
            sqlalchemy_url (str, optional): The SQLAlchemy database URL. If not provided, 
                the method will retrieve the value from the 'SQLALCHEMY_URL' environment variable.

        Returns:
            str: The topic of the scoped session adapter class.
        """
        if cls._engine is None:      
            # Note, we only allow sqlalchemy eventsourcing module
            if not sqlalchemy_url:
                sqlalchemy_url = os.getenv('SQLALCHEMY_URL')
            cls._engine = create_engine(sqlalchemy_url)
            
            # setup tables if they don't exist yet
            Base.metadata.create_all(cls._engine)
            
            # Create a scoped_session object.
            cls._session = scoped_session(
                sessionmaker(autocommit=False, autoflush=True, bind=cls._engine)
            )
            
            class MyScopedSessionAdapter:
                def __getattribute__(self, item: str) -> None:
                    return getattr(cls._session, item)
                
            # Produce the topic of the scoped session adapter class.
            cls._scoped_session_topic = get_topic(MyScopedSessionAdapter)

        return cls._scoped_session_topic
    
    @classmethod
    def get_session(cls):
        """
        Retrieves the current scoped session.

        Returns:
            Session: The SQLAlchemy session for interacting with the database.
        """
        return cls._session()
    
    @classmethod
    def commit(cls):
        """
        Commits the current transaction in the scoped session.
        """
        cls._session.commit()
            
    @classmethod
    def close_engine(cls):
        """
        Closes the database engine and cleans up the session.

        This method disposes of the SQLAlchemy engine, removes the session, 
        and resets all associated class attributes to `None`.
        """
        if cls._engine is not None:
            cls._session.remove()
            cls._engine.dispose()
            cls._engine = None
            cls._session = None  
            cls._scoped_session_topic = None
            clear_topic_cache()
