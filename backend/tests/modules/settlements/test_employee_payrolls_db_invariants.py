"""Rules of internal payrolls that the database enforces whoever writes (migration 0028), checked with raw SQL (Enzo's
R8-4 point 18, invariants 1-21). Valid payrolls are prepared and closed through the API; each case then tries to break
one rule directly. The number in brackets is the invariant of Enzo's list."""

import json
from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_loan_support import funded, given, installment
from tests.modules.settlements.employee_movement_support import area_admin, internal_account, registered
from tests.modules.settlements.employee_payroll_support import chosen, closed, leg, line, opened

__all__ = ["funded"]

FINGERPRINT = "d" * 64


def _scalar(db: Session, sql: str, **params: object) -> Any:
    db.rollback()
    return db.execute(text(sql), params).scalar_one()


def _entry(db: Session, operation_id: int, account_id: int) -> int:
    return int(
        _scalar(
            db,
            "SELECT id FROM settlements.ledger_entries WHERE operation_id = :o AND account_id = :a",
            o=operation_id,
            a=account_id,
        )
    )


@pytest.fixture
def world(
    user_as: Callable[..., TestClient], employee: Callable[[str], int], funded: dict[str, int], db: Session
) -> dict[str, Any]:
    """Pedro: a consumption ($20.000), an adjustment in his favour ($5.000) and a loan ($100.000 in 2, the first one
    due). Payroll A (REGULAR, closed) applied the consumption and the first installment, not the adjustment; B
    (REGULAR, after A) and C (EXTRAORDINARY) are open. Beto has a consumption of his own."""
    admin = area_admin(user_as)
    pedro, beto = employee("Pedro"), employee("Beto")
    consumption = registered(admin, "CONSUMPTION", pedro, "20000", on=days_ago(5))
    credit = registered(admin, "ADJUSTMENT", pedro, "5000", on=days_ago(5), direction="CREDIT")
    loan = given(admin, pedro, funded["main_cash"], "100000", 2, on=days_ago(20), first_due_on=days_ago(10))
    other = registered(admin, "CONSUMPTION", beto, "1000", on=days_ago(5))
    a = opened(admin, pedro, days_ago(30), days_ago(3))
    line(admin, a["id"], "SALARY", "500000")
    chosen(admin, a["id"], entry=leg(a, "ADJUSTMENT_CREDIT")["entry_id"], included=False)
    a = closed(admin, a["id"])
    b = opened(admin, pedro, days_ago(2), today())
    c = opened(admin, pedro, days_ago(30), today(), kind="EXTRAORDINARY")
    account = internal_account(db, pedro)
    return {
        "pedro": pedro,
        "beto": beto,
        "account": account,
        "beto_account": internal_account(db, beto),
        "consumption": consumption,
        "consumption_entry": _entry(db, consumption["operation_id"], account),
        "credit_entry": _entry(db, credit["operation_id"], account),
        "loan": loan,
        "loan_entry": _entry(db, loan["operation_id"], account),
        "installment_1": installment(loan, 1)["id"],
        "installment_2": installment(loan, 2)["id"],
        "beto_entry": _entry(db, other["operation_id"], internal_account(db, beto)),
        "a": a,
        "b": b,
        "c": c,
        "user": int(_scalar(db, "SELECT id FROM users ORDER BY id LIMIT 1")),
        **funded,
    }


def _refused(db: Session, write: Callable[[], None]) -> str:
    def _write_and_commit() -> None:
        write()
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write_and_commit()
    db.rollback()
    return str(caught.value.orig)


def _sql(db: Session, sql: str, **params: object) -> Callable[[], None]:
    return lambda: db.execute(text(sql), params) and None


