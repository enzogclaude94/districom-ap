"""The cleanup between tests (tests/conftest.py, `reset_database`) is exact: one TRUNCATE without CASCADE of an
explicit list. These tests keep that list complete and prove it leaves the database as the migrations did."""

import json

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from tests import conftest

# Not emptied between tests: the migration history and the source systems 0001 seeds (never written by a test).
KEPT_TABLES = {"public.alembic_version", "public.source_systems"}


def _qualified(table: str) -> str:
    return table if "." in table else f"public.{table}"


def _tables(conn: Connection) -> set[str]:
    return set(
        conn.execute(
            text(
                "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE c.relkind = 'r' AND n.nspname IN ('public', 'settlements')"
            )
        ).scalars()
    )


def _rows(conn: Connection, table: str) -> int:
    return int(conn.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def test_every_table_is_emptied_or_kept_on_purpose(db: Session) -> None:
    """A new table has to be listed: missing, a test would leak its rows into the next one (or the TRUNCATE would
    fail, when it references a listed table)."""
    listed = {_qualified(t) for t in conftest.TRUNCATED_TABLES}
    assert len(listed) == len(conftest.TRUNCATED_TABLES)
    assert _tables(db.connection()) == listed | KEPT_TABLES


def test_reset_leaves_the_database_as_the_migrations_did(db: Session, make_user) -> None:  # type: ignore[no-untyped-def]
    """Writes into platform and Rendiciones tables, resets, and finds only the seeds, with identities restarted."""
    user = make_user()
    db.execute(text("INSERT INTO employees (full_name) VALUES ('Pedro')"))
    db.execute(
        text(
            "INSERT INTO financial_institutions (bcra_code, name, source, created_by, manual_reason) "
            "VALUES (99999, 'Banco X', 'MANUAL', :u, 'test')"
        ),
        {"u": user.id},
    )
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE code = 'change_cash'"))
    db.commit()
    seeded_accounts = json.loads(conftest.SEEDED_ACCOUNT_ROWS)

    with db.get_bind().begin() as conn:  # type: ignore[union-attr]
        conftest.reset_database(conn)
        seeded = {"settlements.accounts", "financial_institutions", "settlements.expense_categories"}
        leftovers = {t: n for t in conftest.TRUNCATED_TABLES if t not in seeded and (n := _rows(conn, t))}
        assert leftovers == {}
        accounts = json.loads(
            conn.execute(
                text("SELECT coalesce(jsonb_agg(to_jsonb(a) ORDER BY a.id), '[]')::text FROM settlements.accounts a")
            ).scalar_one()
        )
        assert accounts == seeded_accounts
        banks = conn.execute(text("SELECT count(*) FROM financial_institutions WHERE source = 'MANUAL'")).scalar_one()
        assert banks == 0
        assert _rows(conn, "financial_institutions") == len(json.loads(conftest.SEEDED_BANKS))
        categories = conn.execute(text("SELECT code FROM settlements.expense_categories")).scalars().all()
        assert categories == ["FUEL"]
        new_user = conn.execute(
            text(
                "INSERT INTO users (email, full_name, password_hash, role) "
                "VALUES ('x@districom.test', 'X', 'x', 'viewer') RETURNING id"
            )
        ).scalar_one()
        assert new_user == 1
        conftest.reset_database(conn)
