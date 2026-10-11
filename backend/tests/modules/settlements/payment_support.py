"""Shared helpers of the R6-1 payment tests: third parties, means of payment, paying and reversing."""

from datetime import date
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import today
from tests.modules.settlements.direct_support import BASE

THIRD_PARTIES = "/api/v1/third-parties"


def third_party(admin: TestClient, name: str = "Distribuidora Pérez S.A.", tax_id: str | None = "30712345671") -> int:
    """A third party of the platform master, registered by a platform admin."""
    response = admin.post(THIRD_PARTIES, json={"name": name, "tax_id": tax_id})
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def cash(account: int, amount: str, reference: str | None = None) -> dict[str, Any]:
    return {"method": "CASH", "account_id": account, "amount": amount, "reference": reference}


def transfer(account: int, amount: str, reference: str | None = None) -> dict[str, Any]:
    return {"method": "TRANSFER", "account_id": account, "amount": amount, "reference": reference}


def pay(
    client: TestClient,
    third_party_id: int,
    items: list[dict[str, Any]],
    concept: str = "MERCHANDISE",
    on: date | None = None,
    **extra: Any,
) -> Any:
    if on is None:
        on = today()
    body: dict[str, Any] = {
        "client_request_id": str(uuid4()),
        "business_date": on.isoformat(),
        "third_party_id": third_party_id,
        "concept": concept,
        "items": items,
        **extra,
    }
    return client.post(f"{BASE}/payments", json=body)


def paid(client: TestClient, third_party_id: int, items: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    response = pay(client, third_party_id, items, **extra)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def reverse_payment(
    client: TestClient, payment_id: int, reason: str = "Se pagó al tercero equivocado", **extra: Any
) -> Any:
    body = {"client_request_id": str(uuid4()), "reason": reason, **extra}
    return client.post(f"{BASE}/payments/{payment_id}/reverse", json=body)


def counts(db: Session) -> tuple[int, int, int, int]:
    """Payments, items, financial operations and ledger entries: what a refused request must not leave."""
    return tuple(  # type: ignore[return-value]
        db.execute(text(f"SELECT count(*) FROM settlements.{table}")).scalar_one()  # noqa: S608
        for table in ("payments", "payment_items", "financial_operations", "ledger_entries")
    )
