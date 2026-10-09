"""The fuel each close publishes (R5-4, migration 0017): written by the close itself, append-only, exactly the
loads of the settlement in effect, and rebuilt by the migration for settlements closed before it."""

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.fuel_support import BASE, add_fuel, fuel, fuel_category, last_expense, vehicle
from tests.modules.settlements.test_settlement_close_api import close, create, reopen

SNAPSHOT = (
    "SELECT close_operation_id, settlement_version, fuel_load_id, expense_id, vehicle_id, employee_id, liters, "
    "price_per_liter, amount FROM settlements.settlement_closing_fuel_loads ORDER BY id"
)


@pytest.fixture
def closed(as_role, driver, db: Session) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """A settlement with a 10 l x $2.000 load and a voided one, closed balanced at $20.000."""
    pedro = driver("Pedro Gómez")
    operator = as_role("operator")
    truck = vehicle(db, "AB123CD")
    category = fuel_category(db)
    sid = create(operator, pedro, "20000")
    kept = last_expense(add_fuel(operator, sid, category, fuel(truck, "10", "2000")))
    gone = last_expense(add_fuel(operator, sid, category, fuel(truck, "1", "2000")))
    operator.post(f"{BASE}/deliveries/{sid}/expenses/{gone['id']}/void", json={"reason": "Cargado dos veces"})
    response = close(operator, sid)
    assert response.status_code == 201, response.text
    load = db.execute(text("SELECT id FROM settlements.fuel_loads WHERE expense_id = :e"), {"e": kept["id"]})
    return {
        "settlement": sid,
        "expense": kept["id"],
        "voided": gone["id"],
        "load": load.scalar_one(),
        "close": response.json()["close_in_effect"]["operation_id"],
        "truck": truck,
        "pedro": pedro,
    }


def _snapshot(db: Session) -> list[tuple[object, ...]]:
    db.expire_all()
    return [tuple(r) for r in db.execute(text(SNAPSHOT)).all()]


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def test_the_close_freezes_its_loads_in_effect(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    """Only the load in effect: the voided one is not published."""
    assert [(r[0], r[1], r[2], r[3], str(r[6]), str(r[7]), str(r[8])) for r in _snapshot(db)] == [
        (closed["close"], 1, closed["load"], closed["expense"], "10.000", "2000.0000", "20000.00")
    ]


def test_a_new_close_freezes_again_and_keeps_the_previous_one(as_role, db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    sid = closed["settlement"]
    assert reopen(as_role("admin"), sid, 1).status_code == 200
    operator = as_role("operator")
    patched = operator.patch(
        f"{BASE}/deliveries/{sid}/expenses/{closed['expense']}",
        json={"fuel": {"liters": "8", "price_per_liter": "2500"}},
    )
    assert patched.status_code == 200, patched.text
    assert len(_snapshot(db)) == 1  # a correction writes nothing until its close
    second = close(operator, sid, version=2).json()["close_in_effect"]["operation_id"]
    rows = _snapshot(db)
    assert [(r[0], r[1], str(r[6]), str(r[7])) for r in rows] == [
        (closed["close"], 1, "10.000", "2000.0000"),
        (second, 2, "8.000", "2500.0000"),
    ]


def test_what_a_close_published_is_never_changed_nor_deleted(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    _refused(db, "UPDATE settlements.settlement_closing_fuel_loads SET liters = 1")
    _refused(db, "DELETE FROM settlements.settlement_closing_fuel_loads")
    assert len(_snapshot(db)) == 1


def test_only_a_load_of_that_settlement_and_a_close_of_it(as_role, driver, db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    other = create(operator, closed["pedro"], "5000")
    foreign = last_expense(add_fuel(operator, other, fuel_category(db), fuel(closed["truck"], "1", "5000")))
    foreign_load = db.execute(
        text("SELECT id FROM settlements.fuel_loads WHERE expense_id = :e"), {"e": foreign["id"]}
    ).scalar_one()
    insert = (
        "INSERT INTO settlements.settlement_closing_fuel_loads (close_operation_id, settlement_id, settlement_version, "
        "fuel_load_id, expense_id, vehicle_id, employee_id, liters, price_per_liter, amount) "
        "VALUES (:o, :s, 1, :l, :e, :v, :p, 1, 5000, 5000)"
    )
    params = {"v": closed["truck"], "p": closed["pedro"]}
    message = _refused(db, insert, o=closed["close"], s=closed["settlement"], l=foreign_load, e=foreign["id"], **params)
    assert "is not of expense" in message
    message = _refused(db, insert, o=closed["close"], s=other, l=foreign_load, e=foreign["id"], **params)
    assert "is not a close of settlement" in message


def test_a_close_publishes_exactly_its_loads_in_effect(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    """Checked at commit: adding the voided load to what the close published is refused."""
    voided_load = db.execute(
        text("SELECT id FROM settlements.fuel_loads WHERE expense_id = :e"), {"e": closed["voided"]}
    ).scalar_one()
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_closing_fuel_loads (close_operation_id, settlement_id, settlement_version, "
        "fuel_load_id, expense_id, vehicle_id, employee_id, liters, price_per_liter, amount) "
        "VALUES (:o, :s, 1, :l, :e, :v, :p, 1, 2000, 2000)",
        o=closed["close"],
        s=closed["settlement"],
        l=voided_load,
        e=closed["voided"],
        v=closed["truck"],
        p=closed["pedro"],
    )
    assert "published fuel loads differ" in message


def test_the_migration_rebuilds_the_closes_in_effect(as_role, db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    """Settlements closed before 0017 get what their close in effect published; an open one gets nothing."""
    operator = as_role("operator")
    create(operator, closed["pedro"], "1000")
    before = _snapshot(db)
    db.commit()
    db.close()
    cfg = alembic_config()
    command.downgrade(cfg, "0016")
    command.upgrade(cfg, "head")
    after = _snapshot(db)
    assert after == before
    page = operator.get(f"{BASE}/fuel-loads").json()
    assert ([r["state"] for r in page["items"]], page["summary"]["liters"]) == (["VOIDED", "PUBLISHED"], "10.000")
