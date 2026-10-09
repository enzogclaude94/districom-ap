"""Fuel inside a delivery settlement's expense (R5-2): the FUEL category, its load, the computed amount, the
legacy exception, the close, its preview token and a reopening. Database rules are in
`test_fuel_db_invariants.py`, races in `test_fuel_concurrency.py`, the migration in `test_fuel_migration.py`."""

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
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
from tests.modules.settlements.test_settlement_close_api import (
    close,
    count,
    create,
    error,
    legs,
    preview_token,
    reopen,
)


@pytest.fixture
def ids(as_role) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {a["code"]: a["id"] for a in as_role("admin").get(f"{BASE}/accounts").json()}


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def helper(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Juan Ayudante", function="settlements.cash_viewer")  # type: ignore[no-any-return]


@pytest.fixture
def fuel_id(db: Session) -> int:
    return fuel_category(db)


@pytest.fixture
def truck(as_role, db: Session) -> int:  # type: ignore[no-untyped-def]
    as_role("admin")  # a user exists to register it
    return vehicle(db, "AB123CD")


@pytest.fixture
def sid(as_role, pedro) -> int:  # type: ignore[no-untyped-def]
    return create(as_role("operator"), pedro, "100000")


@pytest.fixture
def tolls(as_role) -> int:  # type: ignore[no-untyped-def]
    return as_role("admin").post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]  # type: ignore[no-any-return]


def _audit(db: Session, event: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = :e ORDER BY id"),
        {"e": f"settlements.expense.{event}"},
    ).scalars()
    return list(rows)


# --- category ---------------------------------------------------------------------------------------


def test_the_fuel_category_is_seeded_with_its_code(as_role, fuel_id) -> None:  # type: ignore[no-untyped-def]
    rows = as_role("viewer").get(f"{BASE}/expense-categories").json()
    assert [(r["id"], r["name"], r["code"], r["active"]) for r in rows if r["code"]] == [
        (fuel_id, "Combustible", "FUEL", True)
    ]


def test_the_api_never_takes_a_code(as_role, fuel_id, tolls) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    assert admin.post(f"{BASE}/expense-categories", json={"name": "Gas", "code": "GAS"}).status_code == 422
    assert admin.patch(f"{BASE}/expense-categories/{tolls}", json={"code": "FUEL"}).status_code == 422
    assert admin.patch(f"{BASE}/expense-categories/{fuel_id}", json={"code": None}).status_code == 422


def test_renaming_fuel_keeps_its_behavior(as_role, fuel_id, sid, truck) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    renamed = admin.patch(f"{BASE}/expense-categories/{fuel_id}", json={"name": "Gasoil"}).json()
    assert (renamed["name"], renamed["code"]) == ("Gasoil", "FUEL")
    operator = as_role("operator")
    without = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": fuel_id, "amount": "100"},
    )
    assert error(without) == (422, "fuel_amount_is_computed")
    item = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1500")))
    assert (item["category_name"], item["category_code"], item["amount"]) == ("Gasoil", "FUEL", "15000.00")


def test_a_deactivated_fuel_category_takes_no_new_loads_and_keeps_old_ones(  # type: ignore[no-untyped-def]
    as_role, fuel_id, sid, truck
) -> None:
    operator = as_role("operator")
    first = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1500")))
    as_role("admin").patch(f"{BASE}/expense-categories/{fuel_id}", json={"active": False})
    blocked = add_fuel(as_role("operator"), sid, fuel_id, fuel(truck, "5", "1500"))
    assert error(blocked) == (422, "invalid_expense_category")
    (kept,) = as_role("viewer").get(f"{BASE}/deliveries/{sid}").json()["expenses"]
    assert (kept["id"], kept["fuel"]["liters"], kept["amount"]) == (first["id"], "10.000", "15000.00")


# --- create ---------------------------------------------------------------------------------------


