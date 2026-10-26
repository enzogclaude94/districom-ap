"""Shared helpers of the R8-5 tests: payouts of closed internal payrolls."""

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.settlements.ledger.models import Account
from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_finance_support import EF
from tests.modules.settlements.employee_payroll_support import closed, line, opened


def payer(user_as: Callable[..., TestClient]) -> TestClient:
    """Operator of the area and of Rendiciones: enough to pay, not to reverse (Enzo's R8-5 permissions)."""
    return user_as("cajera", settlements="operator", area="operator")


def area_admin(user_as: Callable[..., TestClient]) -> TestClient:
    """Admin of the area and operator of Rendiciones: pays and reverses."""
    return user_as("rrhh", settlements="operator", area="admin")


def payout_body(closing_id: int, *items: tuple[str, int, str], **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "client_request_id": str(fields.pop("request", None) or uuid4()),
        "payroll_closing_id": closing_id,
        "business_date": str(fields.pop("on", None) or today()),
        "items": [{"method": m, "account_id": a, "amount": amount} for m, a, amount in items],
    }
    return payload | fields


def pay(client: TestClient, payroll: dict[str, Any], *items: tuple[str, int, str], **fields: Any) -> Any:
    closing_id = fields.pop("closing_id", None) or payroll["closing"]["id"]
    return client.post(f"{EF}/payrolls/{payroll['id']}/payouts", json=payout_body(closing_id, *items, **fields))


def paid(client: TestClient, payroll: dict[str, Any], *items: tuple[str, int, str], **fields: Any) -> dict[str, Any]:
    response = pay(client, payroll, *items, **fields)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def preview(client: TestClient, payroll: dict[str, Any], *items: tuple[str, int, str], **fields: Any) -> Any:
    body = payout_body(payroll["closing"]["id"], *items, **fields)
    body.pop("client_request_id")
    return client.post(f"{EF}/payrolls/{payroll['id']}/payouts/preview", json=body)


def reverse_payout(
    client: TestClient, payout_id: int, reason: str = "Se cargó en la caja equivocada", request: UUID | None = None
) -> Any:
    return client.post(
        f"{EF}/payouts/{payout_id}/reverse", json={"client_request_id": str(request or uuid4()), "reason": reason}
    )


def net_payroll(
    client: TestClient, employee_id: int, salary: str, start: date | None = None, end: date | None = None
) -> dict[str, Any]:
    """A REGULAR payroll closed with one salary line and nothing else applicable: its net is the salary."""
    p = opened(client, employee_id, start or days_ago(30), end or today())
    line(client, p["id"], "SALARY", salary)
    return closed(client, p["id"])


def payment(client: TestClient, payroll_id: int) -> tuple[str, str, str, str]:
    p = client.get(f"{EF}/payrolls/{payroll_id}").json()
    m = p["payment"]
    return m["net"], m["paid"], m["pending"], m["status"]


def payout_legs(db: Session, payout_id: int) -> list[tuple[str, str]]:
    """(account kind, amount) of the payout's operation."""
    db.rollback()
    rows = db.execute(
        text(
            "SELECT a.kind, e.amount FROM settlements.employee_payouts x "
            "JOIN settlements.ledger_entries e ON e.operation_id = x.operation_id "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE x.id = :x ORDER BY a.kind, e.amount"
        ),
        {"x": payout_id},
    ).all()
    return [(k, str(a)) for k, a in rows]


def cash_box(db: Session, name: str = "Caja chica", *, active: bool = True) -> int:
    db.rollback()
    account = Account(name=name, kind="CASH_BOX", allow_negative=False, active=active)
    db.add(account)
    db.commit()
    return account.id
