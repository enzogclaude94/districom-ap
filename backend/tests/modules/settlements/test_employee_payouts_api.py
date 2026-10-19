"""Payouts of closed internal payrolls through the API (R8-5, ADR 010, Enzo's R8-5 decisions): partial and final
payments, NO_ACCRUAL, dates, methods, funds, permissions, reversal, reopening and idempotency.

Signs: + the employee owes the company. The database repeats each rule (`test_employee_payouts_db_invariants.py`);
races are in `test_employee_payouts_concurrency.py`.
"""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_finance_support import EF, error
from tests.modules.settlements.employee_loan_support import funded, given
from tests.modules.settlements.employee_movement_support import (
    area_admin_without_cash,
    area_viewer,
    balance,
    internal_account,
    outsider,
    registered,
)
from tests.modules.settlements.employee_payout_support import (
    area_admin,
    cash_box,
    net_payroll,
    paid,
    pay,
    payer,
    payment,
    payout_legs,
    preview,
    reverse_payout,
)
from tests.modules.settlements.employee_payroll_support import (
    chosen,
    closed,
    leg,
    line,
    opened,
    payroll,
    reopen,
    reopened,
    shortage,
    totals,
)

__all__ = ["funded"]


@pytest.fixture
def pedro(driver: Callable[..., int]) -> int:
    return driver("Pedro Gómez")


@pytest.fixture
def ids(accounts: dict[str, int]) -> dict[str, int]:
    return accounts


def _admin(user_as: Any) -> TestClient:
    return user_as("rrhh", settlements="admin", area="admin")  # type: ignore[no-any-return]


def _audit(db: Session, event: str) -> list[Any]:
    db.rollback()
    return list(
        db.execute(
            text("SELECT payload->'after' FROM audit_events WHERE event_type = :e ORDER BY id"), {"e": event}
        ).scalars()
    )


def _case_1(as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], post: Any) -> Any:
    """R8-4's case 1 closed: net 650.000, the internal account at -400.000, the loan at 250.000."""
    post(
        "OPENING_BALANCE", (ids["main_cash"], "400000"), (ids["opening_balance"], "-400000"), business_date=days_ago(40)
    )
    shortage(as_role, ids, pedro, "30000")
    admin = _admin(user_as)
    registered(admin, "CONSUMPTION", pedro, "20000", on=days_ago(5))
    registered(admin, "ADVANCE", pedro, "100000", on=days_ago(5), funding_account_id=funded["main_cash"])
    loan = given(admin, pedro, funded["main_cash"], "300000", 6, on=days_ago(20), first_due_on=days_ago(10))
    p = opened(admin, pedro, days_ago(30))
    line(admin, p["id"], "SALARY", "800000")
    line(admin, p["id"], "BONUS", "50000")
    return closed(admin, p["id"]), loan


# --- Enzo's cases -------------------------------------------------------------------------------------------------


