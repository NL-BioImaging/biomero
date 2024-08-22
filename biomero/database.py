from eventsourcing.utils import get_topic
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
    

class EngineManager:
    _engine = None
    _scoped_session_topic = None
    _session = None
        
    @classmethod
    def create_scoped_session(cls, sqlalchemy_url=None):
        if cls._engine is None:      
            # Note, we only allow sqlalchemy eventsourcing module
            if not sqlalchemy_url:
                sqlalchemy_url = os.getenv('SQLALCHEMY_URL')
            cls._engine = create_engine(sqlalchemy_url)
            
            # setup tables if needed
            Base.metadata.create_all(cls._engine)
            
            # Create a scoped_session object.
            cls._session = scoped_session(
                sessionmaker(autocommit=False, autoflush=False, bind=cls._engine)
            )
            
            class MyScopedSessionAdapter:
                def __getattribute__(self, item: str) -> None:
                    return getattr(cls._session, item)
                
            # Produce the topic of the scoped session adapter class.
            cls._scoped_session_topic = get_topic(MyScopedSessionAdapter)

        return cls._scoped_session_topic
    
    @classmethod
    def get_session(cls):
        return cls._session()
    
    @classmethod
    def commit(cls):
        cls._session.commit()
            
    @classmethod
    def close_engine(cls):
        if cls._engine is not None:
            cls._session.remove()
            cls._engine.dispose()
            cls._engine = None
            cls._session = None  
            cls._scoped_session_topic = None
