"""Delivery settlements API (R2-1): creation, sequence, count, additions, transfers, expenses and board.

The numbers in brackets refer to the tests Enzo asked for R2-1 (the sequence under concurrency, test 1,
is in `test_delivery_concurrency.py`).
"""

from collections.abc import Callable
from datetime import date, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today

BASE = "/api/v1/settlements"
TODAY = business_today()
DAY = TODAY - timedelta(days=1)


def create(client: TestClient, employee_id: int, expected: str = "100000", **body: Any) -> Any:
    body = {
        "client_request_id": str(uuid4()),
        "business_date": DAY.isoformat(),
        "employee_id": employee_id,
        "expected_amount": expected,
        **body,
    }
    return client.post(f"{BASE}/deliveries", json=body)


def post(client: TestClient, path: str, **body: Any) -> Any:
    body.setdefault("client_request_id", str(uuid4()))
    return client.post(f"{BASE}{path}", json=body)


@pytest.fixture
def ids(as_role) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {a["code"]: a["id"] for a in as_role("admin").get(f"{BASE}/accounts").json()}


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def opened(as_role, pedro) -> int:  # type: ignore[no-untyped-def]
    response = create(as_role("operator"), pedro)
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


@pytest.fixture
def category(as_role) -> Callable[[str], int]:  # type: ignore[no-untyped-def]
    def _create(name: str) -> int:
        response = as_role("admin").post(f"{BASE}/expense-categories", json={"name": name})
        assert response.status_code == 201, response.text
        return response.json()["id"]  # type: ignore[no-any-return]

    return _create


def lines(settlement: dict[str, Any]) -> list[tuple[str, str, int, str]]:
    return [
        (line["denomination_code"], line["denomination_value"], line["quantity"], line["destination_account_name"])
        for line in settlement["count_lines"]
    ]


# --- creation and sequence ------------------------------------------------------------------------


def test_operator_creates_a_settlement_numbered_1(as_role, pedro) -> None:  # type: ignore[no-untyped-def]
    response = create(as_role("operator"), pedro, "185000.50", external_reference="HR-77")
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["type"], body["status"], body["version"], body["sequence_number"]) == ("DELIVERY", "OPEN", 1, 1)
    assert (body["employee_name"], body["expected_amount"], body["expected_source"]) == (
        "Pedro Gómez",
        "185000.50",
        "MANUAL",
    )
    assert body["business_date"] == DAY.isoformat()
    assert body["same_day_settlement_ids"] == []
    assert body["summary"]["difference"] == "-185000.50"


def test_retry_with_the_same_request_id_does_not_create_rendicion_2(as_role, pedro) -> None:  # type: ignore[no-untyped-def]
    """[2] A network retry returns the same settlement (200), never a second one."""
    operator = as_role("operator")
    key = str(uuid4())
    first = create(operator, pedro, client_request_id=key)
    again = create(operator, pedro, client_request_id=key)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert again.json()["sequence_number"] == 1
    assert operator.get(f"{BASE}/deliveries").json()["total"] == 1


def test_same_request_id_with_other_content_is_a_conflict(as_role, pedro) -> None:  # type: ignore[no-untyped-def]
    """[3]"""
    operator = as_role("operator")
    key = str(uuid4())
    first = create(operator, pedro, "1000", client_request_id=key)
    conflict = create(operator, pedro, "2000", client_request_id=key)
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "idempotency_conflict"
    assert conflict.json()["details"] == {"settlement_id": first.json()["id"]}
    assert operator.get(f"{BASE}/deliveries").json()["total"] == 1


def test_a_driver_can_have_rendicion_1_and_2_the_same_day(as_role, pedro, driver) -> None:  # type: ignore[no-untyped-def]
    """[4] The number is generated per driver and day, never typed."""
    operator = as_role("operator")
    juan = driver("Juan Pérez")
    first = create(operator, pedro).json()
    second = create(operator, pedro).json()
    other_driver = create(operator, juan).json()
    other_day = create(operator, pedro, business_date=(DAY - timedelta(days=1)).isoformat()).json()
    assert [first["sequence_number"], second["sequence_number"]] == [1, 2]
    assert other_driver["sequence_number"] == 1
    assert other_day["sequence_number"] == 1
    assert second["same_day_settlement_ids"] == [first["id"]]
    listed = operator.get(f"{BASE}/deliveries", params={"employee_id": pedro, "date_from": DAY.isoformat()}).json()
    assert [(i["sequence_number"], i["employee_name"]) for i in listed["items"]] == [
        (1, "Pedro Gómez"),
        (2, "Pedro Gómez"),
    ]


