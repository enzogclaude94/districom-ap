"""What Cajas and the internal account show of a payout (R8-5, ADR 010, Enzo's R8-5 point 13).

Without the area a payout reads «Movimiento de empleado (reservado)» in its cash box or bank (date, amount, the box,
the direction): no employee, payroll, salary, reference, net nor the internal account. With the area, the whole
operation, «Pago de liquidación interna», with its payout as source.
"""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.modules.settlements.employee_finance_support import BASE, EF, error
from tests.modules.settlements.employee_loan_support import funded
from tests.modules.settlements.employee_movement_support import area_viewer, outsider
from tests.modules.settlements.employee_payout_support import area_admin, net_payroll, paid, reverse_payout

__all__ = ["funded"]


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


def _movements(client: TestClient, account_id: int) -> list[dict[str, Any]]:
    response = client.get(f"{BASE}/accounts/{account_id}/movements")
    assert response.status_code == 200, response.text
    return [m for m in response.json()["items"] if m["operation_type"] != "OPENING_BALANCE"]


def test_without_the_area_a_payout_is_reserved_in_its_cash_box_and_bank(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int]
) -> None:
    admin = area_admin(user_as)
    p = net_payroll(admin, pedro, "124456")
    x = paid(
        admin, p, ("CASH", funded["main_cash"], "23456"), ("TRANSFER", funded["bank"], "100000"), reference="REC-99"
    )
    other = paid(admin, p, ("CASH", funded["main_cash"], "1000"))
    reversal = reverse_payout(admin, other["id"], "Se pagó al empleado equivocado").json()

    client = outsider(user_as)
    cash = _movements(client, funded["main_cash"])
    assert sorted((m["operation_type"], m["amount"], m["description"], m["source_type"]) for m in cash) == [
        ("EMPLOYEE_RESERVED", "-1000.00", "Movimiento de empleado (reservado)", None),
        ("EMPLOYEE_RESERVED", "-23456.00", "Movimiento de empleado (reservado)", None),
        ("EMPLOYEE_RESERVED", "1000.00", "Movimiento de empleado (reservado)", None),
    ]
    bank = _movements(client, funded["bank"])
    assert [(m["operation_type"], m["amount"]) for m in bank] == [("EMPLOYEE_RESERVED", "-100000.00")]
    detail = client.get(f"{BASE}/operations/{x['operation_id']}").json()
    assert (detail["type"], detail["source_type"], detail["source_id"]) == ("EMPLOYEE_RESERVED", None, None)
    assert sorted((e["account_kind"], e["amount"]) for e in detail["entries"]) == [
        ("BANK", "-100000.00"),
        ("CASH_BOX", "-23456.00"),
    ]
    undone = client.get(f"{BASE}/operations/{reversal['reversal_operation_id']}").json()
    assert undone["type"] == "EMPLOYEE_RESERVED"
    shown = str(cash) + str(bank) + str(detail) + str(undone)
    for secret in ("Pedro", "REC-99", "124456", "liquidación", "Liquidación", "pago n.º", "equivocado", 'EMPLOYEE"'):
        assert secret not in shown, secret
    # Nothing of R8-5 is discoverable.
    assert error(client.get(f"{EF}/payouts/{x['id']}")) == (404, "not_found")
    assert error(client.get(f"{EF}/payouts")) == (404, "not_found")
    assert error(client.get(f"{EF}/employees/{pedro}/pay-summary")) == (404, "not_found")
    assert error(client.post(f"{EF}/payrolls/{p['id']}/payouts/preview", json={})) == (404, "not_found")
    assert error(client.post(f"{EF}/payouts/{x['id']}/reverse", json={})) == (404, "not_found")


def test_with_the_area_cajas_shows_the_payout(
    user_as: Callable[..., TestClient], pedro: int, funded: dict[str, int]
) -> None:
    p = net_payroll(area_admin(user_as), pedro, "100000")
    x = paid(area_admin(user_as), p, ("CASH", funded["main_cash"], "100000"))
    client = area_viewer(user_as)
    detail = client.get(f"{BASE}/operations/{x['operation_id']}").json()
    assert (detail["type"], detail["source_type"], detail["source_id"]) == (
        "EMPLOYEE_PAYOUT",
        "employee_payout",
        x["id"],
    )
    assert detail["description"].startswith("Pago de liquidación interna")
    assert sorted((e["account_kind"], e["amount"]) for e in detail["entries"]) == [
        ("CASH_BOX", "-100000.00"),
        ("EMPLOYEE", "100000.00"),
    ]
    shown = client.get(f"{EF}/payouts/{x['id']}").json()
    assert (shown["employee_name"], shown["payroll_id"], shown["in_effect"]) == ("Pedro Gómez", p["id"], True)
