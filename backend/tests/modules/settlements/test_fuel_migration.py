"""Migration 0015 on data loaded before R5-2 (Enzo's decision 1): an existing «Combustible» becomes FUEL keeping
its id, name, state and expenses, and its expenses without detail, even in closed settlements, are the only
rows that are ever legacy. Without one, FUEL is created. Each test goes down to 0014 and back up to head."""

from alembic import command
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import alembic_config
from tests.modules.settlements.fuel_support import BASE, add_fuel, fuel, fuel_category, last_expense, vehicle
from tests.modules.settlements.test_settlement_close_api import close, create, reopen


def _cycle(db: Session, *at_0014: str) -> None:
    """Back to 0014 (the data stays, fuel_loads and the codes go), run `at_0014`, and upgrade again."""
    db.commit()
    db.close()
    cfg = alembic_config()
    command.downgrade(cfg, "0014")
    with db.get_bind().begin() as conn:  # type: ignore[union-attr]
        for sql in at_0014:
            conn.execute(text(sql))
    command.upgrade(cfg, "head")


def _expense(db: Session, expense_id: int) -> tuple[int, bool]:
    row = db.execute(
        text("SELECT category_id, legacy_fuel_without_detail FROM settlements.expenses WHERE id = :e"),
        {"e": expense_id},
    ).one()
    return row.category_id, row.legacy_fuel_without_detail


def test_an_existing_combustible_becomes_fuel_and_its_history_is_legacy(as_role, driver, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[legacy 1, 3, 10] Including a fuel expense of a closed settlement; ordinary expenses are untouched.
    Then the reopened settlement gets the detail, which clears the exception, and closes again."""
    pedro = driver("Pedro Gómez")
    admin = as_role("admin")
    tolls = admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]
    truck = vehicle(db, "AB123CD")
    combustible = fuel_category(db)
    operator = as_role("operator")
    closed = create(operator, pedro, "10000")
    old_fuel = last_expense(add_fuel(operator, closed, combustible, fuel(truck, "10", "1000")))["id"]
    assert close(as_role("operator"), closed).status_code == 201
    still_open = create(as_role("operator"), pedro, "5000")
    toll = as_role("operator").post(
        f"{BASE}/deliveries/{still_open}/expenses",
        json={"client_request_id": "00000000-0000-4000-8000-000000000001", "category_id": tolls, "amount": "500"},
    )
    toll_id = last_expense(toll)["id"]

    _cycle(db)  # at 0014 «Combustible» is an ordinary category and its expense has no detail

    db.expire_all()
    rows = db.execute(
        text("SELECT id, name, active, code FROM settlements.expense_categories WHERE name_key = 'combustible'")
    ).all()
    assert [tuple(r) for r in rows] == [(combustible, "Combustible", True, "FUEL")]  # reused: no duplicate
    assert db.execute(text("SELECT count(*) FROM settlements.expense_categories WHERE code = 'FUEL'")).scalar_one() == 1
    assert _expense(db, old_fuel) == (combustible, True)  # legacy although its settlement is closed
    assert _expense(db, toll_id) == (tolls, False)
    assert db.execute(text("SELECT count(*) FROM settlements.fuel_loads")).scalar_one() == 0

    (item,) = as_role("viewer").get(f"{BASE}/deliveries/{closed}").json()["expenses"]
    assert (item["legacy_fuel_without_detail"], item["fuel"], item["amount"]) == (True, None, "10000.00")
    assert reopen(as_role("admin"), closed, 1).status_code == 200
    operator = as_role("operator")
    url = f"{BASE}/deliveries/{closed}/expenses/{old_fuel}"
    assert operator.patch(url, json={"amount": "9000"}).json()["code"] == "fuel_load_required"
    converted = operator.patch(url, json={"fuel": fuel(truck, "10", "1000")})
    assert converted.status_code == 200, converted.text
    assert (last_expense(converted)["legacy_fuel_without_detail"], last_expense(converted)["amount"]) == (
        False,
        "10000.00",
    )
    assert close(operator, closed, version=2).status_code == 201


def test_without_a_combustible_fuel_is_created(as_role, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[legacy 2] An unrelated «Nafta» is not taken for fuel and nothing is legacy."""
    as_role("admin")
    _cycle(
        db,
        "UPDATE settlements.expense_categories SET name = 'Nafta', name_key = 'nafta' WHERE name_key = 'combustible'",
    )

    db.expire_all()
    rows = db.execute(text("SELECT name, active, code FROM settlements.expense_categories ORDER BY id")).all()
    assert [tuple(r) for r in rows] == [("Nafta", True, None), ("Combustible", True, "FUEL")]
    assert (
        db.execute(text("SELECT count(*) FROM settlements.expenses WHERE legacy_fuel_without_detail")).scalar_one() == 0
    )