def test_the_number_cannot_be_typed(as_role, pedro) -> None:  # type: ignore[no-untyped-def]
    response = create(as_role("operator"), pedro, sequence_number=5)
    assert response.status_code == 422


def test_only_drivers_of_that_day_can_be_chosen(as_role, driver, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[5] Without `settlements.delivery_driver` on the settlement's date: not listed, not accepted."""
    operator = as_role("operator")
    active = driver("Ana Activa")
    expired = driver("Ernesto Ex", valid_from=date(2026, 1, 1), valid_to=DAY - timedelta(days=1))
    future = driver("Fabio Futuro", valid_from=TODAY)
    salesperson = driver("Sara Ventas", function="settlements.other")
    inactive = driver("Iván Inactivo")
    db.execute(text("UPDATE employees SET active = false WHERE id = :id"), {"id": inactive})
    db.commit()

    listed = operator.get(f"{BASE}/drivers", params={"on": DAY.isoformat()}).json()
    assert listed == [{"id": active, "full_name": "Ana Activa"}]
    for employee_id in (expired, future, salesperson, inactive):
        response = create(operator, employee_id)
        assert response.status_code == 422, employee_id
        assert response.json()["code"] == "employee_not_driver"
    assert create(operator, 999_999).json()["code"] == "employee_not_found"
    # The function that expired yesterday was valid two days ago.
    assert create(operator, expired, business_date=(DAY - timedelta(days=1)).isoformat()).status_code == 201


def test_business_date_cannot_be_future(as_role, pedro) -> None:  # type: ignore[no-untyped-def]
    response = create(as_role("operator"), pedro, business_date=(TODAY + timedelta(days=1)).isoformat())
    assert (response.status_code, response.json()["code"]) == (422, "future_business_date")


def test_roles(as_role, pedro, opened) -> None:  # type: ignore[no-untyped-def]
    viewer = as_role("viewer")
    assert viewer.get(f"{BASE}/deliveries/{opened}").status_code == 200
    assert viewer.get(f"{BASE}/cash-denominations").status_code == 200
    assert viewer.get(f"{BASE}/expense-categories").status_code == 200
    assert create(viewer, pedro).status_code == 403
    assert viewer.put(f"{BASE}/deliveries/{opened}/count", json={"lines": []}).status_code == 403
    assert post(viewer, f"/deliveries/{opened}/additions", amount="1", destination_account_id=1).status_code == 403
    operator = as_role("operator")
    assert operator.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).status_code == 403
    assert operator.put(f"{BASE}/cash-denominations", json={"denominations": []}).status_code in (403, 422)
    assert (
        operator.put(
            f"{BASE}/cash-denominations", json={"denominations": [{"code": "ARS_100", "value": "100"}]}
        ).status_code
        == 403
    )


def test_expected_amount_is_corrected_while_open_and_audited(as_role, opened, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    response = operator.patch(f"{BASE}/deliveries/{opened}", json={"expected_amount": "90000", "notes": " Ruta 3 "})
    assert response.status_code == 200, response.text
    assert (response.json()["expected_amount"], response.json()["notes"]) == ("90000.00", "Ruta 3")
    payload = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = 'settlements.settlement.updated'")
    ).scalar_one()
    assert (payload["before"]["expected_amount"], payload["after"]["expected_amount"]) == ("100000.00", "90000.00")
    assert operator.patch(f"{BASE}/deliveries/{opened}", json={"employee_id": 5}).status_code == 422


# --- denominations --------------------------------------------------------------------------------


def test_initial_denominations_have_stable_codes_and_no_coins(as_role, ids) -> None:  # type: ignore[no-untyped-def]
    """[6] [9] [10] Notes of $100 or more only, each with its own code and a suggested cash box."""
    listed = as_role("viewer").get(f"{BASE}/cash-denominations").json()
    assert [(d["code"], d["value"], d["suggested_destination"]) for d in listed] == [
        ("ARS_20000", "20000.00", "main_cash"),
        ("ARS_10000", "10000.00", "main_cash"),
        ("ARS_2000", "2000.00", "main_cash"),
        ("ARS_1000", "1000.00", "main_cash"),
        ("ARS_500", "500.00", "change_cash"),
        ("ARS_200", "200.00", "change_cash"),
        ("ARS_100", "100.00", "change_cash"),
    ]
    assert listed[0]["suggested_destination_account_id"] == ids["main_cash"]
    assert listed[0]["suggested_destination_name"] == "Caja principal"
    assert all(d["active"] for d in listed)


