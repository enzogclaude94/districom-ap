"""The internal account of an employee, read-only (R8-1, ADR 010).

It shows what R2-R7 already write on the employee's `EMPLOYEE` account (differences of settlements, pending
write-offs and their reversals), read from the ledger and the source documents, never copied. Positive: the
employee owes the company; negative: the company owes the employee.
"""

from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.employee_finance_support import EF
from tests.modules.settlements.test_pending_receipts_resolutions_api import new_pending, resolved, reverse
from tests.modules.settlements.test_settlement_close_api import close, count, create, employee, reopen


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def juan(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Juan Repartidor")  # type: ignore[no-any-return]


def _reader(user_as: Any) -> TestClient:
    return user_as("socio", settlements="viewer", area="viewer")  # type: ignore[no-any-return]


def _shortage(as_role: Any, ids: dict[str, int], who: int, expected: str, counted_20k: int) -> int:
    operator = as_role("operator")
    sid = create(operator, who, expected)
    count(operator, sid, ("ARS_20000", counted_20k, ids["main_cash"]))
    short = Decimal(expected) - counted_20k * 20000
    response = close(operator, sid, employee(who, f"{abs(short)}"))
    assert response.status_code == 201, response.text
    return sid


def _movements(client: TestClient, employee_id: int) -> list[dict[str, Any]]:
    response = client.get(f"{EF}/employees/{employee_id}/movements")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["total"] == len(body["items"])
    return body["items"]  # type: ignore[no-any-return]


def _row(client: TestClient, employee_id: int) -> dict[str, Any]:
    response = client.get(f"{EF}/employees/{employee_id}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_shortage_charges_and_surplus_credits(as_role, ids, pedro, user_as) -> None:  # type: ignore[no-untyped-def]
    short = _shortage(as_role, ids, pedro, "100000", 4)  # $20.000 short
    over = _shortage(as_role, ids, pedro, "100000", 6)  # $20.000 over
    client = _reader(user_as)
    moves = _movements(client, pedro)
    assert sorted((m["origin"], m["amount"], m["reading"], m["document_kind"], m["document_id"]) for m in moves) == [
        ("SETTLEMENT_SHORTAGE", "20000.00", "CHARGE", "DELIVERY_SETTLEMENT", short),
        ("SETTLEMENT_SURPLUS", "-20000.00", "CREDIT", "DELIVERY_SETTLEMENT", over),
    ]
    assert all(m["document_number"] is not None and m["document_date"] is not None for m in moves)
    row = _row(client, pedro)
    assert (row["balance"], row["balance_reading"], row["has_account"]) == ("0.00", "NONE", True)


def test_positive_negative_and_no_balance(as_role, ids, pedro, juan, driver, user_as) -> None:  # type: ignore[no-untyped-def]
    _shortage(as_role, ids, pedro, "100000", 4)
    _shortage(as_role, ids, juan, "100000", 6)
    nobody = driver("Nadie Sin Cuenta")
    client = _reader(user_as)
    listed = {e["employee_id"]: e for e in client.get(f"{EF}/employees").json()["items"]}
    assert (listed[pedro]["balance"], listed[pedro]["balance_reading"]) == ("20000.00", "OWES_COMPANY")
    assert (listed[juan]["balance"], listed[juan]["balance_reading"]) == ("-20000.00", "COMPANY_OWES")
    assert (listed[nobody]["balance"], listed[nobody]["balance_reading"]) == ("0.00", "NONE")
    assert listed[nobody]["current_term"] is None
    assert _row(client, nobody)["has_account"] is False
    assert _movements(client, nobody) == []
    # Search by name.
    found = client.get(f"{EF}/employees", params={"q": "nadie"}).json()
    assert [e["employee_id"] for e in found["items"]] == [nobody]


def test_a_settlement_correction_shows_its_reversal_with_the_same_document(as_role, ids, pedro, user_as) -> None:  # type: ignore[no-untyped-def]
    """Reopen and close again: the reversal of the old close and the new close, both pointing at the settlement."""
    sid = _shortage(as_role, ids, pedro, "100000", 4)
    assert reopen(as_role("admin"), sid, 1).status_code == 200
    operator = as_role("operator")
    count(operator, sid, ("ARS_20000", 4, ids["main_cash"]), ("ARS_10000", 1, ids["main_cash"]))
    assert close(operator, sid, employee(pedro, "10000"), version=2).status_code == 201
    moves = _movements(_reader(user_as), pedro)
    assert sorted((m["origin"], m["amount"], m["document_id"]) for m in moves) == [
        ("SETTLEMENT_CORRECTION", "-20000.00", sid),
        ("SETTLEMENT_SHORTAGE", "10000.00", sid),
        ("SETTLEMENT_SHORTAGE", "20000.00", sid),
    ]
    assert _row(_reader(user_as), pedro)["balance"] == "10000.00"


def test_pending_write_off_and_its_reversal(as_role, ids, pedro, user_as, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    sid = create(operator, pedro, "1000000")
    count(operator, sid, ("ARS_20000", 45, ids["main_cash"]))
    pid = new_pending(operator, sid)
    assert close(operator, sid).status_code == 201
    admin = as_role("admin")
    body = resolved(admin, pid, "100000", [(pedro, "30000"), (None, "70000")])
    reversed_ = reverse(admin, pid, "resolutions", body["resolutions"][0]["id"])
    assert reversed_.status_code == 201, reversed_.text
    moves = _movements(_reader(user_as), pedro)
    assert sorted((m["origin"], m["amount"], m["document_kind"], m["document_id"]) for m in moves) == [
        ("PENDING_WRITE_OFF", "30000.00", "PENDING_COLLECTION", pid),
        ("PENDING_WRITE_OFF_REVERSAL", "-30000.00", "PENDING_COLLECTION", pid),
    ]
    # Read, never copied: the balance is the ledger's.
    ledger = db.execute(
        text(
            "SELECT sum(e.amount) FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id "
            "WHERE a.kind = 'EMPLOYEE' AND a.employee_id = :e"
        ),
        {"e": pedro},
    ).scalar_one()
    assert Decimal(_row(_reader(user_as), pedro)["balance"]) == ledger == 0


def test_an_inactive_employee_keeps_their_account(as_role, ids, pedro, user_as, db: Session) -> None:  # type: ignore[no-untyped-def]
    _shortage(as_role, ids, pedro, "100000", 4)
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": pedro})
    db.commit()
    client = _reader(user_as)
    listed = {e["employee_id"]: e for e in client.get(f"{EF}/employees").json()["items"]}
    assert (listed[pedro]["active"], listed[pedro]["balance"]) == (False, "20000.00")
    assert len(_movements(client, pedro)) == 1


def test_reading_writes_nothing(as_role, ids, pedro, user_as, db: Session) -> None:  # type: ignore[no-untyped-def]
    _shortage(as_role, ids, pedro, "100000", 4)

    def snapshot() -> tuple[int, int, int]:
        row = db.execute(
            text(
                "SELECT (SELECT count(*) FROM settlements.accounts), (SELECT count(*) FROM settlements.ledger_entries),"
                " (SELECT count(*) FROM settlements.financial_operations)"
            )
        ).one()
        return (row[0], row[1], row[2])

    before = snapshot()
    client = _reader(user_as)
    client.get(f"{EF}/employees")
    _row(client, pedro)
    _movements(client, pedro)
    db.expire_all()
    assert snapshot() == before
    assert client.get(f"{EF}/employees/999999").status_code == 404
