"""Shared helpers of the R5-3 direct expense tests: funding the boxes, spending and reading back."""

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import today

BASE = "/api/v1/settlements"


def account_id(client: TestClient, code: str) -> int:
    accounts = client.get(f"{BASE}/accounts", params={"include_inactive": True}).json()
    return next(a["id"] for a in accounts if a["code"] == code)  # type: ignore[no-any-return]


def balance(client: TestClient, code: str, on: date | None = None) -> Decimal:
    params = {"on": on.isoformat()} if on else {}
    return Decimal(client.get(f"{BASE}/accounts/{account_id(client, code)}", params=params).json()["balance"])


def fund(admin: TestClient, code: str, amount: str, on: date | None = None) -> None:
    """An opening balance of the box or the bank (as admin) on that day."""
    if on is None:
        on = today()
    response = admin.post(
        f"{BASE}/operations/opening-balance",
        json={
            "client_request_id": str(uuid4()),
            "business_date": on.isoformat(),
            "account_id": account_id(admin, code),
            "amount": amount,
        },
    )
    assert response.status_code == 201, response.text


def spend(client: TestClient, account: int, category: int, amount: str | None = None, **extra: Any) -> Any:
    body: dict[str, Any] = {
        "client_request_id": str(uuid4()),
        "business_date": today().isoformat(),
        "category_id": category,
        "paid_from_account_id": account,
        **extra,
    }
    if amount is not None:
        body["amount"] = amount
    for key, value in list(body.items()):
        if isinstance(value, date):
            body[key] = value.isoformat()
    return client.post(f"{BASE}/expenses", json=body)


def spent(client: TestClient, account: int, category: int, amount: str | None = None, **extra: Any) -> dict[str, Any]:
    response = spend(client, account, category, amount, **extra)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def reverse(
    client: TestClient, expense_id: int, reason: str = "Se cargó desde la caja equivocada", **extra: Any
) -> Any:
    body = {"client_request_id": str(uuid4()), "reason": reason, **extra}
    return client.post(f"{BASE}/expenses/{expense_id}/reverse", json=body)


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def legs(db: Session, operation_id: int) -> list[tuple[str, Decimal]]:
    rows = db.execute(
        text(
            "SELECT a.code, e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :op ORDER BY e.amount"
        ),
        {"op": operation_id},
    ).all()
    return [(code, amount) for code, amount in rows]


def counts(db: Session) -> tuple[int, int, int, int]:
    """Expenses, fuel loads, financial operations and ledger entries: what a refused request must not leave."""
    return tuple(  # type: ignore[return-value]
        db.execute(text(f"SELECT count(*) FROM settlements.{table}")).scalar_one()  # noqa: S608
        for table in ("expenses", "fuel_loads", "financial_operations", "ledger_entries")
    )
