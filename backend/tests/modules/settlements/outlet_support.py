"""Shared helpers of the R7-2 tests: boca settlements and their loads, through the API. The fixtures (`r`,
`boca`, `employee`, `ids`) are in this package's `conftest.py`."""

from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import yesterday

BASE = "/api/v1/settlements"
OUT = f"{BASE}/outlet-settlements"
REASON = "Faltaba cargar un billete de $500"


def open_settlement(
    client: TestClient, outlet_id: int, expected: str | None = None, *, confirm_additional: bool = False, **extra: Any
) -> Any:
    body: dict[str, Any] = {
        "client_request_id": str(uuid4()),
        "business_date": yesterday().isoformat(),
        "outlet_id": outlet_id,
        "confirm_additional": confirm_additional,
        **extra,
    }
    if expected is not None:
        body["expected_amount"] = expected
    return client.post(OUT, json=body)


def create(client: TestClient, outlet_id: int, expected: str | None = None, **extra: Any) -> int:
    response = open_settlement(client, outlet_id, expected, **extra)
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def set_expected(client: TestClient, sid: int, expected: str | None) -> Any:
    response = client.patch(f"{OUT}/{sid}", json={"expected_amount": expected})
    assert response.status_code == 200, response.text
    return response.json()


def count(client: TestClient, sid: int, *lines: tuple[str, int, int]) -> Any:
    body = [{"denomination_code": c, "quantity": q, "destination_account_id": box} for c, q, box in lines]
    response = client.put(f"{OUT}/{sid}/count", json={"lines": body})
    assert response.status_code == 200, response.text
    return response.json()


def addition(client: TestClient, sid: int, amount: str, box: int) -> int:
    response = client.post(
        f"{OUT}/{sid}/additions",
        json={"client_request_id": str(uuid4()), "amount": amount, "destination_account_id": box},
    )
    assert response.status_code == 201, response.text
    return response.json()["additions"][-1]["id"]  # type: ignore[no-any-return]


def transfer(client: TestClient, sid: int, amount: str, status: str | None = None) -> int:
    response = client.post(f"{OUT}/{sid}/transfers", json={"client_request_id": str(uuid4()), "amount": amount})
    assert response.status_code == 201, response.text
    tid: int = response.json()["transfers"][-1]["id"]
    if status:
        reason = "No figura en el banco" if status == "REJECTED" else None
        decided = client.post(f"{OUT}/{sid}/transfers/{tid}/status", json={"status": status, "reason": reason})
        assert decided.status_code == 200, decided.text
    return tid


def expense(client: TestClient, sid: int, category: int, amount: str) -> int:
    response = client.post(
        f"{OUT}/{sid}/expenses", json={"client_request_id": str(uuid4()), "category_id": category, "amount": amount}
    )
    assert response.status_code == 201, response.text
    return response.json()["expenses"][-1]["id"]  # type: ignore[no-any-return]


def category(client: TestClient, name: str = "Limpieza") -> int:
    response = client.post(f"{BASE}/expense-categories", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def preview(client: TestClient, sid: int) -> dict[str, Any]:
    response = client.get(f"{OUT}/{sid}/closing-preview")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def close(
    client: TestClient,
    sid: int,
    *allocations: dict[str, Any],
    version: int = 1,
    confirm: bool = True,
    token: str | None = None,
) -> Any:
    return client.post(
        f"{OUT}/{sid}/close",
        json={
            "version": version,
            "preview_token": token or preview(client, sid)["preview_token"],
            "allocations": list(allocations),
            "confirm": confirm,
        },
    )


def reopen(client: TestClient, sid: int, version: int, reason: str = REASON) -> Any:
    return client.post(f"{OUT}/{sid}/reopen", json={"version": version, "reason": reason})


def employee_part(employee_id: int, amount: str) -> dict[str, Any]:
    return {"target": "EMPLOYEE", "employee_id": employee_id, "amount": amount}


def company_part(amount: str, reason: str = "Diferencia menor absorbida por la empresa") -> dict[str, Any]:
    return {"target": "COMPANY", "amount": amount, "reason": reason}


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def balances(client: TestClient) -> dict[str, str]:
    return {a["code"] or a["name"]: a["balance"] for a in client.get(f"{BASE}/accounts").json()}


def legs(db: Session, operation_id: int) -> dict[str, str]:
    rows = db.execute(
        text(
            "SELECT coalesce(a.code, 'employee:' || a.employee_id), e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :o"
        ),
        {"o": operation_id},
    )
    return {key: str(amount) for key, amount in rows.all()}


def operations_of(db: Session, sid: int) -> list[tuple[int, str]]:
    """Every ledger operation of the settlement: its closes and the reversals of its closes."""
    rows = db.execute(
        text(
            "SELECT o.id, o.type FROM settlements.financial_operations o "
            "LEFT JOIN settlements.financial_operations c ON c.id = o.reverses_operation_id "
            "WHERE (o.source_type = 'settlement' AND o.source_id = :s) "
            "OR (c.source_type = 'settlement' AND c.source_id = :s) ORDER BY o.id"
        ),
        {"s": sid},
    )
    return [(int(i), str(t)) for i, t in rows.all()]


def zero_entries(db: Session) -> int:
    return int(db.execute(text("SELECT count(*) FROM settlements.ledger_entries WHERE amount = 0")).scalar_one())


# An administrative zero close written by hand: the closing row with a snapshot of $0 (`:cash` lets a test
# make it lie) and the settlement marked CLOSED on it. Params: `s` (settlement), `f` (fingerprint), `cash`.
ZERO_CLOSE_SQL = (
    "WITH c AS (INSERT INTO settlements.settlement_closings (settlement_id, settlement_version, kind, "
    "operation_id, request_fingerprint, snapshot, closed_by, closed_at) SELECT id, version, "
    "'ADMINISTRATIVE_ZERO', NULL, :f, jsonb_build_object('type', 'OUTLET', 'outlet_id', outlet_id::text, "
    "'version', version::text, 'business_date', business_date::text, 'expected_amount', '0.00', 'cash', "
    "CAST(:cash AS text), 'additions', '0.00', 'transfers_verified', '0.00', 'cheques', '0.00', 'expenses', "
    "'0.00', 'pending', '0.00', 'difference', '0.00'), created_by, now() "
    "FROM settlements.settlements WHERE id = :s RETURNING id) "
    "UPDATE settlements.settlements SET status = 'CLOSED', current_close_operation_id = NULL, "
    "current_closing_id = (SELECT id FROM c), reopened_at = NULL, reopened_by = NULL, reopen_reason = NULL, "
    "closed_at = now(), closed_by = created_by WHERE id = :s"
)
