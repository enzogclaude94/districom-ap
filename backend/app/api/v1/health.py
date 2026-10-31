"""Health endpoints (R9-5-4, O6).

- `/health/live`: the process answers. Touches nothing else, so it stays 200 while PostgreSQL is down.
- `/health/ready`: the app can serve: PostgreSQL answers a query and its migration is exactly the head of the code
  that is running. A database behind (a migration not applied) or ahead/unknown (code rolled back without the
  database) is 503: serving would fail on missing or changed tables.
- `/health`: unchanged (process + database), for the Docker healthchecks and S1's smoke test and monitor.

Every response carries `Cache-Control: no-store` (middleware, R9-5-2).
"""

import logging
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, text

from app.core.logging import db_error_fields
from app.db.session import get_engine

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)

ALEMBIC_INI = Path(__file__).resolve().parents[3] / "alembic.ini"


@router.get("/health")
def health(engine: Annotated[Engine, Depends(get_engine)]) -> JSONResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception:
        db_status = "error"
    status_code = 200 if db_status == "ok" else 503
    return JSONResponse(
        status_code=status_code, content={"status": "ok" if db_status == "ok" else "degraded", "db": db_status}
    )


@router.get("/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


@lru_cache
def migration_script() -> ScriptDirectory:
    """The migrations shipped with this code: read from Alembic's own files, never a hardcoded revision."""
    return ScriptDirectory.from_config(Config(str(ALEMBIC_INI)))


def code_heads() -> frozenset[str]:
    return frozenset(migration_script().get_heads())


def _migration_state(db_revisions: frozenset[str]) -> str:
    """`ok`, `behind` (every revision of the database is an older one of this code) or `unknown` (missing, or a
    revision this code does not know: a newer code's migration)."""
    if db_revisions == code_heads():
        return "ok"
    if not db_revisions:
        return "unknown"
    script = migration_script()
    try:
        known = all(script.get_revision(revision) is not None for revision in db_revisions)
    except CommandError:
        known = False
    return "behind" if known else "unknown"


@router.get("/health/ready")
def ready(engine: Annotated[Engine, Depends(get_engine)]) -> JSONResponse:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            has_table = conn.execute(text("SELECT to_regclass('alembic_version') IS NOT NULL")).scalar_one()
            rows = conn.execute(text("SELECT version_num FROM alembic_version")).scalars().all() if has_table else []
            db_revisions = frozenset(str(row) for row in rows)
    except Exception as exc:
        logger.warning("not_ready", extra={"reason": "database", **db_error_fields(exc)})
        return JSONResponse(status_code=503, content={"status": "not_ready", "db": "error", "migrations": "unknown"})
    migrations = _migration_state(db_revisions)
    if migrations != "ok":
        logger.warning(
            "not_ready",
            extra={
                "reason": f"migrations_{migrations}",
                "db_revision": sorted(db_revisions),
                "code_revision": sorted(code_heads()),
            },
        )
        return JSONResponse(status_code=503, content={"status": "not_ready", "db": "ok", "migrations": migrations})
    return JSONResponse(status_code=200, content={"status": "ready", "db": "ok", "migrations": "ok"})