def test_a_fuel_expense_computes_its_amount_and_defaults_to_the_driver(  # type: ignore[no-untyped-def]
    as_role, fuel_id, sid, truck, pedro, db: Session
) -> None:
    response = add_fuel(as_role("operator"), sid, fuel_id, fuel(truck, "45.678", "1234.5678", odometer_km=120500))
    assert response.status_code == 201, response.text
    item = last_expense(response)
    assert item["amount"] == "56392.59"
    assert item["legacy_fuel_without_detail"] is False
    load = item["fuel"]
    assert (load["vehicle_id"], load["vehicle_plate"], load["vehicle_active"]) == (truck, "AB 123 CD", True)
    assert (load["employee_id"], load["employee_name"]) == (pedro, "Pedro Gómez")
    assert (load["liters"], load["price_per_liter"], load["odometer_km"]) == ("45.678", "1234.5678", 120500)
    stored = db.execute(text("SELECT amount FROM settlements.expenses WHERE id = :e"), {"e": item["id"]}).scalar_one()
    assert str(stored) == "56392.59"
    assert "amount" not in db.execute(text("SELECT * FROM settlements.fuel_loads")).mappings().one()
    summary = response.json()["summary"]
    assert summary["expenses"] == "56392.59"
    [created] = _audit(db, "created")
    assert created["after"]["fuel"] == {
        "vehicle_id": truck,
        "employee_id": pedro,
        "liters": "45.678",
        "price_per_liter": "1234.5678",
        "odometer_km": 120500,
    }


def test_another_active_employee_can_have_loaded(as_role, fuel_id, sid, truck, helper) -> None:  # type: ignore[no-untyped-def]
    item = last_expense(add_fuel(as_role("operator"), sid, fuel_id, fuel(truck, "10", "1000", employee_id=helper)))
    assert item["fuel"]["employee_name"] == "Juan Ayudante"


def test_an_inactive_or_unknown_employee_is_refused(as_role, fuel_id, sid, truck, helper, db: Session) -> None:  # type: ignore[no-untyped-def]
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": helper})
    db.commit()
    operator = as_role("operator")
    for employee_id in (helper, 999999):
        refused = add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000", employee_id=employee_id))
        assert error(refused) == (422, "invalid_employee")


def test_an_inactive_vehicle_is_refused_for_a_new_load(as_role, fuel_id, sid, db: Session) -> None:  # type: ignore[no-untyped-def]
    as_role("admin")
    parked = vehicle(db, "AC456DE", active=False)
    assert error(add_fuel(as_role("operator"), sid, fuel_id, fuel(parked, "10", "1000"))) == (422, "invalid_vehicle")
    assert error(add_fuel(as_role("operator"), sid, fuel_id, fuel(999999, "10", "1000"))) == (422, "invalid_vehicle")


def test_a_vehicle_deactivated_later_stays_on_its_loads(as_role, fuel_id, sid, truck, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    item = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000")))
    db.execute(text("UPDATE vehicles SET active = false WHERE id = :v"), {"v": truck})
    db.commit()
    shown = as_role("viewer").get(f"{BASE}/deliveries/{sid}").json()["expenses"][0]["fuel"]
    assert (shown["vehicle_plate"], shown["vehicle_active"]) == ("AB 123 CD", False)
    # Changing the liters does not choose the vehicle again.
    changed = as_role("operator").patch(
        f"{BASE}/deliveries/{sid}/expenses/{item['id']}", json={"fuel": {"liters": "12"}}
    )
    assert changed.status_code == 200, changed.text
    assert last_expense(changed)["amount"] == "12000.00"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"amount": "15000", "fuel": "LOAD"}, "fuel_amount_is_computed"),
        ({}, "fuel_detail_required"),
    ],
)
def test_a_fuel_expense_has_detail_and_no_written_amount(as_role, fuel_id, sid, truck, body, code) -> None:  # type: ignore[no-untyped-def]
    payload: dict[str, Any] = {"client_request_id": str(uuid4()), "category_id": fuel_id, **body}
    if payload.get("fuel") == "LOAD":
        payload["fuel"] = fuel(truck, "10", "1500")
    assert error(as_role("operator").post(f"{BASE}/deliveries/{sid}/expenses", json=payload)) == (422, code)


def test_an_ordinary_expense_keeps_its_amount_and_takes_no_fuel(as_role, tolls, sid, truck) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    with_fuel = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "500", "fuel": fuel(truck, "1", "1")},
    )
    assert error(with_fuel) == (422, "fuel_detail_not_allowed")
    without_amount = operator.post(
        f"{BASE}/deliveries/{sid}/expenses", json={"client_request_id": str(uuid4()), "category_id": tolls}
    )
    assert error(without_amount) == (422, "invalid_amount")
    item = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "500"},
    )
    assert (last_expense(item)["amount"], last_expense(item)["fuel"], last_expense(item)["category_code"]) == (
        "500.00",
        None,
        None,
    )