def test_admin_configures_denominations_without_removing_or_revaluing(as_role, ids, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    current = admin.get(f"{BASE}/cash-denominations").json()
    config = [
        {k: d[k] for k in ("code", "value", "active", "suggested_destination")} | {"active": d["code"] != "ARS_200"}
        for d in current
    ]
    config.append({"code": "ARS_50000", "value": "50000", "active": True, "suggested_destination": None})
    response = admin.put(f"{BASE}/cash-denominations", json={"denominations": config})
    assert response.status_code == 200, response.text
    assert [d["code"] for d in admin.get(f"{BASE}/cash-denominations").json()][-1] == "ARS_50000"
    assert "ARS_200" not in [d["code"] for d in admin.get(f"{BASE}/cash-denominations").json()]
    assert "ARS_200" in [d["code"] for d in admin.get(f"{BASE}/cash-denominations?include_inactive=true").json()]
    assert (
        db.execute(
            text("SELECT count(*) FROM audit_events WHERE event_type = 'settlements.cash_denominations.updated'")
        ).scalar_one()
        == 1
    )

    def rejected(denominations: list[dict[str, Any]]) -> str:
        response = admin.put(f"{BASE}/cash-denominations", json={"denominations": denominations})
        assert response.status_code == 422, response.text
        return str(response.json()["message"])

    assert "no se elimina" in rejected(config[1:])
    revalued = [dict(d, value="999") if d["code"] == "ARS_100" else d for d in config]
    assert "no cambia de valor" in rejected(revalued)
    assert "caja activa" in rejected([dict(d, suggested_destination="bank") for d in config])
    assert "repetidos" in rejected([*config, config[0]])
    assert "mayúsculas" in rejected([*config, {"code": "ars 5", "value": "5"}])
    assert "al menos una" in rejected([dict(d, active=False) for d in config])


# --- cash count -----------------------------------------------------------------------------------


def test_count_with_destination_chosen_line_by_line(as_role, ids, opened) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    count = [
        {"denomination_code": "ARS_20000", "quantity": 3, "destination_account_id": ids["main_cash"]},
        # Against the suggestion (main_cash): the operator decides.
        {"denomination_code": "ARS_1000", "quantity": 7, "destination_account_id": ids["change_cash"]},
        {"denomination_code": "ARS_100", "quantity": 15, "destination_account_id": ids["change_cash"]},
        {"denomination_code": "ARS_500", "quantity": 0, "destination_account_id": ids["change_cash"]},
    ]
    response = operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": count})
    assert response.status_code == 200, response.text
    body = response.json()
    assert lines(body) == [
        ("ARS_20000", "20000.00", 3, "Caja principal"),
        ("ARS_1000", "1000.00", 7, "Caja de cambio"),
        ("ARS_100", "100.00", 15, "Caja de cambio"),
    ]
    assert body["summary"]["cash_counted"] == "68500.00"
    assert [(b["account_name"], b["counted"]) for b in body["summary"]["cash_by_box"]] == [
        ("Caja principal", "60000.00"),
        ("Caja de cambio", "8500.00"),
    ]
    # Replacing the count: the new one is the whole count.
    response = operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": count[:1]})
    assert lines(response.json()) == [("ARS_20000", "20000.00", 3, "Caja principal")]


def test_count_rejects_bad_lines(as_role, ids, opened) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")

    def put(*lines: dict[str, Any]) -> Any:
        return operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": list(lines)}).json()["code"]

    line = {"denomination_code": "ARS_1000", "quantity": 1, "destination_account_id": ids["main_cash"]}
    assert put(dict(line, destination_account_id=ids["bank"])) == "invalid_destination"
    assert put(dict(line, denomination_code="ARS_5")) == "invalid_denomination"
    assert put(line, line) == "duplicate_denomination"
    assert put(dict(line, quantity=-1)) == "validation_error"


def test_configuration_changes_never_touch_historical_counts(as_role, ids, opened, pedro) -> None:  # type: ignore[no-untyped-def]
    """[7] [8] Deactivating a denomination or changing its suggestion keeps every counted line as it was."""
    operator = as_role("operator")
    count = [
        {"denomination_code": "ARS_1000", "quantity": 4, "destination_account_id": ids["main_cash"]},
        {"denomination_code": "ARS_500", "quantity": 2, "destination_account_id": ids["change_cash"]},
    ]
    before = lines(operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": count}).json())

    admin = as_role("admin")
    config = [
        {k: d[k] for k in ("code", "value", "active", "suggested_destination")}
        for d in admin.get(f"{BASE}/cash-denominations").json()
    ]
    for d in config:
        if d["code"] == "ARS_1000":
            d["active"] = False
        if d["code"] == "ARS_500":
            d["suggested_destination"] = "main_cash"
    assert admin.put(f"{BASE}/cash-denominations", json={"denominations": config}).status_code == 200

    operator = as_role("operator")
    after = operator.get(f"{BASE}/deliveries/{opened}").json()
    assert lines(after) == before
    assert after["summary"]["cash_counted"] == "5000.00"
    # The open count can be saved again with the denomination it already had...
    again = operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": count})
    assert again.status_code == 200
    assert lines(again.json()) == before
    # ...but a new count cannot use a deactivated denomination.
    other = create(operator, pedro).json()["id"]
    response = operator.put(f"{BASE}/deliveries/{other}/count", json={"lines": count})
    assert response.json()["code"] == "invalid_denomination"


# --- additions ------------------------------------------------------------------------------------


def test_an_addition_never_changes_the_count(as_role, ids, opened) -> None:  # type: ignore[no-untyped-def]
    """[11]"""
    operator = as_role("operator")
    count = [{"denomination_code": "ARS_10000", "quantity": 5, "destination_account_id": ids["main_cash"]}]
    counted = operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": count}).json()
    response = post(
        operator,
        f"/deliveries/{opened}/additions",
        amount="25000",
        destination_account_id=ids["main_cash"],
        description="Apareció en la guantera",
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert lines(body) == lines(counted)
    (addition,) = body["additions"]
    assert (addition["amount"], addition["destination_account_name"], addition["description"]) == (
        "25000.00",
        "Caja principal",
        "Apareció en la guantera",
    )
    assert addition["created_by_name"] == "Usuario Test"
    assert addition["voided"] is False
    summary = body["summary"]
    assert (summary["cash_counted"], summary["cash_additions"], summary["cash_total"]) == (
        "50000.00",
        "25000.00",
        "75000.00",
    )
    assert summary["cash_by_box"] == [
        {
            "account_id": ids["main_cash"],
            "account_name": "Caja principal",
            "counted": "50000.00",
            "additions": "25000.00",
            "total": "75000.00",
        }
    ]


def test_addition_is_idempotent_and_needs_a_cash_box(as_role, ids, opened) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    key = str(uuid4())
    first = post(
        operator,
        f"/deliveries/{opened}/additions",
        client_request_id=key,
        amount="10",
        destination_account_id=ids["change_cash"],
    )
    again = post(
        operator,
        f"/deliveries/{opened}/additions",
        client_request_id=key,
        amount="10",
        destination_account_id=ids["change_cash"],
    )
    other = post(
        operator,
        f"/deliveries/{opened}/additions",
        client_request_id=key,
        amount="11",
        destination_account_id=ids["change_cash"],
    )
    assert (first.status_code, again.status_code, other.status_code) == (201, 200, 409)
    assert len(again.json()["additions"]) == 1
    bank = post(operator, f"/deliveries/{opened}/additions", amount="10", destination_account_id=ids["bank"])
    assert bank.json()["code"] == "invalid_destination"


def test_voided_addition_keeps_its_full_history(as_role, ids, opened, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[12]"""
    operator = as_role("operator")
    created = post(operator, f"/deliveries/{opened}/additions", amount="25000", destination_account_id=ids["main_cash"])
    addition_id = created.json()["additions"][0]["id"]
    void = f"{BASE}/deliveries/{opened}/additions/{addition_id}/void"
    assert operator.post(void, json={"reason": "   "}).json()["code"] == "reason_required"
    response = operator.post(void, json={"reason": "Cargado dos veces"})
    assert response.status_code == 200, response.text
    (addition,) = response.json()["additions"]
    assert addition["voided"] is True
    assert (addition["amount"], addition["void_reason"], addition["voided_by_name"]) == (
        "25000.00",
        "Cargado dos veces",
        "Usuario Test",
    )
    assert addition["voided_at"] is not None
    assert response.json()["summary"]["cash_additions"] == "0.00"
    assert operator.post(void, json={"reason": "otra vez"}).json()["code"] == "addition_already_voided"
    events = db.execute(
        text("SELECT event_type FROM audit_events WHERE entity_type = 'cash_addition' ORDER BY id")
    ).scalars()
    assert list(events) == ["settlements.cash_addition.created", "settlements.cash_addition.voided"]


# --- transfers ------------------------------------------------------------------------------------


def _transfer(client: TestClient, settlement_id: int, amount: str, **body: Any) -> int:
    response = post(client, f"/deliveries/{settlement_id}/transfers", amount=amount, **body)
    assert response.status_code == 201, response.text
    return response.json()["transfers"][-1]["id"]  # type: ignore[no-any-return]


def _status(client: TestClient, settlement_id: int, transfer_id: int, status: str, reason: str | None = None) -> Any:
    return client.post(
        f"{BASE}/deliveries/{settlement_id}/transfers/{transfer_id}/status", json={"status": status, "reason": reason}
    )


def test_only_verified_transfers_justify_money(as_role, ids, opened) -> None:  # type: ignore[no-untyped-def]
    """[13] [14] [15]"""
    operator = as_role("operator")
    declared = _transfer(operator, opened, "1000", payer_name="Almacén Don José", reference="OP-1")
    verified = _transfer(operator, opened, "2000")
    rejected = _transfer(operator, opened, "4000")
    body = operator.get(f"{BASE}/deliveries/{opened}").json()
    assert body["transfers"][0]["bank_account_name"] == "Banco"
    assert body["summary"]["justified"] == "0.00"
    assert body["summary"]["transfers_declared"] == "7000.00"

    assert _status(operator, opened, verified, "VERIFIED").status_code == 200
    assert _status(operator, opened, rejected, "REJECTED").json()["code"] == "reason_required"
    summary = _status(operator, opened, rejected, "REJECTED", "No llegó al banco").json()["summary"]
    assert (summary["transfers_declared"], summary["transfers_verified"], summary["transfers_rejected"]) == (
        "1000.00",
        "2000.00",
        "4000.00",
    )
    assert summary["declared_transfers_count"] == 1
    assert summary["justified"] == "2000.00"
    transfer = next(t for t in operator.get(f"{BASE}/deliveries/{opened}").json()["transfers"] if t["id"] == verified)
    assert (transfer["status"], transfer["status_changed_by_name"]) == ("VERIFIED", "Usuario Test")
    assert transfer["status_changed_at"] is not None
    assert declared


def test_transfer_data_changes_only_while_declared(as_role, ids, opened) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    transfer = _transfer(operator, opened, "1000")
    path = f"{BASE}/deliveries/{opened}/transfers/{transfer}"
    assert operator.patch(path, json={"amount": "1500"}).json()["transfers"][0]["amount"] == "1500.00"
    assert operator.patch(path, json={"bank_account_id": ids["main_cash"]}).json()["code"] == "invalid_bank_account"
    _status(operator, opened, transfer, "VERIFIED")
    assert operator.patch(path, json={"amount": "1"}).json()["code"] == "transfer_already_decided"


def test_admin_corrects_a_decision_with_a_reason_and_keeps_the_previous_one(as_role, opened, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[16] Operators verify or reject; only an admin changes a decision taken, always with a reason."""
    operator = as_role("operator")
    transfer = _transfer(operator, opened, "3000")
    _status(operator, opened, transfer, "VERIFIED")
    assert _status(operator, opened, transfer, "REJECTED", "me equivoqué").status_code == 403
    assert _status(operator, opened, transfer, "VERIFIED").status_code == 200  # same status: nothing changes

    admin = as_role("admin")
    assert _status(admin, opened, transfer, "DECLARED").json()["code"] == "reason_required"
    assert _status(admin, opened, transfer, "REJECTED", "El banco la devolvió").status_code == 200
    body = _status(admin, opened, transfer, "DECLARED", "Revisar con el cliente").json()
    (row,) = body["transfers"]
    assert row["status"] == "DECLARED"
    assert [(e["from_status"], e["to_status"], e["reason"], e["is_correction"]) for e in row["events"]] == [
        (None, "DECLARED", None, False),
        ("DECLARED", "VERIFIED", None, False),
        ("VERIFIED", "REJECTED", "El banco la devolvió", True),
        ("REJECTED", "DECLARED", "Revisar con el cliente", True),
    ]
    assert body["summary"]["transfers_verified"] == "0.00"
    audited = db.execute(
        text(
            "SELECT payload->>'action', payload->'before'->>'status', payload->'after'->>'status' FROM audit_events "
            "WHERE event_type = 'settlements.transfer.status_changed' ORDER BY id"
        )
    ).all()
    assert [tuple(r) for r in audited] == [
        ("decide", "DECLARED", "VERIFIED"),
        ("correct", "VERIFIED", "REJECTED"),
        ("correct", "REJECTED", "DECLARED"),
    ]


# --- expenses -------------------------------------------------------------------------------------


def test_expenses_justify_money_and_are_voided_not_deleted(as_role, opened, category) -> None:  # type: ignore[no-untyped-def]
    """[17]"""
    tolls = category("Peajes")
    repairs = category("Gomería")
    operator = as_role("operator")
    response = post(operator, f"/deliveries/{opened}/expenses", category_id=tolls, amount="3500", description="Ruta 9")
    assert response.status_code == 201, response.text
    expense = response.json()["expenses"][0]
    assert (expense["category_name"], expense["amount"], expense["description"]) == ("Peajes", "3500.00", "Ruta 9")
    assert response.json()["summary"]["expenses"] == "3500.00"
    assert response.json()["summary"]["justified"] == "3500.00"

    path = f"{BASE}/deliveries/{opened}/expenses/{expense['id']}"
    changed = operator.patch(path, json={"category_id": repairs, "amount": "4000"}).json()
    assert (changed["expenses"][0]["category_name"], changed["summary"]["expenses"]) == ("Gomería", "4000.00")
    voided = operator.post(f"{path}/void", json={"reason": "Lo pagó la empresa"}).json()
    assert voided["expenses"][0]["voided"] is True
    assert voided["summary"]["expenses"] == "0.00"
    assert operator.patch(path, json={"amount": "1"}).json()["code"] == "expense_voided"


def test_expense_categories_are_edited_by_an_admin(as_role, opened, category) -> None:  # type: ignore[no-untyped-def]
    tolls = category("Peajes")
    admin = as_role("admin")
    duplicate = admin.post(f"{BASE}/expense-categories", json={"name": "  peajes "})
    assert (duplicate.status_code, duplicate.json()["code"]) == (409, "expense_category_name_taken")
    assert admin.patch(f"{BASE}/expense-categories/{tolls}", json={"active": False}).json()["active"] is False
    assert admin.get(f"{BASE}/expense-categories").json() == []
    assert [c["name"] for c in admin.get(f"{BASE}/expense-categories?include_inactive=true").json()] == ["Peajes"]
    response = post(as_role("operator"), f"/deliveries/{opened}/expenses", category_id=tolls, amount="10")
    assert response.json()["code"] == "invalid_expense_category"


# --- closed settlements and the board --------------------------------------------------------------


def test_a_closed_settlement_admits_no_load_changes(as_role, ids, opened, pedro, category) -> None:  # type: ignore[no-untyped-def]
    """[18] Once closed (R2-2), nothing of its load changes."""
    tolls = category("Peajes")
    operator = as_role("operator")
    addition = post(operator, f"/deliveries/{opened}/additions", amount="5", destination_account_id=ids["main_cash"])
    addition_id = addition.json()["additions"][0]["id"]
    transfer = _transfer(operator, opened, "7")
    expense = post(operator, f"/deliveries/{opened}/expenses", category_id=tolls, amount="3").json()["expenses"][0]
    assert _status(operator, opened, transfer, "VERIFIED").status_code == 200
    allocation = {"target": "EMPLOYEE", "employee_id": pedro, "amount": "99985"}
    closed = operator.post(
        f"{BASE}/deliveries/{opened}/close", json={"version": 1, "allocations": [allocation], "confirm": True}
    )
    assert closed.status_code == 201, closed.text

    path = f"{BASE}/deliveries/{opened}"
    admin = as_role("admin")
    attempts = [
        admin.patch(path, json={"expected_amount": "1"}),
        admin.put(f"{path}/count", json={"lines": []}),
        post(admin, f"/deliveries/{opened}/additions", amount="1", destination_account_id=ids["main_cash"]),
        admin.post(f"{path}/additions/{addition_id}/void", json={"reason": "x"}),
        post(admin, f"/deliveries/{opened}/transfers", amount="1"),
        admin.patch(f"{path}/transfers/{transfer}", json={"amount": "2"}),
        _status(admin, opened, transfer, "VERIFIED"),
        post(admin, f"/deliveries/{opened}/expenses", category_id=tolls, amount="1"),
        admin.patch(f"{path}/expenses/{expense['id']}", json={"amount": "2"}),
        admin.post(f"{path}/expenses/{expense['id']}/void", json={"reason": "x"}),
    ]
    assert [(r.status_code, r.json()["code"]) for r in attempts] == [(409, "settlement_closed")] * len(attempts)
    body = admin.get(path).json()
    assert body["status"] == "CLOSED"
    assert body["summary"]["justified"] == "15.00"


def test_the_board_is_computed_by_the_backend(as_role, ids, opened, category) -> None:  # type: ignore[no-untyped-def]
    """[19] Every official total comes from the API; DECLARED/REJECTED and voided rows never count."""
    tolls = category("Peajes")
    operator = as_role("operator")
    count = [
        {"denomination_code": "ARS_20000", "quantity": 2, "destination_account_id": ids["main_cash"]},
        {"denomination_code": "ARS_200", "quantity": 10, "destination_account_id": ids["change_cash"]},
    ]
    operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": count})
    post(operator, f"/deliveries/{opened}/additions", amount="5000", destination_account_id=ids["change_cash"])
    voided = post(operator, f"/deliveries/{opened}/additions", amount="9999", destination_account_id=ids["main_cash"])
    void_id = voided.json()["additions"][-1]["id"]
    operator.post(f"{BASE}/deliveries/{opened}/additions/{void_id}/void", json={"reason": "error"})
    verified = _transfer(operator, opened, "30000")
    _status(operator, opened, verified, "VERIFIED")
    _transfer(operator, opened, "8000")
    post(operator, f"/deliveries/{opened}/expenses", category_id=tolls, amount="1500")

    summary = operator.get(f"{BASE}/deliveries/{opened}").json()["summary"]
    assert summary == {
        "expected": "100000.00",
        "cash_counted": "42000.00",
        "cash_additions": "5000.00",
        "cash_total": "47000.00",
        "cash_by_box": [
            {
                "account_id": ids["main_cash"],
                "account_name": "Caja principal",
                "counted": "40000.00",
                "additions": "0.00",
                "total": "40000.00",
            },
            {
                "account_id": ids["change_cash"],
                "account_name": "Caja de cambio",
                "counted": "2000.00",
                "additions": "5000.00",
                "total": "7000.00",
            },
        ],
        "transfers_verified": "30000.00",
        "transfers_declared": "8000.00",
        "transfers_rejected": "0.00",
        "declared_transfers_count": 1,
        "cheques": "0.00",
        "pending": "0.00",
        "expenses": "1500.00",
        "justified": "78500.00",
        "difference": "-21500.00",
        "difference_kind": "SHORTAGE",
    }
    (item,) = operator.get(f"{BASE}/deliveries").json()["items"]
    assert (item["justified"], item["difference"]) == ("78500.00", "-21500.00")


def test_r2_1_never_writes_the_ledger(as_role, ids, opened, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    count = [{"denomination_code": "ARS_1000", "quantity": 1, "destination_account_id": ids["main_cash"]}]
    operator.put(f"{BASE}/deliveries/{opened}/count", json={"lines": count})
    post(operator, f"/deliveries/{opened}/additions", amount="1", destination_account_id=ids["main_cash"])
    _status(operator, opened, _transfer(operator, opened, "1"), "VERIFIED")
    assert db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one() == 0
    assert all(a["balance"] == "0.00" for a in operator.get(f"{BASE}/accounts").json())


def test_unknown_settlement_is_404(as_role) -> None:  # type: ignore[no-untyped-def]
    response = as_role("viewer").get(f"{BASE}/deliveries/999999")
    assert (response.status_code, response.json()["code"]) == (404, "settlement_not_found")
