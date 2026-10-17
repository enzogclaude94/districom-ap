"""Migration 0026 (R8-2) on data: internal accounts moved by R2-R7 survive the round trip down to 0025 and back; an
internal account moved by any other operation type stops the upgrade instead of being accepted silently."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.test_settlement_close_api import close, count, create, employee

OBJECTS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('check_employee_technical_account', 'check_employee_leg', "
    "'check_employee_movement', 'verify_employee_movement', 'check_employee_movement_operation')"
)


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def _internal_balance(db: Session, who: int) -> Decimal:
    return Decimal(
        db.execute(
            text(
                "SELECT coalesce(sum(e.amount), 0) FROM settlements.ledger_entries e "
                "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.employee_id = :e"
            ),
            {"e": who},
        ).scalar_one()
    )


def test_internal_accounts_of_r2_go_down_to_0025_and_up_unchanged(
    as_role: Any, ids: dict[str, int], driver: Any, db: Session, back_at_head: None
) -> None:
    pedro = driver("Pedro Gómez")
    operator = as_role("operator")
    sid = create(operator, pedro, "100000")
    count(operator, sid, ("ARS_20000", 4, ids["main_cash"]))
    assert close(operator, sid, employee(pedro, "20000")).status_code == 201
    db.commit()
    db.close()
    cfg = alembic_config()
    command.downgrade(cfg, "0025")
    assert db.execute(text(OBJECTS)).scalar_one() == 0
    assert (
        db.execute(
            text(
                "SELECT count(*) FROM settlements.accounts "
                "WHERE code IN ('employee_consumptions', 'employee_adjustments')"
            )
        ).scalar_one()
        == 0
    )
    assert _internal_balance(db, pedro) == Decimal("20000")
    db.close()
    command.upgrade(cfg, "head")
    assert db.execute(text(OBJECTS)).scalar_one() == 5
    assert _internal_balance(db, pedro) == Decimal("20000")


def test_an_internal_account_moved_by_another_type_stops_the_upgrade(
    as_role: Any, ids: dict[str, int], driver: Any, db: Session, back_at_head: None
) -> None:
    pedro = driver("Pedro Gómez")
    operator = as_role("operator")
    sid = create(operator, pedro, "100000")
    count(operator, sid, ("ARS_20000", 4, ids["main_cash"]))
    assert close(operator, sid, employee(pedro, "20000")).status_code == 201
    db.commit()
    db.close()
    cfg = alembic_config()
    command.downgrade(cfg, "0025")
    # Before 0026 nothing in the database stopped it: an ADJUSTMENT written by hand on the internal account.
    account = db.execute(text("SELECT id FROM settlements.accounts WHERE employee_id = :e"), {"e": pedro}).scalar_one()
    operation = db.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
            "request_fingerprint, created_by) VALUES ('ADJUSTMENT', current_date, 'A mano', :k, :f, "
            "(SELECT id FROM users ORDER BY id LIMIT 1)) RETURNING id"
        ),
        {"k": uuid4(), "f": "d" * 64},
    ).scalar_one()
    db.execute(
        text(
            "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES "
            "(:o, :a, 10), (:o, (SELECT id FROM settlements.accounts WHERE code = 'adjustment'), -10)"
        ),
        {"o": operation, "a": account},
    )
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match="an internal account was moved by another operation type"):
        command.upgrade(cfg, "head")
    # Leave the database at head for the next tests: the stray operation goes (tests only; the ledger is append-only).
    db.execute(text("ALTER TABLE settlements.ledger_entries DISABLE TRIGGER USER"))
    db.execute(text("ALTER TABLE settlements.financial_operations DISABLE TRIGGER USER"))
    db.execute(text("DELETE FROM settlements.ledger_entries WHERE operation_id = :o"), {"o": operation})
    db.execute(text("DELETE FROM settlements.financial_operations WHERE id = :o"), {"o": operation})
    db.execute(text("ALTER TABLE settlements.ledger_entries ENABLE TRIGGER USER"))
    db.execute(text("ALTER TABLE settlements.financial_operations ENABLE TRIGGER USER"))
    db.commit()
