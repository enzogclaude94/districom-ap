"""Test configuration. Integration tests run against a real PostgreSQL (never SQLite).

Set TEST_DATABASE_URL to point at a disposable database; it is migrated from scratch per session.
"""

import os

os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://districom:districom@localhost:5432/districom_test"
)
os.environ["SECRET_KEY"] = "test-secret-key-0123456789abcdef0123456789"
os.environ["APP_ENV"] = "test"
os.environ["COOKIE_SECURE"] = "false"
os.environ["CORS_ORIGINS"] = ""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Tables emptied after each test that uses the database.
TRUNCATED_TABLES = (
    "users",
    "user_sessions",
    "audit_events",
    "app_settings",
    "employees",
    "employee_functions",
    "user_module_roles",
)


def alembic_config() -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["configure_logger"] = False
    cfg.set_main_option("sqlalchemy.url", os.environ["DATABASE_URL"])
    return cfg


@pytest.fixture(scope="session")
def migrated_db() -> None:
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


@pytest.fixture
def db(migrated_db: None) -> Iterator[Session]:
    from app.db.session import get_db

    session = next(get_db())
    yield session
    session.close()
    with session.get_bind().begin() as conn:  # type: ignore[union-attr]
        conn.execute(text(f"TRUNCATE {', '.join(TRUNCATED_TABLES)} RESTART IDENTITY CASCADE"))


@pytest.fixture
def client(db: Session) -> Iterator[TestClient]:
    from app.main import app
    from app.platform.auth.router import login_limiter

    login_limiter._failures.clear()
    with TestClient(app, headers={"X-Requested-With": "test"}) as test_client:
        yield test_client


PASSWORD = "correct-horse-battery"


@pytest.fixture
def make_user(db: Session):  # type: ignore[no-untyped-def]
    from app.platform.auth.service import create_user

    def _make(email: str = "admin@districom.test", role: str = "admin", password: str = PASSWORD):  # type: ignore[no-untyped-def]
        return create_user(db, email, "Usuario Test", password, role)

    return _make


@pytest.fixture
def grant(db: Session):  # type: ignore[no-untyped-def]
    """Give `<role>@districom.test` a role in a module (per-module access, stage 1)."""
    from app.platform.auth.module_roles import grant_module_role

    def _grant(user_role: str, module: str, module_role: str) -> None:
        grant_module_role(db, f"{user_role}@districom.test", module, module_role)

    return _grant


@pytest.fixture
def login(client: TestClient, make_user):  # type: ignore[no-untyped-def]
    def _login(role: str = "admin") -> TestClient:
        email = f"{role}@districom.test"
        make_user(email=email, role=role)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _login