def _raw_close(
    db: Session,
    w: dict[str, Any],
    payroll_id: int,
    applications: list[tuple[int, str, int | None]],
    *,
    kind: str = "NO_ACCRUAL",
    operation_id: int | None = None,
    credits: str | None = None,
    version: int = 1,
    snapshot_version: int | None = None,
    net: str | None = None,
) -> Callable[[], None]:
    """Close an OPEN payroll by hand: the close, its applications (leg, signed amount, installment), the payroll.
    Deductions without `credits` get a valid FINANCIAL close of $100.000, so only the case under test fails."""

    def _write() -> None:
        nonlocal kind, operation_id, credits
        amounts = [Decimal(a) for _, a, _ in applications]
        deductions = sum((a for a in amounts if a > 0), Decimal("0"))
        applied = sum((-a for a in amounts if a < 0), Decimal("0"))
        if credits is None:
            credits = "0"
            if deductions > applied:
                credits, kind = "100000", "FINANCIAL"
                operation_id = _payroll_close_op(db, w, payroll_id, credits, _period_end(db, payroll_id))
        total = Decimal(net) if net is not None else Decimal(credits) - deductions + applied
        closing = db.execute(
            text(
                "INSERT INTO settlements.employee_payroll_closings (payroll_id, payroll_version, kind, operation_id, "
                "credits, deductions, credits_applied, net, snapshot, request_fingerprint, closed_by, closed_at) "
                "VALUES (:p, :v, :k, :o, :c, :d, :a, :n, CAST(:s AS jsonb), :f, :u, now()) RETURNING id"
            ),
            {
                "p": payroll_id,
                "v": version,
                "k": kind,
                "o": operation_id,
                "c": Decimal(credits),
                "d": deductions,
                "a": applied,
                "n": total,
                "s": json.dumps({"version": snapshot_version or version, "net": str(total)}),
                "f": FINGERPRINT,
                "u": w["user"],
            },
        ).scalar_one()
        if Decimal(credits) > 0:
            db.execute(
                text(
                    "INSERT INTO settlements.employee_payroll_closing_lines (closing_id, kind, amount, source) "
                    "VALUES (:c, 'BONUS', :m, 'MANUAL')"
                ),
                {"c": closing, "m": Decimal(credits)},
            )
        for entry, amount, inst in applications:
            db.execute(
                text(
                    "INSERT INTO settlements.employee_payroll_applications (closing_id, payroll_id, "
                    "employee_account_id, ledger_entry_id, installment_id, amount) VALUES (:c, :p, :acc, :e, :i, :m)"
                ),
                {"c": closing, "p": payroll_id, "acc": w["account"], "e": entry, "i": inst, "m": Decimal(amount)},
            )
        db.execute(
            text("UPDATE settlements.employee_payrolls SET status = 'CLOSED', current_closing_id = :c WHERE id = :p"),
            {"c": closing, "p": payroll_id},
        )

    return _write


def _payroll(db: Session, w: dict[str, Any], kind: str, start: Any, end: Any) -> Callable[[], None]:
    return _sql(
        db,
        "INSERT INTO settlements.employee_payrolls (employee_id, employee_account_id, kind, period_start, period_end, "
        "status, version, client_request_id, request_fingerprint, created_by) "
        "VALUES (:e, :a, :k, :s, :t, 'OPEN', 1, :r, :f, :u)",
        e=w["pedro"],
        a=w["account"],
        k=kind,
        s=start,
        t=end,
        r=uuid4(),
        f=FINGERPRINT,
        u=w["user"],
    )


def _period_end(db: Session, payroll_id: int) -> Any:
    return db.execute(
        text("SELECT period_end FROM settlements.employee_payrolls WHERE id = :p"), {"p": payroll_id}
    ).scalar_one()


def _payroll_close_op(db: Session, w: dict[str, Any], payroll_id: int, amount: str, on: Any) -> int:
    expense = int(_scalar(db, "SELECT id FROM settlements.accounts WHERE code = 'payroll_expense'"))
    op = db.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
            "request_fingerprint, source_type, source_id, created_by) VALUES "
            "('PAYROLL_CLOSE', :d, 'A mano', :k, :f, 'employee_payroll', :p, :u) RETURNING id"
        ),
        {"d": on, "k": uuid4(), "f": FINGERPRINT, "p": payroll_id, "u": w["user"]},
    ).scalar_one()
    for account, value in ((expense, Decimal(amount)), (w["account"], -Decimal(amount))):
        db.execute(
            text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)"),
            {"o": op, "a": account, "m": value},
        )
    return int(op)


# --- payrolls and their draft -------------------------------------------------------------------------------------


def test_period_and_regular_overlap(db: Session, world: dict[str, Any]) -> None:
    w = world
    assert "ck_employee_payrolls_period" in _refused(db, _payroll(db, w, "EXTRAORDINARY", today(), days_ago(1)))  # [1]
    assert "ex_employee_payrolls_regular_no_overlap" in _refused(  # [2]
        db, _payroll(db, w, "REGULAR", days_ago(4), days_ago(3))
    )
    _payroll(db, w, "EXTRAORDINARY", days_ago(4), days_ago(3))()
    db.commit()


