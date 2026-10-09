"""Close and reopen of delivery settlements towards the ledger (R2-2).

The numbers in brackets are the tests Enzo asked for R2-2. Concurrency ([15], [29] and the rest) is
in `test_settlement_close_concurrency.py`; the database rules in `test_settlement_close_db_invariants.py`.
"""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.ledger import service as ledger
from app.platform.auth.models import User

BASE = "/api/v1/settlements"
DAY = business_today() - timedelta(days=1)
REASON = "Faltaba cargar un billete de $500"


# --- helpers --------------------------------------------------------------------------------------


def create(client: TestClient, employee_id: int, expected: str) -> int:
    response = client.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": DAY.isoformat(),
            "employee_id": employee_id,
            "expected_amount": expected,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def count(client: TestClient, sid: int, *lines: tuple[str, int, int]) -> None:
    body = [{"denomination_code": c, "quantity": q, "destination_account_id": box} for c, q, box in lines]
    response = client.put(f"{BASE}/deliveries/{sid}/count", json={"lines": body})
    assert response.status_code == 200, response.text


def transfer(client: TestClient, sid: int, amount: str, status: str | None = None) -> int:
    response = client.post(
        f"{BASE}/deliveries/{sid}/transfers", json={"client_request_id": str(uuid4()), "amount": amount}
    )
    assert response.status_code == 201, response.text
    tid: int = response.json()["transfers"][-1]["id"]
    if status:
        reason = "No figura en el banco" if status == "REJECTED" else None
        decided = client.post(
            f"{BASE}/deliveries/{sid}/transfers/{tid}/status", json={"status": status, "reason": reason}
        )
        assert decided.status_code == 200, decided.text
    return tid


def expense(client: TestClient, sid: int, category: int, amount: str) -> None:
    response = client.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": category, "amount": amount},
    )
    assert response.status_code == 201, response.text


def employee(employee_id: int, amount: str) -> dict[str, Any]:
    return {"target": "EMPLOYEE", "employee_id": employee_id, "amount": amount}


def company(amount: str, reason: str | None = "Diferencia menor absorbida por la empresa") -> dict[str, Any]:
    return {"target": "COMPANY", "amount": amount, "reason": reason}


def preview_token(client: TestClient, sid: int) -> str:
    response = client.get(f"{BASE}/deliveries/{sid}/closing-preview")
    assert response.status_code == 200, response.text
    return response.json()["preview_token"]  # type: ignore[no-any-return]


def close(
    client: TestClient,
    sid: int,
    *allocations: dict[str, Any],
    version: int = 1,
    confirm: bool = True,
    token: str | None = None,
) -> Any:
    """Close as the screen does: preview first (unless a token is given), then confirm with its token."""
    return client.post(
        f"{BASE}/deliveries/{sid}/close",
        json={
            "version": version,
            "preview_token": token or preview_token(client, sid),
            "allocations": list(allocations),
            "confirm": confirm,
        },
    )


def reopen(client: TestClient, sid: int, version: int, reason: str = REASON) -> Any:
    return client.post(f"{BASE}/deliveries/{sid}/reopen", json={"version": version, "reason": reason})


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json().get("code")


def balances(client: TestClient) -> dict[str, str]:
    return {a["code"] or a["name"]: a["balance"] for a in client.get(f"{BASE}/accounts").json()}


def legs(db: Session, operation_id: int) -> dict[str, str]:
    rows = db.execute(
        text(
            "SELECT coalesce(a.code, 'employee:' || a.employee_id), e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :o"
        ),
        {"o": operation_id},
    )
    return {key: str(amount) for key, amount in rows.all()}


def count_rows(db: Session, table: str) -> int:
    return int(db.execute(text(f"SELECT count(*) FROM settlements.{table}")).scalar_one())  # noqa: S608


