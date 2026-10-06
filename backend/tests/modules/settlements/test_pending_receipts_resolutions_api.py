"""Receipts, resolutions, their reversals, the responsible after the close and the alerts (R3-2).

The numbers in brackets are the tests Enzo asked for R3-2. Concurrency ([35], [36]) is in
`test_settlement_close_concurrency.py`; what only the database guarantees ([34], [45] in SQL) in
`test_pending_collections_db_invariants.py`.

Every scenario that moves money ends checking the three invariants [53]-[55] against the rows, apart
from the API: "Pendientes de cobro" = Σ balances in effect, "Incobrables" = Σ company parts and each
employee's account = Σ their parts (of resolutions not reversed).
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.pending import listing
from app.platform.settings.service import set_setting

BASE = "/api/v1/settlements"
TODAY = business_today()
DAY = TODAY - timedelta(days=1)
CUSTOMER = "Almacén Don José"
D = Decimal


# --- helpers --------------------------------------------------------------------------------------


def create(client: TestClient, employee_id: int, expected: str) -> int:
    response = client.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": DAY.isoformat(),
            "employee_id": employee_id,
            "expected_amount": expected,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def count(client: TestClient, sid: int, code: str, quantity: int, box: int) -> None:
    response = client.put(
        f"{BASE}/deliveries/{sid}/count",
        json={"lines": [{"denomination_code": code, "quantity": quantity, "destination_account_id": box}]},
    )
    assert response.status_code == 200, response.text


def new_pending(client: TestClient, sid: int, amount: str = "100000", **fields: Any) -> int:
    response = client.post(
        f"{BASE}/deliveries/{sid}/pending-collections",
        json={
            "client_request_id": str(uuid4()),
            "customer_name": fields.pop("customer_name", CUSTOMER),
            "amount": amount,
            "reason": "Paga mañana",
            **fields,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["pending_collections"][-1]["id"]  # type: ignore[no-any-return]


def preview_token(client: TestClient, sid: int) -> str:
    response = client.get(f"{BASE}/deliveries/{sid}/closing-preview")
    assert response.status_code == 200, response.text
    return response.json()["preview_token"]  # type: ignore[no-any-return]


def close(client: TestClient, sid: int, version: int = 1, token: str | None = None, **body: Any) -> Any:
    return client.post(
        f"{BASE}/deliveries/{sid}/close",
        json={
            "version": version,
            "preview_token": token or preview_token(client, sid),
            "allocations": body.pop("allocations", []),
            "confirm": True,
        },
    )


def closed(client: TestClient, sid: int, version: int = 1, **body: Any) -> None:
    response = close(client, sid, version, **body)
    assert response.status_code == 201, response.text


def reopen(admin: TestClient, sid: int, version: int) -> None:
    response = admin.post(f"{BASE}/deliveries/{sid}/reopen", json={"version": version, "reason": "Corrección"})
    assert response.status_code == 200, response.text


def receipt(
    client: TestClient,
    pid: int,
    amount: str,
    destination: int,
    method: str = "CASH",
    business_date: Any = None,
    request_id: UUID | None = None,
) -> Any:
    body: dict[str, Any] = {
        "client_request_id": str(request_id or uuid4()),
        "amount": amount,
        "method": method,
        "destination_account_id": destination,
    }
    if business_date is not None:
        body["business_date"] = business_date.isoformat()
    return client.post(f"{BASE}/pending-collections/{pid}/receipts", json=body)


def received(client: TestClient, pid: int, amount: str, destination: int, **fields: Any) -> dict[str, Any]:
    response = receipt(client, pid, amount, destination, **fields)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def resolve(
    client: TestClient,
    pid: int,
    amount: str,
    parts: list[tuple[int | None, str]],
    reason: str = "El cliente cerró el negocio",
    business_date: Any = None,
    request_id: UUID | None = None,
) -> Any:
    body: dict[str, Any] = {
        "client_request_id": str(request_id or uuid4()),
        "amount": amount,
        "reason": reason,
        "allocations": [
            {"target": "COMPANY", "amount": part}
            if employee is None
            else {"target": "EMPLOYEE", "employee_id": employee, "amount": part}
            for employee, part in parts
        ],
    }
    if business_date is not None:
        body["business_date"] = business_date.isoformat()
    return client.post(f"{BASE}/pending-collections/{pid}/resolutions", json=body)


def resolved(client: TestClient, pid: int, amount: str, parts: list[tuple[int | None, str]], **kw: Any) -> Any:
    response = resolve(client, pid, amount, parts, **kw)
    assert response.status_code == 201, response.text
    return response.json()


def reverse(
    client: TestClient, pid: int, kind: str, row_id: int, request_id: UUID | None = None, reason: str = "Estaba mal"
) -> Any:
    return client.post(
        f"{BASE}/pending-collections/{pid}/{kind}/{row_id}/reverse",
        json={"client_request_id": str(request_id or uuid4()), "reason": reason},
    )


def detail(client: TestClient, pid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/pending-collections/{pid}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def listed(client: TestClient, **params: Any) -> dict[int, dict[str, Any]]:
    response = client.get(f"{BASE}/pending-collections", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"])
    return {p["id"]: p for p in body["items"]}


def summary(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{BASE}/pending-collections/summary")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def balances(client: TestClient) -> dict[str, Decimal]:
    return {a["code"]: D(a["balance"]) for a in client.get(f"{BASE}/accounts").json() if a["code"]}


def employee_balance(db: Session, employee_id: int) -> Decimal:
    value = db.execute(
        text(
            "SELECT coalesce(sum(e.amount), 0) FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.employee_id = :e"
        ),
        {"e": employee_id},
    ).scalar_one()
    db.rollback()
    return D(value)


def operation(db: Session, operation_id: int) -> dict[str, Any]:
    row = db.execute(
        text(
            "SELECT type, business_date, created_at, source_type, source_id, reverses_operation_id "
            "FROM settlements.financial_operations WHERE id = :o"
        ),
        {"o": operation_id},
    ).one()
    legs = db.execute(
        text(
            "SELECT coalesce(a.code, 'employee:' || a.employee_id), e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :o"
        ),
        {"o": operation_id},
    ).all()
    db.rollback()
    return {**row._asdict(), "legs": {code: str(amount) for code, amount in legs}}


def operations_count(db: Session) -> int:
    value = db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one()
    db.rollback()
    return int(value)


def assert_invariants(client: TestClient, db: Session) -> None:
    """[53] Pendientes = Σ balances in effect. [54] Incobrables = Σ company parts. [55] Each employee's
    account = Σ their parts plus what settlement closes put there. All from the rows, not the API."""
    in_effect, applied = db.execute(
        text(
            "SELECT (SELECT coalesce(sum(cp.amount), 0) FROM settlements.settlement_closing_pendings cp "
            "        JOIN settlements.settlements s ON s.current_close_operation_id = cp.close_operation_id), "
            "       (SELECT coalesce(sum(amount), 0) FROM settlements.pending_collection_receipts "
            "        WHERE reversal_operation_id IS NULL) + "
            "       (SELECT coalesce(sum(amount), 0) FROM settlements.pending_collection_resolutions "
            "        WHERE reversal_operation_id IS NULL)"
        )
    ).one()
    company = db.execute(
        text(
            "SELECT coalesce(sum(a.amount), 0) FROM settlements.pending_resolution_allocations a "
            "JOIN settlements.pending_collection_resolutions r ON r.id = a.resolution_id "
            "WHERE r.reversal_operation_id IS NULL AND a.target = 'COMPANY'"
        )
    ).scalar_one()
    employees = db.execute(
        text(
            "SELECT acc.employee_id, sum(e.amount) AS ledger, coalesce(("
            "   SELECT sum(a.amount) FROM settlements.pending_resolution_allocations a"
            "   JOIN settlements.pending_collection_resolutions r ON r.id = a.resolution_id"
            "   WHERE r.reversal_operation_id IS NULL AND a.employee_id = acc.employee_id), 0) AS parts, "
            " coalesce(sum(e.amount) FILTER (WHERE o.type = 'SETTLEMENT_CLOSE' OR (o.type = 'REVERSAL' AND "
            "   orig.type = 'SETTLEMENT_CLOSE')), 0) AS closes "
            "FROM settlements.ledger_entries e JOIN settlements.accounts acc ON acc.id = e.account_id "
            "JOIN settlements.financial_operations o ON o.id = e.operation_id "
            "LEFT JOIN settlements.financial_operations orig ON orig.id = o.reverses_operation_id "
            "WHERE acc.kind = 'EMPLOYEE' GROUP BY acc.employee_id"
        )
    ).all()
    db.rollback()
    accounts = balances(client)
    assert accounts["pending_collections"] == in_effect - applied
    every = listed(client, state=["OPEN", "PARTIAL", "COLLECTED", "RESOLVED"], limit=200)
    assert sum((D(p["balance"]) for p in every.values()), D(0)) == in_effect - applied
    assert accounts["uncollectible"] == company
    for _, ledger, parts, closes in employees:
        assert ledger == parts + closes


@pytest.fixture
def ids(as_role) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {a["code"]: a["id"] for a in as_role("admin").get(f"{BASE}/accounts").json() if a["code"]}


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def juan(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Juan Cobrador", function="settlements.other")  # type: ignore[no-any-return]


@pytest.fixture
def settlement(as_role, ids, pedro) -> int:  # type: ignore[no-untyped-def]
    """$1.000.000 expected, $900.000 cash in Caja principal and a $100.000 pending; not closed yet."""
    operator = as_role("operator")
    sid = create(operator, pedro, "1000000")
    count(operator, sid, "ARS_20000", 45, ids["main_cash"])
    return sid


@pytest.fixture
def pending(as_role, settlement) -> int:  # type: ignore[no-untyped-def]
    """The $100.000 pending, in effect: its settlement was closed (v1)."""
    operator = as_role("operator")
    pid = new_pending(operator, settlement)
    closed(operator, settlement)
    return pid


@pytest.fixture
def later(monkeypatch) -> Any:  # type: ignore[no-untyped-def]
    """Move the clock of the alerts forward by some hours."""

    def _later(hours: float) -> None:
        moment = datetime.now(UTC) + timedelta(hours=hours)
        monkeypatch.setattr(listing, "utc_now", lambda: moment)

    return _later


# --- receipts -------------------------------------------------------------------------------------


def test_full_receipt_collects_the_pending(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[1] All of it, in cash: COLLECTED, balance 0, the money in Caja principal and out of Pendientes."""
    operator = as_role("operator")
    before = balances(operator)
    body = received(operator, pending, "100000", ids["main_cash"])
    assert (body["state"], body["collected"], body["balance"]) == ("COLLECTED", "100000.00", "0.00")
    assert body["receipts"][0]["destination_name"] == "Caja principal"
    assert [e["event_type"] for e in body["events"]][-1] == "RECEIPT_RECORDED"
    assert body["events"][-1]["receipt_id"] == body["receipts"][0]["id"]
    after = balances(operator)
    assert after["main_cash"] - before["main_cash"] == D(100000)
    assert after["pending_collections"] == D(0)
    assert_invariants(operator, db)


