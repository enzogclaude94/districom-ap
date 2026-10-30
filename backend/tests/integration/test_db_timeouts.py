"""R9-5-2: the backend's PostgreSQL limits (statement 30 s, lock 10 s, idle in transaction 60 s) are set on its own
connections only, a statement or a lock over them is a standard 503 and never an opaque 500, and the connection is
reusable afterwards."""

import time
from collections.abc import Callable, Iterator
from typing import Annotated, Any

import pytest
from alembic.config import Config
from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, engine_from_config, pool, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import database_timeout
from app.db.session import get_db, get_engine
from tests.conftest import BACKEND_DIR

LIMITS = {"statement_timeout": "30s", "lock_timeout": "10s", "idle_in_transaction_session_timeout": "1min"}
DEFAULTS = {"statement_timeout": "0", "lock_timeout": "0", "idle_in_transaction_session_timeout": "0"}


def _limits(conn: Any) -> dict[str, str]:
    return {name: conn.execute(text(f"SHOW {name}")).scalar_one() for name in LIMITS}


@pytest.fixture
def route(client: TestClient) -> Iterator[Callable[[str, Callable[..., Any]], None]]:
    """Mounts a test-only endpoint on the app for the test, with the app's own database session."""
    from app.main import app

    added = 0

    def _add(path: str, endpoint: Callable[..., Any]) -> None:
        nonlocal added
        app.add_api_route(path, endpoint)
        added += 1

    yield _add
    for _ in range(added):
        app.router.routes.pop()


def test_a_backend_connection_starts_with_the_three_limits(migrated_db: None) -> None:
    with get_engine().connect() as conn:
        assert _limits(conn) == LIMITS


def test_every_new_connection_of_the_pool_has_them_too(migrated_db: None) -> None:
    engine = get_engine()
    engine.dispose()
    with engine.connect() as first, engine.connect() as second:
        pids = {c.execute(text("SELECT pg_backend_pid()")).scalar_one() for c in (first, second)}
        assert len(pids) == 2
        assert _limits(first) == LIMITS
        assert _limits(second) == LIMITS


def test_they_come_from_the_settings() -> None:
    settings = get_settings()
    assert (settings.db_statement_timeout_ms, settings.db_lock_timeout_ms) == (30_000, 10_000)
    assert settings.db_idle_in_transaction_timeout_ms == 60_000


def test_connections_outside_the_backend_keep_the_server_defaults(migrated_db: None) -> None:
    """No ALTER ROLE / ALTER DATABASE: a plain connection (pg_dump, psql, restore) and the migrations' own engine
    (alembic `env.py`) do not get the backend's limits."""
    plain = create_engine(get_settings().database_url, poolclass=pool.NullPool)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", get_settings().database_url)
    migrations = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    for engine in (plain, migrations):
        with engine.connect() as conn:
            assert _limits(conn) == DEFAULTS
            assert conn.execute(text("SELECT count(*) FROM pg_db_role_setting")).scalar_one() == 0


def test_a_statement_over_the_timeout_is_a_standard_503_and_the_connection_is_reused(
    client: TestClient, route: Callable[..., None]
) -> None:
    def slow(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
        db.execute(text("SET LOCAL statement_timeout = '50ms'"))  # this transaction only; the limit itself is above
        db.execute(text("SELECT pg_sleep(2)"))
        return {}

    route("/api/v1/_test/slow", slow)
    started = time.monotonic()
    response = client.get("/api/v1/_test/slow", headers={"X-Request-ID": "timeout-test-01"})
    assert time.monotonic() - started < 1.5
    assert response.status_code == 503
    assert response.json() == {
        "code": "database_timeout",
        "message": "La operación tardó demasiado y se canceló. Probá de nuevo en unos minutos.",
        "details": None,
        "request_id": "timeout-test-01",
    }
    assert response.headers["Cache-Control"] == "no-store"
    _the_pool_is_healthy(client)


def test_a_lock_over_the_timeout_is_a_standard_503(client: TestClient, route: Callable[..., None], db: Session) -> None:
    db.execute(text("INSERT INTO app_settings (key, value) VALUES ('platform.test_lock', '1'::jsonb)"))
    db.commit()
    holder = create_engine(get_settings().database_url, poolclass=pool.NullPool)

    def locked(db: Annotated[Session, Depends(get_db)]) -> dict[str, str]:
        db.execute(text("SET LOCAL lock_timeout = '100ms'"))
        db.execute(text("SELECT 1 FROM app_settings WHERE key = 'platform.test_lock' FOR UPDATE"))
        return {}

    route("/api/v1/_test/locked", locked)
    with holder.begin() as other:
        other.execute(text("SELECT 1 FROM app_settings WHERE key = 'platform.test_lock' FOR UPDATE"))
        response = client.get("/api/v1/_test/locked")
    assert response.status_code == 503
    assert response.json()["code"] == "database_busy"
    _the_pool_is_healthy(client)


def test_a_transaction_left_idle_is_closed_by_the_server_and_the_pool_recovers(migrated_db: None) -> None:
    sessions = get_db()
    session = next(sessions)
    session.execute(text("SET LOCAL idle_in_transaction_session_timeout = '100ms'"))
    time.sleep(0.5)
    with pytest.raises(DBAPIError) as closed:
        session.execute(text("SELECT 1"))
    timeout = database_timeout(closed.value)  # answered as a standard 503, like the other two limits
    assert timeout is not None
    assert (timeout.status_code, timeout.code) == (503, "database_timeout")
    sessions.close()
    with get_engine().connect() as conn:  # a broken connection is never handed out again
        assert conn.execute(text("SELECT 1")).scalar_one() == 1
        assert _limits(conn) == LIMITS


def _the_pool_is_healthy(client: TestClient) -> None:
    """After the rollback the pooled connections work and kept the backend's limits (SET LOCAL did not leak)."""
    for _ in range(3):
        assert client.get("/api/v1/health").status_code == 200
    engine = get_engine()
    assert engine.pool.checkedout() == 0  # type: ignore[attr-defined]
    for _ in range(engine.pool.size()):  # type: ignore[attr-defined]
        with engine.connect() as conn:
            assert _limits(conn) == LIMITS