@pytest.fixture
def ids(as_role) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {a["code"]: a["id"] for a in as_role("admin").get(f"{BASE}/accounts").json()}


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def juan(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Juan Acompañante")  # type: ignore[no-any-return]


@pytest.fixture
def tolls(as_role) -> int:  # type: ignore[no-untyped-def]
    response = as_role("admin").post(f"{BASE}/expense-categories", json={"name": "Peajes"})
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


@pytest.fixture
def balanced(as_role, ids, pedro, tolls) -> int:  # type: ignore[no-untyped-def]
    """$100.000 expected: $60.000 cash (main and change boxes), $30.000 verified, $10.000 of expenses."""
    operator = as_role("operator")
    sid = create(operator, pedro, "100000")
    count(operator, sid, ("ARS_20000", 2, ids["main_cash"]), ("ARS_10000", 2, ids["change_cash"]))
    transfer(operator, sid, "30000", "VERIFIED")
    expense(operator, sid, tolls, "10000")
    return sid


@pytest.fixture
def short(as_role, ids, pedro) -> int:  # type: ignore[no-untyped-def]
    """$100.000 expected, $99.000 counted: a $1.000 shortage."""
    operator = as_role("operator")
    sid = create(operator, pedro, "100000")
    count(operator, sid, ("ARS_20000", 4, ids["main_cash"]), ("ARS_1000", 19, ids["change_cash"]))
    return sid


# --- balanced close -------------------------------------------------------------------------------


def test_balanced_close_publishes_one_balanced_operation(as_role, ids, balanced, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[1] [17] Legs of a close without difference, summing zero, with the settlement's business date."""
    operator = as_role("operator")
    preview = operator.get(f"{BASE}/deliveries/{balanced}/closing-preview").json()
    assert (preview["difference"], preview["amount_to_allocate"], preview["blockers"]) == ("0.00", "0.00", [])
    assert {leg["account_name"]: leg["amount"] for leg in preview["legs"]} == {
        "Caja principal": "40000.00",
        "Caja de cambio": "20000.00",
        "Banco": "30000.00",
        "Cobranzas de reparto": "-100000.00",
        "Gastos operativos": "10000.00",
    }

    response = close(operator, balanced)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["financial_state"], body["version"]) == ("CLOSED", "CLOSED", 1)
    assert body["current_difference"] is None
    operation_id = body["close_in_effect"]["operation_id"]
    assert legs(db, operation_id) == {
        "main_cash": "40000.00",
        "change_cash": "20000.00",
        "bank": "30000.00",
        "delivery_collections": "-100000.00",
        "expense": "10000.00",
    }
    assert (
        db.execute(
            text("SELECT sum(amount) FROM settlements.ledger_entries WHERE operation_id = :o"), {"o": operation_id}
        ).scalar_one()
        == 0
    )
    operation = db.execute(
        text("SELECT type, business_date, source_type, source_id FROM settlements.financial_operations WHERE id = :o"),
        {"o": operation_id},
    ).one()
    assert tuple(operation) == ("SETTLEMENT_CLOSE", DAY, "settlement", balanced)
    assert balances(operator)["main_cash"] == "40000.00"


def test_balanced_close_creates_no_difference(as_role, balanced, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[2]"""
    assert close(as_role("operator"), balanced).status_code == 201
    assert count_rows(db, "settlement_differences") == 0
    assert count_rows(db, "difference_allocations") == 0


def test_close_is_never_automatic_even_at_zero(as_role, balanced, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert operator.get(f"{BASE}/deliveries/{balanced}").json()["financial_state"] == "NOT_CLOSED"
    assert error(close(operator, balanced, confirm=False)) == (422, "confirmation_required")
    assert count_rows(db, "financial_operations") == 0
    assert operator.get(f"{BASE}/deliveries/{balanced}").json()["status"] == "OPEN"


# --- shortage -------------------------------------------------------------------------------------


def test_shortage_to_one_employee(as_role, short, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[3] The driver owes the $1.000: his internal account goes +1.000 (positive: he owes the company)."""
    operator = as_role("operator")
    response = close(operator, short, employee(pedro, "1000"))
    assert response.status_code == 201, response.text
    body = response.json()
    difference = body["current_difference"]
    assert (difference["kind"], difference["amount"], difference["settlement_version"]) == ("SHORTAGE", "1000.00", 1)
    (allocation,) = difference["allocations"]
    assert (allocation["target"], allocation["employee_id"], allocation["employee_name"], allocation["amount"]) == (
        "EMPLOYEE",
        pedro,
        "Pedro Gómez",
        "1000.00",
    )
    assert allocation["decided_by_name"] == "Usuario Test"
    assert datetime.fromisoformat(allocation["decided_at"]) <= datetime.now(UTC)
    assert legs(db, body["close_in_effect"]["operation_id"]) == {
        "main_cash": "80000.00",
        "change_cash": "19000.00",
        "delivery_collections": "-100000.00",
        f"employee:{pedro}": "1000.00",
    }
    accounts = as_role("viewer").get(f"{BASE}/accounts").json()
    (internal,) = [a for a in accounts if a["kind"] == "EMPLOYEE"]
    assert (internal["name"], internal["balance"]) == (f"Cuenta interna de Pedro Gómez (#{pedro})", "1000.00")


def test_shortage_split_among_employees(as_role, short, pedro, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[4]"""
    response = close(as_role("operator"), short, employee(pedro, "600"), employee(juan, "400"))
    assert response.status_code == 201, response.text
    operation_id = response.json()["close_in_effect"]["operation_id"]
    assert legs(db, operation_id)[f"employee:{pedro}"] == "600.00"
    assert legs(db, operation_id)[f"employee:{juan}"] == "400.00"
    assert count_rows(db, "difference_allocations") == 2


def test_shortage_split_between_employee_and_company(as_role, short, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[5] The company's part goes to Diferencias de rendición, decided by an admin with a reason."""
    response = close(as_role("admin"), short, employee(pedro, "700"), company("300"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert legs(db, body["close_in_effect"]["operation_id"])["settlement_difference"] == "300.00"
    parts = {a["target"]: (a["amount"], a["reason"]) for a in body["current_difference"]["allocations"]}
    assert parts == {
        "EMPLOYEE": ("700.00", None),
        "COMPANY": ("300.00", "Diferencia menor absorbida por la empresa"),
    }
    audit = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = 'settlements.settlement.closed'")
    ).scalar_one()
    assert {a["target"] for a in audit["after"]["allocations"]} == {"EMPLOYEE", "COMPANY"}


def test_company_allocation_needs_an_admin(as_role, short, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[6]"""
    response = close(as_role("operator"), short, employee(pedro, "700"), company("300"))
    assert error(response) == (403, "company_allocation_requires_admin")
    assert count_rows(db, "financial_operations") == 0


def test_company_allocation_needs_a_reason(as_role, short, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[7]"""
    admin = as_role("admin")
    assert error(close(admin, short, company("1000", reason=None))) == (422, "reason_required")
    assert error(close(admin, short, company("1000", reason="   "))) == (422, "reason_required")
    assert count_rows(db, "financial_operations") == 0


def test_the_difference_must_be_allocated_exactly(as_role, short, balanced, pedro, juan) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert error(close(operator, short)) == (422, "difference_not_fully_allocated")
    assert error(close(operator, short, employee(pedro, "999.99"))) == (422, "difference_not_fully_allocated")
    assert error(close(operator, short, employee(pedro, "1000.01"))) == (422, "difference_allocation_mismatch")
    assert error(close(operator, short, employee(pedro, "500"), employee(pedro, "500"))) == (
        422,
        "difference_allocation_mismatch",
    )
    assert error(close(operator, balanced, employee(juan, "1"))) == (422, "difference_allocation_mismatch")
    assert error(close(operator, short, {"target": "COMPANY", "employee_id": pedro, "amount": "1000"})) == (
        422,
        "invalid_allocation",
    )
    assert error(close(operator, short, employee(999999, "1000"))) == (422, "employee_not_found")


# --- surplus --------------------------------------------------------------------------------------


@pytest.fixture
def over(as_role, ids, pedro) -> int:  # type: ignore[no-untyped-def]
    """$100.000 expected, $100.500 counted: a $500 surplus."""
    operator = as_role("operator")
    sid = create(operator, pedro, "100000")
    count(operator, sid, ("ARS_20000", 5, ids["main_cash"]), ("ARS_500", 1, ids["change_cash"]))
    return sid


def test_surplus_to_an_employee(as_role, over, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[8] The company owes the driver the $500: his internal account goes -500."""
    response = close(as_role("operator"), over, employee(pedro, "500"))
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["current_difference"]["kind"], body["current_difference"]["amount"]) == ("SURPLUS", "500.00")
    assert legs(db, body["close_in_effect"]["operation_id"]) == {
        "main_cash": "100000.00",
        "change_cash": "500.00",
        "delivery_collections": "-100000.00",
        f"employee:{pedro}": "-500.00",
    }


def test_surplus_to_the_company(as_role, over, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[9] Only when an admin decides it, with a reason: a surplus is never assumed to be the company's."""
    assert error(close(as_role("operator"), over, company("500"))) == (403, "company_allocation_requires_admin")
    assert error(close(as_role("operator"), over)) == (422, "difference_not_fully_allocated")
    response = close(as_role("admin"), over, company("500", "Sobrante sin reclamo del repartidor"))
    assert response.status_code == 201, response.text
    assert legs(db, response.json()["close_in_effect"]["operation_id"])["settlement_difference"] == "-500.00"


# --- transfers ------------------------------------------------------------------------------------


def test_declared_transfers_block_the_close(as_role, ids, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[10]"""
    operator = as_role("operator")
    sid = create(operator, pedro, "1000")
    count(operator, sid, ("ARS_500", 1, ids["main_cash"]))
    transfer(operator, sid, "500")
    preview = operator.get(f"{BASE}/deliveries/{sid}/closing-preview").json()
    assert [b["code"] for b in preview["blockers"]] == ["declared_transfers_pending"]
    response = close(operator, sid, employee(pedro, "500"))
    assert error(response) == (409, "declared_transfers_pending")
    assert response.json()["details"] == {"declared_transfers_count": 1}
    assert count_rows(db, "financial_operations") == 0


def test_rejected_transfers_do_not_count_and_verified_do(as_role, ids, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[11] [12] Each verified transfer goes to its bank; a rejected one is not money received."""
    operator = as_role("operator")
    sid = create(operator, pedro, "1000")
    transfer(operator, sid, "700", "VERIFIED")
    transfer(operator, sid, "300", "REJECTED")
    preview = operator.get(f"{BASE}/deliveries/{sid}/closing-preview").json()
    assert (preview["difference"], preview["difference_kind"]) == ("-300.00", "SHORTAGE")
    response = close(operator, sid, employee(pedro, "300"))
    assert response.status_code == 201, response.text
    assert legs(db, response.json()["close_in_effect"]["operation_id"]) == {
        "bank": "700.00",
        "delivery_collections": "-1000.00",
        f"employee:{pedro}": "300.00",
    }


# --- idempotency ----------------------------------------------------------------------------------


def test_double_close_returns_the_same_close(as_role, short, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[13] A retry (even by another user) returns the same close, 200, without a second operation."""
    first = close(as_role("operator"), short, employee(pedro, "1000"))
    again = close(as_role("admin"), short, employee(pedro, "1000"))
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["close_in_effect"] == first.json()["close_in_effect"]
    assert count_rows(db, "financial_operations") == 1
    assert count_rows(db, "settlement_differences") == 1
    assert count_rows(db, "settlement_closings") == 1


def test_same_close_with_other_content_is_a_conflict(as_role, short, pedro, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[14] Same settlement and version (same close id), other allocation."""
    operator = as_role("operator")
    assert close(operator, short, employee(pedro, "1000")).status_code == 201
    conflict = close(operator, short, employee(juan, "1000"))
    assert error(conflict) == (409, "idempotency_conflict")
    assert conflict.json()["details"]["version"] == 1
    assert count_rows(db, "financial_operations") == 1


def test_only_one_internal_account_per_employee(as_role, ids, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[16] Two closes charging the same driver reuse his account, created on first use only."""
    operator = as_role("operator")
    assert count_rows(db, "accounts WHERE kind = 'EMPLOYEE'") == 0
    for _ in range(2):
        sid = create(operator, pedro, "1000")
        count(operator, sid, ("ARS_500", 1, ids["main_cash"]))
        assert close(operator, sid, employee(pedro, "500")).status_code == 201
    assert count_rows(db, "accounts WHERE kind = 'EMPLOYEE'") == 1
    assert balances(operator)[f"Cuenta interna de Pedro Gómez (#{pedro})"] == "1000.00"


# --- closed and reopened --------------------------------------------------------------------------


def test_a_closed_settlement_is_immutable(as_role, ids, balanced) -> None:  # type: ignore[no-untyped-def]
    """[18]"""
    operator = as_role("operator")
    assert close(operator, balanced).status_code == 201
    attempts = [
        operator.patch(f"{BASE}/deliveries/{balanced}", json={"expected_amount": "1"}),
        operator.put(f"{BASE}/deliveries/{balanced}/count", json={"lines": []}),
    ]
    assert [error(r) for r in attempts] == [(409, "settlement_closed")] * 2
    assert error(close(operator, balanced, version=2)) == (409, "settlement_already_closed")


def test_reopen_needs_admin_and_reason_and_a_closed_settlement(as_role, balanced) -> None:  # type: ignore[no-untyped-def]
    assert error(reopen(as_role("admin"), balanced, 1)) == (409, "settlement_not_closed")
    operator = as_role("operator")
    close(operator, balanced)
    assert reopen(operator, balanced, 1).status_code == 403
    admin = as_role("admin")
    assert reopen(admin, balanced, 1, reason="").status_code == 422
    assert error(reopen(admin, balanced, 1, reason="   ")) == (422, "reason_required")
    assert error(reopen(admin, balanced, 2)) == (409, "settlement_concurrent_change")


def test_reopen_keeps_the_ledger_and_is_being_corrected(as_role, short, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[19] [20] [21]"""
    closed = close(as_role("operator"), short, employee(pedro, "1000")).json()
    before = balances(as_role("viewer"))
    operations = count_rows(db, "financial_operations")

    admin = as_role("admin")
    response = reopen(admin, short, 1)
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["financial_state"], body["version"]) == ("OPEN", "BEING_CORRECTED", 2)
    assert "sigue vigente en Cajas" in body["financial_state_detail"]
    assert body["close_in_effect"] == closed["close_in_effect"]
    assert (body["reopen_reason"], body["reopened_by_name"]) == (REASON, "Usuario Test")
    assert body["current_difference"]["amount"] == "1000.00"  # the one in effect in Cajas
    assert balances(admin) == before
    assert count_rows(db, "financial_operations") == operations
    # A retry of the same reopening is not a second one.
    assert reopen(admin, short, 1).status_code == 200
    assert admin.get(f"{BASE}/deliveries/{short}").json()["version"] == 2
    (item,) = admin.get(f"{BASE}/deliveries").json()["items"]
    assert item["financial_state"] == "BEING_CORRECTED"


def test_list_filters_by_financial_state(as_role, short, balanced, pedro) -> None:  # type: ignore[no-untyped-def]
    """The list screen filters Sin cerrar / Cerrada / En corrección, not the editing status."""
    viewer = as_role("viewer")
    third = create(as_role("operator"), pedro, "1000")
    close(as_role("operator"), short, employee(pedro, "1000"))
    close(as_role("operator"), balanced)
    reopen(as_role("admin"), short, 1)

    def listed(state: str) -> list[int]:
        items = viewer.get(f"{BASE}/deliveries", params={"financial_state": state}).json()["items"]
        return [i["id"] for i in items]

    assert listed("NOT_CLOSED") == [third]
    assert listed("CLOSED") == [balanced]
    assert listed("BEING_CORRECTED") == [short]
    assert viewer.get(f"{BASE}/deliveries", params={"financial_state": "OTHER"}).status_code == 422


@pytest.fixture
def million(as_role, ids, pedro) -> int:  # type: ignore[no-untyped-def]
    """A settlement closed with $1.000.000 in Caja principal against $1.000.500 expected ($500 short)."""
    operator = as_role("operator")
    sid = create(operator, pedro, "1000500")
    count(operator, sid, ("ARS_20000", 50, ids["main_cash"]))
    assert close(operator, sid, employee(pedro, "500")).status_code == 201
    return sid


def _correct(as_role, ids, sid: int) -> Any:  # type: ignore[no-untyped-def]
    """Reopen and count the $500 that was missing: v2 closes balanced with $1.000.500."""
    assert reopen(as_role("admin"), sid, 1).status_code == 200
    operator = as_role("operator")
    count(operator, sid, ("ARS_20000", 50, ids["main_cash"]), ("ARS_500", 1, ids["main_cash"]))
    return close(operator, sid, version=2)


def test_new_close_publishes_reversal_and_new_close(as_role, ids, million, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[22] [23] [24] [27] [28]"""
    original = as_role("viewer").get(f"{BASE}/deliveries/{million}").json()["close_in_effect"]["operation_id"]
    started = datetime.now(UTC)
    response = _correct(as_role, ids, million)
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body["status"], body["financial_state"], body["version"]) == ("CLOSED", "CLOSED", 2)
    assert body["current_difference"] is None
    replacement = body["close_in_effect"]["operation_id"]

    rows = db.execute(
        text(
            "SELECT id, type, business_date, reverses_operation_id, created_at FROM settlements.financial_operations "
            "ORDER BY id"
        )
    ).all()
    assert [(r.id, r.type, r.reverses_operation_id) for r in rows] == [
        (original, "SETTLEMENT_CLOSE", None),
        (original + 1, "REVERSAL", original),
        (replacement, "SETTLEMENT_CLOSE", None),
    ]
    assert {r.business_date for r in rows} == {DAY}  # [23] the settlement's date, not the correction's
    assert all(r.created_at >= started for r in rows[1:])  # [24] when the correction really happened
    assert legs(db, replacement)["main_cash"] == "1000500.00"
    assert legs(db, original + 1) == {
        "main_cash": "-1000000.00",
        "delivery_collections": "1000500.00",
        f"employee:{pedro}": "-500.00",
    }
    balance = balances(as_role("viewer"))
    assert (balance["main_cash"], balance[f"Cuenta interna de Pedro Gómez (#{pedro})"]) == ("1000500.00", "0.00")

    history = as_role("viewer").get(f"{BASE}/deliveries/{million}/history").json()
    assert [(c["settlement_version"], c["in_effect"]) for c in history["closings"]] == [(1, False), (2, True)]
    assert history["closings"][0]["reversed_by_operation_id"] == original + 1
    assert history["closings"][1]["reversal_operation_id"] == original + 1
    assert [(r["from_version"], r["to_version"], r["reason"]) for r in history["reopenings"]] == [(1, 2, REASON)]
    (old,) = history["differences"]  # [27] superseded, never deleted
    assert (old["settlement_version"], old["amount"], old["superseded_by_name"]) == (1, "500.00", "Usuario Test")
    assert old["superseded_at"] is not None
    assert old["allocations"][0]["employee_id"] == pedro


def test_net_correction_works_after_the_cash_was_moved(as_role, ids, million, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[25] The $1.000.000 went to the bank today; correcting to $1.000.500 only needs the +$500."""
    operator = as_role("operator")
    deposit = operator.post(
        f"{BASE}/operations/bank-deposit",
        json={
            "client_request_id": str(uuid4()),
            "cash_account_id": ids["main_cash"],
            "bank_account_id": ids["bank"],
            "amount": "1000000",
        },
    )
    assert deposit.status_code == 201, deposit.text
    response = _correct(as_role, ids, million)
    assert response.status_code == 201, response.text
    assert balances(operator)["main_cash"] == "500.00"


def test_net_effect_cannot_leave_a_cash_box_negative_in_the_past(as_role, ids, million, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[26] Correcting Caja principal down to $980.000 after depositing the $1.000.000 would leave it at
    -$20.000 since yesterday, although the net change of the correction is only -$20.000."""
    operator = as_role("operator")
    operator.post(
        f"{BASE}/operations/bank-deposit",
        json={
            "client_request_id": str(uuid4()),
            "cash_account_id": ids["main_cash"],
            "bank_account_id": ids["bank"],
            "amount": "1000000",
        },
    )
    assert reopen(as_role("admin"), million, 1).status_code == 200
    count(operator, million, ("ARS_20000", 49, ids["main_cash"]), ("ARS_10000", 1, ids["change_cash"]))
    response = close(operator, million, employee(pedro, "10500"), version=2)
    assert error(response) == (409, "insufficient_funds")
    assert "psycopg" not in response.text
    assert "violates" not in response.text
    detail = operator.get(f"{BASE}/deliveries/{million}").json()
    assert (detail["status"], detail["financial_state"]) == ("OPEN", "BEING_CORRECTED")
    assert count_rows(db, "financial_operations") == 2  # the original close and the deposit


def test_history_rebuilds_every_version(as_role, ids, million, pedro) -> None:  # type: ignore[no-untyped-def]
    """[28] v1 -> difference -> close -> reopen -> v2 -> new difference -> reversal + close -> reopen -> v3."""
    assert reopen(as_role("admin"), million, 1).status_code == 200
    operator = as_role("operator")
    assert close(operator, million, employee(pedro, "500"), version=2).status_code == 201
    assert reopen(as_role("admin"), million, 2, "Revisión de la gerencia").status_code == 200

    history = operator.get(f"{BASE}/deliveries/{million}/history").json()
    assert (history["version"], history["financial_state"]) == (3, "BEING_CORRECTED")
    closings = history["closings"]
    assert [(c["settlement_version"], c["in_effect"]) for c in closings] == [(1, False), (2, True)]
    assert closings[0]["reversal_operation_id"] is None
    assert closings[1]["reversal_operation_id"] == closings[0]["reversed_by_operation_id"]
    assert [r["to_version"] for r in history["reopenings"]] == [2, 3]
    assert [(d["settlement_version"], d["superseded_at"] is None) for d in history["differences"]] == [
        (1, False),
        (2, True),
    ]


def test_ledger_reversal_of_a_close_is_refused(as_role, balanced, db: Session) -> None:  # type: ignore[no-untyped-def]
    """A close is corrected by reopening the settlement, never by reversing it from Cajas."""
    close(as_role("operator"), balanced)
    operation_id = as_role("viewer").get(f"{BASE}/deliveries/{balanced}").json()["close_in_effect"]["operation_id"]
    admin = db.scalars(select(User).where(User.email == "admin@districom.test")).one()
    with pytest.raises(AppError) as caught:
        ledger.reverse_operation(db, actor=admin, operation_id=operation_id, reason="x", client_request_id=uuid4())
    assert caught.value.code == "operation_owned_by_document"


def test_viewer_reads_but_cannot_close(as_role, balanced) -> None:  # type: ignore[no-untyped-def]
    viewer = as_role("viewer")
    assert viewer.get(f"{BASE}/deliveries/{balanced}/closing-preview").status_code == 200
    assert viewer.get(f"{BASE}/deliveries/{balanced}/history").status_code == 200
    assert close(viewer, balanced).status_code == 403
    assert error(as_role("viewer").get(f"{BASE}/deliveries/999999/history")) == (404, "settlement_not_found")


# --- binding preview (Enzo's R2-2 adjustment 1) ---------------------------------------------------


@pytest.fixture
def loaded(as_role, ids, pedro, tolls) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """$100.000 expected: $41.000 counted, $1.000 added, $8.000 verified, $500 rejected and $2.000 spent."""
    operator = as_role("operator")
    sid = create(operator, pedro, "100000")
    count(operator, sid, ("ARS_20000", 2, ids["main_cash"]), ("ARS_1000", 1, ids["change_cash"]))
    added = operator.post(
        f"{BASE}/deliveries/{sid}/additions",
        json={"client_request_id": str(uuid4()), "amount": "1000", "destination_account_id": ids["main_cash"]},
    )
    verified = transfer(operator, sid, "8000", "VERIFIED")
    transfer(operator, sid, "500", "REJECTED")
    response = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "2000"},
    )
    return {
        "settlement": sid,
        "addition": added.json()["additions"][-1]["id"],
        "transfer": verified,
        "expense": response.json()["expenses"][-1]["id"],
    }


def _change_expected(client: TestClient, ids: dict[str, int], d: dict[str, int], tolls: int) -> None:
    assert client.patch(f"{BASE}/deliveries/{d['settlement']}", json={"expected_amount": "99000"}).status_code == 200


def _change_count(client: TestClient, ids: dict[str, int], d: dict[str, int], tolls: int) -> None:
    # Same total, other notes: $41.000 as 2 x $20.000 + 2 x $500 instead of 2 x $20.000 + 1 x $1.000.
    count(client, d["settlement"], ("ARS_20000", 2, ids["main_cash"]), ("ARS_500", 2, ids["change_cash"]))


def _change_destination(client: TestClient, ids: dict[str, int], d: dict[str, int], tolls: int) -> None:
    # The $1.000 note goes to Caja principal instead of Caja de cambio: same difference.
    count(client, d["settlement"], ("ARS_20000", 2, ids["main_cash"]), ("ARS_1000", 1, ids["main_cash"]))


def _add_cash(client: TestClient, ids: dict[str, int], d: dict[str, int], tolls: int) -> None:
    response = client.post(
        f"{BASE}/deliveries/{d['settlement']}/additions",
        json={"client_request_id": str(uuid4()), "amount": "100", "destination_account_id": ids["change_cash"]},
    )
    assert response.status_code == 201, response.text


def _reject_transfer(client: TestClient, ids: dict[str, int], d: dict[str, int], tolls: int) -> None:
    response = client.post(
        f"{BASE}/deliveries/{d['settlement']}/transfers/{d['transfer']}/status",
        json={"status": "REJECTED", "reason": "No figura en el extracto"},
    )
    assert response.status_code == 200, response.text


def _void_expense(client: TestClient, ids: dict[str, int], d: dict[str, int], tolls: int) -> None:
    response = client.post(
        f"{BASE}/deliveries/{d['settlement']}/expenses/{d['expense']}/void", json={"reason": "Cargado dos veces"}
    )
    assert response.status_code == 200, response.text


def _recategorize_expense(client: TestClient, ids: dict[str, int], d: dict[str, int], tolls: int) -> None:
    other = client.post(f"{BASE}/expense-categories", json={"name": "Lavadero"}).json()["id"]
    response = client.patch(f"{BASE}/deliveries/{d['settlement']}/expenses/{d['expense']}", json={"category_id": other})
    assert response.status_code == 200, response.text


def test_preview_then_close_without_changes(as_role, loaded, pedro) -> None:  # type: ignore[no-untyped-def]
    """Token 1: the preview the user reviewed is exactly what closes."""
    operator = as_role("operator")
    sid = loaded["settlement"]
    first = preview_token(operator, sid)
    assert preview_token(as_role("viewer"), sid) == first  # stable: same state, same token, whoever reads
    response = close(as_role("operator"), sid, employee(pedro, "48000"), token=first)
    assert response.status_code == 201, response.text


@pytest.mark.parametrize(
    "change",
    [
        _change_expected,
        _change_count,
        _change_destination,
        _add_cash,
        _reject_transfer,
        _void_expense,
        _recategorize_expense,
    ],
)
def test_a_change_after_the_preview_makes_it_stale(as_role, ids, loaded, tolls, pedro, change, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Tokens 2-9: expected, count, destination with the same difference, addition, transfer decision and
    expense. The close is refused before any financial write; a new preview closes."""
    sid = loaded["settlement"]
    old = preview_token(as_role("operator"), sid)
    change(as_role("admin"), ids, loaded, tolls)
    operator = as_role("operator")
    owed = operator.get(f"{BASE}/deliveries/{sid}/closing-preview").json()["amount_to_allocate"]

    stale = close(operator, sid, employee(pedro, owed), token=old)
    assert error(stale) == (409, "settlement_preview_stale")
    assert "cambió desde la última vista previa" in stale.json()["message"]
    assert count_rows(db, "financial_operations") == 0
    assert count_rows(db, "settlement_closings") == 0
    assert count_rows(db, "accounts WHERE kind = 'EMPLOYEE'") == 0
    assert operator.get(f"{BASE}/deliveries/{sid}").json()["status"] == "OPEN"

    fresh = preview_token(operator, sid)
    assert fresh != old
    assert close(operator, sid, employee(pedro, owed), token=fresh).status_code == 201


def test_the_preview_token_binds_the_new_close_after_a_reopening(as_role, ids, million, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Token 10: the same rule for the close that replaces a previous one."""
    assert reopen(as_role("admin"), million, 1).status_code == 200
    operator = as_role("operator")
    old = preview_token(operator, million)
    count(operator, million, ("ARS_20000", 50, ids["main_cash"]), ("ARS_500", 1, ids["main_cash"]))
    assert error(close(operator, million, version=2, token=old)) == (409, "settlement_preview_stale")
    assert count_rows(db, "financial_operations") == 1  # only the original close, still in effect
    assert operator.get(f"{BASE}/deliveries/{million}").json()["financial_state"] == "BEING_CORRECTED"
    assert close(operator, million, version=2).status_code == 201


def test_preview_token_is_required(as_role, balanced) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    response = operator.post(
        f"{BASE}/deliveries/{balanced}/close", json={"version": 1, "allocations": [], "confirm": True}
    )
    assert response.status_code == 422
    bad = close(operator, balanced, token="0" * 64)
    assert error(bad) == (409, "settlement_preview_stale")


# --- reopenings as domain history (Enzo's R2-2 adjustment 2) ---------------------------------------


def _reopenings(db: Session) -> list[tuple[Any, ...]]:
    rows = db.execute(
        text(
            "SELECT from_version, to_version, close_operation_id, reason, reopened_by, reopened_at "
            "FROM settlements.settlement_reopenings ORDER BY id"
        )
    )
    return [tuple(row) for row in rows.all()]


def test_each_reopening_is_a_row_that_never_changes(as_role, million, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """History 1, 2, 3, 8: one row per reopening, with the close in effect, reason, user and time."""
    admin = as_role("admin")
    first_close = admin.get(f"{BASE}/deliveries/{million}").json()["close_in_effect"]["operation_id"]
    before = datetime.now(UTC)
    assert reopen(admin, million, 1).status_code == 200
    assert reopen(admin, million, 1).status_code == 200  # a retry is not a second reopening
    (first,) = _reopenings(db)
    admin_id = db.execute(text("SELECT id FROM users WHERE email = 'admin@districom.test'")).scalar_one()
    assert first[:5] == (1, 2, first_close, REASON, admin_id)
    assert before <= first[5] <= datetime.now(UTC)

    operator = as_role("operator")
    assert close(operator, million, employee(pedro, "500"), version=2).status_code == 201
    second_close = operator.get(f"{BASE}/deliveries/{million}").json()["close_in_effect"]["operation_id"]
    assert reopen(as_role("admin"), million, 2, "Revisión de la gerencia").status_code == 200
    rows = _reopenings(db)
    assert rows[0] == first
    assert rows[1][:5] == (2, 3, second_close, "Revisión de la gerencia", admin_id)


def test_history_does_not_need_the_audit_log(as_role, ids, million, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """History 6, 7: with the audit log emptied, /history still rebuilds every version in order."""
    assert reopen(as_role("admin"), million, 1).status_code == 200
    operator = as_role("operator")
    count(operator, million, ("ARS_20000", 50, ids["main_cash"]), ("ARS_200", 1, ids["main_cash"]))
    assert close(operator, million, employee(pedro, "300"), version=2).status_code == 201
    assert reopen(as_role("admin"), million, 2, "Segunda revisión").status_code == 200
    db.execute(text("TRUNCATE audit_events"))
    db.commit()

    history = as_role("viewer").get(f"{BASE}/deliveries/{million}/history").json()
    closings, reopenings = history["closings"], history["reopenings"]
    assert [(c["settlement_version"], c["in_effect"]) for c in closings] == [(1, False), (2, True)]
    assert [(r["from_version"], r["to_version"], r["reason"]) for r in reopenings] == [
        (1, 2, REASON),
        (2, 3, "Segunda revisión"),
    ]
    assert reopenings[0]["close_operation_id"] == closings[0]["operation_id"]
    assert reopenings[1]["close_operation_id"] == closings[1]["operation_id"]
    assert closings[1]["reversal_operation_id"] == closings[0]["reversed_by_operation_id"]
    assert all(r["reopened_by_name"] == "Usuario Test" for r in reopenings)
    assert reopenings[0]["reopened_at"] < closings[1]["closed_at"] < reopenings[1]["reopened_at"]
    assert [(d["settlement_version"], d["amount"], d["superseded_at"] is None) for d in history["differences"]] == [
        (1, "500.00", False),
        (2, "300.00", True),
    ]