def test_case_1_partial_then_final_payment_leaves_the_account_in_the_employees_debt(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], post: Any, db: Session
) -> None:
    p, loan = _case_1(as_role, user_as, ids, pedro, funded, post)
    internal = internal_account(db, pedro)
    assert totals(p)[3] == "650000.00"
    assert balance(db, internal) == Decimal("-400000")
    cash_before, bank_before = balance(db, ids["main_cash"]), balance(db, ids["bank"])
    client = payer(user_as)
    assert payment(client, p["id"]) == ("650000.00", "0.00", "650000.00", "UNPAID")

    shown = preview(client, p, ("CASH", ids["main_cash"], "100000"), ("TRANSFER", ids["bank"], "200000"))
    assert shown.status_code == 200, shown.text
    v = shown.json()
    assert (v["net"], v["paid"], v["amount"], v["pending_after"]) == ("650000.00", "0.00", "300000.00", "350000.00")
    assert (v["balance_before"], v["balance_after"]) == ("-400000.00", "-100000.00")
    assert sorted((x["account_kind"], x["amount"]) for x in v["legs"]) == [
        ("BANK", "-200000.00"),
        ("CASH_BOX", "-100000.00"),
        ("EMPLOYEE", "300000.00"),
    ]

    first = paid(client, p, ("CASH", ids["main_cash"], "100000"), ("TRANSFER", ids["bank"], "200000"))
    assert (first["amount"], first["in_effect"], first["payroll_closing_id"]) == ("300000.00", True, p["closing"]["id"])
    assert sorted((i["method"], i["amount"]) for i in first["items"]) == [
        ("CASH", "100000.00"),
        ("TRANSFER", "200000.00"),
    ]
    assert payout_legs(db, first["id"]) == [
        ("BANK", "-200000.00"),
        ("CASH_BOX", "-100000.00"),
        ("EMPLOYEE", "300000.00"),
    ]
    assert payment(client, p["id"]) == ("650000.00", "300000.00", "350000.00", "PARTIALLY_PAID")
    assert balance(db, internal) == Decimal("-100000")

    second = paid(client, p, ("CASH", ids["main_cash"], "350000"))
    assert second["amount"] == "350000.00"
    assert payment(client, p["id"]) == ("650000.00", "650000.00", "0.00", "PAID")
    assert balance(db, internal) == Decimal("250000")  # «Debe a la empresa $250.000»
    assert balance(db, ids["main_cash"]) == cash_before - Decimal("450000")
    assert balance(db, ids["bank"]) == bank_before - Decimal("200000")
    after = client.get(f"{EF}/loans/{loan['id']}").json()
    assert after["balance"] == "250000.00"  # paying never touches the loan
    assert client.get(f"{EF}/payrolls", params={"employee_id": pedro}).json()["items"][0]["payment_status"] == "PAID"
    summary = client.get(f"{EF}/employees/{pedro}/pay-summary").json()
    assert (summary["pending_payrolls"], summary["pending_payroll_count"]) == ("0.00", 0)
    assert [x["id"] for x in summary["recent_payouts"]] == [second["id"], first["id"]]
    assert summary["last_payroll"]["id"] == p["id"]
    assert client.get(f"{EF}/employees/{pedro}").json()["balance"] == "250000.00"
    # Nothing more to pay.
    assert error(pay(client, p, ("CASH", ids["main_cash"], "1"))) == (409, "payroll_fully_paid")
    assert _audit(db, "settlements.employee_finance.payout.created")[1]["pending_after"] == "0.00"


def test_case_4_no_accrual_net_is_paid(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, funded: Any, db: Session
) -> None:
    shortage(as_role, ids, pedro, "-20000")
    admin = _admin(user_as)
    p = opened(admin, pedro, days_ago(30), kind="EXTRAORDINARY")
    p = chosen(admin, p["id"], entry=leg(p, "SETTLEMENT_SURPLUS")["entry_id"])
    p = closed(admin, p["id"])
    assert (p["closing"]["kind"], totals(p)[3]) == ("NO_ACCRUAL", "20000.00")
    internal = internal_account(db, pedro)
    assert balance(db, internal) == Decimal("-20000")
    client = payer(user_as)
    paid(client, p, ("CASH", ids["main_cash"], "20000"))
    assert payment(client, p["id"]) == ("20000.00", "20000.00", "0.00", "PAID")
    assert balance(db, internal) == Decimal("0")


