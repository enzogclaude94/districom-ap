"""«Banco en descubierto» (Enzo's R4-2 policy), the ten scenarios he asked for.

A cheque rejected after its crediting debits the bank even into an overdraft (an external event, not a
decision of the company). Later incomes reduce it. Any other debit of the bank (a withdrawal, a payment out,
a reversal) keeps every end-of-day balance from its date on >= 0, so it can neither create an overdraft nor
deepen one. The service applies the rule and the database repeats it (`check_account_not_negative`, 0013):
the bank keeps `allow_negative = false`.
"""

from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from tests.modules.settlements.test_cheque_cycle_api import (
    BASE,
    account_id,
    balance,
    credited,
    deposited,
    entered,
    rejected,
)

T = business_today()
CREDITED = T - timedelta(days=6)
SPENT = T - timedelta(days=5)
REJECTED = T - timedelta(days=4)


def cash_op(client: TestClient, kind: str, on: date = T, **body: object) -> object:
    return client.post(
        f"{BASE}/operations/{kind}", json={"client_request_id": str(uuid4()), "business_date": on.isoformat(), **body}
    )


def withdraw(client: TestClient, amount: str, on: date = T) -> object:
    return cash_op(
        client,
        "bank-withdrawal",
        on,
        bank_account_id=account_id(client, "bank"),
        cash_account_id=account_id(client, "main_cash"),
        amount=amount,
    )


def bring(client: TestClient, amount: str, on: date = T) -> object:
    """An income: cash taken to the bank (the cash comes from an opening balance of the box)."""
    funded = cash_op(client, "opening-balance", on, account_id=account_id(client, "change_cash"), amount=amount)
    assert funded.status_code == 201, funded.text  # type: ignore[attr-defined]
    return cash_op(
        client,
        "bank-deposit",
        on,
        cash_account_id=account_id(client, "change_cash"),
        bank_account_id=account_id(client, "bank"),
        amount=amount,
    )


def credited_cheque(client: TestClient, amount: str = "100000") -> int:
    cid = entered(client, amount)
    deposited(client, [cid])
    credited(client, [cid], on=CREDITED)
    return cid


def bank_out(client: TestClient) -> dict[str, object]:
    return next(a for a in client.get(f"{BASE}/accounts").json() if a["code"] == "bank")  # type: ignore[no-any-return]


