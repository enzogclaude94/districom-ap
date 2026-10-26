"""Shared helpers of the R9-1 tests: the Store Sys canonical contract and the expected amount's events."""

from collections.abc import Callable
from datetime import date
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.platform.auth.module_roles import grant_module_role
from app.platform.parties.models import Employee
from tests.conftest import PASSWORD
from tests.modules.settlements.days import yesterday

BASE = "/api/v1/settlements"
DELIVERIES = f"{BASE}/deliveries"
IMPORT = f"{BASE}/imports/store-sys/expected-amounts"
CODES = f"{BASE}/store-sys/employee-codes"
AREA = "settlements.store_sys_import"


@pytest.fixture
def as_user(client: TestClient, db: Session, make_user) -> Callable[..., TestClient]:  # type: ignore[no-untyped-def]
    """`as_user("x", settlements="operator", store_sys="operator")` logs the client in as that user, created on first
    use with exactly those grants (None: no grant)."""
    created: set[str] = set()

    def _as(
        name: str, *, platform: str = "viewer", settlements: str | None = None, store_sys: str | None = None
    ) -> TestClient:
        email = f"{name}@districom.test"
        if name not in created:
            make_user(email=email, role=platform)
            if settlements is not None:
                grant_module_role(db, email, "settlements", settlements)
            if store_sys is not None:
                grant_module_role(db, email, AREA, store_sys)
            created.add(name)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _as


def integration(as_user: Callable[..., TestClient]) -> TestClient:
    """The technical user of the integration: operator of the area and of Rendiciones."""
    return as_user("store-sys", settlements="operator", store_sys="operator")


def with_code(db: Session, employee_id: int, code: str) -> None:
    employee = db.get(Employee, employee_id)
    assert employee is not None
    employee.external_store_id = code
    db.commit()


def record(
    reference: str,
    amount: str,
    *,
    revision: int = 1,
    code: str | None = "R-01",
    outlet: str | None = None,
    on: date | None = None,
    sequence: int | None = None,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "external_reference": reference,
        "revision": revision,
        "settlement_type": "OUTLET" if outlet else "DELIVERY",
        "business_date": (on or yesterday()).isoformat(),
        "expected_amount": amount,
    }
    if outlet:
        row["outlet_code"] = outlet
    elif code is not None:
        row["employee_store_code"] = code
    if sequence is not None:
        row["sequence_number"] = sequence
    return row


def send(client: TestClient, *records: dict[str, Any], request: str | None = None) -> Any:
    return client.post(
        IMPORT, json={"source": "STORE_SYS", "client_request_id": request or str(uuid4()), "records": list(records)}
    )


def sent(client: TestClient, *records: dict[str, Any], request: str | None = None) -> list[dict[str, Any]]:
    response = send(client, *records, request=request)
    assert response.status_code == 200, response.text
    return response.json()["results"]  # type: ignore[no-any-return]


def outcome(client: TestClient, row: dict[str, Any]) -> tuple[str | None, str | None]:
    result = sent(client, row)[0]
    return result["outcome"], result["code"]


def open_delivery(client: TestClient, driver_id: int, expected: str | None = None, **extra: Any) -> int:
    body: dict[str, Any] = {
        "client_request_id": str(uuid4()),
        "business_date": yesterday().isoformat(),
        "employee_id": driver_id,
        **extra,
    }
    if expected is not None:
        body["expected_amount"] = expected
    response = client.post(DELIVERIES, json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def delivery(client: TestClient, sid: int) -> dict[str, Any]:
    response = client.get(f"{DELIVERIES}/{sid}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def history(client: TestClient, sid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/settlements/{sid}/expected-history")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def pending_id(client: TestClient, sid: int) -> int:
    pending = history(client, sid)["pending"]
    assert pending is not None
    return pending["event_id"]  # type: ignore[no-any-return]


def accept(client: TestClient, event_id: int) -> Any:
    return client.post(f"{BASE}/expected-updates/{event_id}/accept")


def keep(client: TestClient, event_id: int, reason: str | None = "El remito de Store Sys tiene un error") -> Any:
    return client.post(f"{BASE}/expected-updates/{event_id}/keep", json={"reason": reason} if reason else {})


def count_cash(client: TestClient, sid: int, box: int, bills: int) -> None:
    body = [{"denomination_code": "ARS_10000", "quantity": bills, "destination_account_id": box}]
    response = client.put(f"{DELIVERIES}/{sid}/count", json={"lines": body})
    assert response.status_code == 200, response.text


def preview(client: TestClient, sid: int) -> dict[str, Any]:
    response = client.get(f"{DELIVERIES}/{sid}/closing-preview")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def close(client: TestClient, sid: int, *allocations: dict[str, Any], version: int = 1) -> Any:
    return client.post(
        f"{DELIVERIES}/{sid}/close",
        json={
            "version": version,
            "preview_token": preview(client, sid)["preview_token"],
            "allocations": list(allocations),
            "confirm": True,
        },
    )


def reopen(client: TestClient, sid: int, version: int = 1) -> Any:
    return client.post(f"{DELIVERIES}/{sid}/reopen", json={"version": version, "reason": "Faltó un billete"})
