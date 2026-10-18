"""Migration 0027 (R8-3) on data: the movements of R8-2 survive the round trip down to 0026 and back, and the internal
account's discipline goes back to exactly what 0026 allowed (a loan is no longer accepted there)."""

from collections.abc import Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.employee_movement_support import area_admin, internal_account, registered

OBJECTS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('check_employee_loan', 'check_employee_loan_installment', "
    "'verify_employee_loan', 'check_employee_loan_operation')"
)


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def test_r8_2_goes_down_to_0026_and_up_unchanged(
    user_as: Any, employee: Any, accounts: dict[str, int], db: Session, back_at_head: None
) -> None:
    ana = employee("Ana Pérez")
    client: TestClient = area_admin(user_as)
    registered(client, "CONSUMPTION", ana, "1500")
    account = internal_account(db, ana)
    db.commit()
    db.close()
    cfg = alembic_config()
    command.downgrade(cfg, "0026")
    assert db.execute(text(OBJECTS)).scalar_one() == 0
    tables = db.execute(
        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'settlements' AND tablename LIKE 'employee_loan%'")
    ).scalar_one()
    assert tables == 0
    # 0026's discipline again: an EMPLOYEE_LOAN does not even exist as a type.
    with pytest.raises(DBAPIError, match="ck_financial_operations_type"):
        db.execute(
            text(
                "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
                "request_fingerprint, created_by) VALUES ('EMPLOYEE_LOAN', current_date, 'x', :k, :f, "
                "(SELECT id FROM users ORDER BY id LIMIT 1))"
            ),
            {"k": uuid4(), "f": "d" * 64},
        )
    db.rollback()
    body = db.execute(text("SELECT prosrc FROM pg_proc WHERE proname = 'check_employee_leg'")).scalar_one()
    assert "EMPLOYEE_LOAN" not in body
    assert "EMPLOYEE_ADJUSTMENT" in body
    db.close()
    command.upgrade(cfg, "head")
    assert db.execute(text(OBJECTS)).scalar_one() == 4
    assert (
        "EMPLOYEE_LOAN"
        in db.execute(text("SELECT prosrc FROM pg_proc WHERE proname = 'check_employee_leg'")).scalar_one()
    )
    balance = db.execute(
        text("SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a"), {"a": account}
    ).scalar_one()
    assert Decimal(balance) == Decimal("1500")