def test_reopen_is_refused_while_paid_and_allowed_after_reversing(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], db: Session
) -> None:
    admin = area_admin(user_as)
    p = net_payroll(admin, pedro, "100000")
    first = paid(admin, p, ("CASH", ids["main_cash"], "60000"))
    response = reopen(admin, p["id"], p["version"])
    assert error(response) == (409, "payroll_has_payouts")
    assert response.json()["message"] == (
        "Esta liquidación tiene pagos vigentes. Revertí los pagos antes de reabrirla o registrá la corrección "
        "económica en una liquidación posterior."
    )
    internal = internal_account(db, pedro)
    cash = balance(db, ids["main_cash"])
    done = reverse_payout(admin, first["id"], "Se pagó de más")
    assert done.status_code == 201, done.text
    r = done.json()
    assert (r["in_effect"], r["reversal_reason"], r["reversed_by_name"]) == (False, "Se pagó de más", "Usuario Test")
    assert balance(db, ids["main_cash"]) == cash + Decimal("60000")
    assert balance(db, internal) == Decimal("-100000")
    db.rollback()
    on = db.execute(
        text("SELECT o.business_date FROM settlements.financial_operations o WHERE o.id = :o"),
        {"o": r["reversal_operation_id"]},
    ).scalar_one()
    assert on == first["business_date"] or str(on) == first["business_date"]
    assert payment(admin, p["id"]) == ("100000.00", "0.00", "100000.00", "UNPAID")
    again = reopened(admin, p["id"], p["version"])
    assert (again["status"], again["payment"]) == ("OPEN", None)
    # History intact: the reversed payout is still listed, with its reason.
    assert [(x["id"], x["in_effect"]) for x in again["payouts"]] == [(first["id"], False)]
    # Paying an open payroll is refused.
    assert error(pay(admin, p, ("CASH", ids["main_cash"], "1"))) == (409, "payroll_not_closed")


def test_a_new_close_after_reopening_is_paid_on_its_own(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int]
) -> None:
    admin = area_admin(user_as)
    p = net_payroll(admin, pedro, "100000")
    first = paid(admin, p, ("CASH", ids["main_cash"], "100000"))
    assert reverse_payout(admin, first["id"]).status_code == 201
    reopened(admin, p["id"], p["version"])
    line(admin, p["id"], "BONUS", "20000")
    p = closed(admin, p["id"])
    assert payment(admin, p["id"]) == ("120000.00", "0.00", "120000.00", "UNPAID")
    # A request built on the superseded close is stale.
    stale = pay(admin, p, ("CASH", ids["main_cash"], "1"), closing_id=first["payroll_closing_id"])
    assert error(stale) == (409, "payroll_version_changed")
    assert paid(admin, p, ("CASH", ids["main_cash"], "120000"))["payroll_version"] == 2


# --- amount, dates and methods ----------------------------------------------------------------------------------


def test_never_more_than_pending(user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int]) -> None:
    client = payer(user_as)
    p = net_payroll(_admin(user_as), pedro, "100000")
    client = payer(user_as)
    assert error(pay(client, p, ("CASH", ids["main_cash"], "100000.01"))) == (409, "payout_exceeds_pending")
    paid(client, p, ("CASH", ids["main_cash"], "70000"))
    assert error(pay(client, p, ("CASH", ids["main_cash"], "20000"), ("TRANSFER", ids["bank"], "10000.01"))) == (
        409,
        "payout_exceeds_pending",
    )
    paid(client, p, ("CASH", ids["main_cash"], "20000"), ("TRANSFER", ids["bank"], "10000"))
    assert payment(client, p["id"])[3] == "PAID"


def test_dates_from_the_period_end_up_to_today(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int]
) -> None:
    admin = _admin(user_as)
    p = net_payroll(admin, pedro, "100000", days_ago(30), days_ago(10))
    client = payer(user_as)
    assert error(pay(client, p, ("CASH", ids["main_cash"], "1000"), on=days_ago(11))) == (
        422,
        "payout_before_period_end",
    )
    assert error(pay(client, p, ("CASH", ids["main_cash"], "1000"), on=today() + timedelta(days=1))) == (
        422,
        "future_business_date",
    )
    assert paid(client, p, ("CASH", ids["main_cash"], "1000"), on=days_ago(10))["business_date"] == str(days_ago(10))
    assert paid(client, p, ("CASH", ids["main_cash"], "1000"), on=days_ago(3))["business_date"] == str(days_ago(3))


