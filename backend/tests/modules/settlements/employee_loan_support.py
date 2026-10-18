"""Shared helpers of the R8-3 tests: loans to employees, their installments and deferrals."""

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_finance_support import EF


@pytest.fixture
def funded(post, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """Caja principal with $500.000 and the bank with $300.000, both since forty days ago."""
    post(
        "OPENING_BALANCE",
        (accounts["main_cash"], "500000"),
        (accounts["opening_balance"], "-500000"),
        business_date=days_ago(40),
    )
    post(
        "OPENING_BALANCE",
        (accounts["bank"], "300000"),
        (accounts["opening_balance"], "-300000"),
        business_date=days_ago(40),
    )
    return accounts  # type: ignore[no-any-return]


def loan_body(funding_account_id: int, principal: str = "300000", count: int = 3, **fields: Any) -> dict[str, Any]:
    on: date = fields.pop("on", None) or today()
    payload: dict[str, Any] = {
        "client_request_id": str(fields.pop("request", None) or uuid4()),
        "business_date": str(on),
        "principal": principal,
        "funding_account_id": funding_account_id,
        "installment_count": count,
        "first_due_on": str(fields.pop("first_due_on", None) or on),
    }
    schedule = fields.pop("installments", None)
    if schedule is not None:
        payload["installments"] = [{"due_on": str(d), "amount": a} for d, a in schedule]
    return payload | fields


def give(
    client: TestClient,
    employee_id: int,
    funding_account_id: int,
    principal: str = "300000",
    count: int = 3,
    **fields: Any,
) -> Any:
    return client.post(
        f"{EF}/employees/{employee_id}/loans", json=loan_body(funding_account_id, principal, count, **fields)
    )


def given(
    client: TestClient,
    employee_id: int,
    funding_account_id: int,
    principal: str = "300000",
    count: int = 3,
    **fields: Any,
) -> dict[str, Any]:
    response = give(client, employee_id, funding_account_id, principal, count, **fields)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def preview(
    client: TestClient,
    employee_id: int,
    funding_account_id: int,
    principal: str = "300000",
    count: int = 3,
    **fields: Any,
) -> Any:
    payload = loan_body(funding_account_id, principal, count, **fields)
    del payload["client_request_id"]
    return client.post(f"{EF}/employees/{employee_id}/loans/preview", json=payload)


def defer(
    client: TestClient,
    installment_id: int,
    replacements: list[tuple[date, str]],
    reason: str = "Pidió postergar",
    request: UUID | None = None,
) -> Any:
    return client.post(
        f"{EF}/loan-installments/{installment_id}/defer",
        json={
            "client_request_id": str(request or uuid4()),
            "reason": reason,
            "replacements": [{"due_on": str(d), "amount": a} for d, a in replacements],
        },
    )


def deferred(
    client: TestClient, installment_id: int, replacements: list[tuple[date, str]], **kw: Any
) -> dict[str, Any]:
    response = defer(client, installment_id, replacements, **kw)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def reverse_loan(
    client: TestClient, loan_id: int, reason: str = "Cargado por error", request: UUID | None = None
) -> Any:
    return client.post(
        f"{EF}/loans/{loan_id}/reverse", json={"client_request_id": str(request or uuid4()), "reason": reason}
    )


def installment(loan: dict[str, Any], number: int) -> dict[str, Any]:
    return next(i for i in loan["installments"] if i["number"] == number)


def schedule(loan: dict[str, Any]) -> list[tuple[int, str, str, str]]:
    """(number, due_on, amount, status) of every installment."""
    return [(i["number"], i["due_on"], i["amount"], i["status"]) for i in loan["installments"]]
