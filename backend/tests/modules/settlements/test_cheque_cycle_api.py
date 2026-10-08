"""The financial cycle of a cheque (R4-2): deposit, crediting, rejection, return, their reversals and a pending
collected with a cheque.

Every scenario checks the exact legs of its operation and, at the end, the invariant of the cheque accounts:
"Cartera de cheques" and "Cheques depositados" hold exactly the cheques the ledger names there. The bank
overdraft policy is in `test_cheque_overdraft.py`, the database rules in `test_cheque_cycle_db_invariants.py`
and the races in `test_cheque_cycle_concurrency.py`.
"""

from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.ledger.models import Account
from tests.modules.settlements.test_cheques_api import bank, closed, create, new, physical

BASE = "/api/v1/settlements"
T = business_today()
ISSUED = T - timedelta(days=12)
ENTERED = T - timedelta(days=10)
DEPOSITED = T - timedelta(days=8)
CREDITED = T - timedelta(days=6)


# --- helpers --------------------------------------------------------------------------------------


_numbers = iter(range(1000, 100000))


def instrument(bank_id: int, amount: str = "100000", **fields: Any) -> dict[str, Any]:
    number = str(next(_numbers))
    return physical(bank_id, amount=amount, cheque_number=number, account_ref=f"77{number}", **fields)


def entered(client: TestClient, amount: str = "100000", on: date = ENTERED, **fields: Any) -> int:
    """A cheque of the portfolio (opening balance, as admin), issued on ISSUED unless said."""
    body = {
        **instrument(bank(client), amount, **{"issue_date": ISSUED.isoformat(), **fields}),
        "business_date": on.isoformat(),
    }
    response = client.post(f"{BASE}/cheques/opening-balance", json=body)
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def deposit(client: TestClient, ids: list[int], on: date = DEPOSITED, bank_id: int | None = None, **extra: Any) -> Any:
    return client.post(
        f"{BASE}/cheque-deposits",
        json={
            "client_request_id": str(uuid4()),
            "bank_account_id": bank_id or account_id(client, "bank"),
            "cheque_ids": ids,
            "business_date": on.isoformat(),
            **extra,
        },
    )


def deposited(client: TestClient, ids: list[int], on: date = DEPOSITED) -> dict[str, Any]:
    response = deposit(client, ids, on)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def credit(client: TestClient, ids: list[int], on: date = CREDITED, bank_id: int | None = None) -> Any:
    return client.post(
        f"{BASE}/cheque-credits",
        json={
            "client_request_id": str(uuid4()),
            "bank_account_id": bank_id or account_id(client, "bank"),
            "cheque_ids": ids,
            "business_date": on.isoformat(),
        },
    )


def credited(client: TestClient, ids: list[int], on: date = CREDITED) -> dict[str, Any]:
    response = credit(client, ids, on)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def reject(client: TestClient, cid: int, on: date, responsible: int | None = None, **extra: Any) -> Any:
    return client.post(
        f"{BASE}/cheques/{cid}/rejection",
        json={
            "client_request_id": str(uuid4()),
            "reason": "Sin fondos suficientes",
            "business_date": on.isoformat(),
            "responsible_employee_id": responsible,
            **extra,
        },
    )


def rejected(client: TestClient, cid: int, on: date, responsible: int | None = None) -> dict[str, Any]:
    response = reject(client, cid, on, responsible)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def give_back(client: TestClient, cid: int, on: date, responsible: int | None = None, **extra: Any) -> Any:
    return client.post(
        f"{BASE}/cheques/{cid}/return",
        json={
            "client_request_id": str(uuid4()),
            "reason": "El cliente lo reemplaza por efectivo",
            "business_date": on.isoformat(),
            "responsible_employee_id": responsible,
            **extra,
        },
    )


def reverse(client: TestClient, path: str, request_id: str | None = None, reason: str = "Carga equivocada") -> Any:
    return client.post(
        f"{BASE}/{path}/reverse", json={"client_request_id": request_id or str(uuid4()), "reason": reason}
    )


