"""Shared helpers of the R8-4 tests: internal payrolls, their draft, close, reopening and what they apply."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import today
from tests.modules.settlements.employee_finance_support import EF
from tests.modules.settlements.test_settlement_close_api import close as close_settlement
from tests.modules.settlements.test_settlement_close_api import count, create, employee

NOTE = "Período parcial: sueldo cargado a mano"


def open_payroll(
    client: TestClient,
    employee_id: int,
    start: date,
    end: date | None = None,
    kind: str = "REGULAR",
    request: UUID | None = None,
    **fields: Any,
) -> Any:
    return client.post(
        f"{EF}/payrolls",
        json={
            "client_request_id": str(request or uuid4()),
            "employee_id": employee_id,
            "kind": kind,
            "period_start": str(start),
            "period_end": str(end or today()),
        }
        | fields,
    )


def opened(client: TestClient, employee_id: int, start: date, end: date | None = None, **kw: Any) -> dict[str, Any]:
    response = open_payroll(client, employee_id, start, end, **kw)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def payroll(client: TestClient, payroll_id: int) -> dict[str, Any]:
    response = client.get(f"{EF}/payrolls/{payroll_id}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def add_line(client: TestClient, payroll_id: int, kind: str, amount: str, description: str | None = None) -> Any:
    if description is None and kind in ("SALARY", "OTHER_CREDIT"):
        description = NOTE if kind == "SALARY" else "Otro haber"
    return client.post(
        f"{EF}/payrolls/{payroll_id}/lines", json={"kind": kind, "amount": amount, "description": description}
    )


def line(client: TestClient, payroll_id: int, kind: str, amount: str, description: str | None = None) -> dict[str, Any]:
    response = add_line(client, payroll_id, kind, amount, description)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def choose(
    client: TestClient,
    payroll_id: int,
    *,
    entry: int | None = None,
    installment: int | None = None,
    included: bool = True,
    amount: str | None = None,
) -> Any:
    body: dict[str, Any] = {"included": included}
    if entry is not None:
        body["ledger_entry_id"] = entry
    if installment is not None:
        body["installment_id"] = installment
    if amount is not None:
        body["amount"] = amount
    return client.put(f"{EF}/payrolls/{payroll_id}/choices", json=body)


def chosen(client: TestClient, payroll_id: int, **kw: Any) -> dict[str, Any]:
    response = choose(client, payroll_id, **kw)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def close(client: TestClient, payroll_id: int, token: str | None = None, version: int | None = None) -> Any:
    """Close as the screen does: read the preview, then confirm with its token."""
    current = payroll(client, payroll_id)
    preview = current["preview"]
    return client.post(
        f"{EF}/payrolls/{payroll_id}/close",
        json={
            "version": version or current["version"],
            "preview_token": token or (preview["preview_token"] if preview else "0" * 64),
            "confirm": True,
        },
    )


def closed(client: TestClient, payroll_id: int) -> dict[str, Any]:
    response = close(client, payroll_id)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def reopen(client: TestClient, payroll_id: int, version: int, reason: str = "Faltó el bono") -> Any:
    return client.post(f"{EF}/payrolls/{payroll_id}/reopen", json={"version": version, "reason": reason})


def reopened(client: TestClient, payroll_id: int, version: int) -> dict[str, Any]:
    response = reopen(client, payroll_id, version)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def cancel(client: TestClient, payroll_id: int, reason: str = "Cargada por error") -> Any:
    return client.post(f"{EF}/payrolls/{payroll_id}/cancel", json={"reason": reason})


def leg(p: dict[str, Any], origin: str, amount: str | None = None) -> dict[str, Any]:
    """The draft's leg of that origin (and original amount)."""
    found = [x for x in p["legs"] if x["origin"] == origin and (amount is None or x["original_amount"] == amount)]
    assert len(found) == 1, (origin, amount, p["legs"])
    return found[0]


def draft_legs(p: dict[str, Any]) -> list[tuple[str, str, str, bool]]:
    """(origin, available, amount to apply, included) of the draft, sorted."""
    return sorted((x["origin"], x["available"], x["amount"], x["included"]) for x in p["legs"])


def totals(p: dict[str, Any]) -> tuple[str, str, str, str]:
    t = p["preview"] if p["status"] == "OPEN" else p["totals"]
    return t["credits"], t["deductions"], t["credits_applied"], t["net"]


def shortage(as_role: Any, ids: dict[str, int], who: int, amount: str) -> int:
    """A delivery settlement (yesterday) short or over by `amount` (multiple of $10.000), charged to the employee:
    expected $100.000 and counted 100.000 - amount (a negative amount is a surplus)."""
    operator = as_role("operator")
    sid = create(operator, who, "100000")
    counted = (Decimal("100000") - Decimal(amount)) / 10000
    count(operator, sid, ("ARS_10000", int(counted), ids["main_cash"]))
    response = close_settlement(operator, sid, employee(who, str(abs(Decimal(amount)))))
    assert response.status_code == 201, response.text
    return sid


def term(client: TestClient, employee_id: int, valid_from: date, salary: str) -> dict[str, Any]:
    response = client.post(
        f"{EF}/employees/{employee_id}/compensation-terms", json={"valid_from": str(valid_from), "base_salary": salary}
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def operations(db: Session, payroll_id: int) -> list[tuple[str, str]]:
    """(type, amount on the internal account) of the payroll's ledger operations and their reversals, in order."""
    db.rollback()
    rows = db.execute(
        text(
            "SELECT o.type, e.amount FROM settlements.financial_operations o "
            "JOIN settlements.ledger_entries e ON e.operation_id = o.id "
            "JOIN settlements.accounts a ON a.id = e.account_id AND a.kind = 'EMPLOYEE' "
            "LEFT JOIN settlements.financial_operations r ON r.id = o.reverses_operation_id "
            "WHERE coalesce(r.source_type, o.source_type) = 'employee_payroll' "
            "AND coalesce(r.source_id, o.source_id) = :p ORDER BY o.id"
        ),
        {"p": payroll_id},
    ).all()
    return [(t, str(a)) for t, a in rows]


def applications(db: Session, payroll_id: int, *, in_effect: bool = True) -> list[tuple[str, int | None]]:
    db.rollback()
    rows = db.execute(
        text(
            "SELECT amount, installment_id FROM settlements.employee_payroll_applications "
            "WHERE payroll_id = :p AND (superseded_at IS NULL) = :e ORDER BY id"
        ),
        {"p": payroll_id, "e": in_effect},
    ).all()
    return [(str(a), i) for a, i in rows]
