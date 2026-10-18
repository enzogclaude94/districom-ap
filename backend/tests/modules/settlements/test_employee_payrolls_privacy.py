"""What is seen of an internal payroll outside the area (R8-4, ADR 010 §20, Enzo's R8-4 point 16).

Everything lives under `settlements.employee_finance`: without the area every payroll endpoint answers 404 and Cajas
does not even list «Sueldos y bonos internos» (a PAYROLL_CLOSE moves no cash box, so nothing else appears there).
With the area, the operation shows whole and the internal account shows the credits as one movement."""

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from tests.modules.settlements.days import days_ago
from tests.modules.settlements.employee_finance_support import BASE, EF, error
from tests.modules.settlements.employee_movement_support import area_admin, area_viewer, outsider, registered
from tests.modules.settlements.employee_payroll_support import closed, line, opened


def _closed_payroll(user_as: Callable[..., TestClient], employee: Callable[[str], int]) -> tuple[int, dict[str, Any]]:
    admin = area_admin(user_as)
    pedro = employee("Pedro Gómez")
    registered(admin, "CONSUMPTION", pedro, "20000", on=days_ago(5))
    p = opened(admin, pedro, days_ago(30))
    line(admin, p["id"], "SALARY", "800000", "Sueldo de Pedro, período parcial")
    return pedro, closed(admin, p["id"])


def test_without_the_area_nothing_of_a_payroll_is_discoverable(
    user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    pedro, p = _closed_payroll(user_as, employee)
    client = outsider(user_as)
    for path in (f"{EF}/payrolls", f"{EF}/payrolls/{p['id']}", f"{EF}/payrolls?employee_id={pedro}"):
        assert error(client.get(path)) == (404, "not_found"), path
    for method, path in (
        ("post", f"{EF}/payrolls/{p['id']}/reopen"),
        ("post", f"{EF}/payrolls/{p['id']}/close"),
        ("put", f"{EF}/payrolls/{p['id']}/choices"),
        ("post", f"{EF}/payrolls/{p['id']}/lines"),
    ):
        assert error(getattr(client, method)(path, json={})) == (404, "not_found"), path
    accounts = client.get(f"{BASE}/accounts").json()
    assert all(a["code"] != "payroll_expense" for a in accounts)
    assert all(a["kind"] not in ("EMPLOYEE", "PAYROLL_EXPENSE") for a in accounts)
    detail = client.get(f"{BASE}/operations/{p['closing']['operation_id']}").json()
    assert (detail["type"], detail["entries"], detail["source_type"]) == ("EMPLOYEE_RESERVED", [], None)
    assert "Pedro" not in str(detail)


def test_with_the_area_the_close_and_its_credits_show(
    user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    pedro, p = _closed_payroll(user_as, employee)
    client = area_viewer(user_as)
    detail = client.get(f"{BASE}/operations/{p['closing']['operation_id']}").json()
    assert (detail["type"], detail["source_type"], detail["source_id"]) == (
        "PAYROLL_CLOSE",
        "employee_payroll",
        p["id"],
    )
    assert sorted((e["account_kind"], e["amount"]) for e in detail["entries"]) == [
        ("EMPLOYEE", "-800000.00"),
        ("PAYROLL_EXPENSE", "800000.00"),
    ]
    moves = client.get(f"{EF}/employees/{pedro}/movements").json()["items"]
    payroll = [m for m in moves if m["origin"] == "PAYROLL"]
    assert [(m["amount"], m["document_kind"], m["document_id"]) for m in payroll] == [
        ("-800000.00", "EMPLOYEE_PAYROLL", p["id"])
    ]
    assert [m["applied"] for m in moves if m["origin"] == "CONSUMPTION"] == ["20000.00"]
    assert {a["code"] for a in client.get(f"{BASE}/accounts").json()} >= {"payroll_expense"}