def test_partial_receipts_sixty_then_forty(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[2] Enzo's example: $60.000 leaves PARTIAL with $40.000; $40.000 more leaves COLLECTED with $0."""
    operator = as_role("operator")
    body = received(operator, pending, "60000", ids["main_cash"])
    assert (body["state"], body["collected"], body["balance"]) == ("PARTIAL", "60000.00", "40000.00")
    assert_invariants(operator, db)
    body = received(operator, pending, "40000", ids["main_cash"])
    assert (body["state"], body["collected"], body["balance"]) == ("COLLECTED", "100000.00", "0.00")
    assert_invariants(operator, db)


def test_cash_receipt_enters_the_cash_box(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[3] CASH: Pendientes -X, the chosen cash box +X, on the day the customer paid, from the pending."""
    operator = as_role("operator")
    body = received(operator, pending, "30000", ids["change_cash"], business_date=DAY)
    op = operation(db, body["receipts"][0]["operation_id"])
    assert op["type"] == "PENDING_COLLECTION"
    assert op["legs"] == {"pending_collections": "-30000.00", "change_cash": "30000.00"}
    assert (op["business_date"], op["source_type"], op["source_id"]) == (DAY, "pending_collection", pending)


def test_transfer_receipt_enters_the_bank(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[4] TRANSFER: Pendientes -X, Banco +X."""
    operator = as_role("operator")
    body = received(operator, pending, "25000", ids["bank"], method="TRANSFER")
    op = operation(db, body["receipts"][0]["operation_id"])
    assert op["legs"] == {"pending_collections": "-25000.00", "bank": "25000.00"}
    assert body["receipts"][0]["method"] == "TRANSFER"
    assert_invariants(operator, db)


def test_cash_cannot_go_to_the_bank(as_role, ids, pending) -> None:  # type: ignore[no-untyped-def]
    """[5]"""
    response = receipt(as_role("operator"), pending, "10000", ids["bank"], method="CASH")
    assert error(response) == (422, "invalid_destination")


def test_transfer_cannot_go_to_a_cash_box(as_role, ids, pending) -> None:  # type: ignore[no-untyped-def]
    """[6] Nor to a technical account."""
    operator = as_role("operator")
    assert error(receipt(operator, pending, "10000", ids["main_cash"], method="TRANSFER")) == (
        422,
        "invalid_destination",
    )
    assert error(receipt(operator, pending, "10000", ids["uncollectible"], method="TRANSFER")) == (
        422,
        "invalid_destination",
    )


def test_receipt_above_the_balance_is_rejected(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[7] Never more than the balance: $60.000 collected leaves room for $40.000, not $40.000,01."""
    operator = as_role("operator")
    assert error(receipt(operator, pending, "100000.01", ids["main_cash"])) == (409, "pending_amount_exceeds_balance")
    received(operator, pending, "60000", ids["main_cash"])
    response = receipt(operator, pending, "40000.01", ids["main_cash"])
    assert error(response) == (409, "pending_amount_exceeds_balance")
    assert response.json()["details"]["balance"] == "40000.00"
    assert_invariants(operator, db)


def test_receipt_before_the_settlement_is_rejected(as_role, ids, pending) -> None:  # type: ignore[no-untyped-def]
    """[8] Enzo's D1: not before the settlement date."""
    response = receipt(as_role("operator"), pending, "10000", ids["main_cash"], business_date=DAY - timedelta(days=1))
    assert error(response) == (422, "invalid_receipt_date")


def test_future_receipt_is_rejected(as_role, ids, pending) -> None:  # type: ignore[no-untyped-def]
    """[9] Nor in the future."""
    response = receipt(as_role("operator"), pending, "10000", ids["main_cash"], business_date=TODAY + timedelta(days=1))
    assert error(response) == (422, "future_business_date")


def test_draft_and_voided_pendings_are_not_collected(as_role, ids, settlement) -> None:  # type: ignore[no-untyped-def]
    """[10] A pending its settlement has not closed yet is not in Cajas: nothing to collect."""
    operator = as_role("operator")
    pid = new_pending(operator, settlement)
    assert error(receipt(operator, pid, "10000", ids["main_cash"])) == (409, "pending_not_in_effect")
    voided = operator.post(
        f"{BASE}/deliveries/{settlement}/pending-collections/{pid}/void", json={"reason": "Pagó en el momento"}
    )
    assert voided.status_code == 200, voided.text
    assert error(receipt(operator, pid, "10000", ids["main_cash"])) == (409, "pending_not_in_effect")
    assert error(resolve(as_role("admin"), pid, "10000", [(None, "10000")])) == (409, "pending_not_in_effect")


def test_receipt_is_idempotent(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[11] The same request twice: one receipt, one operation, one event."""
    operator = as_role("operator")
    request_id = uuid4()
    operations = operations_count(db)
    first = receipt(operator, pending, "30000", ids["main_cash"], request_id=request_id)
    again = receipt(operator, pending, "30000", ids["main_cash"], request_id=request_id)
    assert (first.status_code, again.status_code) == (201, 200)
    assert len(again.json()["receipts"]) == 1
    assert again.json()["balance"] == "70000.00"
    assert operations_count(db) == operations + 1
    assert [e["event_type"] for e in again.json()["events"]].count("RECEIPT_RECORDED") == 1


def test_receipt_idempotency_conflict(as_role, ids, pending) -> None:  # type: ignore[no-untyped-def]
    """[12] The same id with other content is a conflict, not a second receipt."""
    operator = as_role("operator")
    request_id = uuid4()
    received(operator, pending, "30000", ids["main_cash"], request_id=request_id)
    response = receipt(operator, pending, "31000", ids["main_cash"], request_id=request_id)
    assert error(response) == (409, "idempotency_conflict")
    assert len(detail(operator, pending)["receipts"]) == 1


# --- receipt reversal -----------------------------------------------------------------------------


def test_admin_reverses_a_receipt(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[13] The balance comes back, the cash leaves the box, the receipt stays with its reversal."""
    operator = as_role("operator")
    before = balances(operator)
    rid = received(operator, pending, "60000", ids["main_cash"])["receipts"][0]["id"]
    admin = as_role("admin")
    response = reverse(admin, pending, "receipts", rid)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["state"], body["collected"], body["balance"]) == ("OPEN", "0.00", "100000.00")
    reversal = body["receipts"][0]["reversal"]
    assert (reversal["reason"], reversal["reversed_by_name"]) == ("Estaba mal", "Usuario Test")
    op = operation(db, reversal["operation_id"])
    assert op["type"] == "REVERSAL"
    assert op["reverses_operation_id"] == body["receipts"][0]["operation_id"]
    assert op["legs"] == {"pending_collections": "60000.00", "main_cash": "-60000.00"}
    assert [e["event_type"] for e in body["events"]][-2:] == ["RECEIPT_RECORDED", "RECEIPT_REVERSED"]
    assert balances(admin) == before
    assert_invariants(admin, db)


def test_operator_cannot_reverse_a_receipt(as_role, ids, pending) -> None:  # type: ignore[no-untyped-def]
    """[14]"""
    operator = as_role("operator")
    rid = received(operator, pending, "60000", ids["main_cash"])["receipts"][0]["id"]
    assert error(reverse(operator, pending, "receipts", rid)) == (403, "forbidden")


def test_receipt_is_reversed_once(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[15] A second reversal is refused; repeating the same reversal request returns it (idempotent)."""
    rid = received(as_role("operator"), pending, "60000", ids["main_cash"])["receipts"][0]["id"]
    admin = as_role("admin")
    request_id = uuid4()
    assert reverse(admin, pending, "receipts", rid, request_id).status_code == 201
    operations = operations_count(db)
    assert reverse(admin, pending, "receipts", rid, request_id).status_code == 200
    assert error(reverse(admin, pending, "receipts", rid, request_id, reason="Otro motivo")) == (
        409,
        "idempotency_conflict",
    )
    assert error(reverse(admin, pending, "receipts", rid)) == (409, "receipt_already_reversed")
    assert operations_count(db) == operations
    assert_invariants(admin, db)


def test_receipt_reversal_keeps_the_original_date(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[16] [17] The reversal is dated like the receipt (it was wrong then); created_at is when it happened."""
    body = received(as_role("operator"), pending, "60000", ids["main_cash"], business_date=DAY)
    original = operation(db, body["receipts"][0]["operation_id"])
    started = datetime.now(UTC)
    response = reverse(as_role("admin"), pending, "receipts", body["receipts"][0]["id"])
    assert response.status_code == 201, response.text
    reversal = response.json()["receipts"][0]["reversal"]
    op = operation(db, reversal["operation_id"])
    assert original["business_date"] == op["business_date"] == DAY
    assert op["created_at"] >= started > original["created_at"]
    assert datetime.fromisoformat(reversal["reversed_at"]) >= started


def test_historical_reversal_cannot_leave_a_cash_box_negative(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[18] $100.000 entered Caja de cambio yesterday and were moved to Caja principal today: reversing the
    receipt on its own date would leave Caja de cambio negative today. Refused; nothing changes."""
    operator = as_role("operator")
    rid = received(operator, pending, "100000", ids["change_cash"], business_date=DAY)["receipts"][0]["id"]
    moved = operator.post(
        f"{BASE}/operations/cash-transfer",
        json={
            "client_request_id": str(uuid4()),
            "from_account_id": ids["change_cash"],
            "to_account_id": ids["main_cash"],
            "amount": "100000",
        },
    )
    assert moved.status_code == 201, moved.text
    operations = operations_count(db)
    admin = as_role("admin")
    response = reverse(admin, pending, "receipts", rid)
    assert error(response) == (409, "insufficient_funds")
    assert response.json()["details"]["business_date"] == TODAY.isoformat()
    assert operations_count(db) == operations
    assert detail(admin, pending)["receipts"][0]["reversal"] is None
    assert_invariants(admin, db)


# --- resolutions ----------------------------------------------------------------------------------


def test_company_resolution_goes_to_uncollectible(as_role, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[19] The company gives it up: Pendientes -100.000, Incobrables +100.000, RESOLVED."""
    admin = as_role("admin")
    body = resolved(admin, pending, "100000", [(None, "100000")])
    assert (body["state"], body["resolved"], body["balance"]) == ("RESOLVED", "100000.00", "0.00")
    resolution = body["resolutions"][0]
    assert resolution["allocations"] == [
        {"target": "COMPANY", "employee_id": None, "employee_name": None, "amount": "100000.00"}
    ]
    op = operation(db, resolution["operation_id"])
    assert op["type"] == "PENDING_WRITE_OFF"
    assert op["legs"] == {"pending_collections": "-100000.00", "uncollectible": "100000.00"}
    assert [e["event_type"] for e in body["events"]][-1] == "RESOLVED"
    assert body["events"][-1]["reason"] == "El cliente cerró el negocio"
    assert_invariants(admin, db)


def test_employee_resolution_goes_to_their_account(as_role, pending, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[20] The driver answers for it: his internal account +100.000 (he owes it)."""
    admin = as_role("admin")
    body = resolved(admin, pending, "100000", [(pedro, "100000")])
    assert body["resolutions"][0]["allocations"][0]["employee_name"] == "Pedro Gómez"
    assert employee_balance(db, pedro) == D(100000)
    assert balances(admin)["uncollectible"] == D(0)
    assert_invariants(admin, db)


def test_resolution_to_several_employees(as_role, pending, pedro, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[21]"""
    admin = as_role("admin")
    resolved(admin, pending, "100000", [(pedro, "60000"), (juan, "40000")])
    assert (employee_balance(db, pedro), employee_balance(db, juan)) == (D(60000), D(40000))
    assert_invariants(admin, db)


def test_mixed_resolution(as_role, pending, pedro, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[22] Company and employees in one resolution, one operation."""
    admin = as_role("admin")
    body = resolved(admin, pending, "100000", [(None, "50000"), (pedro, "30000"), (juan, "20000")])
    op = operation(db, body["resolutions"][0]["operation_id"])
    assert op["legs"] == {
        "pending_collections": "-100000.00",
        "uncollectible": "50000.00",
        f"employee:{pedro}": "30000.00",
        f"employee:{juan}": "20000.00",
    }
    assert_invariants(admin, db)


def test_partial_resolution(as_role, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[23] Part of the balance: PARTIAL with the rest still owed."""
    admin = as_role("admin")
    body = resolved(admin, pending, "30000", [(None, "30000")])
    assert (body["state"], body["resolved"], body["balance"]) == ("PARTIAL", "30000.00", "70000.00")
    assert_invariants(admin, db)


def test_total_resolution_after_a_receipt(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[24] $40.000 collected and the remaining $60.000 resolved: RESOLVED, balance 0."""
    received(as_role("operator"), pending, "40000", ids["main_cash"])
    admin = as_role("admin")
    body = resolved(admin, pending, "60000", [(None, "60000")])
    assert (body["state"], body["collected"], body["resolved"], body["balance"]) == (
        "RESOLVED",
        "40000.00",
        "60000.00",
        "0.00",
    )
    assert_invariants(admin, db)


def test_resolution_requires_admin(as_role, pending) -> None:  # type: ignore[no-untyped-def]
    """[25]"""
    assert error(resolve(as_role("operator"), pending, "100000", [(None, "100000")])) == (403, "forbidden")


def test_resolution_requires_a_reason(as_role, pending) -> None:  # type: ignore[no-untyped-def]
    """[26]"""
    admin = as_role("admin")
    assert error(resolve(admin, pending, "100000", [(None, "100000")], reason="   ")) == (422, "reason_required")
    assert resolve(admin, pending, "100000", [(None, "100000")], reason="").status_code == 422


def test_resolution_parts_must_add_up(as_role, pending, pedro) -> None:  # type: ignore[no-untyped-def]
    """[27] Exactly the total, each party once."""
    admin = as_role("admin")
    assert error(resolve(admin, pending, "100000", [(None, "50000"), (pedro, "49999.99")])) == (
        422,
        "resolution_allocation_mismatch",
    )
    assert error(resolve(admin, pending, "100000", [(None, "50000"), (pedro, "50000.01")])) == (
        422,
        "resolution_allocation_mismatch",
    )
    assert error(resolve(admin, pending, "100000", [(pedro, "50000"), (pedro, "50000")])) == (
        422,
        "resolution_allocation_mismatch",
    )
    assert detail(admin, pending)["resolutions"] == []


def test_resolution_above_the_balance_is_rejected(as_role, ids, pending) -> None:  # type: ignore[no-untyped-def]
    """[28]"""
    received(as_role("operator"), pending, "60000", ids["main_cash"])
    response = resolve(as_role("admin"), pending, "40000.01", [(None, "40000.01")])
    assert error(response) == (409, "pending_amount_exceeds_balance")


def test_resolution_is_idempotent(as_role, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Like a receipt: the same request returns it, other content with the same id is a conflict."""
    admin = as_role("admin")
    request_id = uuid4()
    assert resolve(admin, pending, "30000", [(None, "30000")], request_id=request_id).status_code == 201
    operations = operations_count(db)
    again = resolve(admin, pending, "30000", [(None, "30000")], request_id=request_id)
    assert again.status_code == 200
    assert len(again.json()["resolutions"]) == 1
    assert error(resolve(admin, pending, "30000", [(None, "30000")], reason="Otro", request_id=request_id)) == (
        409,
        "idempotency_conflict",
    )
    assert operations_count(db) == operations


# --- resolution reversal --------------------------------------------------------------------------


def test_resolution_reversal(as_role, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[29] The balance comes back and Incobrables goes back to zero."""
    admin = as_role("admin")
    rid = resolved(admin, pending, "100000", [(None, "100000")])["resolutions"][0]["id"]
    response = reverse(admin, pending, "resolutions", rid)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["state"], body["resolved"], body["balance"]) == ("OPEN", "0.00", "100000.00")
    assert body["resolutions"][0]["reversal"]["reason"] == "Estaba mal"
    assert [e["event_type"] for e in body["events"]][-1] == "RESOLUTION_REVERSED"
    assert balances(admin)["uncollectible"] == D(0)
    assert error(reverse(admin, pending, "resolutions", rid)) == (409, "resolution_already_reversed")
    assert_invariants(admin, db)


def test_resolution_reversal_restores_the_employee_account(as_role, ids, pending, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[30] Enzo's D3: the employee's part is undone too, whatever happened to the account since. Here a later
    surplus was assigned to him, so his account ends negative: the company owes him, and that is valid."""
    admin = as_role("admin")
    rid = resolved(admin, pending, "100000", [(pedro, "100000")])["resolutions"][0]["id"]
    operator = as_role("operator")
    surplus = create(operator, pedro, "100000")
    count(operator, surplus, "ARS_10000", 25, ids["main_cash"])
    closed(operator, surplus, allocations=[{"target": "EMPLOYEE", "employee_id": pedro, "amount": "150000"}])
    assert employee_balance(db, pedro) == D(-50000)
    response = reverse(as_role("admin"), pending, "resolutions", rid)
    assert response.status_code == 201, response.text
    assert employee_balance(db, pedro) == D(-150000)
    assert_invariants(admin, db)


def test_resolution_reversal_keeps_the_original_date(as_role, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[31]"""
    admin = as_role("admin")
    body = resolved(admin, pending, "100000", [(None, "100000")], business_date=DAY)
    started = datetime.now(UTC)
    response = reverse(admin, pending, "resolutions", body["resolutions"][0]["id"])
    op = operation(db, response.json()["resolutions"][0]["reversal"]["operation_id"])
    assert op["business_date"] == DAY
    assert op["created_at"] >= started


def test_cajas_cannot_reverse_a_receipt(as_role, ids, pending, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[32] It belongs to the pending: only the pending's flow reverses it (and marks the receipt)."""
    operation_id = received(as_role("operator"), pending, "60000", ids["main_cash"])["receipts"][0]["operation_id"]
    response = as_role("admin").post(
        f"{BASE}/operations/{operation_id}/reverse", json={"client_request_id": str(uuid4()), "reason": "Por Cajas"}
    )
    assert error(response) == (409, "operation_owned_by_document")
    assert response.json()["details"] == {"source_type": "pending_collection", "source_id": pending}


def test_cajas_cannot_reverse_a_resolution(as_role, pending) -> None:  # type: ignore[no-untyped-def]
    """[33]"""
    admin = as_role("admin")
    operation_id = resolved(admin, pending, "100000", [(None, "100000")])["resolutions"][0]["operation_id"]
    response = admin.post(
        f"{BASE}/operations/{operation_id}/reverse", json={"client_request_id": str(uuid4()), "reason": "Por Cajas"}
    )
    assert error(response) == (409, "operation_owned_by_document")


# --- during a correction --------------------------------------------------------------------------


def test_receipt_during_a_correction_uses_the_amount_in_effect(as_role, ids, settlement, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[37] Reopened, the load proposes $50.000, but $100.000 is still in effect: $80.000 can be collected.
    The new close then cannot publish $50.000 under what was collected."""
    operator = as_role("operator")
    pid = new_pending(operator, settlement)
    closed(operator, settlement)
    reopen(as_role("admin"), settlement, 1)
    operator = as_role("operator")
    patched = operator.patch(f"{BASE}/deliveries/{settlement}/pending-collections/{pid}", json={"amount": "50000"})
    assert patched.status_code == 200, patched.text
    body = received(operator, pid, "80000", ids["main_cash"])
    assert (body["state"], body["amount_in_effect"], body["balance"], body["load_change"]) == (
        "PARTIAL",
        "100000.00",
        "20000.00",
        "MODIFIED",
    )
    assert error(close(operator, settlement, 2)) == (409, "pending_below_applied")
    assert_invariants(operator, db)


def test_receipt_makes_a_preview_stale(as_role, ids, settlement) -> None:  # type: ignore[no-untyped-def]
    """[38] What was applied is part of what the preview showed."""
    operator = as_role("operator")
    pid = new_pending(operator, settlement)
    closed(operator, settlement)
    reopen(as_role("admin"), settlement, 1)
    operator = as_role("operator")
    token = preview_token(operator, settlement)
    received(operator, pid, "10000", ids["main_cash"])
    assert error(close(operator, settlement, 2, token=token)) == (409, "settlement_preview_stale")
    closed(operator, settlement, 2)


def test_amount_can_go_down_to_what_was_applied(as_role, ids, settlement, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[39] $60.000 collected: the correction may publish exactly $60.000, which leaves it COLLECTED."""
    operator = as_role("operator")
    pid = new_pending(operator, settlement)
    closed(operator, settlement)
    received(operator, pid, "60000", ids["main_cash"])
    reopen(as_role("admin"), settlement, 1)
    operator = as_role("operator")
    patched = operator.patch(f"{BASE}/deliveries/{settlement}/pending-collections/{pid}", json={"amount": "60000"})
    assert patched.status_code == 200, patched.text
    count(operator, settlement, "ARS_20000", 47, ids["main_cash"])  # the $40.000 not owed came in cash
    closed(operator, settlement, 2)
    body = detail(operator, pid)
    assert (body["state"], body["amount_in_effect"], body["balance"]) == ("COLLECTED", "60000.00", "0.00")
    assert_invariants(operator, db)


def test_amount_cannot_go_below_what_was_applied(as_role, ids, pending, settlement) -> None:  # type: ignore[no-untyped-def]
    """[40]"""
    received(as_role("operator"), pending, "60000", ids["main_cash"])
    reopen(as_role("admin"), settlement, 1)
    operator = as_role("operator")
    patched = operator.patch(
        f"{BASE}/deliveries/{settlement}/pending-collections/{pending}", json={"amount": "59999.99"}
    )
    assert error(patched) == (409, "pending_below_applied")


def test_pending_with_activity_cannot_be_voided(as_role, pending, settlement) -> None:  # type: ignore[no-untyped-def]
    """[41] Resolved in part: voiding it would contradict that. First reverse the resolution."""
    resolved(as_role("admin"), pending, "10000", [(None, "10000")])
    reopen(as_role("admin"), settlement, 1)
    operator = as_role("operator")
    voided = operator.post(
        f"{BASE}/deliveries/{settlement}/pending-collections/{pending}/void", json={"reason": "No corresponde"}
    )
    assert error(voided) == (409, "pending_has_activity")


def test_customer_of_a_pending_with_activity_is_locked(as_role, ids, pending, settlement) -> None:  # type: ignore[no-untyped-def]
    """[42] It was collected from that customer; responsible, reason and expected date still change."""
    received(as_role("operator"), pending, "10000", ids["main_cash"])
    reopen(as_role("admin"), settlement, 1)
    operator = as_role("operator")
    url = f"{BASE}/deliveries/{settlement}/pending-collections/{pending}"
    assert error(operator.patch(url, json={"customer_name": "Otro cliente"})) == (409, "pending_customer_locked")
    allowed = operator.patch(url, json={"reason": "Pagará el lunes", "expected_date": TODAY.isoformat()})
    assert allowed.status_code == 200, allowed.text


# --- responsible after the close ------------------------------------------------------------------


def test_admin_changes_the_responsible_after_the_close(as_role, pending, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[43] Only the responsible changes: no ledger, no reopening, an event and an audit record."""
    admin = as_role("admin")
    operations = operations_count(db)
    response = admin.patch(
        f"{BASE}/pending-collections/{pending}/responsible",
        json={"responsible_employee_id": juan, "reason": "Lo cobra el cobrador"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["responsible_employee_id"], body["responsible_name"]) == (juan, "Juan Cobrador")
    assert body["settlement_financial_state"] == "CLOSED"
    event = body["events"][-1]
    assert (event["event_type"], event["after"], event["reason"]) == (
        "RESPONSIBLE_CHANGED",
        {"responsible_employee_id": juan},
        "Lo cobra el cobrador",
    )
    assert operations_count(db) == operations
    audited = db.execute(
        text(
            "SELECT count(*) FROM audit_events WHERE event_type = 'settlements.pending_collection.responsible_changed'"
        )
    ).scalar_one()
    db.rollback()
    assert audited == 1


def test_operator_cannot_change_the_responsible_after_the_close(as_role, pending, juan) -> None:  # type: ignore[no-untyped-def]
    """[44]"""
    response = as_role("operator").patch(
        f"{BASE}/pending-collections/{pending}/responsible", json={"responsible_employee_id": juan}
    )
    assert error(response) == (403, "forbidden")


def test_other_fields_stay_frozen_after_the_close(as_role, pending, settlement) -> None:  # type: ignore[no-untyped-def]
    """[45] Through the API (the database rule is tested in SQL)."""
    url = f"{BASE}/deliveries/{settlement}/pending-collections/{pending}"
    for client in (as_role("operator"), as_role("admin")):
        assert error(client.patch(url, json={"amount": "90000"})) == (409, "settlement_closed")
        assert error(client.patch(url, json={"expected_date": TODAY.isoformat()})) == (409, "settlement_closed")
    response = as_role("admin").patch(
        f"{BASE}/pending-collections/{pending}/responsible", json={"responsible_employee_id": 999999}
    )
    assert error(response) == (422, "employee_not_found")


# --- alerts ---------------------------------------------------------------------------------------


def test_alert_after_more_than_24_hours(as_role, pending, later) -> None:  # type: ignore[no-untyped-def]
    """[46] In effect with a balance: no alert at 23 h, alert at 25 h, in the list and the summary."""
    viewer = as_role("viewer")
    later(23)
    assert listed(viewer)[pending]["alert"] is False
    assert summary(viewer) == {"alert_hours": 24, "count": 0, "balance": "0.00"}
    later(25)
    row = listed(viewer)[pending]
    assert (row["alert"], row["age_hours"]) == (True, 25)
    assert summary(viewer) == {"alert_hours": 24, "count": 1, "balance": "100000.00"}
    assert list(listed(viewer, alert=True)) == [pending]


def test_partial_alerts_on_what_is_left(as_role, ids, pending, later) -> None:  # type: ignore[no-untyped-def]
    """[47]"""
    received(as_role("operator"), pending, "60000", ids["main_cash"])
    later(25)
    viewer = as_role("viewer")
    assert (listed(viewer)[pending]["state"], listed(viewer)[pending]["alert"]) == ("PARTIAL", True)
    assert summary(viewer)["balance"] == "40000.00"


def test_collected_does_not_alert(as_role, ids, pending, later) -> None:  # type: ignore[no-untyped-def]
    """[48]"""
    received(as_role("operator"), pending, "100000", ids["main_cash"])
    later(25)
    viewer = as_role("viewer")
    assert detail(viewer, pending)["alert"] is False
    assert summary(viewer)["count"] == 0


def test_resolved_does_not_alert(as_role, pending, later) -> None:  # type: ignore[no-untyped-def]
    """[49]"""
    resolved(as_role("admin"), pending, "100000", [(None, "100000")])
    later(25)
    viewer = as_role("viewer")
    assert detail(viewer, pending)["alert"] is False
    assert summary(viewer)["count"] == 0


def test_draft_does_not_alert(as_role, settlement, later) -> None:  # type: ignore[no-untyped-def]
    """[50] Not in Cajas yet."""
    pid = new_pending(as_role("operator"), settlement)
    later(25)
    viewer = as_role("viewer")
    assert (detail(viewer, pid)["state"], detail(viewer, pid)["alert"]) == ("DRAFT", False)
    assert summary(viewer)["count"] == 0


def test_alert_hours_come_from_the_setting(as_role, pending, later, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[51] At 25 h it alerts with 24 h, not with 48 h."""
    later(25)
    viewer = as_role("viewer")
    assert summary(viewer)["count"] == 1
    user_id = db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar_one()
    set_setting(db, "settlements.pending_alert_hours", 48, user_id=user_id)
    db.commit()
    assert summary(viewer) == {"alert_hours": 48, "count": 0, "balance": "0.00"}
    assert listed(viewer)[pending]["alert"] is False


def test_overdue_expected_date_is_apart_from_the_alert(as_role, settlement) -> None:  # type: ignore[no-untyped-def]
    """[52] Expected yesterday and registered a moment ago: "Fecha esperada vencida", but no alert."""
    operator = as_role("operator")
    pid = new_pending(operator, settlement, "60000", expected_date=DAY.isoformat())
    other = new_pending(operator, settlement, "40000", customer_name="Kiosco Sur", expected_date=TODAY.isoformat())
    closed(operator, settlement)
    rows = listed(as_role("viewer"))
    assert (rows[pid]["expected_date_overdue"], rows[pid]["alert"]) == (True, False)
    assert rows[other]["expected_date_overdue"] is False


# --- global list ----------------------------------------------------------------------------------


def test_list_shows_what_is_owed_by_default_and_filters(as_role, ids, pending, settlement, pedro, driver) -> None:  # type: ignore[no-untyped-def]
    """Enzo's D2: OPEN and PARTIAL by default; every other state by filter. Filters by customer, driver,
    responsible, settlement, age and alert."""
    luis = driver("Luis Repartidor")
    operator = as_role("operator")
    other = create(operator, luis, "50000")
    collected = new_pending(operator, other, "50000", customer_name="Kiosco Sur")
    closed(operator, other)
    received(operator, collected, "50000", ids["bank"], method="TRANSFER")
    draft = create(operator, pedro, "10000")
    new_pending(operator, draft, "10000", customer_name="Kiosco Norte")

    viewer = as_role("viewer")
    assert list(listed(viewer)) == [pending]
    row = listed(viewer)[pending]
    assert (row["driver_name"], row["settlement_business_date"], row["settlement_sequence_number"]) == (
        "Pedro Gómez",
        DAY.isoformat(),
        1,
    )
    assert list(listed(viewer, state="COLLECTED")) == [collected]
    assert len(listed(viewer, state=["DRAFT", "OPEN"])) == 2
    assert list(listed(viewer, state="COLLECTED", customer="kiosco s")) == [collected]
    assert list(listed(viewer, customer="DON JOSÉ")) == [pending]
    assert list(listed(viewer, driver_id=luis, state="COLLECTED")) == [collected]
    assert listed(viewer, driver_id=luis) == {}
    assert list(listed(viewer, responsible_employee_id=pedro)) == [pending]
    assert list(listed(viewer, settlement_id=settlement)) == [pending]
    assert listed(viewer, min_age_hours=1) == {}
    assert listed(viewer, alert=True) == {}
    assert as_role("viewer").get(f"{BASE}/pending-collections", params={"state": "OTHER"}).status_code == 422
