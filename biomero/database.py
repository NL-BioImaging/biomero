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
from sqlalchemy import (
    create_engine,
    Column,
    Integer,
    String,
    DateTime,
    event,
)
from sqlalchemy.orm import sessionmaker, declarative_base, scoped_session
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.exc import OperationalError, IntegrityError
import os
import time
import random
from contextlib import contextmanager
from functools import wraps

# Import EventSourcing exceptions
try:
    from eventsourcing.persistence import IntegrityError as EventSourcingIntegrityError
    from eventsourcing.persistence import OperationalError as EventSourcingOperationalError
except ImportError:
    # Fallback for older versions
    EventSourcingIntegrityError = None
    EventSourcingOperationalError = None

logger = logging.getLogger(__name__)

# --------------------- CONCURRENCY HELPERS ---------------------------- #

def retry_on_database_conflict(max_retries=10, base_delay=0.1, max_delay=5.0):
    """Decorator to retry database operations on concurrency conflicts.
    
    Args:
        max_retries: Maximum number of retry attempts
        base_delay: Base delay between retries in seconds
        max_delay: Maximum delay between retries in seconds
    """
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            
            # Build list of exception types to catch
            exception_types = [IntegrityError, OperationalError]
            if EventSourcingIntegrityError:
                exception_types.append(EventSourcingIntegrityError)
            if EventSourcingOperationalError:
                exception_types.append(EventSourcingOperationalError)
            exception_types = tuple(exception_types)
            
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except exception_types as e:
                    last_exception = e
                    
                    # Don't retry on the last attempt
                    if attempt == max_retries:
                        break
                    
                    # Check if it's a conflict we can retry
                    error_msg = str(e).lower()
                    
                    # For EventSourcing exceptions, also check the underlying cause
                    if hasattr(e, '__cause__') and e.__cause__:
                        error_msg += " " + str(e.__cause__).lower()
                    
                    # These are retryable concurrency conflicts
                    retryable_conflicts = [
                        'unique constraint',
                        'duplicate key',
                        'concurrent update',
                        'transaction is aborted',
                        'deadlock detected'
                    ]
                    
                    # These are permanent data validation errors - do NOT retry
                    non_retryable_errors = [
                        'not null constraint',
                        'null value in column',
                        'cannot be null'
                    ]
                    
                    # Check for non-retryable errors first
                    if any(error in error_msg for error in non_retryable_errors):
                        logger.debug(f"Non-retryable data validation error: {e}")
                        break
                    
                    # Check for retryable conflicts
                    if any(conflict in error_msg for conflict in retryable_conflicts):
                        # Calculate exponential backoff with jitter
                        delay = min(
                            base_delay * (2 ** attempt) + random.uniform(0, 0.1),
                            max_delay
                        )
                        logger.warning(
                            f"Database conflict on attempt {attempt + 1}/{max_retries + 1}: {e}. "
                            f"Retrying in {delay:.2f}s..."
                        )
                        
                        time.sleep(delay)
                        
                        # Rollback current transaction
                        try:
                            EngineManager.rollback()
                        except:
                            pass
                    else:
                        # Not a retryable error
                        break
                        
            # All retries exhausted, raise the last exception
            logger.error(f"Database operation failed after {max_retries + 1} attempts: {last_exception}")
            raise last_exception
            
        return wrapper
    return decorator


@contextmanager
def database_transaction(isolation_level=None):
    """Context manager for database transactions with proper error handling.
    
    Args:
        isolation_level: SQLAlchemy isolation level (e.g., 'READ_COMMITTED', 'SERIALIZABLE')
    """
    # Don't manage session lifecycle here - let the scoped session handle it
    # Just provide transaction boundaries and error handling
    try:
        # Set isolation level if specified
        if isolation_level:
            session = EngineManager.get_session()
            session.connection(execution_options={'isolation_level': isolation_level})
            
        yield
        
        # Commit if we reach this point
        EngineManager.commit()
        
    except Exception as e:
        try:
            EngineManager.rollback()
            logger.debug(f"Transaction rolled back due to: {e}")
        except Exception as rollback_error:
            logger.error(f"Error during rollback: {rollback_error}")
        raise

# --------------------- VIEWS DB tables/classes ---------------------------- #

# Base class for declarative class definitions
Base = declarative_base()

# Set to True when Base.metadata.create_all created any tables in this process
CREATED_ANY_TABLES = False