def test_payment_date_is_informative_only(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int]
) -> None:
    admin = _admin(user_as)
    p = opened(admin, pedro, days_ago(30), days_ago(10), payment_date=str(days_ago(2)))
    line(admin, p["id"], "SALARY", "100000")
    p = closed(admin, p["id"])
    client = payer(user_as)
    paid(client, p, ("CASH", ids["main_cash"], "1000"), on=days_ago(10))  # before payment_date
    paid(client, p, ("CASH", ids["main_cash"], "1000"), on=days_ago(2))
    paid(client, p, ("CASH", ids["main_cash"], "1000"), on=today())  # after it


def test_only_cash_from_a_cash_box_and_transfer_from_a_bank(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], db: Session
) -> None:
    p = net_payroll(_admin(user_as), pedro, "100000")
    client = payer(user_as)
    for method in ("CHEQUE", "ECHEQ", "OTHER"):
        assert pay(client, p, (method, ids["main_cash"], "1000")).status_code == 422
    assert error(pay(client, p, ("CASH", ids["bank"], "1000"))) == (422, "invalid_payout_account")
    assert error(pay(client, p, ("TRANSFER", ids["main_cash"], "1000"))) == (422, "invalid_payout_account")
    for code in ("cheque_portfolio", "opening_balance", "payroll_expense", "expense"):
        assert error(pay(client, p, ("CASH", ids[code], "1000"))) == (422, "invalid_payout_account"), code
    assert error(pay(client, p, ("CASH", internal_account(db, pedro), "1000"))) == (422, "invalid_payout_account")
    closed_box = cash_box(db, "Caja cerrada", active=False)
    assert error(pay(client, p, ("CASH", closed_box, "1000"))) == (422, "invalid_payout_account")
    assert error(pay(client, p, ("CASH", ids["main_cash"], "1000"), ("CASH", ids["main_cash"], "2000"))) == (
        422,
        "duplicate_payout_account",
    )
    assert pay(client, p, ("CASH", ids["main_cash"], "0")).status_code == 422
    assert (
        error(
            client.post(
                f"{EF}/payrolls/{p['id']}/payouts",
                json={
                    "client_request_id": str(uuid4()),
                    "payroll_closing_id": p["closing"]["id"],
                    "business_date": str(today()),
                    "items": [],
                },
            )
        )[0]
        == 422
    )
    assert payment(client, p["id"])[1] == "0.00"  # nothing was written


def test_two_cash_boxes_and_the_bank_in_one_payout(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], post: Any, db: Session
) -> None:
    petty = cash_box(db)
    post("OPENING_BALANCE", (petty, "5000"), (ids["opening_balance"], "-5000"), business_date=days_ago(40))
    p = net_payroll(_admin(user_as), pedro, "100000")
    x = paid(
        payer(user_as),
        p,
        ("CASH", ids["main_cash"], "45000"),
        ("CASH", petty, "5000"),
        ("TRANSFER", ids["bank"], "50000"),
    )
    assert payout_legs(db, x["id"]) == [
        ("BANK", "-50000.00"),
        ("CASH_BOX", "-45000.00"),
        ("CASH_BOX", "-5000.00"),
        ("EMPLOYEE", "100000.00"),
    ]
    assert balance(db, petty) == Decimal("0")


# --- funds ------------------------------------------------------------------------------------------------------


def test_funds_cash_never_negative_and_the_bank_never_overdrawn(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], post: Any, db: Session
) -> None:
    p = net_payroll(_admin(user_as), pedro, "900000", days_ago(30), days_ago(5))
    client = payer(user_as)
    assert error(pay(client, p, ("CASH", ids["main_cash"], "500000.01"))) == (409, "insufficient_funds")
    assert error(pay(client, p, ("TRANSFER", ids["bank"], "300000.01"))) == (409, "insufficient_funds")
    # Mixed: the bank lacks, so the cash box is not touched either.
    cash = balance(db, ids["main_cash"])
    assert error(pay(client, p, ("CASH", ids["main_cash"], "100000"), ("TRANSFER", ids["bank"], "300000.01"))) == (
        409,
        "insufficient_funds",
    )
    assert balance(db, ids["main_cash"]) == cash
    # A later day's debit counts: money spent on a later day is not available on an earlier date.
    post("BANK_DEPOSIT", (ids["main_cash"], "-450000"), (ids["bank"], "450000"), business_date=today())
    assert error(pay(client, p, ("CASH", ids["main_cash"], "60000"), on=days_ago(3))) == (
        409,
        "insufficient_funds",
    )
    paid(client, p, ("CASH", ids["main_cash"], "50000"), ("TRANSFER", ids["bank"], "750000"), on=today())
    assert balance(db, ids["main_cash"]) == Decimal("0")
    assert balance(db, ids["bank"]) == Decimal("0")


