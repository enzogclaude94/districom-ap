"""What Cajas and the internal account show of a loan (R8-3, ADR 010, Enzo's R8-3 points 10 and 11).

Without the area a loan reads «Movimiento de empleado (reservado)» in its cash box (date, amount, the box, the
direction): no employee, loan, schedule, reference nor description. With the area, everything. The internal account
shows a loan as one movement (never one per installment) next to R2-R8 movements, and its balance is exact.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.modules.settlements.employee_finance_support import BASE, EF, error
from tests.modules.settlements.employee_loan_support import deferred, funded, given, installment, reverse_loan
from tests.modules.settlements.employee_movement_support import (
    area_admin,
    area_viewer,
    balance,
    internal_account,
    outsider,
    registered,
)
from tests.modules.settlements.test_employee_movements_privacy import _shortage

__all__ = ["funded"]


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


def _cash_movements(client: TestClient, account_id: int) -> list[dict[str, Any]]:
    response = client.get(f"{BASE}/accounts/{account_id}/movements")
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


def test_without_the_area_a_loan_is_reserved_in_its_cash_box(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int]
) -> None:
    admin = area_admin(user_as)
    loan = given(admin, pedro, funded["main_cash"], "300000", 3, description="Préstamo moto Pedro", reference="PRES-77")
    other = given(admin, pedro, funded["main_cash"], "1000", 1)
    reversed_ = reverse_loan(admin, other["id"])
    assert reversed_.status_code == 200

    client = outsider(user_as)
    rows = [m for m in _cash_movements(client, funded["main_cash"]) if m["operation_type"] != "OPENING_BALANCE"]
    assert sorted((m["operation_type"], m["amount"], m["description"], m["source_type"]) for m in rows) == [
        ("EMPLOYEE_RESERVED", "-1000.00", "Movimiento de empleado (reservado)", None),
        ("EMPLOYEE_RESERVED", "-300000.00", "Movimiento de empleado (reservado)", None),
        ("EMPLOYEE_RESERVED", "1000.00", "Movimiento de empleado (reservado)", None),
    ]
    detail = client.get(f"{BASE}/operations/{loan['operation_id']}").json()
    assert (detail["type"], detail["description"], detail["source_type"], detail["source_id"]) == (
        "EMPLOYEE_RESERVED",
        "Movimiento de empleado (reservado)",
        None,
        None,
    )
    assert [(e["account_kind"], e["amount"]) for e in detail["entries"]] == [("CASH_BOX", "-300000.00")]
    reversal = client.get(f"{BASE}/operations/{reversed_.json()['reversal_operation_id']}").json()
    assert reversal["type"] == "EMPLOYEE_RESERVED"
    shown = str(rows) + str(detail) + str(reversal)
    for secret in ("Pedro", "PRES-77", "moto", "préstamo n.º", "Préstamo", "cuota"):
        assert secret not in shown, secret
    # The loan, its schedule and the employee's loans are not discoverable.
    assert error(client.get(f"{EF}/loans/{loan['id']}")) == (404, "not_found")
    assert error(client.get(f"{EF}/employees/{pedro}/loans")) == (404, "not_found")
    two = installment(loan, 2)["id"]
    assert error(client.post(f"{EF}/loan-installments/{two}/defer", json={})) == (404, "not_found")


def test_with_the_area_cajas_shows_the_whole_operation(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int]
) -> None:
    loan = given(area_admin(user_as), pedro, funded["main_cash"], "300000", 3)
    client = area_viewer(user_as)
    detail = client.get(f"{BASE}/operations/{loan['operation_id']}").json()
    assert (detail["type"], detail["source_type"], detail["source_id"]) == (
        "EMPLOYEE_LOAN",
        "employee_loan",
        loan["id"],
    )
    assert sorted((e["account_kind"], e["amount"]) for e in detail["entries"]) == [
        ("CASH_BOX", "-300000.00"),
        ("EMPLOYEE", "300000.00"),
    ]
    shown = client.get(f"{EF}/loans/{loan['id']}").json()
    assert (shown["employee_name"], shown["status"], len(shown["installments"])) == ("Pedro Gómez", "ACTIVE", 3)


def test_the_internal_account_shows_a_loan_as_one_movement_next_to_the_rest(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int], as_role: Any, db: Session
) -> None:
    """Loan + shortage + consumption + advance, and a reversed loan: the balance reconciles exactly."""
    settlement = _shortage(as_role, funded, pedro)  # +20.000
    admin = area_admin(user_as)
    loan = given(admin, pedro, funded["main_cash"], "300000", 3, description="Moto", first_due_on=date(2026, 11, 30))
    deferred(admin, installment(loan, 2)["id"], [(date(2027, 2, 28), "100000")])  # moves nothing
    consumption = registered(admin, "CONSUMPTION", pedro, "20000", description="Gaseosas")  # +20.000
    advance = registered(admin, "ADVANCE", pedro, "50000", funding_account_id=funded["main_cash"])  # +50.000
    wrong = given(admin, pedro, funded["bank"], "10000", 1)  # +10.000
    assert reverse_loan(admin, wrong["id"], "Otro empleado").status_code == 200  # -10.000
    client = area_viewer(user_as)
    moves = client.get(f"{EF}/employees/{pedro}/movements").json()["items"]
    assert sorted(
        (m["origin"], m["amount"], m["document_kind"], m["document_id"], m["description"], m["document_reversed"])
        for m in moves
    ) == sorted(
        [
            ("SETTLEMENT_SHORTAGE", "20000.00", "DELIVERY_SETTLEMENT", settlement, None, None),
            ("LOAN", "300000.00", "EMPLOYEE_LOAN", loan["id"], "Moto", False),
            ("CONSUMPTION", "20000.00", "EMPLOYEE_MOVEMENT", consumption["id"], "Gaseosas", False),
            ("ADVANCE", "50000.00", "EMPLOYEE_MOVEMENT", advance["id"], "advance de prueba", False),
            ("LOAN", "10000.00", "EMPLOYEE_LOAN", wrong["id"], None, True),
            ("LOAN_REVERSAL", "-10000.00", "EMPLOYEE_LOAN", wrong["id"], None, True),
        ]
    )
    loan_moves = [m for m in moves if m["document_id"] == loan["id"] and m["document_kind"] == "EMPLOYEE_LOAN"]
    assert len(loan_moves) == 1  # never one per installment
    assert all(m["reading"] == "CHARGE" for m in loan_moves)
    # 20.000 + 300.000 + 20.000 + 50.000 + 10.000 - 10.000
    row = client.get(f"{EF}/employees/{pedro}").json()
    assert (row["balance"], row["balance_reading"]) == ("390000.00", "OWES_COMPANY")
    assert balance(db, internal_account(db, pedro)) == Decimal("390000")
    assert sum(Decimal(m["amount"]) for m in moves) == Decimal("390000")
    # The loan's own balance comes from the loan, never from the internal account.
    assert client.get(f"{EF}/loans/{loan['id']}").json()["balance"] == "300000.00"
    assert client.get(f"{EF}/loans/{wrong['id']}").json()["balance"] == "0.00"
