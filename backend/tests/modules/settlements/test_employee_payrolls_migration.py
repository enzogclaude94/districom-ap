"""Migration 0028 (R8-4) on data: R8-3 survives the round trip down to 0027 and back, the internal account's discipline
goes back to exactly what 0027 allowed, and a database with payrolls refuses the downgrade (Enzo's R8-4 point 21)."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_loan_support import funded, given
from tests.modules.settlements.employee_movement_support import area_admin, internal_account
from tests.modules.settlements.employee_payroll_support import line, opened

__all__ = ["funded"]

OBJECTS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('check_employee_payroll', 'check_employee_payroll_draft', "
    "'check_employee_payroll_closing', 'check_employee_payroll_closing_row', 'verify_employee_payroll', "
    "'check_employee_payroll_ledger', 'check_not_applied')"
)


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def test_r8_3_goes_down_to_0027_and_up_unchanged(
    user_as: Callable[..., TestClient],
    employee: Callable[[str], int],
    funded: dict[str, int],
    db: Session,
    back_at_head: None,
) -> None:
    ana = employee("Ana Pérez")
    given(area_admin(user_as), ana, funded["main_cash"], "3000", 3, on=today(), first_due_on=today())
    account = internal_account(db, ana)
    db.commit()
    db.close()
    cfg = alembic_config()
    assert db.execute(text(OBJECTS)).scalar_one() == 7
    db.close()
    command.downgrade(cfg, "0027")
    assert db.execute(text(OBJECTS)).scalar_one() == 0
    tables = db.execute(
        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'settlements' AND tablename LIKE 'employee_payroll%'")
    ).scalar_one()
    assert tables == 0
    assert (
        db.execute(text("SELECT count(*) FROM settlements.accounts WHERE code = 'payroll_expense'")).scalar_one() == 0
    )
    body = db.execute(text("SELECT prosrc FROM pg_proc WHERE proname = 'check_employee_leg'")).scalar_one()
    assert "PAYROLL_CLOSE" not in body
    assert "EMPLOYEE_LOAN" in body
    db.close()
    command.upgrade(cfg, "head")
    assert db.execute(text(OBJECTS)).scalar_one() == 7
    balance = db.execute(
        text("SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a"), {"a": account}
    ).scalar_one()
    assert Decimal(balance) == Decimal("3000")


def test_a_database_with_payrolls_refuses_the_downgrade(
    user_as: Callable[..., TestClient], employee: Callable[[str], int], db: Session, back_at_head: Any
) -> None:
    admin = area_admin(user_as)
    p = opened(admin, employee("Ana"), days_ago(30), today(), kind="EXTRAORDINARY")
    line(admin, p["id"], "BONUS", "1000")
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match="downgrade of 0028 refused"):
        command.downgrade(alembic_config(), "0027")
    db.close()
    assert db.execute(text("SELECT count(*) FROM settlements.employee_payrolls")).scalar_one() == 1
