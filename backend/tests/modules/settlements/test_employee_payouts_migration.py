"""Migration 0029 (R8-5) on data: R8-4 survives the round trip down to 0028 and back, the internal account's discipline
goes back to exactly what 0028 allowed, and a database with payouts refuses the downgrade (Enzo's R8-5 point 23)."""

from collections.abc import Callable, Iterator
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.employee_loan_support import funded
from tests.modules.settlements.employee_movement_support import internal_account
from tests.modules.settlements.employee_payout_support import area_admin, net_payroll, paid

__all__ = ["funded"]

OBJECTS = (
    "SELECT count(*) FROM pg_proc WHERE proname IN ('check_employee_payout', 'check_employee_payout_item', "
    "'check_payroll_not_paid', 'verify_employee_payout', 'check_employee_payout_operation')"
)


@pytest.fixture
def back_at_head(db: Session) -> Iterator[None]:
    yield
    db.rollback()
    db.close()
    command.upgrade(alembic_config(), "head")


def test_r8_4_goes_down_to_0028_and_up_unchanged(
    user_as: Callable[..., TestClient], employee: Callable[[str], int], db: Session, back_at_head: None
) -> None:
    ana = employee("Ana Pérez")
    net_payroll(area_admin(user_as), ana, "3000")
    account = internal_account(db, ana)
    db.commit()
    db.close()
    cfg = alembic_config()
    assert db.execute(text(OBJECTS)).scalar_one() == 5
    db.close()
    command.downgrade(cfg, "0028")
    assert db.execute(text(OBJECTS)).scalar_one() == 0
    tables = db.execute(
        text("SELECT count(*) FROM pg_tables WHERE schemaname = 'settlements' AND tablename LIKE 'employee_payout%'")
    ).scalar_one()
    assert tables == 0
    body = db.execute(text("SELECT prosrc FROM pg_proc WHERE proname = 'check_employee_leg'")).scalar_one()
    assert "EMPLOYEE_PAYOUT" not in body
    assert "PAYROLL_CLOSE" in body
    db.close()
    command.upgrade(cfg, "head")
    assert db.execute(text(OBJECTS)).scalar_one() == 5
    balance = db.execute(
        text("SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a"), {"a": account}
    ).scalar_one()
    assert Decimal(balance) == Decimal("-3000")


def test_a_database_with_payouts_refuses_the_downgrade(
    user_as: Callable[..., TestClient],
    employee: Callable[[str], int],
    funded: dict[str, int],
    db: Session,
    back_at_head: None,
) -> None:
    admin = area_admin(user_as)
    paid(admin, net_payroll(admin, employee("Ana"), "1000"), ("CASH", funded["main_cash"], "1000"))
    db.commit()
    db.close()
    with pytest.raises(DBAPIError, match="downgrade of 0029 refused"):
        command.downgrade(alembic_config(), "0028")
    db.close()
    assert db.execute(text("SELECT count(*) FROM settlements.employee_payouts")).scalar_one() == 1