@pytest.mark.parametrize(
    ("liters", "price", "code"),
    [("0.001", "0.0001", "fuel_amount_zero"), ("9999999.999", "9999999999.9999", "amount_too_large")],
)
def test_a_computed_amount_outside_the_money_range_is_refused(
    as_role, fuel_id, sid, truck, liters, price, code
) -> None:  # type: ignore[no-untyped-def]
    assert error(add_fuel(as_role("operator"), sid, fuel_id, fuel(truck, liters, price))) == (422, code)


@pytest.mark.parametrize(
    "detail",
    [
        {"liters": "0", "price_per_liter": "1"},
        {"liters": "1", "price_per_liter": "0"},
        {"liters": "1.0001", "price_per_liter": "1"},
        {"liters": "1", "price_per_liter": "1.00001"},
        {"liters": "1", "price_per_liter": "1", "odometer_km": -1},
    ],
)
def test_load_values_are_validated(as_role, fuel_id, sid, truck, detail) -> None:  # type: ignore[no-untyped-def]
    assert add_fuel(as_role("operator"), sid, fuel_id, {"vehicle_id": truck, **detail}).status_code == 422


def test_the_legacy_flag_is_never_input(as_role, fuel_id, sid, truck) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    refused = add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000"), legacy_fuel_without_detail=True)
    assert refused.status_code == 422
    item = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000")))
    patched = operator.patch(
        f"{BASE}/deliveries/{sid}/expenses/{item['id']}", json={"legacy_fuel_without_detail": True}
    )
    assert patched.status_code == 422


def test_a_retry_creates_one_expense_and_one_load(as_role, fuel_id, sid, truck, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    body = {"client_request_id": str(uuid4()), "category_id": fuel_id, "fuel": fuel(truck, "10", "1000")}
    first = operator.post(f"{BASE}/deliveries/{sid}/expenses", json=body)
    again = operator.post(f"{BASE}/deliveries/{sid}/expenses", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert db.execute(text("SELECT count(*) FROM settlements.fuel_loads")).scalar_one() == 1
    other = operator.post(f"{BASE}/deliveries/{sid}/expenses", json={**body, "fuel": fuel(truck, "11", "1000")})
    assert error(other) == (409, "idempotency_conflict")


# --- corrections ----------------------------------------------------------------------------------


def test_a_load_correction_recomputes_the_amount_and_is_audited(  # type: ignore[no-untyped-def]
    as_role, fuel_id, sid, truck, helper, db: Session
) -> None:
    operator = as_role("operator")
    item = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000")))
    as_role("admin")
    other = vehicle(db, "AC456DE")
    url = f"{BASE}/deliveries/{sid}/expenses/{item['id']}"
    operator = as_role("operator")
    changes = [
        ({"vehicle_id": other}, "10000.00"),
        ({"employee_id": helper}, "10000.00"),
        ({"liters": "33.333"}, "33333.00"),
        ({"price_per_liter": "1499.9900"}, "49999.17"),
        ({"odometer_km": 1000}, "49999.17"),
        ({"odometer_km": None}, "49999.17"),
    ]
    for change, amount in changes:
        response = operator.patch(url, json={"fuel": change})
        assert response.status_code == 200, response.text
        assert last_expense(response)["amount"] == amount
    events = _audit(db, "updated")
    assert len(events) == len(changes)
    assert (events[0]["before"]["fuel"]["vehicle_id"], events[0]["after"]["fuel"]["vehicle_id"]) == (truck, other)
    assert events[1]["after"]["fuel"]["employee_id"] == helper
    assert (events[2]["before"]["amount"], events[2]["after"]["amount"]) == ("10000.00", "33333.00")
    assert (events[3]["after"]["fuel"]["price_per_liter"], events[3]["after"]["amount"]) == ("1499.9900", "49999.17")
    assert (events[4]["before"]["fuel"]["odometer_km"], events[4]["after"]["fuel"]["odometer_km"]) == (None, 1000)
    # The same values again change nothing and record nothing.
    assert operator.patch(url, json={"fuel": {"liters": "33.333"}}).status_code == 200
    assert len(_audit(db, "updated")) == len(changes)


def test_a_fuel_expense_amount_is_never_written(as_role, fuel_id, sid, truck) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    item = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000")))
    response = operator.patch(f"{BASE}/deliveries/{sid}/expenses/{item['id']}", json={"amount": "9000"})
    assert error(response) == (422, "fuel_amount_is_computed")


def test_fuel_never_switches_category(as_role, fuel_id, tolls, sid, truck) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    fueled = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000")))
    toll = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "500"},
    )
    toll_id = last_expense(toll)["id"]
    url = f"{BASE}/deliveries/{sid}/expenses"
    assert error(operator.patch(f"{url}/{fueled['id']}", json={"category_id": tolls})) == (422, "fuel_category_change")
    assert error(operator.patch(f"{url}/{toll_id}", json={"category_id": fuel_id})) == (422, "fuel_category_change")
    assert error(operator.patch(f"{url}/{toll_id}", json={"fuel": {"liters": "1"}})) == (422, "fuel_detail_not_allowed")