def test_draft_lines_are_valid_and_only_while_open(db: Session, world: dict[str, Any]) -> None:  # [3]
    w = world
    insert = (
        "INSERT INTO settlements.employee_payroll_lines (payroll_id, kind, amount, description, source, created_by) "
        "VALUES (:p, :k, :m, :d, 'MANUAL', :u)"
    )
    b = w["b"]["id"]
    assert "ck_employee_payroll_lines" in _refused(db, _sql(db, insert, p=b, k="BONUS", m=0, d=None, u=w["user"]))
    assert "other_credit_described" in _refused(db, _sql(db, insert, p=b, k="OTHER_CREDIT", m=1, d=None, u=w["user"]))
    assert "manual_salary_explained" in _refused(db, _sql(db, insert, p=b, k="SALARY", m=1, d=None, u=w["user"]))
    assert "is not open" in _refused(db, _sql(db, insert, p=w["a"]["id"], k="BONUS", m=1, d=None, u=w["user"]))
    choice = (
        "INSERT INTO settlements.employee_payroll_choices (payroll_id, ledger_entry_id, installment_id, included, "
        "updated_by) VALUES (:p, :e, :i, true, :u)"
    )
    assert "not of the payroll's internal account" in _refused(
        db, _sql(db, choice, p=b, e=w["beto_entry"], i=None, u=w["user"])
    )
    assert "never applies loan installments" in _refused(
        db, _sql(db, choice, p=w["c"]["id"], e=None, i=w["installment_2"], u=w["user"])
    )


# --- applications -------------------------------------------------------------------------------------------------


def test_an_application_is_of_the_employee_and_has_the_sign_of_its_leg(db: Session, world: dict[str, Any]) -> None:
    w = world
    b = w["b"]["id"]
    assert "not of the payroll's internal account" in _refused(  # [4]
        db, _raw_close(db, w, b, [(w["beto_entry"], "1000", None)])
    )
    assert "has the sign of its leg" in _refused(db, _raw_close(db, w, b, [(w["credit_entry"], "5000", None)]))  # [5]
    # [6] the consumption was applied whole by A: nothing is left of it.
    assert "exceed" in _refused(db, _raw_close(db, w, b, [(w["consumption_entry"], "1", None)]))
    # [7] the same leg twice in one close.
    assert "uq_employee_payroll_applications" in _refused(
        db, _raw_close(db, w, b, [(w["credit_entry"], "-1000", None), (w["credit_entry"], "-1000", None)])
    )
    # A valid one, in part: the credit in Pedro's favour.
    _raw_close(db, w, b, [(w["credit_entry"], "-1000", None)])()
    db.commit()


def test_only_applicable_legs(db: Session, world: dict[str, Any]) -> None:  # [8]
    w = world
    a_close = int(
        _scalar(
            db, "SELECT operation_id FROM settlements.employee_payroll_closings WHERE payroll_id = :p", p=w["a"]["id"]
        )
    )
    payroll_leg = _entry(db, a_close, w["account"])
    assert "a leg of a PAYROLL_CLOSE is not applied" in _refused(
        db, _raw_close(db, w, w["b"]["id"], [(payroll_leg, "-1", None)])
    )
    assert "only through its installments" in _refused(
        db, _raw_close(db, w, w["b"]["id"], [(w["loan_entry"], "1", None)])
    )


def test_installments_are_whole_of_their_loan_and_only_regular(db: Session, world: dict[str, Any]) -> None:
    w = world
    b, c = w["b"]["id"], w["c"]["id"]
    assert "on its own loan's leg" in _refused(  # [9]
        db, _raw_close(db, w, b, [(w["consumption_entry"], "50000", w["installment_2"])])
    )
    assert "is applied whole" in _refused(  # [10]
        db, _raw_close(db, w, b, [(w["loan_entry"], "10000", w["installment_2"])])
    )
    assert "never applies loan installments" in _refused(  # [11]
        db, _raw_close(db, w, c, [(w["loan_entry"], "50000", w["installment_2"])])
    )
    # [7] an installment applied twice: #1 is in A's close in effect.
    assert "uq_employee_payroll_applications_installment" in _refused(
        db, _raw_close(db, w, b, [(w["loan_entry"], "50000", w["installment_1"])])
    )


