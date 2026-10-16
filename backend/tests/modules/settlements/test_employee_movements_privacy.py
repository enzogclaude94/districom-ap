"""What Cajas and the internal account show of the movements of R8-2 (ADR 010, Enzo's R8-2 points 9 and 10).

Without the area an advance reads «Movimiento de empleado (reservado)» in its cash box or bank (date, amount, the
box, the direction) and the technical accounts of R8 are not discoverable; with it, everything. The internal account
lists R2-R7 and R8-2 movements together, each with its origin and document, and its balance is exact.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.modules.settlements.days import days_ago
from tests.modules.settlements.employee_finance_support import BASE, EF, error
from tests.modules.settlements.employee_movement_support import (
    area_admin,
    area_viewer,
    balance,
    internal_account,
    outsider,
    registered,
    reverse,
)
from tests.modules.settlements.test_settlement_close_api import close, count, create, employee


@pytest.fixture
def funded(post, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    post(
        "OPENING_BALANCE",
        (accounts["main_cash"], "500000"),
        (accounts["opening_balance"], "-500000"),
        business_date=days_ago(10),
    )
    return accounts  # type: ignore[no-any-return]


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


def _cash_movements(client: TestClient, account_id: int) -> list[dict[str, Any]]:
    response = client.get(f"{BASE}/accounts/{account_id}/movements")
    assert response.status_code == 200, response.text
    return response.json()["items"]  # type: ignore[no-any-return]


def test_without_the_area_an_advance_is_reserved_in_its_cash_box(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int]
) -> None:
    admin = area_admin(user_as)
    advance = registered(
        admin, "ADVANCE", pedro, "100000", funding_account_id=funded["main_cash"], description="Adelanto quincena"
    )
    reversed_ = reverse(admin, advance["id"])
    assert reversed_.status_code == 200
    registered(admin, "CONSUMPTION", pedro, "5000")
    registered(admin, "ADJUSTMENT", pedro, "700", direction="CREDIT")

    client = outsider(user_as)
    rows = [m for m in _cash_movements(client, funded["main_cash"]) if m["operation_type"] != "OPENING_BALANCE"]
    assert sorted((m["operation_type"], m["amount"], m["description"], m["source_type"]) for m in rows) == [
        ("EMPLOYEE_RESERVED", "-100000.00", "Movimiento de empleado (reservado)", None),
        ("EMPLOYEE_RESERVED", "100000.00", "Movimiento de empleado (reservado)", None),
    ]
    assert all(m["business_date"] == advance["business_date"] for m in rows)
    detail = client.get(f"{BASE}/operations/{advance['operation_id']}").json()
    assert (detail["type"], detail["description"], detail["source_type"], detail["source_id"]) == (
        "EMPLOYEE_RESERVED",
        "Movimiento de empleado (reservado)",
        None,
        None,
    )
    assert [(e["account_kind"], e["amount"]) for e in detail["entries"]] == [("CASH_BOX", "-100000.00")]
    reversal = client.get(f"{BASE}/operations/{reversed_.json()['reversal_operation_id']}").json()
    assert reversal["type"] == "EMPLOYEE_RESERVED"
    assert [(e["account_kind"], e["amount"]) for e in reversal["entries"]] == [("CASH_BOX", "100000.00")]
    assert "Pedro" not in str(detail) + str(reversal) + str(rows)
    # The technical accounts and the internal account are not discoverable.
    listed = {a["kind"] for a in client.get(f"{BASE}/accounts", params={"include_inactive": True}).json()}
    assert listed.isdisjoint({"EMPLOYEE", "EMPLOYEE_CONSUMPTIONS", "EMPLOYEE_ADJUSTMENTS"})
    for code in ("employee_consumptions", "employee_adjustments"):
        assert error(client.get(f"{BASE}/accounts/{funded[code]}")) == (404, "account_not_found")
        assert error(client.get(f"{BASE}/accounts/{funded[code]}/movements")) == (404, "account_not_found")
    # Nor the movement documents.
    assert error(client.get(f"{EF}/employee-movements/{advance['id']}")) == (404, "not_found")


def test_with_the_area_cajas_shows_the_whole_operation(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int]
) -> None:
    admin = area_admin(user_as)
    advance = registered(admin, "ADVANCE", pedro, "100000", funding_account_id=funded["main_cash"])
    consumption = registered(admin, "CONSUMPTION", pedro, "5000")
    client = area_viewer(user_as)
    detail = client.get(f"{BASE}/operations/{advance['operation_id']}").json()
    assert (detail["type"], detail["source_type"], detail["source_id"]) == (
        "EMPLOYEE_ADVANCE",
        "employee_movement",
        advance["id"],
    )
    assert sorted(e["account_kind"] for e in detail["entries"]) == ["CASH_BOX", "EMPLOYEE"]
    listed = {a["kind"] for a in client.get(f"{BASE}/accounts").json()}
    assert {"EMPLOYEE", "EMPLOYEE_CONSUMPTIONS", "EMPLOYEE_ADJUSTMENTS"} <= listed
    technical = client.get(f"{BASE}/accounts/{funded['employee_consumptions']}").json()
    assert (technical["name"], technical["balance"]) == ("Consumos de empleados", "-5000.00")
    shown = client.get(f"{EF}/employee-movements/{consumption['id']}").json()
    assert (shown["employee_name"], shown["status"]) == ("Pedro Gómez", "IN_EFFECT")


def _shortage(as_role: Any, ids: dict[str, int], who: int) -> int:
    operator = as_role("operator")
    sid = create(operator, who, "100000")
    count(operator, sid, ("ARS_20000", 4, ids["main_cash"]))
    response = close(operator, sid, employee(who, "20000"))
    assert response.status_code == 201, response.text
    return sid  # type: ignore[no-any-return]


def test_the_internal_account_reads_r2_and_r8_movements_together(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int], as_role, db: Session
) -> None:  # type: ignore[no-untyped-def]
    settlement = _shortage(as_role, funded, pedro)  # +20.000
    admin = area_admin(user_as)
    consumption = registered(admin, "CONSUMPTION", pedro, "20000", description="Gaseosas")  # +20.000
    advance = registered(admin, "ADVANCE", pedro, "100000", funding_account_id=funded["main_cash"])  # +100.000
    charge = registered(admin, "ADJUSTMENT", pedro, "3000", direction="CHARGE", description="Rotura")  # +3.000
    credit = registered(admin, "ADJUSTMENT", pedro, "15000", direction="CREDIT", description="Reconocimiento")
    assert reverse(admin, charge["id"], "No correspondía").status_code == 200  # -3.000
    client = area_viewer(user_as)
    moves = client.get(f"{EF}/employees/{pedro}/movements").json()["items"]
    assert sorted(
        (m["origin"], m["amount"], m["reading"], m["document_kind"], m["document_id"], m["description"]) for m in moves
    ) == sorted(
        [
            ("SETTLEMENT_SHORTAGE", "20000.00", "CHARGE", "DELIVERY_SETTLEMENT", settlement, None),
            ("CONSUMPTION", "20000.00", "CHARGE", "EMPLOYEE_MOVEMENT", consumption["id"], "Gaseosas"),
            ("ADVANCE", "100000.00", "CHARGE", "EMPLOYEE_MOVEMENT", advance["id"], "advance de prueba"),
            ("ADJUSTMENT_CHARGE", "3000.00", "CHARGE", "EMPLOYEE_MOVEMENT", charge["id"], "Rotura"),
            ("ADJUSTMENT_REVERSAL", "-3000.00", "CREDIT", "EMPLOYEE_MOVEMENT", charge["id"], "Rotura"),
            ("ADJUSTMENT_CREDIT", "-15000.00", "CREDIT", "EMPLOYEE_MOVEMENT", credit["id"], "Reconocimiento"),
        ]
    )
    reversed_flags = {(m["origin"], m["document_reversed"]) for m in moves if m["document_kind"] == "EMPLOYEE_MOVEMENT"}
    assert ("ADJUSTMENT_CHARGE", True) in reversed_flags
    assert ("CONSUMPTION", False) in reversed_flags
    # The balance is exactly the sum of the legs: 20.000 + 20.000 + 100.000 + 3.000 - 3.000 - 15.000.
    row = client.get(f"{EF}/employees/{pedro}").json()
    assert (row["balance"], row["balance_reading"]) == ("125000.00", "OWES_COMPANY")
    assert balance(db, internal_account(db, pedro)) == Decimal("125000")
    assert sum(Decimal(m["amount"]) for m in moves) == Decimal("125000")


def test_balance_can_be_in_favour_or_zero(user_as: Callable[..., TestClient], pedro: int) -> None:
    admin = area_admin(user_as)
    registered(admin, "CONSUMPTION", pedro, "1000")
    credit = registered(admin, "ADJUSTMENT", pedro, "4000", direction="CREDIT")
    row = admin.get(f"{EF}/employees/{pedro}").json()
    assert (row["balance"], row["balance_reading"]) == ("-3000.00", "COMPANY_OWES")
    assert reverse(admin, credit["id"]).status_code == 200
    registered(admin, "ADJUSTMENT", pedro, "1000", direction="CREDIT")
    row = admin.get(f"{EF}/employees/{pedro}").json()
    assert (row["balance"], row["balance_reading"]) == ("0.00", "NONE")
