import os
import logging
import pathlib
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

MIGRATIONS_DIR = str(pathlib.Path(__file__).with_name("migrations"))
VERSION_TABLE = "alembic_version_biomero"


def _mask_url(url: str) -> str:
    try:
        # Basic masking: replace password between ://user:pass@ with ******
        if '://' in url and '@' in url and ':' in url.split('://', 1)[1]:
            scheme, rest = url.split('://', 1)
            creds, tail = rest.split('@', 1)
            if ':' in creds:
                user, _ = creds.split(':', 1)
                return f"{scheme}://{user}:******@{tail}"
    except Exception:
        pass
    return url


def run_migrations_on_startup():
    if os.getenv("BIOMERO_RUN_MIGRATIONS", "1") != "1":
        return

    # Prefer the DB URL from EngineManager (engine already created).
    db_url = None
    try:
        from .database import EngineManager
        if getattr(EngineManager, "_engine", None):
            db_url = str(EngineManager._engine.url)
    except Exception:
        pass
    # Fallback to env vars if tracker not available
    if not db_url:
        db_url = os.getenv("SQLALCHEMY_URL")
    if not db_url:
        raise RuntimeError(
            "BIOMERO migrations: No DB URL found. Ensure EngineManager is "
            "initialized or set SQLALCHEMY_URL."
        )

    logger = logging.getLogger(__name__)
    logger.info(f"BIOMERO Alembic DB: {_mask_url(db_url)}")
    
    # Try to reuse existing engine if available to avoid connection issues
    engine = None
    try:
        from .database import EngineManager
        if getattr(EngineManager, "_engine", None):
            engine = EngineManager._engine
    except Exception:
        pass
    
    if engine is None:
        engine = create_engine(db_url)

    # Check if there are any migration files first
    versions_dir = pathlib.Path(MIGRATIONS_DIR) / "versions"
    migration_files = []
    if versions_dir.exists():
        migration_files = [f for f in versions_dir.glob("*.py") 
                          if not f.name.startswith("__")]
    
    if not migration_files:
        logger.info("No migration files found. Auto-stamping existing schema as head.")
        # Just stamp the current schema as head if tables exist
        insp = inspect(engine)
        known_tables = {
            "biomero_job_view",
            "biomero_job_progress_view", 
            "biomero_workflow_progress_view",
            "biomero_task_execution"
        }
        if any(insp.has_table(t) for t in known_tables):
            cfg = Config()
            cfg.set_main_option("script_location", MIGRATIONS_DIR)
            # Don't set sqlalchemy.url in config - let env.py use environment variable
            cfg.set_main_option("version_table", VERSION_TABLE)
            
            # Just stamp to head - no actual migration needed
            command.stamp(cfg, "head")
            logger.info("Stamped database to head revision (no migration files exist)")
        else:
            logger.info("No BIOMERO tables found, skipping stamp")
        return

    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    # Don't set sqlalchemy.url in config - let env.py use environment variable  
    cfg.set_main_option("version_table", VERSION_TABLE)

    insp = inspect(engine)
    has_version_table = insp.has_table(VERSION_TABLE)
    # Allow auto-stamp if explicitly enabled OR if tables were just created
    # in this process (fresh install detected by Base.metadata.after_create).
    allow_stamp = os.getenv("BIOMERO_ALLOW_AUTO_STAMP", "0") == "1"
    try:
        from .database import CREATED_ANY_TABLES
        allow_stamp = allow_stamp or CREATED_ANY_TABLES
    except Exception:
        pass

    # Postgres advisory lock to prevent concurrent migrations
    # from multiple replicas
    is_pg = engine.dialect.name == "postgresql"

    with engine.begin() as conn:
        if is_pg:
            conn.execute(
                text(
                    "SELECT pg_advisory_lock("
                    "hashtext('biomero_migrations'))"
                )
            )
        try:
            if allow_stamp and not has_version_table:
                # Check if any BIOMERO table already exists (reliable table name)
                known_tables = {
                    "biomero_job_view",
                    "biomero_job_progress_view", 
                    "biomero_workflow_progress_view",
                    "biomero_task_execution"
                }
                if any(insp.has_table(t) for t in known_tables):
                    command.stamp(cfg, "head")  # baseline existing DB
            command.upgrade(cfg, "head")
        finally:
            if is_pg:
                conn.execute(
                    text(
                        "SELECT pg_advisory_unlock("
                        "hashtext('biomero_migrations'))"
                    )
                )
