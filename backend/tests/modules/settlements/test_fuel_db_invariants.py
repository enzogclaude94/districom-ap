"""The fuel rules the database keeps whoever writes (R5-2): direct SQL, no API. Above all, Enzo's invariant
that `legacy_fuel_without_detail = true` only exists because migration 0015 marked a historical row."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.fuel_support import (
    BASE,
    add_fuel,
    fuel,
    fuel_category,
    last_expense,
    make_legacy,
    vehicle,
)
from tests.modules.settlements.test_settlement_close_api import close, count, create

FINGERPRINT = "0" * 64


def _refused(db: Session, *statements: str, **params: object) -> str:
    """Run the statements in one transaction and return why the database refused it (at commit at the latest)."""

    def _write() -> None:
        for sql in statements:
            db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def _accepted(db: Session, *statements: str, **params: object) -> None:
    for sql in statements:
        db.execute(text(sql), params)
    db.commit()


@pytest.fixture
def world(as_role, driver, db: Session) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """An OPEN settlement with one fuel expense (10 l x $1.000) and one $500 toll, a second active vehicle,
    an inactive one and an inactive employee."""
    pedro = driver("Pedro Gómez")
    retired = driver("Ex Empleado")
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": retired})
    db.commit()
    admin = as_role("admin")
    tolls = admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]
    truck, other, parked = vehicle(db, "AB123CD"), vehicle(db, "AC456DE"), vehicle(db, "AD789EF", active=False)
    operator = as_role("operator")
    sid = create(operator, pedro, "100000")
    fueled = last_expense(add_fuel(operator, sid, fuel_category(db), fuel(truck, "10", "1000")))["id"]
    toll = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "500"},
    )
    return {
        "settlement": sid,
        "fuel": fueled,
        "toll": last_expense(toll)["id"],
        "tolls": tolls,
        "FUEL": fuel_category(db),
        "truck": truck,
        "other": other,
        "parked": parked,
        "pedro": pedro,
        "retired": retired,
        "user": int(db.execute(text("SELECT min(id) FROM users")).scalar_one()),
    }


def _new_expense(category: str = ":category", legacy: str = "false") -> str:
    return (
        "INSERT INTO settlements.expenses (settlement_id, category_id, amount, client_request_id, request_fingerprint, "  # noqa: S608
        f"created_by, legacy_fuel_without_detail) VALUES (:sid, {category}, 1000, gen_random_uuid(), '{FINGERPRINT}', "
        f":user, {legacy})"
    )


LOAD = (
    "INSERT INTO settlements.fuel_loads (expense_id, vehicle_id, employee_id, liters, price_per_liter, created_by, "
    "updated_by) VALUES (:expense, :vehicle, :employee, :liters, :price, :user, :user)"
)


# --- legacy is born only in the migration -------------------------------------------------------------


def test_a_new_row_is_never_legacy(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[legacy 4, 6]"""
    message = _refused(
        db, _new_expense(legacy="true"), sid=world["settlement"], category=world["FUEL"], user=world["user"]
    )
    assert "a new expense is never legacy_fuel_without_detail" in message
    message = _refused(
        db, _new_expense(legacy="true"), sid=world["settlement"], category=world["tolls"], user=world["user"]
    )
    assert "never legacy" in message


@pytest.mark.parametrize("which", ["fuel", "toll"])
def test_no_row_becomes_legacy(world, db: Session, which: str) -> None:  # type: ignore[no-untyped-def]
    """[legacy 7, 12] false -> true is refused, for a fuel expense with its load and for any other."""
    message = _refused(
        db, "UPDATE settlements.expenses SET legacy_fuel_without_detail = true WHERE id = :e", e=world[which]
    )
    assert "never goes from false to true" in message


