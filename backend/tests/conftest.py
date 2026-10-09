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
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parents[1]

# Tables emptied after each test that uses the database.
TRUNCATED_TABLES = (
    "vehicles",
    "users",
    "user_sessions",
    "audit_events",
    "app_settings",
    "user_module_roles",
    "settlements.cheque_credit_items",
    "settlements.cheque_credits",
    "settlements.cheque_deposit_items",
    "settlements.cheque_deposits",
    "settlements.cheque_rejections",
    "settlements.cheque_returns",
    "settlements.cheque_events",
    "settlements.settlement_closing_cheques",
    "settlements.cheques",
    "settlements.pending_collection_events",
    "settlements.pending_resolution_allocations",
    "settlements.pending_collection_resolutions",
    "settlements.pending_collection_receipts",
    "settlements.settlement_closing_pendings",
    "settlements.pending_collections",
    "settlements.difference_allocations",
    "settlements.settlement_differences",
    "settlements.settlement_closings",
    "settlements.settlement_reopenings",
    "settlements.ledger_entries",
    "settlements.financial_operations",
    "settlements.transfer_status_events",
    "settlements.settlement_transfers",
    "settlements.cash_additions",
    "settlements.cash_count_lines",
    "settlements.fuel_loads",
    "settlements.expenses",
    "settlements.expense_categories",
    "settlements.settlements",
)
# Rendiciones accounts seeded by migrations 0005 and 0008-0010; tests may add others, removed afterwards.
# The official bank catalog as migration 0011 seeds it (JSON), restored after each test.
SEEDED_BANKS = "[]"
SEEDED_ACCOUNTS = (
    "main_cash",
    "change_cash",
    "bank",
    "opening_balance",
    "adjustment",
    "delivery_collections",
    "expense",
    "settlement_difference",
    "pending_collections",
    "uncollectible",
    "cheque_portfolio",
    "cheques_in_transit",
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
    global SEEDED_BANKS
    engine = create_engine(os.environ["DATABASE_URL"])
    with engine.connect() as conn:
        SEEDED_BANKS = conn.execute(
            text(
                "SELECT coalesce(jsonb_agg(jsonb_build_object('id', id, 'bcra_code', bcra_code, 'name', name, "
                "'source', source)), '[]')::text FROM financial_institutions WHERE source = 'BCRA'"
            )
        ).scalar_one()
    engine.dispose()


@pytest.fixture
def db(migrated_db: None) -> Iterator[Session]:
    from app.db.session import get_db

    session = next(get_db())
    yield session
    session.close()
    with session.get_bind().begin() as conn:  # type: ignore[union-attr]
        conn.execute(text(f"TRUNCATE {', '.join(TRUNCATED_TABLES)} RESTART IDENTITY CASCADE"))
        # TRUNCATE users CASCADE empties the bank catalog too (a manual entry names its author): put back
        # the official entries the migration seeded, with their ids.
        conn.execute(text("TRUNCATE financial_institutions CASCADE"))
        conn.execute(
            text(
                "INSERT INTO financial_institutions (id, bcra_code, name, source, active) OVERRIDING SYSTEM VALUE "
                "SELECT id, bcra_code, name, source, true FROM jsonb_to_recordset(CAST(:rows AS jsonb)) "
                "AS r(id bigint, bcra_code int, name text, source text)"
            ),
            {"rows": SEEDED_BANKS},
        )
        conn.execute(
            text(
                "SELECT setval(pg_get_serial_sequence('financial_institutions', 'id'), "
                "(SELECT coalesce(max(id), 0) + 1 FROM financial_institutions), false)"
            )
        )
        # The FUEL category migration 0015 seeds (a fresh database has no «Combustible» to reuse).
        conn.execute(
            text(
                "INSERT INTO settlements.expense_categories (name, name_key, active, code) "
                "VALUES ('Combustible', 'combustible', true, 'FUEL')"
            )
        )
        conn.execute(
            text("DELETE FROM settlements.accounts WHERE code IS NULL OR code <> ALL(:codes)"),
            {"codes": list(SEEDED_ACCOUNTS)},
        )
        # A test may deactivate a seeded account (R5-3: an inactive cash box never pays).
        conn.execute(text("UPDATE settlements.accounts SET active = true WHERE NOT active"))
        # Not truncated: an employee's internal account references it, and TRUNCATE ... CASCADE would
        # empty the seeded accounts too.
        for table in ("employee_functions", "employees"):
            conn.execute(text(f"DELETE FROM {table}"))  # noqa: S608
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN id RESTART"))


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