def cheque(client: TestClient, cid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/cheques/{cid}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def pending(client: TestClient, pid: int) -> dict[str, Any]:
    response = client.get(f"{BASE}/pending-collections/{pid}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def account_id(client: TestClient, code: str) -> int:
    accounts = client.get(f"{BASE}/accounts", params={"include_inactive": True}).json()
    return next(a["id"] for a in accounts if a["code"] == code)  # type: ignore[no-any-return]


def balance(client: TestClient, code: str, on: date | None = None) -> Decimal:
    params = {"on": on.isoformat()} if on else {}
    response = client.get(f"{BASE}/accounts/{account_id(client, code)}", params=params)
    assert response.status_code == 200, response.text
    return Decimal(response.json()["balance"])


def legs(db: Session, operation_id: int) -> list[tuple[str, int | None, Decimal]]:
    rows = db.execute(
        text(
            "SELECT a.kind, e.cheque_id, e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :op ORDER BY e.id"
        ),
        {"op": operation_id},
    ).all()
    return [(kind, cid, amount) for kind, cid, amount in rows]


def operation(db: Session, operation_id: int) -> dict[str, Any]:
    row = db.execute(
        text(
            "SELECT type, business_date, created_at, reverses_operation_id, source_type, source_id "
            "FROM settlements.financial_operations WHERE id = :id"
        ),
        {"id": operation_id},
    ).one()
    return dict(row._mapping)


def assert_cheque_accounts_invariant(client: TestClient, db: Session) -> None:
    """The balance of each cheque account is the sum of the cheques it holds, and none is held twice."""
    for kind, code in (("CHEQUE_PORTFOLIO", "cheque_portfolio"), ("CHEQUES_IN_TRANSIT", "cheques_in_transit")):
        held = db.execute(
            text(
                "SELECT e.cheque_id, sum(e.amount) FROM settlements.ledger_entries e "
                "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.kind = :k "
                "GROUP BY e.cheque_id HAVING sum(e.amount) <> 0"
            ),
            {"k": kind},
        ).all()
        assert all(amount > 0 for _, amount in held)
        amounts = dict(held)
        assert balance(client, code) == sum(amounts.values(), Decimal(0))
        for cid, amount in amounts.items():
            assert Decimal(cheque(client, cid)["amount"]) == amount


@pytest.fixture
def admin(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("admin")  # type: ignore[no-any-return]


@pytest.fixture
def ana(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Ana Cobranzas")  # type: ignore[no-any-return]


# --- deposit --------------------------------------------------------------------------------------


def test_a_deposit_slip_is_one_operation_with_a_pair_of_legs_per_cheque(admin: TestClient, db: Session) -> None:
    first, second = entered(admin, "100000"), entered(admin, "40000")
    slip = deposited(admin, [second, first])
    assert slip["total"] == "140000.00"
    assert [i["cheque_id"] for i in slip["items"]] == [first, second]
    assert all(i["state"] == "DEPOSITED" for i in slip["items"])
    assert legs(db, slip["operation_id"]) == [
        ("CHEQUE_PORTFOLIO", first, Decimal("-100000.00")),
        ("CHEQUES_IN_TRANSIT", first, Decimal("100000.00")),
        ("CHEQUE_PORTFOLIO", second, Decimal("-40000.00")),
        ("CHEQUES_IN_TRANSIT", second, Decimal("40000.00")),
    ]
    op = operation(db, slip["operation_id"])
    assert (op["type"], op["business_date"], op["source_type"], op["source_id"]) == (
        "CHEQUE_DEPOSIT",
        DEPOSITED,
        "cheque_deposit",
        slip["id"],
    )
    assert balance(admin, "bank") == 0  # a deposited cheque is not money yet
    summary = admin.get(f"{BASE}/cheques/summary").json()
    assert (summary["portfolio_count"], summary["in_transit_count"], summary["in_transit_amount"]) == (
        0,
        2,
        "140000.00",
    )
    detail = cheque(admin, first)
    assert [d["type"] for d in detail["documents"]] == ["DEPOSIT"]
    assert detail["events"][-1]["event_type"] == "DEPOSITED"
    assert_cheque_accounts_invariant(admin, db)


def test_a_deposit_is_idempotent_and_never_deposits_a_cheque_twice(admin: TestClient) -> None:
    cid = entered(admin)
    body = {
        "client_request_id": str(uuid4()),
        "bank_account_id": account_id(admin, "bank"),
        "cheque_ids": [cid],
        "business_date": DEPOSITED.isoformat(),
    }
    first = admin.post(f"{BASE}/cheque-deposits", json=body)
    again = admin.post(f"{BASE}/cheque-deposits", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    conflict = admin.post(f"{BASE}/cheque-deposits", json={**body, "slip_number": "999"})
    assert (conflict.status_code, conflict.json()["code"]) == (409, "idempotency_conflict")
    twice = deposit(admin, [cid])
    assert (twice.status_code, twice.json()["code"]) == (409, "cheque_not_in_portfolio")


def test_an_expired_cheque_is_never_deposited_not_even_by_an_admin(admin: TestClient, ana: int) -> None:
    issued = T - timedelta(days=35)
    cid = entered(admin, issue_date=issued.isoformat(), on=T - timedelta(days=34))
    deadline = issued + timedelta(days=30)
    late = deposit(admin, [cid], on=T)
    assert late.status_code == 422
    assert late.json()["code"] == "cheque_presentation_expired"
    assert late.json()["details"] == {"deadline": deadline.isoformat()}
    detail = cheque(admin, cid)
    assert (detail["state"], detail["presentation_expired"]) == ("IN_PORTFOLIO", True)
    # On its last day it still could.
    slip = deposit(admin, [cid], on=deadline)
    assert slip.status_code == 201, slip.text
    assert reverse(admin, f"cheque-deposits/{slip.json()['id']}").status_code == 201
    # An expired cheque can be given back; out of a settlement, someone must answer for the new pending.
    nobody = give_back(admin, cid, T)
    assert (nobody.status_code, nobody.json()["code"]) == (422, "responsible_required")
    assert give_back(admin, cid, T, responsible=ana).status_code == 201
    assert cheque(admin, cid)["state"] == "RETURNED"


def test_a_deferred_cheque_is_not_deposited_before_its_payment_date(admin: TestClient) -> None:
    payment = T + timedelta(days=3)
    cid = entered(admin, payment_type="DEFERRED", payment_date=payment.isoformat(), on=T - timedelta(days=1))
    early = deposit(admin, [cid], on=T)
    assert (early.status_code, early.json()["code"]) == (422, "cheque_not_yet_payable")
    assert early.json()["details"]["payable_from"] == payment.isoformat()


def test_a_deposit_needs_an_active_bank_and_a_date_after_the_entry(admin: TestClient, accounts) -> None:  # type: ignore[no-untyped-def]
    cid = entered(admin)
    wrong = deposit(admin, [cid], bank_id=accounts["main_cash"])
    assert (wrong.status_code, wrong.json()["code"]) == (422, "invalid_bank_account")
    before = deposit(admin, [cid], on=ENTERED - timedelta(days=1))
    assert (before.status_code, before.json()["code"]) == (422, "invalid_deposit_date")
    future = deposit(admin, [cid], on=T + timedelta(days=1))
    assert (future.status_code, future.json()["code"]) == (422, "future_business_date")
    missing = deposit(admin, [cid, 999999])
    assert (missing.status_code, missing.json()["code"]) == (404, "cheque_not_found")


# --- crediting ------------------------------------------------------------------------------------


def test_a_crediting_moves_the_deposited_cheques_to_the_bank(admin: TestClient, db: Session) -> None:
    first, second = entered(admin, "100000"), entered(admin, "40000")
    deposited(admin, [first, second])
    statement = credited(admin, [first, second])
    assert statement["total"] == "140000.00"
    assert legs(db, statement["operation_id"]) == [
        ("CHEQUES_IN_TRANSIT", first, Decimal("-100000.00")),
        ("CHEQUES_IN_TRANSIT", second, Decimal("-40000.00")),
        ("BANK", None, Decimal("140000.00")),
    ]
    assert balance(admin, "bank") == Decimal("140000.00")
    assert balance(admin, "bank", on=CREDITED - timedelta(days=1)) == 0
    assert (cheque(admin, first)["state"], cheque(admin, first)["state_label"]) == ("CREDITED", "Acreditado")
    again = credit(admin, [first])
    assert (again.status_code, again.json()["code"]) == (409, "cheque_not_deposited")
    assert_cheque_accounts_invariant(admin, db)


def test_a_cheque_is_credited_in_the_bank_it_was_deposited_not_before(admin: TestClient, db: Session) -> None:
    other = Account(name="Banco Nación", kind="BANK", allow_negative=False)
    db.add(other)
    db.commit()
    cid = entered(admin)
    deposited(admin, [cid])
    mismatch = credit(admin, [cid], bank_id=other.id)
    assert (mismatch.status_code, mismatch.json()["code"]) == (422, "bank_account_mismatch")
    early = credit(admin, [cid], on=DEPOSITED - timedelta(days=1))
    assert (early.status_code, early.json()["code"]) == (422, "invalid_credit_date")
    portfolio = entered(admin)
    never = credit(admin, [portfolio])
    assert (never.status_code, never.json()["code"]) == (409, "cheque_not_deposited")


# --- rejection ------------------------------------------------------------------------------------


def test_a_rejection_in_transit_leaves_the_bank_untouched_and_creates_a_new_pending(
    admin: TestClient, db: Session, ana: int
) -> None:
    cid = entered(admin, delivered_by_name="Almacén La Esquina")
    deposited(admin, [cid])
    day = T - timedelta(days=5)
    doc = rejected(admin, cid, day, responsible=ana)
    assert (doc["stage"], doc["bank_account_id"]) == ("IN_TRANSIT", None)
    assert legs(db, doc["operation_id"]) == [
        ("CHEQUES_IN_TRANSIT", cid, Decimal("-100000.00")),
        ("PENDING_COLLECTIONS", None, Decimal("100000.00")),
    ]
    assert balance(admin, "bank") == 0
    assert balance(admin, "pending_collections") == Decimal("100000.00")
    owed = pending(admin, doc["pending_id"])
    assert (owed["origin_type"], owed["settlement_id"], owed["origin_cheque_rejection_id"]) == (
        "CHEQUE_REJECTION",
        None,
        doc["id"],
    )
    assert (owed["origin_cheque_id"], owed["origin_business_date"]) == (cid, day.isoformat())
    assert (owed["customer_name"], owed["amount"], owed["responsible_employee_id"]) == (
        "Almacén La Esquina",
        "100000.00",
        ana,
    )
    assert (owed["state"], owed["balance"], owed["driver_id"]) == ("OPEN", "100000.00", None)
    assert [e["event_type"] for e in owed["events"]] == ["CREATED"]
    listed = admin.get(f"{BASE}/pending-collections", params={"origin_type": "CHEQUE_REJECTION"}).json()
    assert [p["id"] for p in listed["items"]] == [owed["id"]]
    detail = cheque(admin, cid)
    assert (detail["state"], detail["state_label"]) == ("REJECTED", "Rechazado")
    assert [d["type"] for d in detail["documents"]] == ["DEPOSIT", "REJECTION"]
    twice = reject(admin, cid, day, responsible=ana)
    assert (twice.status_code, twice.json()["code"]) == (409, "cheque_not_rejectable")
    assert_cheque_accounts_invariant(admin, db)


def test_a_rejection_after_crediting_debits_the_bank_of_the_crediting(admin: TestClient, db: Session, ana: int) -> None:
    cid = entered(admin)
    deposited(admin, [cid])
    credited(admin, [cid])
    early = reject(admin, cid, CREDITED - timedelta(days=1), responsible=ana)
    assert (early.status_code, early.json()["code"]) == (422, "invalid_rejection_date")
    doc = rejected(admin, cid, T - timedelta(days=2), responsible=ana)
    assert (doc["stage"], doc["bank_account_id"]) == ("AFTER_CREDIT", account_id(admin, "bank"))
    assert legs(db, doc["operation_id"]) == [
        ("BANK", None, Decimal("-100000.00")),
        ("PENDING_COLLECTIONS", None, Decimal("100000.00")),
    ]
    assert balance(admin, "bank") == 0
    assert cheque(admin, cid)["state"] == "REJECTED"


def test_the_responsible_of_a_rejection_is_the_driver_of_the_cheque_settlement(
    as_role,
    driver,
    db: Session,  # type: ignore[no-untyped-def]
) -> None:
    operator = as_role("operator")
    pedro = driver("Pedro Gómez")
    sid = create(operator, pedro, "50000")
    cid = new(operator, sid, physical(bank(operator)))
    closed(operator, sid)
    deposited(operator, [cid], on=T)
    doc = rejected(operator, cid, T)
    owed = pending(operator, doc["pending_id"])
    assert (owed["responsible_employee_id"], owed["customer_name"]) == (pedro, "Almacén Don José")
    assert owed["settlement_id"] is None  # a new pending, not the settlement's


def test_a_rejection_needs_a_responsible_when_the_cheque_had_no_settlement(admin: TestClient) -> None:
    cid = entered(admin)
    deposited(admin, [cid])
    response = reject(admin, cid, T)
    assert (response.status_code, response.json()["code"]) == (422, "responsible_required")
    inactive = reject(admin, cid, T, responsible=999999)
    assert (inactive.status_code, inactive.json()["code"]) == (422, "employee_not_found")


# --- return ---------------------------------------------------------------------------------------


def test_a_return_is_admin_only_with_a_reason_and_creates_a_new_pending(
    as_role,
    db: Session,
    ana: int,  # type: ignore[no-untyped-def]
) -> None:
    admin = as_role("admin")
    cid = entered(admin)
    operator = as_role("operator")
    forbidden = give_back(operator, cid, T, responsible=ana)
    assert forbidden.status_code == 403
    admin = as_role("admin")
    blank = give_back(admin, cid, T, responsible=ana, reason="  ")
    assert (blank.status_code, blank.json()["code"]) == (422, "reason_required")
    response = give_back(admin, cid, T, responsible=ana)
    assert response.status_code == 201, response.text
    doc = response.json()
    assert legs(db, doc["operation_id"]) == [
        ("CHEQUE_PORTFOLIO", cid, Decimal("-100000.00")),
        ("PENDING_COLLECTIONS", None, Decimal("100000.00")),
    ]
    owed = pending(admin, doc["pending_id"])
    assert (owed["origin_type"], owed["origin_cheque_return_id"], owed["state"]) == (
        "CHEQUE_RETURN",
        doc["id"],
        "OPEN",
    )
    assert cheque(admin, cid)["state"] == "RETURNED"
    again = give_back(admin, cid, T, responsible=ana)
    assert (again.status_code, again.json()["code"]) == (409, "cheque_not_in_portfolio")
    deposited_cheque = entered(admin)
    deposited(admin, [deposited_cheque])
    not_held = give_back(admin, deposited_cheque, T, responsible=ana)
    assert (not_held.status_code, not_held.json()["code"]) == (409, "cheque_not_in_portfolio")
    assert_cheque_accounts_invariant(admin, db)


# --- reversals ------------------------------------------------------------------------------------


def test_reversing_a_deposit_keeps_its_date_and_puts_the_cheques_back(admin: TestClient, db: Session) -> None:
    cid = entered(admin)
    slip = deposited(admin, [cid])
    request_id = str(uuid4())
    response = reverse(admin, f"cheque-deposits/{slip['id']}", request_id)
    assert response.status_code == 201, response.text
    reversal = response.json()["reversal"]
    op = operation(db, reversal["operation_id"])
    original = operation(db, slip["operation_id"])
    assert (op["type"], op["business_date"], op["reverses_operation_id"]) == (
        "REVERSAL",
        DEPOSITED,
        slip["operation_id"],
    )
    assert op["created_at"] > original["created_at"]
    assert legs(db, reversal["operation_id"]) == [
        ("CHEQUE_PORTFOLIO", cid, Decimal("100000.00")),
        ("CHEQUES_IN_TRANSIT", cid, Decimal("-100000.00")),
    ]
    assert cheque(admin, cid)["state"] == "IN_PORTFOLIO"
    retry = reverse(admin, f"cheque-deposits/{slip['id']}", request_id)
    assert retry.status_code == 200
    other = reverse(admin, f"cheque-deposits/{slip['id']}")
    assert (other.status_code, other.json()["code"]) == (409, "deposit_already_reversed")
    # Deposited again on another slip: the history keeps both.
    deposited(admin, [cid], on=T)
    assert [(d["type"], d["reversed"]) for d in cheque(admin, cid)["documents"]] == [
        ("DEPOSIT", True),
        ("DEPOSIT", False),
    ]
    assert_cheque_accounts_invariant(admin, db)


def test_reversals_are_admin_only_and_never_from_cajas(as_role, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = as_role("admin")
    cid = entered(admin)
    slip = deposited(admin, [cid])
    generic = admin.post(
        f"{BASE}/operations/{slip['operation_id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Desde Cajas"},
    )
    assert (generic.status_code, generic.json()["code"]) == (409, "operation_owned_by_document")
    operator = as_role("operator")
    assert reverse(operator, f"cheque-deposits/{slip['id']}").status_code == 403


def test_a_document_with_a_later_one_in_effect_is_not_reversed(admin: TestClient, ana: int) -> None:
    cid = entered(admin)
    slip = deposited(admin, [cid])
    statement = credited(admin, [cid])
    blocked = reverse(admin, f"cheque-deposits/{slip['id']}")
    assert (blocked.status_code, blocked.json()["code"]) == (409, "cheque_has_activity")
    doc = rejected(admin, cid, T, responsible=ana)
    blocked_credit = reverse(admin, f"cheque-credits/{statement['id']}")
    assert (blocked_credit.status_code, blocked_credit.json()["code"]) == (409, "cheque_has_activity")
    # Undone in reverse order, each document on its own date.
    assert reverse(admin, f"cheque-rejections/{doc['id']}").status_code == 201
    assert cheque(admin, cid)["state"] == "CREDITED"
    assert reverse(admin, f"cheque-credits/{statement['id']}").status_code == 201
    assert cheque(admin, cid)["state"] == "DEPOSITED"
    assert reverse(admin, f"cheque-deposits/{slip['id']}").status_code == 201
    assert cheque(admin, cid)["state"] == "IN_PORTFOLIO"
    assert balance(admin, "bank") == 0
    assert balance(admin, "pending_collections") == 0


def test_reversing_a_rejection_voids_its_pending_unless_it_has_activity(admin: TestClient, ana: int) -> None:
    cid = entered(admin)
    deposited(admin, [cid])
    doc = rejected(admin, cid, T, responsible=ana)
    receipt = admin.post(
        f"{BASE}/pending-collections/{doc['pending_id']}/receipts",
        json={
            "client_request_id": str(uuid4()),
            "amount": "1000",
            "method": "CASH",
            "destination_account_id": account_id(admin, "main_cash"),
            "business_date": T.isoformat(),
        },
    )
    assert receipt.status_code == 201, receipt.text
    blocked = reverse(admin, f"cheque-rejections/{doc['id']}")
    assert (blocked.status_code, blocked.json()["code"]) == (409, "pending_has_activity")
    receipt_id = receipt.json()["receipts"][0]["id"]
    undo = admin.post(
        f"{BASE}/pending-collections/{doc['pending_id']}/receipts/{receipt_id}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Cobro mal cargado"},
    )
    assert undo.status_code == 201, undo.text
    response = reverse(admin, f"cheque-rejections/{doc['id']}")
    assert response.status_code == 201, response.text
    owed = pending(admin, doc["pending_id"])
    assert (owed["state"], owed["voided"]) == ("VOIDED", True)
    assert owed["events"][-1]["event_type"] == "VOIDED"
    assert cheque(admin, cid)["state"] == "DEPOSITED"
    # Rejected again: a NEW pending, never the voided one reopened.
    again = rejected(admin, cid, T, responsible=ana)
    assert again["pending_id"] != doc["pending_id"]


def test_reversing_a_return_puts_the_cheque_back_and_voids_the_pending(
    admin: TestClient, db: Session, ana: int
) -> None:
    cid = entered(admin)
    doc = give_back(admin, cid, T, responsible=ana).json()
    response = reverse(admin, f"cheque-returns/{doc['id']}")
    assert response.status_code == 201, response.text
    assert operation(db, response.json()["reversal"]["operation_id"])["business_date"] == T
    assert cheque(admin, cid)["state"] == "IN_PORTFOLIO"
    assert pending(admin, doc["pending_id"])["state"] == "VOIDED"
    assert_cheque_accounts_invariant(admin, db)


# --- a pending collected with a cheque ------------------------------------------------------------


def _rejected_pending(admin: TestClient, ana: int, amount: str = "100000") -> int:
    cid = entered(admin, amount)
    deposited(admin, [cid])
    return rejected(admin, cid, T - timedelta(days=1), responsible=ana)["pending_id"]  # type: ignore[no-any-return]


def receive(client: TestClient, pid: int, body: dict[str, Any], on: date = T) -> Any:
    return client.post(
        f"{BASE}/pending-collections/{pid}/cheque-receipts", json={**body, "business_date": on.isoformat()}
    )


def test_a_physical_cheque_collects_the_pending_and_enters_the_portfolio(
    admin: TestClient, db: Session, ana: int
) -> None:
    pid = _rejected_pending(admin, ana)
    body = instrument(bank(admin), "100000", issue_date=(T - timedelta(days=1)).isoformat())
    response = receive(admin, pid, body)
    assert response.status_code == 201, response.text
    owed = response.json()
    assert (owed["state"], owed["balance"], owed["collected"]) == ("COLLECTED", "0.00", "100000.00")
    receipt = owed["receipts"][0]
    assert (receipt["method"], receipt["destination_name"]) == ("CHEQUE", "Cartera de cheques")
    cid = receipt["cheque_id"]
    assert legs(db, receipt["operation_id"]) == [
        ("PENDING_COLLECTIONS", None, Decimal("-100000.00")),
        ("CHEQUE_PORTFOLIO", cid, Decimal("100000.00")),
    ]
    paid_with = cheque(admin, cid)
    assert (paid_with["state"], paid_with["origin_type"], paid_with["origin_pending_id"]) == (
        "IN_PORTFOLIO",
        "PENDING_RECEIPT",
        pid,
    )
    assert paid_with["entry_operation_id"] == receipt["operation_id"]
    assert [e["event_type"] for e in paid_with["events"]] == ["RECEIVED", "COLLECTED"]
    replay = receive(admin, pid, body)
    assert (replay.status_code, len(replay.json()["receipts"])) == (200, 1)
    # The cheque of the receipt goes on through the cycle like any other.
    assert deposit(admin, [cid], on=T).status_code == 201
    assert_cheque_accounts_invariant(admin, db)


def test_a_cheque_larger_than_the_balance_is_refused_whole(admin: TestClient, ana: int) -> None:
    pid = _rejected_pending(admin, ana, "100000")
    larger = receive(admin, pid, instrument(bank(admin), "100000.01", issue_date=T.isoformat()))
    assert (larger.status_code, larger.json()["code"]) == (409, "pending_amount_exceeds_balance")
    assert admin.get(f"{BASE}/cheques", params={"origin_type": "PENDING_RECEIPT"}).json()["total"] == 0
    partial = receive(admin, pid, instrument(bank(admin), "30000", issue_date=T.isoformat()))
    assert partial.status_code == 201
    assert (partial.json()["state"], partial.json()["balance"]) == ("PARTIAL", "70000.00")


def test_a_declared_echeq_lowers_nothing_until_verified_and_is_revalidated(
    admin: TestClient, db: Session, ana: int
) -> None:
    pid = _rejected_pending(admin, ana, "100000")
    body = {
        **instrument(bank(admin), "80000", issue_date=T.isoformat()),
        "kind": "ECHEQ",
        "physical_receipt_confirmed": False,
        "branch_code": None,
        "drawer_tax_id": "30712345678",
        "echeq_external_id": f"ECH-{uuid4().hex[:8]}",
    }
    declared = receive(admin, pid, body)
    assert declared.status_code == 201, declared.text
    owed = declared.json()
    assert (owed["balance"], owed["receipts"]) == ("100000.00", [])
    echeq_id = owed["echeqs"][0]["cheque_id"]
    assert owed["echeqs"][0]["verification_status"] == "DECLARED"
    assert owed["events"][-1]["event_type"] == "ECHEQ_DECLARED"
    assert cheque(admin, echeq_id)["state"] == "DECLARED"
    assert balance(admin, "cheque_portfolio") == 0
    # Meanwhile the customer paid part in cash: the ECHEQ no longer fits.
    cash = admin.post(
        f"{BASE}/pending-collections/{pid}/receipts",
        json={
            "client_request_id": str(uuid4()),
            "amount": "30000",
            "method": "CASH",
            "destination_account_id": account_id(admin, "main_cash"),
            "business_date": T.isoformat(),
        },
    )
    assert cash.status_code == 201, cash.text
    path = f"{BASE}/pending-collections/{pid}/cheque-receipts/{echeq_id}/verification"
    refused = admin.post(path, json={"status": "VERIFIED"})
    assert (refused.status_code, refused.json()["code"]) == (409, "pending_amount_exceeds_balance")
    assert cheque(admin, echeq_id)["verification_status"] == "DECLARED"  # nothing changed
    receipt_id = cash.json()["receipts"][0]["id"]
    undo = admin.post(
        f"{BASE}/pending-collections/{pid}/receipts/{receipt_id}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "No era de este cliente"},
    )
    assert undo.status_code == 201
    verified = admin.post(path, json={"status": "VERIFIED"})
    assert verified.status_code == 200, verified.text
    owed = verified.json()
    assert (owed["state"], owed["balance"]) == ("PARTIAL", "20000.00")
    receipt = next(r for r in owed["receipts"] if r["method"] == "CHEQUE")
    assert legs(db, receipt["operation_id"]) == [
        ("PENDING_COLLECTIONS", None, Decimal("-80000.00")),
        ("CHEQUE_PORTFOLIO", echeq_id, Decimal("80000.00")),
    ]
    assert cheque(admin, echeq_id)["state"] == "IN_PORTFOLIO"
    assert admin.post(path, json={"status": "VERIFIED"}).status_code == 200  # a retry changes nothing
    assert len(pending(admin, pid)["receipts"]) == 2
    assert_cheque_accounts_invariant(admin, db)


def test_a_rejected_echeq_verification_is_recorded_on_the_pending(admin: TestClient, ana: int) -> None:
    pid = _rejected_pending(admin, ana)
    body = {
        **instrument(bank(admin), "50000", issue_date=T.isoformat()),
        "kind": "ECHEQ",
        "physical_receipt_confirmed": False,
        "drawer_tax_id": "30712345678",
        "echeq_external_id": f"ECH-{uuid4().hex[:8]}",
    }
    echeq_id = receive(admin, pid, body).json()["echeqs"][0]["cheque_id"]
    path = f"{BASE}/pending-collections/{pid}/cheque-receipts/{echeq_id}/verification"
    no_reason = admin.post(path, json={"status": "VERIFICATION_REJECTED"})
    assert (no_reason.status_code, no_reason.json()["code"]) == (422, "reason_required")
    response = admin.post(path, json={"status": "VERIFICATION_REJECTED", "reason": "No figura en el banco"})
    assert response.status_code == 200
    assert response.json()["events"][-1]["event_type"] == "ECHEQ_DECLINED"
    assert response.json()["balance"] == "100000.00"
    decided = admin.post(path, json={"status": "VERIFIED"})
    assert (decided.status_code, decided.json()["code"]) == (409, "echeq_already_decided")


def test_reversing_a_receipt_with_a_cheque_voids_the_cheque_while_in_the_portfolio(
    admin: TestClient, db: Session, ana: int
) -> None:
    pid = _rejected_pending(admin, ana)
    owed = receive(admin, pid, instrument(bank(admin), "60000", issue_date=T.isoformat())).json()
    receipt = owed["receipts"][0]
    cid = receipt["cheque_id"]
    slip = deposited(admin, [cid], on=T)
    path = f"{BASE}/pending-collections/{pid}/receipts/{receipt['id']}/reverse"
    blocked = admin.post(path, json={"client_request_id": str(uuid4()), "reason": "Error"})
    assert (blocked.status_code, blocked.json()["code"]) == (409, "cheque_has_activity")
    assert reverse(admin, f"cheque-deposits/{slip['id']}").status_code == 201
    response = admin.post(path, json={"client_request_id": str(uuid4()), "reason": "Error"})
    assert response.status_code == 201, response.text
    assert response.json()["balance"] == "100000.00"
    voided = cheque(admin, cid)
    assert (voided["state"], voided["voided"]) == ("VOIDED", True)
    assert voided["events"][-1]["event_type"] == "COLLECTION_REVERSED"
    assert_cheque_accounts_invariant(admin, db)