def test_voiding_keeps_the_load_and_drops_the_amount(as_role, fuel_id, sid, truck, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    item = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "1000")))
    voided = operator.post(f"{BASE}/deliveries/{sid}/expenses/{item['id']}/void", json={"reason": "Cargado dos veces"})
    assert voided.status_code == 200, voided.text
    shown = last_expense(voided)
    assert (shown["voided"], shown["fuel"]["liters"]) == (True, "10.000")
    assert voided.json()["summary"]["expenses"] == "0.00"
    assert db.execute(text("SELECT count(*) FROM settlements.fuel_loads")).scalar_one() == 1
    blocked = operator.patch(f"{BASE}/deliveries/{sid}/expenses/{item['id']}", json={"fuel": {"liters": "1"}})
    assert error(blocked) == (409, "expense_voided")


# --- legacy -----------------------------------------------------------------------------------------


@pytest.fixture
def legacy(as_role, tolls, sid, db: Session) -> int:  # type: ignore[no-untyped-def]
    """A fuel expense of $20.000 loaded before R5-2, as migration 0015 leaves it."""
    response = as_role("operator").post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "20000", "description": "YPF"},
    )
    expense_id: int = last_expense(response)["id"]
    make_legacy(db, expense_id)
    return expense_id


def test_a_legacy_expense_is_shown_without_detail(as_role, legacy, sid) -> None:  # type: ignore[no-untyped-def]
    (item,) = as_role("viewer").get(f"{BASE}/deliveries/{sid}").json()["expenses"]
    assert (item["category_code"], item["fuel"], item["legacy_fuel_without_detail"]) == ("FUEL", None, True)


def test_a_descriptive_change_keeps_the_legacy_exception(as_role, legacy, sid, db: Session) -> None:  # type: ignore[no-untyped-def]
    response = as_role("operator").patch(
        f"{BASE}/deliveries/{sid}/expenses/{legacy}", json={"description": "YPF Ruta 5"}
    )
    assert response.status_code == 200, response.text
    item = last_expense(response)
    assert (item["description"], item["legacy_fuel_without_detail"], item["amount"]) == ("YPF Ruta 5", True, "20000.00")


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"amount": "21000"}, "fuel_load_required"),
        ({"fuel": {"liters": "10"}}, "fuel_load_required"),
        ({"amount": "21000", "fuel": "LOAD"}, "fuel_amount_is_computed"),
    ],
)
def test_a_financial_change_of_a_legacy_expense_needs_its_load(as_role, legacy, sid, truck, body, code) -> None:  # type: ignore[no-untyped-def]
    if body.get("fuel") == "LOAD":
        body = {**body, "fuel": fuel(truck, "10", "2000")}
    response = as_role("operator").patch(f"{BASE}/deliveries/{sid}/expenses/{legacy}", json=body)
    assert error(response) == (422, code)


def test_a_legacy_category_change_is_refused(as_role, legacy, sid, tolls) -> None:  # type: ignore[no-untyped-def]
    response = as_role("operator").patch(f"{BASE}/deliveries/{sid}/expenses/{legacy}", json={"category_id": tolls})
    assert error(response) == (422, "fuel_category_change")


def test_loading_the_detail_clears_the_legacy_exception(  # type: ignore[no-untyped-def]
    as_role, legacy, sid, truck, pedro, db: Session
) -> None:
    response = as_role("operator").patch(
        f"{BASE}/deliveries/{sid}/expenses/{legacy}", json={"fuel": fuel(truck, "15", "1400")}
    )
    assert response.status_code == 200, response.text
    item = last_expense(response)
    assert (item["legacy_fuel_without_detail"], item["amount"]) == (False, "21000.00")
    assert (item["fuel"]["employee_id"], item["fuel"]["liters"]) == (pedro, "15.000")
    [event] = _audit(db, "updated")
    assert event["before"]["legacy_fuel_without_detail"] is True
    assert "legacy_fuel_without_detail" not in event["after"]
    assert event["after"]["fuel"]["vehicle_id"] == truck