def test_a_legacy_row_does_not_come_back_after_being_cleared(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[legacy 11, 12] true -> false is allowed (with its load); after that, false -> true is refused again."""
    make_legacy(db, world["toll"])
    _accepted(
        db,
        "UPDATE settlements.expenses SET legacy_fuel_without_detail = false, amount = 1500 WHERE id = :expense",
        LOAD,
        expense=world["toll"],
        vehicle=world["truck"],
        employee=world["pedro"],
        liters="1.5",
        price="1000",
        user=world["user"],
    )
    message = _refused(
        db, "UPDATE settlements.expenses SET legacy_fuel_without_detail = true WHERE id = :e", e=world["toll"]
    )
    assert "never goes from false to true" in message


def test_a_legacy_row_keeps_its_exception_for_descriptive_changes(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[legacy 8]"""
    make_legacy(db, world["toll"])
    _accepted(db, "UPDATE settlements.expenses SET description = 'YPF' WHERE id = :e", e=world["toll"])
    assert db.execute(
        text("SELECT legacy_fuel_without_detail FROM settlements.expenses WHERE id = :e"), {"e": world["toll"]}
    ).scalar_one()


@pytest.mark.parametrize("change", ["amount = 600", "category_id = :tolls"])
def test_a_financial_change_of_a_legacy_row_needs_to_clear_it(world, db: Session, change: str) -> None:  # type: ignore[no-untyped-def]
    """[legacy 9] Changing the money of a legacy row keeping the flag is refused; clearing it without a load
    is refused at commit by the fuel invariant."""
    make_legacy(db, world["toll"])
    message = _refused(
        db,
        f"UPDATE settlements.expenses SET {change} WHERE id = :e",  # noqa: S608
        e=world["toll"],
        tolls=world["tolls"],
    )
    assert "needs its fuel load" in message
    message = _refused(
        db,
        "UPDATE settlements.expenses SET legacy_fuel_without_detail = false, amount = 600 WHERE id = :e",
        e=world["toll"],
    )
    assert "a FUEL expense needs its fuel load" in message


def test_a_legacy_row_has_no_load(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_legacy(db, world["toll"])
    message = _refused(
        db,
        LOAD,
        expense=world["toll"],
        vehicle=world["truck"],
        employee=world["pedro"],
        liters="0.5",
        price="1000",
        user=world["user"],
    )
    assert "a legacy expense has no fuel load" in message


# --- fuel invariants ----------------------------------------------------------------------------------


def test_a_fuel_expense_in_force_needs_its_load(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[1]"""
    message = _refused(db, _new_expense(), sid=world["settlement"], category=world["FUEL"], user=world["user"])
    assert "a FUEL expense needs its fuel load" in message


def test_a_load_belongs_only_to_a_fuel_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[2]"""
    message = _refused(
        db,
        LOAD,
        expense=world["toll"],
        vehicle=world["truck"],
        employee=world["pedro"],
        liters="0.5",
        price="1000",
        user=world["user"],
    )
    assert "only a FUEL expense has a fuel load" in message


def test_one_load_per_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[3]"""
    message = _refused(
        db,
        LOAD,
        expense=world["fuel"],
        vehicle=world["truck"],
        employee=world["pedro"],
        liters="10",
        price="1000",
        user=world["user"],
    )
    assert "uq_fuel_loads_expense_id" in message


@pytest.mark.parametrize(
    ("change", "constraint"),
    [
        ("liters = 0", "ck_fuel_loads_liters_positive"),
        ("price_per_liter = 0", "ck_fuel_loads_price_positive"),
        ("odometer_km = -1", "ck_fuel_loads_odometer_not_negative"),
    ],
)
def test_load_values(world, db: Session, change: str, constraint: str) -> None:  # type: ignore[no-untyped-def]
    """[4] [5] [6]"""
    assert constraint in _refused(db, f"UPDATE settlements.fuel_loads SET {change}")  # noqa: S608


def test_the_amount_is_liters_times_price(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[7] Changing either side alone is refused at commit; changing both consistently is accepted."""
    assert "is not liters x price per liter" in _refused(db, "UPDATE settlements.fuel_loads SET liters = 11")
    assert "is not liters x price per liter" in _refused(
        db, "UPDATE settlements.expenses SET amount = 10001 WHERE id = :e", e=world["fuel"]
    )
    _accepted(
        db,
        "UPDATE settlements.fuel_loads SET liters = 0.125, price_per_liter = 1",
        "UPDATE settlements.expenses SET amount = 0.13 WHERE id = :e",
        e=world["fuel"],
    )


def test_vehicle_and_employee_must_be_active_when_set(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[8] [9] On a change; a load whose vehicle or employee was deactivated later can still change liters."""
    message = _refused(db, "UPDATE settlements.fuel_loads SET vehicle_id = :v", v=world["parked"])
    assert f"vehicle {world['parked']} is not active" in message
    message = _refused(db, "UPDATE settlements.fuel_loads SET employee_id = :e", e=world["retired"])
    assert f"employee {world['retired']} is not active" in message
    _accepted(db, "UPDATE vehicles SET active = false WHERE id = :v", v=world["truck"])
    _accepted(db, "UPDATE employees SET active = false WHERE id = :e", e=world["pedro"])
    _accepted(
        db,
        "UPDATE settlements.fuel_loads SET liters = 12",
        "UPDATE settlements.expenses SET amount = 12000 WHERE id = :e",
        e=world["fuel"],
    )


def test_a_new_load_needs_an_active_vehicle_and_employee(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[8] [9] On insert, inside the same transaction as its expense."""
    for vehicle_id, employee_id, expected in (
        (world["parked"], world["pedro"], "is not active"),
        (world["truck"], world["retired"], "is not active"),
    ):
        expense_id = db.execute(
            text(_new_expense() + " RETURNING id"),
            {"sid": world["settlement"], "category": world["FUEL"], "user": world["user"]},
        ).scalar_one()
        message = _refused(
            db,
            LOAD,
            expense=expense_id,
            vehicle=vehicle_id,
            employee=employee_id,
            liters="1",
            price="1000",
            user=world["user"],
        )
        assert expected in message


def test_a_category_code_never_changes(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[12]"""
    for change, target in (("code = 'GASOIL'", "FUEL"), ("code = NULL", "FUEL"), ("code = 'TOLLS'", "tolls")):
        message = _refused(db, f"UPDATE settlements.expense_categories SET {change} WHERE id = :c", c=world[target])  # noqa: S608
        assert "code never changes" in message
    # Name and state are free: an admin renames or deactivates FUEL.
    _accepted(
        db, "UPDATE settlements.expense_categories SET name = 'Gasoil', active = false WHERE id = :c", c=world["FUEL"]
    )


def test_nothing_is_deleted(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[13]"""
    assert "never deleted" in _refused(db, "DELETE FROM settlements.fuel_loads")
    assert "never deleted" in _refused(db, "DELETE FROM settlements.expense_categories WHERE code = 'FUEL'")


def test_a_load_changes_only_while_its_settlement_is_open(as_role, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[14] Also its expense, author and creation never change."""
    main_cash = _main_cash(db)
    count(as_role("operator"), world["settlement"], ("ARS_20000", 4, main_cash), ("ARS_10000", 1, main_cash))
    surplus = {"target": "COMPANY", "amount": "500", "reason": "Sobrante menor"}  # 90.000 + 10.500 of expenses
    response = close(as_role("admin"), world["settlement"], surplus)
    assert response.status_code == 201, response.text
    assert "is CLOSED and cannot change" in _refused(db, "UPDATE settlements.fuel_loads SET odometer_km = 1")
    assert "never change" in _refused(
        db, "UPDATE settlements.fuel_loads SET created_by = created_by + 0, expense_id = :e", e=world["toll"]
    )


def _main_cash(db: Session) -> int:
    return int(db.execute(text("SELECT id FROM settlements.accounts WHERE code = 'main_cash'")).scalar_one())