def test_the_internal_balance_is_never_the_limit(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], db: Session
) -> None:
    admin = area_admin(user_as)
    registered(admin, "ADVANCE", pedro, "50000", on=days_ago(5), funding_account_id=ids["main_cash"])
    p = opened(admin, pedro, days_ago(30), kind="EXTRAORDINARY")  # applies nothing: the advance stays owed
    line(admin, p["id"], "BONUS", "30000")
    p = closed(admin, p["id"])
    internal = internal_account(db, pedro)
    assert balance(db, internal) == Decimal("20000")  # the employee owes, yet the payroll's net is owed to them
    paid(admin, p, ("CASH", ids["main_cash"], "30000"))
    assert balance(db, internal) == Decimal("50000")


# --- permissions and privacy ------------------------------------------------------------------------------------


def test_permissions(user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int]) -> None:
    p = net_payroll(_admin(user_as), pedro, "100000")
    item = ("CASH", ids["main_cash"], "1000")
    assert error(pay(outsider(user_as), p, item)) == (404, "not_found")
    assert outsider(user_as).get(f"{EF}/payouts").status_code == 404
    assert error(pay(area_viewer(user_as), p, item)) == (403, "forbidden")
    no_cash = user_as("cajera2", settlements="viewer", area="operator")
    assert error(pay(no_cash, p, item)) == (403, "forbidden")
    assert error(pay(area_admin_without_cash(user_as), p, item)) == (403, "forbidden")
    assert preview(no_cash, p, item).status_code == 403
    x = paid(payer(user_as), p, item)
    assert error(reverse_payout(payer(user_as), x["id"])) == (403, "forbidden")
    assert error(reverse_payout(area_admin_without_cash(user_as), x["id"])) == (403, "forbidden")
    assert error(reverse_payout(outsider(user_as), x["id"])) == (404, "not_found")
    assert area_viewer(user_as).get(f"{EF}/payouts/{x['id']}").status_code == 200
    assert reverse_payout(area_admin(user_as), x["id"]).status_code == 201


def test_an_inactive_employee_is_paid(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], db: Session
) -> None:
    p = net_payroll(_admin(user_as), pedro, "100000")
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": pedro})
    db.commit()
    paid(payer(user_as), p, ("CASH", ids["main_cash"], "100000"))


# --- reversal and idempotency -----------------------------------------------------------------------------------


def test_reversal_rules(user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], db: Session) -> None:
    admin = area_admin(user_as)
    p = net_payroll(admin, pedro, "100000")
    x = paid(admin, p, ("CASH", ids["main_cash"], "40000"), ("TRANSFER", ids["bank"], "60000"))
    assert error(reverse_payout(admin, x["id"], "   ")) in {(422, "reason_required"), (422, "validation_error")}
    request = uuid4()
    first = reverse_payout(admin, x["id"], "Error de carga", request)
    assert first.status_code == 201
    assert reverse_payout(admin, x["id"], "Error de carga", request).status_code == 200
    assert error(reverse_payout(admin, x["id"], "Otro motivo", request)) == (409, "idempotency_conflict")
    assert error(reverse_payout(admin, x["id"], "Error de carga")) == (409, "employee_payout_already_reversed")
    assert error(reverse_payout(admin, 999999)) == (404, "employee_payout_not_found")
    db.rollback()
    legs = db.execute(
        text(
            "SELECT a.kind, e.amount FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.operation_id = :o ORDER BY a.kind"
        ),
        {"o": first.json()["reversal_operation_id"]},
    ).all()
    assert [(k, str(a)) for k, a in legs] == [
        ("BANK", "60000.00"),
        ("CASH_BOX", "40000.00"),
        ("EMPLOYEE", "-100000.00"),
    ]
    moves = admin.get(f"{EF}/employees/{pedro}/movements").json()["items"]
    assert {(m["origin"], m["document_kind"], m["document_reversed"]) for m in moves} >= {
        ("PAYOUT", "EMPLOYEE_PAYOUT", True),
        ("PAYOUT_REVERSAL", "EMPLOYEE_PAYOUT", True),
    }