def test_what_a_close_applied_is_not_undone_directly(db: Session, world: dict[str, Any]) -> None:
    w = world
    some_op = w["consumption"]["operation_id"]
    assert "installment_applied" in _refused(  # [12]
        db,
        _sql(
            db,
            "UPDATE settlements.employee_loan_installments SET status = 'DEFERRED', deferred_reason = 'x', "
            "deferred_by = :u, deferred_at = now() WHERE id = :i",
            u=w["user"],
            i=w["installment_1"],
        ),
    )
    assert "loan_applied" in _refused(  # [13]
        db,
        _sql(
            db,
            "UPDATE settlements.employee_loans SET reversal_operation_id = :o, reversed_by = :u, reversed_at = now(), "
            "reversal_reason = 'x' WHERE id = :l",
            o=some_op,
            u=w["user"],
            l=w["loan"]["id"],
        ),
    )
    assert "movement_applied" in _refused(  # [21]
        db,
        _sql(
            db,
            "UPDATE settlements.employee_movements SET reversal_operation_id = :o, reversed_by = :u, "
            "reversed_at = now(), reversal_reason = 'x' WHERE id = :m",
            o=some_op,
            u=w["user"],
            m=w["consumption"]["id"],
        ),
    )


# --- closes -------------------------------------------------------------------------------------------------------


def test_financial_needs_its_exact_payroll_close_and_no_accrual_none(db: Session, world: dict[str, Any]) -> None:
    w = world
    b = w["b"]["id"]

    def _with_op(amount: str, credits: str, on: Any) -> Callable[[], None]:
        def _write() -> None:
            op = _payroll_close_op(db, w, b, amount, on)
            _raw_close(db, w, b, [], kind="FINANCIAL", operation_id=op, credits=credits)()

        return _write

    assert "is not exactly its credits" in _refused(db, _with_op("1000", "900", today()))  # [14]
    assert "is not exactly its credits" in _refused(db, _with_op("1000", "1000", days_ago(1)))  # wrong date
    assert "ck_employee_payroll_closings_kind_operation" in _refused(  # [15]
        db, _raw_close(db, w, b, [], kind="NO_ACCRUAL", credits="1000")
    )
    assert "has no payroll close" in _refused(db, lambda: _payroll_close_op(db, w, b, "1000", today()) and None)
    _with_op("1000", "1000", today())()
    db.commit()


def test_net_is_never_negative_and_the_close_adds_up(db: Session, world: dict[str, Any]) -> None:
    w = world
    b = w["b"]["id"]
    assert "ck_employee_payroll_closings_net" in _refused(  # [16]
        db, _raw_close(db, w, b, [], net="-1")
    )
    assert "does not add up to its rows" in _refused(  # [17]
        db, _raw_close(db, w, b, [(w["credit_entry"], "-1000", None)], snapshot_version=7)
    )
    assert "a close is for the open version" in _refused(db, _raw_close(db, w, b, [], version=2))  # [17]


def test_closes_and_applications_never_change_nor_vanish(db: Session, world: dict[str, Any]) -> None:
    w = world
    a = w["a"]["id"]
    closing = int(_scalar(db, "SELECT current_closing_id FROM settlements.employee_payrolls WHERE id = :p", p=a))
    assert "never changes" in _refused(  # [18]
        db,
        _sql(
            db, "UPDATE settlements.employee_payroll_applications SET amount = amount WHERE closing_id = :c", c=closing
        ),
    )
    assert "before it is in effect" in _refused(  # [18]
        db,
        _sql(
            db,
            "INSERT INTO settlements.employee_payroll_applications (closing_id, payroll_id, employee_account_id, "
            "ledger_entry_id, amount) VALUES (:c, :p, :a, :e, -1)",
            c=closing,
            p=a,
            a=w["account"],
            e=w["credit_entry"],
        ),
    )
    assert "only the close it points at is in effect" in _refused(  # [19] superseded only by a later close
        db,
        _sql(
            db,
            "UPDATE settlements.employee_payroll_closings SET superseded_at = now(), superseded_by = :u WHERE id = :c",
            u=w["user"],
            c=closing,
        ),
    )
    for table, column in (  # [20]
        ("employee_payrolls", "id"),
        ("employee_payroll_closings", "payroll_id"),
        ("employee_payroll_applications", "payroll_id"),
    ):
        assert "never" in _refused(
            db,
            _sql(db, f"DELETE FROM settlements.{table} WHERE {column} = :p", p=a),  # noqa: S608
        )
    assert "only by closing or reopening" in _refused(
        db, _sql(db, "UPDATE settlements.employee_payrolls SET version = 2 WHERE id = :p", p=a)
    )
    assert "closes with a close of its version" in _refused(
        db, _sql(db, "UPDATE settlements.employee_payrolls SET status = 'CLOSED' WHERE id = :p", p=w["b"]["id"])
    )