@pytest.fixture
def admin(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("admin")  # type: ignore[no-any-return]


@pytest.fixture
def ana(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Ana Cobranzas")  # type: ignore[no-any-return]


def _at_minus(admin: TestClient, ana: int, left: str) -> int:
    """A credited cheque of $100.000 whose money was spent down to `left`, then rejected."""
    cid = credited_cheque(admin)
    spent = Decimal("100000") - Decimal(left)
    if spent:
        assert withdraw(admin, str(spent), SPENT).status_code == 201  # type: ignore[attr-defined]
    rejected(admin, cid, REJECTED, responsible=ana)
    return cid


def test_1_a_rejection_after_crediting_may_overdraw_the_bank(admin: TestClient, ana: int) -> None:
    _at_minus(admin, ana, "50000")
    assert balance(admin, "bank") == Decimal("-50000.00")
    assert balance(admin, "pending_collections") == Decimal("100000.00")


def test_2_from_zero_the_rejection_leaves_the_whole_amount_overdrawn(admin: TestClient, ana: int) -> None:
    _at_minus(admin, ana, "0")
    assert balance(admin, "bank") == Decimal("-100000.00")


def test_3_and_4_incomes_reduce_the_overdraft_and_end_it(admin: TestClient, ana: int) -> None:
    _at_minus(admin, ana, "0")
    assert bring(admin, "50000").status_code == 201  # type: ignore[attr-defined]
    assert balance(admin, "bank") == Decimal("-50000.00")  # [3]
    assert bring(admin, "100000").status_code == 201  # type: ignore[attr-defined]
    assert balance(admin, "bank") == Decimal("50000.00")  # [4]
    assert bank_out(admin)["overdrawn"] is False


def test_5_a_withdrawal_never_deepens_an_overdraft(admin: TestClient, ana: int) -> None:
    _at_minus(admin, ana, "0")
    response = withdraw(admin, "20000")
    assert response.status_code == 409  # type: ignore[attr-defined]
    assert response.json()["code"] == "insufficient_funds"  # type: ignore[attr-defined]
    assert balance(admin, "bank") == Decimal("-100000.00")


def test_6_a_payment_out_of_the_bank_never_deepens_an_overdraft(admin: TestClient, ana: int) -> None:
    _at_minus(admin, ana, "50000")
    owner = admin.post(f"{BASE}/accounts", json={"name": "Socio A"}).json()["id"]
    response = cash_op(
        admin, "owner-withdrawal", owner_account_id=owner, account_id=account_id(admin, "bank"), amount="1"
    )
    assert response.status_code == 409  # type: ignore[attr-defined]
    assert response.json()["code"] == "insufficient_funds"  # type: ignore[attr-defined]
    adjustment = cash_op(
        admin, "adjustment", account_id=account_id(admin, "bank"), amount="-1", description="Comisión bancaria"
    )
    assert adjustment.json()["code"] == "insufficient_funds"  # type: ignore[attr-defined]


def test_7_another_rejection_may_deepen_it(admin: TestClient, ana: int) -> None:
    first, second = entered(admin, "100000"), entered(admin, "30000")
    deposited(admin, [first, second])
    credited(admin, [first, second], on=CREDITED)
    assert withdraw(admin, "130000", SPENT).status_code == 201  # type: ignore[attr-defined]
    rejected(admin, first, REJECTED, responsible=ana)
    assert balance(admin, "bank") == Decimal("-100000.00")
    rejected(admin, second, T, responsible=ana)
    assert balance(admin, "bank") == Decimal("-130000.00")


def test_8_an_ordinary_operation_never_creates_an_overdraft(admin: TestClient, db: Session) -> None:
    assert bring(admin, "10000").status_code == 201  # type: ignore[attr-defined]
    response = withdraw(admin, "10000.01")
    assert (response.status_code, response.json()["code"]) == (409, "insufficient_funds")  # type: ignore[attr-defined]
    # Nor retroactively: a withdrawal dated before the income.
    before = withdraw(admin, "1", T - timedelta(days=1))
    assert before.json()["code"] == "insufficient_funds"  # type: ignore[attr-defined]
    # And the database refuses it even to code that skips the service: the bank is not `allow_negative`.
    assert db.execute(text("SELECT allow_negative FROM settlements.accounts WHERE code = 'bank'")).scalar_one() is False
    connection = db.get_bind().connect()  # type: ignore[union-attr]
    try:
        user_id = connection.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
        op_id = connection.execute(
            text(
                "INSERT INTO settlements.financial_operations (type, business_date, client_request_id,"
                " request_fingerprint, created_by) VALUES ('BANK_WITHDRAWAL', :d, :r, :f, :u) RETURNING id"
            ),
            {"d": T, "r": uuid4(), "f": "0" * 64, "u": user_id},
        ).scalar_one()
        for code, amount in (("bank", "-20000"), ("main_cash", "20000")):
            connection.execute(
                text(
                    "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) "
                    "SELECT :o, id, :m FROM settlements.accounts WHERE code = :c"
                ),
                {"o": op_id, "m": amount, "c": code},
            )
        with pytest.raises(DBAPIError, match="cannot have a negative balance"):
            connection.commit()
    finally:
        connection.rollback()
        connection.close()


def test_9_the_history_by_business_date_is_right(admin: TestClient, ana: int) -> None:
    _at_minus(admin, ana, "50000")
    assert bring(admin, "80000", T - timedelta(days=2)).status_code == 201  # type: ignore[attr-defined]
    assert balance(admin, "bank", CREDITED) == Decimal("100000.00")
    assert balance(admin, "bank", SPENT) == Decimal("50000.00")
    assert balance(admin, "bank", REJECTED) == Decimal("-50000.00")
    assert balance(admin, "bank", T - timedelta(days=3)) == Decimal("-50000.00")
    assert balance(admin, "bank", T - timedelta(days=2)) == Decimal("30000.00")
    # A debit dated inside the overdraft deepens a past day: refused, even if today's balance would cover it.
    retroactive = withdraw(admin, "1000", T - timedelta(days=3))
    assert retroactive.json()["code"] == "insufficient_funds"  # type: ignore[attr-defined]
    assert retroactive.json()["details"]["business_date"] == (T - timedelta(days=3)).isoformat()  # type: ignore[attr-defined]
    # One dated after the overdraft ended, within what is there, is fine.
    assert withdraw(admin, "30000").status_code == 201  # type: ignore[attr-defined]
    movements = admin.get(f"{BASE}/accounts/{account_id(admin, 'bank')}/movements").json()["items"]
    assert "CHEQUE_REJECTION" in {m["operation_type"] for m in movements}


def test_10_cajas_shows_the_bank_overdrawn(admin: TestClient, ana: int) -> None:
    _at_minus(admin, ana, "0")
    bank = bank_out(admin)
    assert (bank["balance"], bank["overdrawn"], bank["overdrawn_label"]) == (
        "-100000.00",
        True,
        "Banco en descubierto",
    )
    cash = next(a for a in admin.get(f"{BASE}/accounts").json() if a["code"] == "main_cash")
    assert (cash["overdrawn"], cash["overdrawn_label"]) == (False, None)
    on_credit = admin.get(f"{BASE}/accounts/{account_id(admin, 'bank')}", params={"on": CREDITED.isoformat()}).json()
    assert on_credit["overdrawn"] is False
