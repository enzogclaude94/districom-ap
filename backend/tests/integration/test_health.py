import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text

from app.api.v1.health import code_heads
from app.db.session import get_engine

VERSIONS = Path(__file__).resolve().parents[2] / "migrations" / "versions"


def test_health_ok(client: TestClient) -> None:
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "db": "ok"}
    assert response.headers["X-Request-ID"]


def test_health_reports_db_down(client: TestClient) -> None:
    from app.main import app

    dead = create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    app.dependency_overrides[get_engine] = lambda: dead
    try:
        response = client.get("/api/v1/health")
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "db": "error"}


# --- R9-5-4 (O6): live and ready -----------------------------------------------------------------------------------


@pytest.fixture
def dead_database(client: TestClient) -> Iterator[None]:
    """PostgreSQL unreachable for the app: its engine points at a closed port, and any other connection fails."""
    from app.main import app

    dead = create_engine("postgresql+psycopg://nobody:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1})
    app.dependency_overrides[get_engine] = lambda: dead
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Engine, "connect", _refuse)
        yield
    app.dependency_overrides.clear()


def _refuse(*_: object, **__: object) -> None:
    raise OSError("database down")


@pytest.fixture
def db_revision(client: TestClient) -> Iterator[Engine]:
    """Lets a test change `alembic_version`; the head is put back afterwards, whatever the test did or asserted."""
    engine = get_engine()
    (head,) = code_heads()
    yield engine
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version"))
        conn.execute(text("INSERT INTO alembic_version (version_num) VALUES (:head)"), {"head": head})


def _set_revision(engine: Engine, revision: str) -> None:
    with engine.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = :revision"), {"revision": revision})


def test_live_is_200(client: TestClient) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["Cache-Control"] == "no-store"


def test_live_is_200_with_the_database_down(client: TestClient, dead_database: None) -> None:
    response = client.get("/api/v1/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.get("/api/v1/health").status_code == 503  # the database is really down for the app


def test_ready_is_200_with_the_database_at_the_code_head(client: TestClient) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "db": "ok", "migrations": "ok"}
    assert response.headers["Cache-Control"] == "no-store"


def test_ready_is_503_with_the_database_down(client: TestClient, dead_database: None) -> None:
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "db": "error", "migrations": "unknown"}
    assert response.headers["Cache-Control"] == "no-store"


def test_ready_is_503_with_a_migration_not_applied(client: TestClient, db_revision: Engine) -> None:
    """The database one migration behind the code (code updated, `migrate` not run)."""
    (head,) = code_heads()
    previous = _down_revision(head)
    _set_revision(db_revision, previous)
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "db": "ok", "migrations": "behind"}
    assert response.headers["Cache-Control"] == "no-store"


def test_ready_is_503_with_a_migration_this_code_does_not_know(client: TestClient, db_revision: Engine) -> None:
    """The database ahead of the code (a newer version's migration, code rolled back without the database)."""
    _set_revision(db_revision, "0999")
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "db": "ok", "migrations": "unknown"}


def test_ready_is_503_without_a_migration_version(client: TestClient, db_revision: Engine) -> None:
    with db_revision.begin() as conn:
        conn.execute(text("DELETE FROM alembic_version"))
    response = client.get("/api/v1/health/ready")
    assert response.status_code == 503
    assert response.json()["migrations"] == "unknown"


def test_the_code_head_comes_from_the_migration_files() -> None:
    """Not a hardcoded revision: the only migration no other one revises, read from the files here."""
    revisions: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        revision = re.search(r'^revision(?:: str)? = "([^"]+)"', source, re.MULTILINE)
        down = re.search(r'^down_revision(?:: [^=]+)? = (?:"([^"]+)"|None)', source, re.MULTILINE)
        assert revision is not None, path
        assert down is not None, path
        revisions[revision.group(1)] = down.group(1)
    heads = set(revisions) - {down for down in revisions.values() if down}
    assert code_heads() == frozenset(heads)
    assert len(heads) == 1


def _down_revision(revision: str) -> str:
    from app.api.v1.health import migration_script

    script = migration_script().get_revision(revision)
    assert script is not None
    assert isinstance(script.down_revision, str)
    return script.down_revision
