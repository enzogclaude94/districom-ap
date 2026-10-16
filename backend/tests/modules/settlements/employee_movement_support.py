"""Shared helpers of the R8-2 tests: consumptions, advances and adjustments of an employee's internal account."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import today
from tests.modules.settlements.employee_finance_support import EF

PATHS = {"CONSUMPTION": "consumptions", "ADVANCE": "advances", "ADJUSTMENT": "adjustments"}


def area_admin(user_as: Callable[..., TestClient]) -> TestClient:
    """An admin of the area and operator of Rendiciones: an advance moves a cash box, which needs both (D9)."""
    return user_as("rrhh", settlements="operator", area="admin")


def area_admin_without_cash(user_as: Callable[..., TestClient]) -> TestClient:
    """An admin of the area who is only a viewer of Rendiciones: the area authorizes everything but the cash box."""
    return user_as("contador", settlements="viewer", area="admin")


def area_operator(user_as: Callable[..., TestClient]) -> TestClient:
    return user_as("cajera", settlements="viewer", area="operator")


def area_viewer(user_as: Callable[..., TestClient]) -> TestClient:
    return user_as("socio", settlements="viewer", area="viewer")


def outsider(user_as: Callable[..., TestClient]) -> TestClient:
    """An admin of Rendiciones without the area."""
    return user_as("tesorero", settlements="admin")


def body(kind: str, amount: str = "20000", **fields: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "client_request_id": str(fields.pop("request", None) or uuid4()),
        "business_date": str(fields.pop("on", None) or today()),
        "amount": amount,
        "description": fields.pop("description", f"{kind.lower()} de prueba"),
    }
    if kind == "ADJUSTMENT":
        payload["direction"] = fields.pop("direction", "CHARGE")
    return payload | fields


def register(client: TestClient, kind: str, employee_id: int, amount: str = "20000", **fields: Any) -> Any:
    return client.post(f"{EF}/employees/{employee_id}/{PATHS[kind]}", json=body(kind, amount, **fields))


def registered(client: TestClient, kind: str, employee_id: int, amount: str = "20000", **fields: Any) -> dict[str, Any]:
    response = register(client, kind, employee_id, amount, **fields)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def reverse(
    client: TestClient, movement_id: int, reason: str = "Cargado por error", request: UUID | None = None
) -> Any:
    return client.post(
        f"{EF}/employee-movements/{movement_id}/reverse",
        json={"client_request_id": str(request or uuid4()), "reason": reason},
    )


def balance(db: Session, account_id: int) -> Decimal:
    db.rollback()
    return Decimal(
        db.execute(
            text("SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a"),
            {"a": account_id},
        ).scalar_one()
    )


def internal_account(db: Session, employee_id: int) -> int:
    db.rollback()
    return int(
        db.execute(
            text("SELECT id FROM settlements.accounts WHERE kind = 'EMPLOYEE' AND employee_id = :e"), {"e": employee_id}
        ).scalar_one()
    )


def legs(movement: dict[str, Any]) -> list[tuple[str, str]]:
    return sorted((leg["account_kind"], leg["amount"]) for leg in movement["legs"])
