"""Shared helpers of the R7-3 tests: remittances of a boca, through the API, and the ledger seen per account."""

from datetime import date, datetime, time
from typing import Any
from uuid import uuid4
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import yesterday
from tests.modules.settlements.outlet_support import OUT

BA = ZoneInfo("America/Argentina/Buenos_Aires")


def at(hour: int, minute: int = 0, on: date | None = None) -> str:
    """An instant of the business day in Buenos Aires (yesterday by default, the day of `outlet_support.create`)."""
    return datetime.combine(on or yesterday(), time(hour, minute), BA).isoformat()


def remittance_body(amount: str, box: int, **extra: Any) -> dict[str, Any]:
    return {
        "client_request_id": str(uuid4()),
        "amount": amount,
        "destination_account_id": box,
        "delivered_at": at(13),
        **extra,
    }


def remit_response(client: TestClient, sid: int, amount: str, box: int, **extra: Any) -> Any:
    return client.post(f"{OUT}/{sid}/remittances", json=remittance_body(amount, box, **extra))


def remit(client: TestClient, sid: int, amount: str, box: int, **extra: Any) -> int:
    response = remit_response(client, sid, amount, box, **extra)
    assert response.status_code == 201, response.text
    return response.json()["remittances"][-1]["id"]  # type: ignore[no-any-return]


def reverse_response(
    client: TestClient, sid: int, rid: int, reason: str = "Se cargó mal el importe", **extra: Any
) -> Any:
    body = {"client_request_id": str(uuid4()), "reason": reason, **extra}
    return client.post(f"{OUT}/{sid}/remittances/{rid}/reverse", json=body)


def reverse(client: TestClient, sid: int, rid: int, reason: str = "Se cargó mal el importe") -> Any:
    response = reverse_response(client, sid, rid, reason)
    assert response.status_code == 200, response.text
    return response.json()


def advances(db: Session, outlet_id: int) -> str:
    """Balance of the boca's «Entregas a cuenta» ("none" before its first remittance)."""
    db.expire_all()
    row = db.execute(
        text(
            "SELECT a.id, coalesce(sum(e.amount), 0) FROM settlements.accounts a "
            "LEFT JOIN settlements.ledger_entries e ON e.account_id = a.id "
            "WHERE a.kind = 'OUTLET_ADVANCES' AND a.outlet_id = :o GROUP BY a.id"
        ),
        {"o": outlet_id},
    ).first()
    return "none" if row is None else f"{row[1]:.2f}"


def legs(db: Session, operation_id: int) -> dict[str, str]:
    """Legs of an operation keyed by account code; a boca's account is `advances:<outlet_id>`."""
    rows = db.execute(
        text(
            "SELECT coalesce(a.code, 'advances:' || a.outlet_id, 'employee:' || a.employee_id), e.amount "
            "FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id "
            "WHERE e.operation_id = :o"
        ),
        {"o": operation_id},
    )
    return {key: str(amount) for key, amount in rows.all()}


def remittance_row(db: Session, rid: int) -> dict[str, Any]:
    db.expire_all()
    row = db.execute(text("SELECT * FROM settlements.outlet_remittances WHERE id = :r"), {"r": rid}).mappings().one()
    return dict(row)
