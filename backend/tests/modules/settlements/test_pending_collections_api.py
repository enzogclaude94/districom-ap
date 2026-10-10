"""Pending collections in the settlement and in its close (R3-1).

The numbers in brackets are the tests Enzo asked for R3-1; "Caso A" and "Caso B" are his two scenarios of
a correction in progress. The database rules are in `test_pending_collections_db_invariants.py`.

Every scenario ends checking the invariant [20]: the balance of "Pendientes de cobro" is the sum of the
amounts in effect (what the close in effect of each settlement published).
"""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import yesterday

BASE = "/api/v1/settlements"
CUSTOMER = "Almacén Don José"


# --- helpers --------------------------------------------------------------------------------------


def create(client: TestClient, employee_id: int, expected: str) -> int:
    response = client.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": yesterday().isoformat(),
            "employee_id": employee_id,
            "expected_amount": expected,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def add_pending(client: TestClient, sid: int, amount: str = "100000", **fields: Any) -> Any:
    body = {
        "client_request_id": str(fields.pop("client_request_id", uuid4())),
        "customer_name": CUSTOMER,
        "amount": amount,
        "reason": "Paga mañana por transferencia",
        **fields,
    }
    return client.post(f"{BASE}/deliveries/{sid}/pending-collections", json=body)


def new_pending(client: TestClient, sid: int, amount: str = "100000", **fields: Any) -> int:
    response = add_pending(client, sid, amount, **fields)
    assert response.status_code == 201, response.text
    return response.json()["pending_collections"][-1]["id"]  # type: ignore[no-any-return]


def patch_pending(client: TestClient, sid: int, pid: int, **fields: Any) -> Any:
    return client.patch(f"{BASE}/deliveries/{sid}/pending-collections/{pid}", json=fields)


def void_pending(client: TestClient, sid: int, pid: int, reason: str = "El cliente pagó en el momento") -> Any:
    return client.post(f"{BASE}/deliveries/{sid}/pending-collections/{pid}/void", json={"reason": reason})


def pending_of(client: TestClient, sid: int, pid: int) -> dict[str, Any]:
    body = client.get(f"{BASE}/deliveries/{sid}").json()
    return next(p for p in body["pending_collections"] if p["id"] == pid)  # type: ignore[no-any-return]