class JobView(Base):
    """
    SQLAlchemy model for the 'biomero_job_view' table.

    Attributes:
        slurm_job_id (Integer): The unique identifier for the Slurm job.
        user (Integer): The ID of the user who submitted the job.
        group (Integer): The group ID associated with the job.
        task_id (UUID): The unique identifier for the biomero task

    Note: Schema changes are handled via event sourcing system rebuild.
    Use SlurmClient.initialize_analytics_system(reset_tables=True) to
    apply changes.
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

    Note: Schema changes are handled via event sourcing system rebuild.
    Use SlurmClient.initialize_analytics_system(reset_tables=True) to
    apply changes.
    """
    __tablename__ = 'biomero_job_progress_view'

    slurm_job_id = Column(Integer, primary_key=True)
    status = Column(String, nullable=False)
    progress = Column(String, nullable=True)


class WorkflowProgressView(Base):
    """
    SQLAlchemy model for the 'workflow_progress_view' table.

    Attributes:
        workflow_id (PGUUID): The unique identifier for the workflow
            (primary key).
        status (String, optional): The current status of the workflow.
        progress (String, optional): The progress status of the workflow.
        user (String, optional): The user who initiated the workflow.
        group (String, optional): The group associated with the workflow.
        name (String, optional): The name of the workflow
        task (String, optional): The current task being executed
        main_task_name (String, optional): The main user-facing workflow task
            (e.g., "cellexpansion" instead of "SLURM_Get_Results.py")

    Note: Schema changes are handled via event sourcing system rebuild.
    Use SlurmClient.initialize_analytics_system(reset_tables=True) to
    apply changes.
    """
    __tablename__ = 'biomero_workflow_progress_view'

    workflow_id = Column(PGUUID(as_uuid=True), primary_key=True)
    status = Column(String, nullable=True)
    progress = Column(String, nullable=True)
    user = Column(Integer, nullable=True)
    group = Column(Integer, nullable=True)
    name = Column(String, nullable=True)
    task = Column(String, nullable=True)
    main_task_name = Column(String, nullable=True)
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
        error_type (String, optional): Type of error encountered during
            execution, if any.

        Migration warning:
            Any schema change (columns, types, constraints, indexes)
            must be captured in a new Alembic revision and applied to
            the DB.

                PowerShell (Windows):
                        $env:SQLALCHEMY_URL = \
                            'postgresql+psycopg2://USER:PASS@HOST:5432/DB'
                        cd biomero/biomero
                        alembic -c migrations/alembic.ini revision \
                            --autogenerate -m "explain change"

                        Bash (Linux/macOS):
                                export SQLALCHEMY_URL= \
                                    postgresql+psycopg2://USER:PASS@HOST:5432/DB
                        cd biomero/biomero
                        alembic -c migrations/alembic.ini revision \
                            --autogenerate -m "explain change"

                Alternatively run upgrade:
                        alembic -c migrations/alembic.ini upgrade head

                        Notes:
                                - Version table: alembic_version_biomero
                                - Only BIOMERO tables are included via env.py
                                    include_object
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
    

# Listen for SQLAlchemy actually creating one or more tables via create_all
@event.listens_for(Base.metadata, "after_create")
def _receive_after_create(target, connection, tables, **kw):
    global CREATED_ANY_TABLES
    if tables:
        CREATED_ANY_TABLES = True
    

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
        Creates and returns a scoped session for interacting with the
        database.

        If the engine doesn't already exist, it initializes the SQLAlchemy
        engine and sets up the scoped session.

        Args:
            sqlalchemy_url (str, optional): The SQLAlchemy database URL. If
                not provided, the method will retrieve the value from the
                'SQLALCHEMY_URL' environment variable.

        Returns:
            str: The topic of the scoped session adapter class.
        """
        if cls._engine is None:
            # Note, we only allow sqlalchemy eventsourcing module
            if not sqlalchemy_url:
                sqlalchemy_url = os.getenv('SQLALCHEMY_URL')
            cls._engine = create_engine(sqlalchemy_url)
            # Setup tables if they don't exist yet, and detect fresh installs
            Base.metadata.create_all(cls._engine)

            # Create a scoped_session object.
            cls._session = scoped_session(
                sessionmaker(
                    autocommit=False,
                    autoflush=True,
                    bind=cls._engine,
                )
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
        try:
            cls._session.commit()
        except Exception as e:
            logger.warning(f"Database commit failed (will be retried if applicable): {e}")
            cls.rollback()
            raise
    
    @classmethod
    def rollback(cls):
        """
        Rolls back the current transaction in the scoped session.
        """
        try:
            cls._session.rollback()
        except Exception as e:
            logger.warning(f"Database rollback failed (forcing session removal): {e}")
            # Force session removal on rollback failure
            cls._session.remove()
            raise
    
    @classmethod
    def remove_session(cls):
        """
        Removes the current session from the scoped session registry.
        """
        cls._session.remove()
    
    @classmethod
    @retry_on_database_conflict(max_retries=10)
    def safe_commit(cls):
        """
        Commits the transaction with automatic retry on conflicts.
        """
        cls.commit()
            
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
