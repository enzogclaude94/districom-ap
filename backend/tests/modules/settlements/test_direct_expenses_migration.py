"""Migration 0016 on existing data (R5-3): every expense loaded before is a SETTLEMENT expense with exactly its
columns, and keeps working; with direct expenses recorded, the database refuses to go back to 0015."""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.direct_support import BASE, account_id, fund, spent
from tests.modules.settlements.fuel_support import add_fuel, fuel, fuel_category, last_expense, vehicle
from tests.modules.settlements.migration_support import as_before_r9
from tests.modules.settlements.test_settlement_close_api import close, create


def test_existing_expenses_stay_settlement_expenses(as_role, driver, db: Session) -> None:  # type: ignore[no-untyped-def]
    pedro = driver("Pedro Gómez")
    operator = as_role("operator")
    truck = vehicle(db, "AB123CD")
    sid = create(operator, pedro, "10000")
    eid = last_expense(add_fuel(operator, sid, fuel_category(db), fuel(truck, "10", "1000")))["id"]
    assert close(as_role("operator"), sid).status_code == 201
    db.commit()
    db.close()
    as_before_r9()  # no R9 data below 0030
    cfg = alembic_config()
    command.downgrade(cfg, "0015")
    command.upgrade(cfg, "head")
    db.expire_all()
    row = db.execute(
        text(
            "SELECT origin, settlement_id, paid_from_account_id, business_date, operation_id, reversal_operation_id "
            "FROM settlements.expenses WHERE id = :e"
        ),
        {"e": eid},
    ).one()
    assert tuple(row) == ("SETTLEMENT", sid, None, None, None, None)
    listed = as_role("viewer").get(f"{BASE}/expenses", params={"origin": "SETTLEMENT"}).json()["items"]
    assert [(i["id"], i["settlement_id"], i["amount"]) for i in listed] == [(eid, sid, "10000.00")]


def test_going_back_with_direct_expenses_is_refused(as_role, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    fund(admin, "main_cash", "1000")
    tolls = admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]
    spent(admin, account_id(admin, "main_cash"), tolls, "100")
    db.commit()
    db.close()
    as_before_r9()  # no R9 data below 0030
    cfg = alembic_config()
    with pytest.raises(Exception, match="ck_expenses_origin"):
        command.downgrade(cfg, "0015")
    command.upgrade(cfg, "head")