def test_reversal_needs_its_accounts_active(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], post: Any, db: Session
) -> None:
    petty = cash_box(db)
    post("OPENING_BALANCE", (petty, "5000"), (ids["opening_balance"], "-5000"), business_date=days_ago(40))
    admin = area_admin(user_as)
    p = net_payroll(admin, pedro, "5000")
    x = paid(admin, p, ("CASH", petty, "5000"))
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :a"), {"a": petty})
    db.commit()
    assert error(reverse_payout(admin, x["id"])) == (409, "account_inactive")


def test_idempotent_create(user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], db: Session) -> None:
    p = net_payroll(_admin(user_as), pedro, "100000")
    client = payer(user_as)
    request = uuid4()
    first = pay(client, p, ("CASH", ids["main_cash"], "1000"), request=request)
    assert first.status_code == 201
    again = pay(client, p, ("CASH", ids["main_cash"], "1000"), request=request)
    assert (again.status_code, again.json()["id"]) == (200, first.json()["id"])
    assert error(pay(client, p, ("CASH", ids["main_cash"], "2000"), request=request)) == (409, "idempotency_conflict")
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM settlements.employee_payouts")).scalar_one() == 1


def test_cannot_pay_a_cancelled_or_unknown_payroll(user_as: Any, ids: dict[str, int], pedro: int, funded: Any) -> None:
    admin = _admin(user_as)
    p = opened(admin, pedro, days_ago(30))
    response = admin.post(f"{EF}/payrolls/{p['id']}/cancel", json={"reason": "Duplicada"})
    assert response.status_code == 200
    fake = {"id": p["id"], "closing": {"id": 1}}
    assert error(pay(payer(user_as), fake, ("CASH", ids["main_cash"], "1"))) == (409, "payroll_not_closed")
    assert error(pay(payer(user_as), {"id": 999999, "closing": {"id": 1}}, ("CASH", ids["main_cash"], "1"))) == (
        404,
        "payroll_not_found",
    )


def test_payroll_shows_payment_and_history(
    user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int]
) -> None:
    admin = area_admin(user_as)
    p = net_payroll(admin, pedro, "100000")
    assert payroll(admin, p["id"])["payouts"] == []
    a = paid(admin, p, ("CASH", ids["main_cash"], "30000"), reference="Recibo 12")
    b = paid(admin, p, ("TRANSFER", ids["bank"], "20000"))
    reverse_payout(admin, b["id"], "Transferencia rechazada")
    shown = payroll(admin, p["id"])
    assert shown["payment"] == {
        "net": "100000.00",
        "paid": "30000.00",
        "pending": "70000.00",
        "status": "PARTIALLY_PAID",
    }
    assert [(x["id"], x["in_effect"], x["reference"]) for x in shown["payouts"]] == [
        (b["id"], False, None),
        (a["id"], True, "Recibo 12"),
    ]
    assert shown["payouts"][1]["items"][0]["account_name"] == "Caja principal"
    listed = admin.get(f"{EF}/payouts", params={"employee_id": pedro}).json()
    assert listed["total"] == 2
    summary = admin.get(f"{EF}/employees/{pedro}/pay-summary").json()
    assert (summary["pending_payrolls"], summary["pending_payroll_count"]) == ("70000.00", 1)
