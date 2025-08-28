import os
import pathlib
import logging
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

MIGRATIONS_DIR = str(pathlib.Path(__file__).with_name("migrations"))
VERSION_TABLE = "alembic_version_biomero"


def _mask_url(url: str) -> str:
    """Mask password in a SQLAlchemy URL for safe logging."""
    try:
        if not url or '://' not in url:
            return url
        scheme, rest = url.split('://', 1)
        if '@' not in rest:
            return url
        creds, tail = rest.split('@', 1)
        if ':' not in creds:
            return url
        user, _ = creds.split(':', 1)
        return f"{scheme}://{user}:******@{tail}"
    except Exception:
        return url


def run_migrations_on_startup():
    """
    Programmatic migration runner for BIOMERO.

    Controlled by env:
      - BIOMERO_RUN_MIGRATIONS=1 to enable (default 1)
      - SQLALCHEMY_URL for DB connection string
      - BIOMERO_ALLOW_AUTO_STAMP=1 to adopt existing schema by stamping head
    """
    if os.getenv("BIOMERO_RUN_MIGRATIONS", "1") != "1":
        return

    logger = logging.getLogger(__name__)

    # Prefer the DB URL from EngineManager (engine already created).
    db_url = None
    try:
        from .database import EngineManager
        if getattr(EngineManager, "_engine", None):
            # Stringify without exposing password
            db_url = str(EngineManager._engine.url)
    except Exception:
        pass

    # Fallback to env var if needed
    if not db_url:
        db_url = os.getenv("SQLALCHEMY_URL")
    if not db_url:
        raise RuntimeError(
            "BIOMERO migrations: No DB URL. Ensure EngineManager is "
            "initialized or set SQLALCHEMY_URL."
        )

    logger.info(f"BIOMERO Alembic DB: {_mask_url(db_url)}")

    engine = create_engine(db_url)

    cfg = Config()
    cfg.set_main_option("script_location", MIGRATIONS_DIR)
    cfg.set_main_option("sqlalchemy.url", db_url)
    cfg.set_main_option("version_table", VERSION_TABLE)

    insp = inspect(engine)
    has_version_table = insp.has_table(VERSION_TABLE)
    allow_stamp = os.getenv("BIOMERO_ALLOW_AUTO_STAMP", "0") == "1"
    created_any_tables = os.getenv("BIOMERO_CREATED_ANY_TABLES", "0") == "1"

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
            # Auto-stamp scenarios:
            # 1) Fresh install: our process just created tables ->
            #    stamp unconditionally.
            # 2) Adoption: admin allowed stamping and existing known tables
            #    are present.
            if not has_version_table:
                if created_any_tables:
                    command.stamp(cfg, "head")
                elif allow_stamp:
                    known_tables = {
                        "biomero_job_view",
                        "biomero_job_progress_view",
                        "biomero_workflow_progress_view",
                        "biomero_task_execution",
                    }
                    if any(insp.has_table(t) for t in known_tables):
                        command.stamp(cfg, "head")
            command.upgrade(cfg, "head")
        finally:
            if is_pg:
                conn.execute(
                    text(
                        "SELECT pg_advisory_unlock("
                        "hashtext('biomero_migrations'))"
                    )
                )