def preview(client: TestClient, sid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/deliveries/{sid}/closing-preview")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def close(client: TestClient, sid: int, version: int = 1) -> Any:
    token = preview(client, sid)["preview_token"]
    return client.post(
        f"{BASE}/deliveries/{sid}/close",
        json={"version": version, "preview_token": token, "allocations": [], "confirm": True},
    )


def closed(client: TestClient, sid: int, version: int = 1) -> dict[str, Any]:
    response = close(client, sid, version)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def reopen(client: TestClient, sid: int, version: int) -> None:
    response = client.post(
        f"{BASE}/deliveries/{sid}/reopen", json={"version": version, "reason": "Corregir el pendiente"}
    )
    assert response.status_code == 200, response.text


def add_cash(client: TestClient, sid: int, amount: str, box: int) -> None:
    response = client.post(
        f"{BASE}/deliveries/{sid}/additions",
        json={"client_request_id": str(uuid4()), "amount": amount, "destination_account_id": box},
    )
    assert response.status_code == 201, response.text


def global_list(client: TestClient) -> dict[int, dict[str, Any]]:
    response = client.get(f"{BASE}/pending-collections")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"])
    return {p["id"]: p for p in body["items"]}


def detail(client: TestClient, pid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/pending-collections/{pid}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def account_balance(client: TestClient) -> str:
    return next(a["balance"] for a in client.get(f"{BASE}/accounts").json() if a["code"] == "pending_collections")


def assert_account_is_sum_in_effect(client: TestClient, db: Session) -> None:
    """[20] "Pendientes de cobro" = Σ amounts in effect, computed apart from the API and the ledger."""
    in_effect = db.execute(
        text(
            "SELECT coalesce(sum(cp.amount), 0) FROM settlements.settlement_closing_pendings cp "
            "JOIN settlements.settlements s ON s.current_close_operation_id = cp.close_operation_id"
        )
    ).scalar_one()
    db.rollback()
    assert Decimal(account_balance(client)) == in_effect
    assert sum((Decimal(p["balance"]) for p in global_list(client).values()), Decimal(0)) == in_effect


def event_types(client: TestClient, pid: int) -> list[str]:
    return [e["event_type"] for e in detail(client, pid)["events"]]


def ledger_legs(db: Session, operation_id: int) -> dict[str, str]:
    rows = db.execute(
        text(
            "SELECT a.code, e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :o"
        ),
        {"o": operation_id},
    )
    return {code: str(amount) for code, amount in rows.all()}


@pytest.fixture
def ids(as_role) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {a["code"]: a["id"] for a in as_role("admin").get(f"{BASE}/accounts").json()}


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def juan(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Juan Cobrador", function="settlements.other")  # type: ignore[no-any-return]


@pytest.fixture
def million(as_role, ids, pedro) -> int:  # type: ignore[no-untyped-def]
    """Enzo's example: $1.000.000 expected, $800.000 cash, $100.000 verified at Banco; no pending yet."""
    operator = as_role("operator")
    sid = create(operator, pedro, "1000000")
    response = operator.put(
        f"{BASE}/deliveries/{sid}/count",
        json={
            "lines": [{"denomination_code": "ARS_20000", "quantity": 40, "destination_account_id": ids["main_cash"]}]
        },
    )
    assert response.status_code == 200, response.text
    sent = operator.post(
        f"{BASE}/deliveries/{sid}/transfers", json={"client_request_id": str(uuid4()), "amount": "100000"}
    )
    tid = sent.json()["transfers"][-1]["id"]
    verified = operator.post(f"{BASE}/deliveries/{sid}/transfers/{tid}/status", json={"status": "VERIFIED"})
    assert verified.status_code == 200, verified.text
    return sid


@pytest.fixture
def closed_with_pending(as_role, million) -> tuple[int, int]:  # type: ignore[no-untyped-def]
    """The example closed (v1) with a $100.000 pending in effect."""
    operator = as_role("operator")
    pid = new_pending(operator, million)
    closed(operator, million)
    return million, pid


# --- load -----------------------------------------------------------------------------------------


def test_creation_is_idempotent(as_role, million, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[1] The same request twice is one pending, with one CREATED event."""
    operator = as_role("operator")
    request_id = uuid4()
    first = add_pending(operator, million, client_request_id=request_id)
    again = add_pending(operator, million, client_request_id=request_id)
    assert (first.status_code, again.status_code) == (201, 200)
    assert len(again.json()["pending_collections"]) == 1
    pid = first.json()["pending_collections"][0]["id"]
    assert event_types(operator, pid) == ["CREATED"]
    count = db.execute(text("SELECT count(*) FROM settlements.pending_collections")).scalar_one()
    assert count == 1


def test_same_request_id_with_other_content_is_a_conflict(as_role, million) -> None:  # type: ignore[no-untyped-def]
    """[2]"""
    operator = as_role("operator")
    request_id = uuid4()
    assert add_pending(operator, million, client_request_id=request_id).status_code == 201
    other = add_pending(operator, million, "90000", client_request_id=request_id)
    assert error(other) == (409, "idempotency_conflict")


def test_responsible_defaults_to_the_driver(as_role, million, pedro) -> None:  # type: ignore[no-untyped-def]
    """[3]"""
    operator = as_role("operator")
    pending = pending_of(operator, million, new_pending(operator, million))
    assert (pending["responsible_employee_id"], pending["responsible_name"]) == (pedro, "Pedro Gómez")


def test_another_active_employee_can_be_responsible(as_role, million, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[4] Any active employee, not only drivers; an inactive or unknown one is refused."""
    operator = as_role("operator")
    pending = pending_of(operator, million, new_pending(operator, million, responsible_employee_id=juan))
    assert (pending["responsible_employee_id"], pending["responsible_name"]) == (juan, "Juan Cobrador")

    assert error(add_pending(operator, million, responsible_employee_id=999999)) == (422, "employee_not_found")
    db.execute(text("UPDATE employees SET active = false WHERE id = :id"), {"id": juan})
    db.commit()
    assert error(add_pending(operator, million, responsible_employee_id=juan)) == (422, "employee_not_found")


def test_expected_date_is_optional(as_role, million) -> None:  # type: ignore[no-untyped-def]
    """[5] Without a date, with one, never before the settlement's date; it can be cleared."""
    operator = as_role("operator")
    without = pending_of(operator, million, new_pending(operator, million))
    assert without["expected_date"] is None
    later = (yesterday() + timedelta(days=3)).isoformat()
    pid = new_pending(operator, million, "1000", expected_date=later)
    assert pending_of(operator, million, pid)["expected_date"] == later
    before = (yesterday() - timedelta(days=1)).isoformat()
    assert error(add_pending(operator, million, expected_date=before)) == (422, "invalid_expected_date")
    assert patch_pending(operator, million, pid, expected_date=None).status_code == 200
    assert pending_of(operator, million, pid)["expected_date"] is None


def test_load_is_validated(as_role, million) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert add_pending(operator, million, customer_name="   ").status_code == 422
    assert add_pending(operator, million, reason="  ").status_code == 422
    assert add_pending(operator, million, "0").status_code == 422
    pid = new_pending(operator, million, customer_name="  Kiosco   El Sol ")
    assert pending_of(operator, million, pid)["customer_name"] == "Kiosco El Sol"
    assert error(patch_pending(operator, million, pid, amount=None)) == (422, "field_required")


def test_only_an_operator_loads_pendings(as_role, million) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    pid = new_pending(operator, million)
    viewer = as_role("viewer")
    assert add_pending(viewer, million).status_code == 403
    assert patch_pending(viewer, million, pid, amount="1").status_code == 403
    assert void_pending(viewer, million, pid).status_code == 403
    assert viewer.get(f"{BASE}/pending-collections/{pid}").status_code == 200


def test_void_needs_a_reason_and_happens_once(as_role, million) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    pid = new_pending(operator, million)
    assert void_pending(operator, million, pid, reason="  ").status_code == 422
    assert void_pending(operator, million, pid).status_code == 200
    assert error(void_pending(operator, million, pid)) == (409, "pending_already_voided")
    assert error(patch_pending(operator, million, pid, amount="1")) == (409, "pending_voided")
    voided = pending_of(operator, million, pid)
    assert (voided["voided"], voided["state"], voided["void_reason"]) == (
        True,
        "VOIDED",
        "El cliente pagó en el momento",
    )


def test_a_draft_is_not_in_effect(as_role, million, db: Session) -> None:  # type: ignore[no-untyped-def]
    """A pending of a settlement never closed is DRAFT: not in Cajas, not in the global list, no alert."""
    operator = as_role("operator")
    pid = new_pending(operator, million)
    pending = pending_of(operator, million, pid)
    assert (pending["state"], pending["amount_in_effect"], pending["balance"], pending["load_change"]) == (
        "DRAFT",
        None,
        None,
        None,
    )
    assert pid not in global_list(operator)
    assert account_balance(operator) == "0.00"


def test_closed_settlement_refuses_load_changes(as_role, closed_with_pending) -> None:  # type: ignore[no-untyped-def]
    sid, pid = closed_with_pending
    operator = as_role("operator")
    assert error(add_pending(operator, sid)) == (409, "settlement_closed")
    assert error(patch_pending(operator, sid, pid, amount="1")) == (409, "settlement_closed")
    assert error(void_pending(operator, sid, pid)) == (409, "settlement_closed")


# --- close ----------------------------------------------------------------------------------------


def test_pending_justifies_the_settlement(as_role, million) -> None:  # type: ignore[no-untyped-def]
    """[6] Esperado 1.000.000 = Caja 800.000 + Banco 100.000 + Pendiente 100.000: difference 0."""
    operator = as_role("operator")
    new_pending(operator, million)
    void = new_pending(operator, million, "5000")
    void_pending(operator, million, void)
    summary = operator.get(f"{BASE}/deliveries/{million}").json()["summary"]
    assert (summary["pending"], summary["justified"], summary["difference"]) == ("100000.00", "1000000.00", "0.00")


def test_close_with_a_pending_is_balanced(as_role, million, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[7] Cobranzas -1.000.000; Caja +800.000; Banco +100.000; Pendientes de cobro +100.000."""
    operator = as_role("operator")
    new_pending(operator, million)
    legs = {leg["account_name"]: leg["amount"] for leg in preview(operator, million)["legs"]}
    assert legs == {
        "Cobranzas de reparto": "-1000000.00",
        "Caja principal": "800000.00",
        "Banco": "100000.00",
        "Pendientes de cobro": "100000.00",
    }
    body = closed(operator, million)
    entries = ledger_legs(db, body["close_in_effect"]["operation_id"])
    assert entries == {
        "delivery_collections": "-1000000.00",
        "main_cash": "800000.00",
        "bank": "100000.00",
        "pending_collections": "100000.00",
    }
    assert sum(Decimal(v) for v in entries.values()) == 0
    assert body["current_difference"] is None
    assert_account_is_sum_in_effect(operator, db)


def test_close_freezes_the_exact_snapshot(as_role, million, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[8] One row per live pending of the close, with its amount and customer; voided ones are not published."""
    operator = as_role("operator")
    pid = new_pending(operator, million, "60000")
    other = new_pending(operator, million, "40000", customer_name="Kiosco El Sol")
    voided = new_pending(operator, million, "1000")
    void_pending(operator, million, voided)
    operation_id = closed(operator, million)["close_in_effect"]["operation_id"]
    rows = db.execute(
        text(
            "SELECT pending_id, amount, customer_name, settlement_version, settlement_id "
            "FROM settlements.settlement_closing_pendings WHERE close_operation_id = :o ORDER BY pending_id"
        ),
        {"o": operation_id},
    ).all()
    assert [tuple(r) for r in rows] == [
        (pid, Decimal("60000.00"), CUSTOMER, 1, million),
        (other, Decimal("40000.00"), "Kiosco El Sol", 1, million),
    ]
    in_effect = pending_of(operator, million, pid)
    assert (in_effect["state"], in_effect["amount_in_effect"], in_effect["balance"], in_effect["collected"]) == (
        "OPEN",
        "60000.00",
        "60000.00",
        "0.00",
    )
    assert pending_of(operator, million, voided)["state"] == "VOIDED"
    assert set(global_list(operator)) == {pid, other}
    assert event_types(operator, pid) == ["CREATED", "PUBLISHED"]
    assert event_types(operator, voided) == ["CREATED", "VOIDED"]
    assert_account_is_sum_in_effect(operator, db)


def test_preview_token_changes_with_every_pending_change(as_role, million, juan) -> None:  # type: ignore[no-untyped-def]
    """[14] Amount, customer, responsible, a new pending and a void each make the preview stale."""
    operator = as_role("operator")
    pid = new_pending(operator, million)
    changes = [
        lambda: patch_pending(operator, million, pid, amount="90000"),
        lambda: patch_pending(operator, million, pid, customer_name="Kiosco El Sol"),
        lambda: patch_pending(operator, million, pid, responsible_employee_id=juan),
        lambda: add_pending(operator, million, "10000"),
        lambda: void_pending(operator, million, pid),
    ]
    for change in changes:
        token = preview(operator, million)["preview_token"]
        assert change().status_code in (200, 201)
        stale = operator.post(
            f"{BASE}/deliveries/{million}/close",
            json={"version": 1, "preview_token": token, "allocations": [], "confirm": True},
        )
        assert error(stale) == (409, "settlement_preview_stale")
    # A change that changes nothing keeps the token.
    kept = new_pending(operator, million, "10")
    token = preview(operator, million)["preview_token"]
    assert patch_pending(operator, million, kept, reason="Paga mañana por transferencia").status_code == 200
    assert preview(operator, million)["preview_token"] == token


def test_preview_lists_the_pendings(as_role, million) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    pid = new_pending(operator, million)
    body = preview(operator, million)
    assert [(p["id"], p["amount"], p["state"]) for p in body["pending_collections"]] == [(pid, "100000.00", "DRAFT")]


# --- correction: what is in effect until the new close ------------------------------------------


def test_case_a_amount_change_during_correction(as_role, ids, closed_with_pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Caso A [10] [12] [13] [15]: $100.000 in effect; the correction loads $70.000 (and $30.000 more cash).

    Before the new close the amount in effect stays $100.000 everywhere; after it, $70.000, with a net
    effect of -$30.000 on "Pendientes de cobro" and the v1 snapshot kept.
    """
    sid, pid = closed_with_pending
    v1_close = as_role("operator").get(f"{BASE}/deliveries/{sid}").json()["close_in_effect"]["operation_id"]
    reopen(as_role("admin"), sid, 1)
    operator = as_role("operator")
    assert patch_pending(operator, sid, pid, amount="70000").status_code == 200
    add_cash(operator, sid, "30000", ids["main_cash"])

    pending = pending_of(operator, sid, pid)
    assert (pending["amount"], pending["amount_in_effect"], pending["balance"], pending["state"]) == (
        "70000.00",
        "100000.00",
        "100000.00",
        "OPEN",
    )
    assert pending["load_change"] == "MODIFIED"
    assert pending["load_change_detail"] == "Modificación pendiente de confirmar en la rendición."
    listed = global_list(operator)[pid]
    assert (listed["amount_in_effect"], listed["balance"], listed["state"]) == ("100000.00", "100000.00", "OPEN")
    assert account_balance(operator) == "100000.00"
    assert_account_is_sum_in_effect(operator, db)

    body = closed(operator, sid, version=2)
    pending = pending_of(operator, sid, pid)
    assert (pending["amount_in_effect"], pending["balance"], pending["load_change"]) == ("70000.00", "70000.00", None)
    assert global_list(operator)[pid]["amount_in_effect"] == "70000.00"
    assert account_balance(operator) == "70000.00"

    # [13] The reversal of v1 and the new close, net -30.000 on the pending account.
    v2_close = body["close_in_effect"]["operation_id"]
    reversal = db.execute(
        text("SELECT id FROM settlements.financial_operations WHERE reverses_operation_id = :o"), {"o": v1_close}
    ).scalar_one()
    net = Decimal(ledger_legs(db, reversal)["pending_collections"]) + Decimal(
        ledger_legs(db, v2_close)["pending_collections"]
    )
    assert net == Decimal("-30000.00")

    # [12] Both snapshots are kept; only v2 is in effect.
    published = detail(operator, pid)["published"]
    assert [(p["settlement_version"], p["amount"], p["in_effect"]) for p in published] == [
        (1, "100000.00", False),
        (2, "70000.00", True),
    ]
    # [15] The amount change is a domain event of v2, then the v2 close published it.
    events = detail(operator, pid)["events"]
    changed = next(e for e in events if e["event_type"] == "AMOUNT_CHANGED")
    assert (changed["before"], changed["after"], changed["settlement_version"]) == (
        {"amount": "100000.00"},
        {"amount": "70000.00"},
        2,
    )
    assert [e["event_type"] for e in events] == ["CREATED", "PUBLISHED", "AMOUNT_CHANGED", "PUBLISHED"]
    assert (events[-1]["before"], events[-1]["after"]) == (
        {"amount": "100000.00", "customer_name": CUSTOMER},
        {"amount": "70000.00", "customer_name": CUSTOMER},
    )
    assert_account_is_sum_in_effect(operator, db)


def test_case_b_marked_for_removal_during_correction(as_role, ids, closed_with_pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Caso B [11] [17]: voided during the correction, it stays in effect (never VOIDED) until the new close."""
    sid, pid = closed_with_pending
    reopen(as_role("admin"), sid, 1)
    operator = as_role("operator")
    assert void_pending(operator, sid, pid, reason="Pagó antes del cierre").status_code == 200
    add_cash(operator, sid, "100000", ids["main_cash"])

    pending = pending_of(operator, sid, pid)
    assert (pending["voided"], pending["state"], pending["amount_in_effect"], pending["balance"]) == (
        True,
        "OPEN",
        "100000.00",
        "100000.00",
    )
    assert pending["load_change"] == "MARKED_FOR_REMOVAL"
    assert pending["load_change_detail"].startswith("Marcado para quitar en la corrección")
    assert global_list(operator)[pid]["state"] == "OPEN"
    assert account_balance(operator) == "100000.00"
    assert_account_is_sum_in_effect(operator, db)

    closed(operator, sid, version=2)
    pending = pending_of(operator, sid, pid)
    assert (pending["state"], pending["amount_in_effect"], pending["balance"], pending["load_change"]) == (
        "VOIDED",
        None,
        None,
        None,
    )
    assert pid not in global_list(operator)
    assert account_balance(operator) == "0.00"
    events = detail(operator, pid)["events"]
    assert [e["event_type"] for e in events] == ["CREATED", "PUBLISHED", "VOIDED", "WITHDRAWN"]
    voided = events[2]
    assert (voided["reason"], voided["settlement_version"]) == ("Pagó antes del cierre", 2)
    assert (events[3]["before"], events[3]["after"]) == ({"amount": "100000.00", "customer_name": CUSTOMER}, None)
    assert_account_is_sum_in_effect(operator, db)


def test_pending_added_during_correction_is_not_in_effect_until_close(
    as_role, closed_with_pending, db: Session
) -> None:  # type: ignore[no-untyped-def]
    """[9] A pending added while correcting is DRAFT ("agregado en la corrección") until the new close."""
    sid, pid = closed_with_pending
    reopen(as_role("admin"), sid, 1)
    operator = as_role("operator")
    assert operator.patch(f"{BASE}/deliveries/{sid}", json={"expected_amount": "1050000"}).status_code == 200
    added = new_pending(operator, sid, "50000", customer_name="Kiosco El Sol")

    pending = pending_of(operator, sid, added)
    assert (pending["state"], pending["amount_in_effect"], pending["load_change"]) == ("DRAFT", None, "ADDED")
    assert set(global_list(operator)) == {pid}
    assert account_balance(operator) == "100000.00"
    assert_account_is_sum_in_effect(operator, db)

    closed(operator, sid, version=2)
    assert pending_of(operator, sid, added)["state"] == "OPEN"
    assert set(global_list(operator)) == {pid, added}
    assert account_balance(operator) == "150000.00"
    assert_account_is_sum_in_effect(operator, db)


def test_customer_change_during_correction_waits_for_the_close(as_role, closed_with_pending) -> None:  # type: ignore[no-untyped-def]
    sid, pid = closed_with_pending
    reopen(as_role("admin"), sid, 1)
    operator = as_role("operator")
    assert patch_pending(operator, sid, pid, customer_name="Kiosco El Sol").status_code == 200
    pending = pending_of(operator, sid, pid)
    assert (pending["customer_name"], pending["customer_in_effect"], pending["load_change"]) == (
        "Kiosco El Sol",
        CUSTOMER,
        "MODIFIED",
    )
    closed(operator, sid, version=2)
    assert pending_of(operator, sid, pid)["customer_in_effect"] == "Kiosco El Sol"
    assert event_types(operator, pid) == ["CREATED", "PUBLISHED", "CUSTOMER_CHANGED", "PUBLISHED"]


def test_responsible_reason_and_date_events(as_role, million, pedro, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[16] Each changed field is its own event, with before and after; nothing else is recorded."""
    operator = as_role("operator")
    pid = new_pending(operator, million)
    later = (yesterday() + timedelta(days=2)).isoformat()
    response = patch_pending(
        operator, million, pid, responsible_employee_id=juan, reason="Paga el viernes", expected_date=later
    )
    assert response.status_code == 200
    events = {e["event_type"]: e for e in detail(operator, pid)["events"]}
    assert set(events) == {"CREATED", "RESPONSIBLE_CHANGED", "REASON_CHANGED", "EXPECTED_DATE_CHANGED"}
    responsible = events["RESPONSIBLE_CHANGED"]
    assert (responsible["before"], responsible["after"], responsible["settlement_version"]) == (
        {"responsible_employee_id": pedro},
        {"responsible_employee_id": juan},
        1,
    )
    assert responsible["created_by_name"] == "Usuario Test"
    assert events["EXPECTED_DATE_CHANGED"]["after"] == {"expected_date": later}
    audit = db.execute(
        text("SELECT count(*) FROM audit_events WHERE event_type = 'settlements.pending_collection.updated'")
    ).scalar_one()
    assert audit == 1


def test_domain_history_does_not_depend_on_audit_events(as_role, ids, closed_with_pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[19] With the audit log emptied, the pending's history is still complete from its own events."""
    sid, pid = closed_with_pending
    reopen(as_role("admin"), sid, 1)
    operator = as_role("operator")
    patch_pending(operator, sid, pid, amount="70000")
    add_cash(operator, sid, "30000", ids["main_cash"])
    closed(operator, sid, version=2)
    db.execute(text("TRUNCATE audit_events"))
    db.commit()

    body = detail(as_role("viewer"), pid)
    assert [(e["event_type"], e["settlement_version"]) for e in body["events"]] == [
        ("CREATED", 1),
        ("PUBLISHED", 1),
        ("AMOUNT_CHANGED", 2),
        ("PUBLISHED", 2),
    ]
    assert [p["amount"] for p in body["published"]] == ["100000.00", "70000.00"]
    assert (body["driver_name"], body["settlement_financial_state"]) == ("Pedro Gómez", "CLOSED")


def test_raising_the_amount_in_a_correction(as_role, closed_with_pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Raising is allowed: net +$20.000 with a higher expected amount."""
    sid, pid = closed_with_pending
    reopen(as_role("admin"), sid, 1)
    operator = as_role("operator")
    assert operator.patch(f"{BASE}/deliveries/{sid}", json={"expected_amount": "1020000"}).status_code == 200
    patch_pending(operator, sid, pid, amount="120000")
    assert account_balance(operator) == "100000.00"
    closed(operator, sid, version=2)
    assert account_balance(operator) == "120000.00"
    assert_account_is_sum_in_effect(operator, db)


def test_a_retry_of_a_close_without_pendings_keeps_its_fingerprint(as_role, ids, pedro) -> None:  # type: ignore[no-untyped-def]
    """A settlement without pendings closes exactly as in R2, and a retry of the close is the same close."""
    operator = as_role("operator")
    sid = create(operator, pedro, "20000")
    operator.put(
        f"{BASE}/deliveries/{sid}/count",
        json={"lines": [{"denomination_code": "ARS_20000", "quantity": 1, "destination_account_id": ids["main_cash"]}]},
    )
    token = preview(operator, sid)["preview_token"]
    body = {"version": 1, "preview_token": token, "allocations": [], "confirm": True}
    assert operator.post(f"{BASE}/deliveries/{sid}/close", json=body).status_code == 201
    assert operator.post(f"{BASE}/deliveries/{sid}/close", json=body).status_code == 200
    assert [leg["account_name"] for leg in preview(operator, sid)["legs"]] == ["Caja principal", "Cobranzas de reparto"]