def test_a_legacy_expense_can_be_voided_without_inventing_a_load(as_role, legacy, sid, db: Session) -> None:  # type: ignore[no-untyped-def]
    response = as_role("operator").post(f"{BASE}/deliveries/{sid}/expenses/{legacy}/void", json={"reason": "Duplicado"})
    assert response.status_code == 200, response.text
    assert db.execute(text("SELECT count(*) FROM settlements.fuel_loads")).scalar_one() == 0


# --- close, token and reopening ---------------------------------------------------------------------


@pytest.fixture
def loaded(as_role, ids, fuel_id, sid, truck) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """$100.000 expected: $80.000 counted and a $20.000 fuel load (10 l x $2.000): balanced."""
    operator = as_role("operator")
    count(operator, sid, ("ARS_20000", 4, ids["main_cash"]))
    item = last_expense(add_fuel(operator, sid, fuel_id, fuel(truck, "10", "2000")))
    return {"settlement": sid, "expense": item["id"]}


def test_the_close_publishes_fuel_as_an_ordinary_expense(as_role, loaded, db: Session) -> None:  # type: ignore[no-untyped-def]
    response = close(as_role("operator"), loaded["settlement"])
    assert response.status_code == 201, response.text
    operation = response.json()["close_in_effect"]["operation_id"]
    assert legs(db, operation) == {"main_cash": "80000.00", "expense": "20000.00", "delivery_collections": "-100000.00"}
    assert db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one() == 1
    blocked = as_role("operator").patch(
        f"{BASE}/deliveries/{loaded['settlement']}/expenses/{loaded['expense']}", json={"fuel": {"liters": "11"}}
    )
    assert error(blocked) == (409, "settlement_closed")


@pytest.mark.parametrize(
    "change",
    [
        {"liters": "10.5"},
        {"price_per_liter": "2100"},
        {"odometer_km": 5000},
        {"employee_id": "HELPER"},
        {"vehicle_id": "OTHER"},
    ],
)
def test_any_change_of_the_load_makes_the_preview_stale(  # type: ignore[no-untyped-def]
    as_role, loaded, helper, db: Session, change
) -> None:
    sid = loaded["settlement"]
    token = preview_token(as_role("operator"), sid)
    if change.get("employee_id") == "HELPER":
        change = {"employee_id": helper}
    if change.get("vehicle_id") == "OTHER":
        as_role("admin")
        change = {"vehicle_id": vehicle(db, "AC456DE")}
    operator = as_role("operator")
    patched = operator.patch(f"{BASE}/deliveries/{sid}/expenses/{loaded['expense']}", json={"fuel": change})
    assert patched.status_code == 200, patched.text
    assert preview_token(operator, sid) != token
    assert error(close(operator, sid, token=token)) == (409, "settlement_preview_stale")


def test_reopen_change_fuel_and_close_again_replaces_the_close(as_role, loaded, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    sid = loaded["settlement"]
    first = close(as_role("operator"), sid).json()["close_in_effect"]["operation_id"]
    operations = db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one()
    assert reopen(as_role("admin"), sid, 1).status_code == 200
    assert db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one() == operations

    operator = as_role("operator")
    patched = operator.patch(f"{BASE}/deliveries/{sid}/expenses/{loaded['expense']}", json={"fuel": {"liters": "9.5"}})
    assert last_expense(patched)["amount"] == "19000.00"
    response = close(operator, sid, {"target": "EMPLOYEE", "employee_id": pedro, "amount": "1000"}, version=2)
    assert response.status_code == 201, response.text
    second = response.json()["close_in_effect"]["operation_id"]
    rows = db.execute(
        text("SELECT id, type, reverses_operation_id FROM settlements.financial_operations ORDER BY id")
    ).all()
    assert [(r.type, r.reverses_operation_id) for r in rows] == [
        ("SETTLEMENT_CLOSE", None),
        ("REVERSAL", first),
        ("SETTLEMENT_CLOSE", None),
    ]
    assert legs(db, second)["expense"] == "19000.00"
    assert legs(db, first + 1)["expense"] == "-20000.00"


def test_a_viewer_cannot_load_fuel(as_role, fuel_id, sid, truck) -> None:  # type: ignore[no-untyped-def]
    assert add_fuel(as_role("viewer"), sid, fuel_id, fuel(truck, "10", "1000")).status_code == 403
