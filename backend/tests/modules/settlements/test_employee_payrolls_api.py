"""Internal payrolls through the API (R8-4, ADR 010 §4-§8): the draft, what it proposes and applies, the close with
or without PAYROLL_CLOSE, reopening and correction, and the guards on what a close applied.

Signs: + the employee owes the company. The database repeats each rule (`test_employee_payrolls_db_invariants.py`);
races are in `test_employee_payrolls_concurrency.py`.
"""

from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_finance_support import EF, error
from tests.modules.settlements.employee_loan_support import defer, funded, given, installment, reverse_loan
from tests.modules.settlements.employee_movement_support import (
    area_operator,
    area_viewer,
    balance,
    internal_account,
    outsider,
    registered,
    reverse,
)
from tests.modules.settlements.employee_payroll_support import (
    add_line,
    applications,
    cancel,
    choose,
    chosen,
    close,
    closed,
    draft_legs,
    leg,
    line,
    open_payroll,
    opened,
    operations,
    payroll,
    reopen,
    reopened,
    shortage,
    term,
    totals,
)
from tests.modules.settlements.test_settlement_close_api import close as close_settlement
from tests.modules.settlements.test_settlement_close_api import count
from tests.modules.settlements.test_settlement_close_api import reopen as reopen_settlement

__all__ = ["funded"]


@pytest.fixture
def pedro(driver: Callable[..., int]) -> int:
    return driver("Pedro Gómez")


@pytest.fixture
def ids(accounts: dict[str, int]) -> dict[str, int]:
    return accounts


def _admin(user_as: Any) -> TestClient:
    """Admin of Rendiciones and of the area: cash role and area together."""
    return user_as("rrhh", settlements="admin", area="admin")  # type: ignore[no-any-return]


def _start() -> date:
    return days_ago(30)


def _previous_month() -> tuple[date, date]:
    first = today().replace(day=1) - timedelta(days=1)
    start = first.replace(day=1)
    return start, first


def _audit(db: Session, event: str) -> list[Any]:
    db.rollback()
    return list(
        db.execute(
            text("SELECT payload->'after' FROM audit_events WHERE event_type = :e ORDER BY id"), {"e": event}
        ).scalars()
    )


# --- Enzo's cases -------------------------------------------------------------------------------------------------


def test_case_1_salary_bonus_shortage_consumption_advance_and_an_installment(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, funded: dict[str, int], db: Session
) -> None:
    shortage(as_role, ids, pedro, "30000")
    admin = _admin(user_as)
    registered(admin, "CONSUMPTION", pedro, "20000", on=days_ago(5))
    registered(admin, "ADVANCE", pedro, "100000", on=days_ago(5), funding_account_id=funded["main_cash"])
    loan = given(admin, pedro, funded["main_cash"], "300000", 6, on=days_ago(20), first_due_on=days_ago(10))
    p = opened(admin, pedro, _start())
    assert p["salary_hint"]["status"] == "MANUAL"  # not a full calendar month: no automatic proration
    line(admin, p["id"], "SALARY", "800000")
    p = line(admin, p["id"], "BONUS", "50000")
    assert draft_legs(p) == [
        ("ADVANCE", "100000.00", "100000.00", True),
        ("CONSUMPTION", "20000.00", "20000.00", True),
        ("SETTLEMENT_SHORTAGE", "30000.00", "30000.00", True),
    ]
    assert [(i["number"], i["amount"], i["included"]) for i in p["installments"]] == [(1, "50000.00", True)]
    assert totals(p) == ("850000.00", "200000.00", "0.00", "650000.00")
    assert p["preview"]["deduction_pct"] == "23.53"
    assert (p["preview"]["accrual"], p["preview"]["blockers"]) == ("FINANCIAL", [])

    done = closed(admin, p["id"])
    assert (done["status"], done["version"], done["closing"]["kind"]) == ("CLOSED", 1, "FINANCIAL")
    assert totals(done) == ("850000.00", "200000.00", "0.00", "650000.00")
    assert sorted(a["amount"] for a in done["closing"]["applications"]) == [
        "100000.00",
        "20000.00",
        "30000.00",
        "50000.00",
    ]
    assert operations(db, p["id"]) == [("PAYROLL_CLOSE", "-850000.00")]
    assert balance(db, internal_account(db, pedro)) == Decimal("-400000")
    assert balance(db, ids["payroll_expense"]) == Decimal("850000")
    after = admin.get(f"{EF}/loans/{loan['id']}").json()
    assert (after["balance"], after["applied_total"], after["scheduled_count"]) == ("250000.00", "50000.00", 5)
    assert (installment(after, 1)["applied"], installment(after, 1)["applied_payroll_id"]) == (True, p["id"])
    assert not installment(after, 2)["applied"]
    # The snapshot is the readable copy; the rows carry the invariants.
    db.rollback()
    snapshot = db.execute(
        text("SELECT snapshot FROM settlements.employee_payroll_closings WHERE payroll_id = :p"), {"p": p["id"]}
    ).scalar_one()
    assert (snapshot["net"], snapshot["credits"], snapshot["version"], len(snapshot["applications"])) == (
        "650000.00",
        "850000.00",
        1,
        3,
    )
    assert [i["amount"] for i in snapshot["installments"]] == ["50000.00"]
    assert {a["pending_after"] for a in snapshot["applications"]} == {"0.00"}
    # The account shows what was applied of each movement.
    moves = admin.get(f"{EF}/employees/{pedro}/movements").json()["items"]
    assert {(m["origin"], m["applied"]) for m in moves} >= {
        ("SETTLEMENT_SHORTAGE", "30000.00"),
        ("ADVANCE", "100000.00"),
        ("LOAN", "50000.00"),
        ("PAYROLL", "0.00"),
    }
    # Nothing applied is proposed again.
    nxt = opened(admin, pedro, today() + timedelta(days=1), today() + timedelta(days=1))
    assert (nxt["legs"], nxt["installments"]) == ([], [])
    assert _audit(db, "settlements.employee_finance.payroll.closed")[0]["net"] == "650000.00"


def test_case_2_a_surplus_is_a_credit_applied_once(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, db: Session
) -> None:
    shortage(as_role, ids, pedro, "-20000")
    admin = _admin(user_as)
    p = opened(admin, pedro, _start())
    line(admin, p["id"], "SALARY", "800000")
    p = line(admin, p["id"], "BONUS", "30000")
    assert draft_legs(p) == [("SETTLEMENT_SURPLUS", "-20000.00", "-20000.00", True)]
    assert totals(p) == ("830000.00", "0.00", "20000.00", "850000.00")
    done = closed(admin, p["id"])
    assert totals(done) == ("830000.00", "0.00", "20000.00", "850000.00")
    assert balance(db, internal_account(db, pedro)) == Decimal("-850000")
    again = opened(admin, pedro, today() + timedelta(days=1), today() + timedelta(days=1), kind="EXTRAORDINARY")
    assert again["legs"] == []


def test_case_3_an_original_and_its_reversal_neutralize_and_never_come_back(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int
) -> None:
    sid = shortage(as_role, ids, pedro, "30000")
    assert reopen_settlement(_admin(user_as), sid, 1).status_code == 200
    operator = as_role("operator")
    count(operator, sid, ("ARS_10000", 10, ids["main_cash"]))
    assert close_settlement(operator, sid, version=2).status_code == 201  # corrected: no difference at all
    admin = _admin(user_as)
    p = opened(admin, pedro, _start())
    shown = leg(p, "SETTLEMENT_SHORTAGE")
    correction = leg(p, "SETTLEMENT_CORRECTION")
    assert shown["group_operation_id"] == correction["group_operation_id"] == shown["operation_id"]
    assert shown["reversed"]
    p = line(admin, p["id"], "SALARY", "800000")
    assert totals(p) == ("800000.00", "30000.00", "30000.00", "800000.00")
    done = closed(admin, p["id"])
    assert len(done["closing"]["applications"]) == 2  # two applications, never one netted
    nxt = opened(admin, pedro, today() + timedelta(days=1), today() + timedelta(days=1))
    assert nxt["legs"] == []


def test_case_3_partial_then_reversed_applies_the_rest_and_the_reversal(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, db: Session
) -> None:
    sid = shortage(as_role, ids, pedro, "30000")
    admin = _admin(user_as)
    first = opened(admin, pedro, _start(), today() - timedelta(days=1))
    line(admin, first["id"], "SALARY", "800000")
    entry = leg(payroll(admin, first["id"]), "SETTLEMENT_SHORTAGE")["entry_id"]
    first = chosen(admin, first["id"], entry=entry, amount="10000")
    assert leg(first, "SETTLEMENT_SHORTAGE")["partial"]
    closed(admin, first["id"])
    assert reopen_settlement(admin, sid, 1).status_code == 200
    operator = as_role("operator")
    count(operator, sid, ("ARS_10000", 10, ids["main_cash"]))
    assert close_settlement(operator, sid, version=2).status_code == 201
    admin = _admin(user_as)
    second = opened(admin, pedro, today(), today())
    assert draft_legs(second) == [
        ("SETTLEMENT_CORRECTION", "-30000.00", "-30000.00", True),
        ("SETTLEMENT_SHORTAGE", "20000.00", "20000.00", True),
    ]
    assert leg(second, "SETTLEMENT_SHORTAGE")["applied_elsewhere"] == "10000.00"
    line(admin, second["id"], "SALARY", "100000")
    done = closed(admin, second["id"])
    assert totals(done) == ("100000.00", "20000.00", "30000.00", "110000.00")
    third = opened(admin, pedro, today() + timedelta(days=1), today() + timedelta(days=1))
    assert third["legs"] == []


def test_case_4_no_accrual_applies_an_existing_credit_without_payroll_close(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, db: Session
) -> None:
    shortage(as_role, ids, pedro, "-20000")
    admin = _admin(user_as)
    p = opened(admin, pedro, _start(), kind="EXTRAORDINARY")
    surplus = leg(p, "SETTLEMENT_SURPLUS")
    assert (surplus["proposed"], surplus["included"]) == (False, False)  # extraordinary: listed, not included
    assert (p["installments"], p["salary_hint"]["status"]) == ([], "NONE")
    p = chosen(admin, p["id"], entry=surplus["entry_id"])
    assert (totals(p), p["preview"]["accrual"]) == (("0.00", "0.00", "20000.00", "20000.00"), "NO_ACCRUAL")
    done = closed(admin, p["id"])
    assert (done["closing"]["kind"], done["closing"]["operation_id"]) == ("NO_ACCRUAL", None)
    assert operations(db, p["id"]) == []
    assert applications(db, p["id"]) == [("-20000.00", None)]
    db.rollback()
    snapshot = db.execute(
        text("SELECT snapshot FROM settlements.employee_payroll_closings WHERE payroll_id = :p"), {"p": p["id"]}
    ).scalar_one()
    assert (snapshot["net"], snapshot["credits"], snapshot["credits_applied"]) == ("20000.00", "0.00", "20000.00")
    assert balance(db, internal_account(db, pedro)) == Decimal("-20000")  # nothing new on the account


# --- period, kind and salary --------------------------------------------------------------------------------------


def test_salary_is_proposed_only_for_a_full_month_with_one_term(
    as_role: Any, user_as: Any, pedro: int, employee: Callable[[str], int]
) -> None:
    admin = _admin(user_as)
    start, end = _previous_month()
    term(admin, pedro, start - timedelta(days=60), "800000")
    p = opened(admin, pedro, start, end)
    assert p["salary_hint"]["status"] == "PROPOSED"
    assert [(x["kind"], x["amount"], x["source"]) for x in p["lines"]] == [("SALARY", "800000.00", "TERM")]
    # A partial period: nothing proposed, a note is needed.
    ana = employee("Ana")
    term(admin, ana, start - timedelta(days=60), "700000")
    partial = opened(admin, ana, start, end - timedelta(days=1))
    assert (partial["salary_hint"]["status"], partial["lines"]) == ("MANUAL", [])
    assert error(add_line(admin, partial["id"], "SALARY", "500000", "")) == (422, "salary_note_required")
    # A salary change inside the month: nothing proposed either.
    beto = employee("Beto")
    term(admin, beto, start - timedelta(days=60), "700000")
    first_term = admin.get(f"{EF}/employees/{beto}/compensation-terms").json()[0]
    ended = admin.post(
        f"{EF}/compensation-terms/{first_term['id']}/end",
        json={"valid_to": str(start + timedelta(days=9)), "reason": "Aumento"},
    )
    assert ended.status_code == 200, ended.text
    term(admin, beto, start + timedelta(days=10), "750000")
    changed = opened(admin, beto, start, end)
    assert (changed["salary_hint"]["status"], changed["lines"]) == ("MANUAL", [])
    # A proposed salary edited by hand becomes MANUAL and needs its note.
    salary = p["lines"][0]["id"]
    assert error(admin.patch(f"{EF}/payroll-lines/{salary}", json={"amount": "810000"})) == (
        422,
        "salary_note_required",
    )
    edited = admin.patch(f"{EF}/payroll-lines/{salary}", json={"amount": "810000", "description": "Redondeo"}).json()
    assert [(x["amount"], x["source"], x["compensation_term_id"]) for x in edited["lines"]] == [
        ("810000.00", "MANUAL", None)
    ]


def test_regular_payrolls_never_overlap_and_extraordinary_may(
    as_role: Any, user_as: Any, pedro: int, employee: Callable[[str], int]
) -> None:
    admin = _admin(user_as)
    first = opened(admin, pedro, days_ago(30), days_ago(1))
    assert error(open_payroll(admin, pedro, days_ago(1), today())) == (409, "regular_payroll_overlap")
    assert error(open_payroll(admin, pedro, today(), days_ago(1))) == (422, "invalid_period")
    opened(admin, pedro, days_ago(10), days_ago(5), kind="EXTRAORDINARY")
    opened(admin, employee("Otra"), days_ago(30), days_ago(1))  # another employee
    assert cancel(admin, first["id"]).status_code == 200
    opened(admin, pedro, days_ago(1), today())  # a cancelled one frees its period


def test_creating_is_idempotent(as_role: Any, user_as: Any, pedro: int) -> None:
    admin = _admin(user_as)
    request = uuid4()
    one = open_payroll(admin, pedro, _start(), request=request)
    again = open_payroll(admin, pedro, _start(), request=request)
    assert (one.status_code, again.status_code, again.json()["id"]) == (201, 200, one.json()["id"])
    assert error(open_payroll(admin, pedro, days_ago(2), request=request)) == (409, "idempotency_conflict")


def test_lines_need_admin_and_a_description_where_due(as_role: Any, user_as: Any, pedro: int) -> None:
    admin = _admin(user_as)
    p = opened(admin, pedro, _start())
    assert error(add_line(admin, p["id"], "OTHER_CREDIT", "1000", "  ")) == (422, "description_required")
    line(admin, p["id"], "INCENTIVE", "15000")  # manual; nothing read from Comercial
    operator = area_operator(user_as)
    assert error(add_line(operator, p["id"], "BONUS", "1000")) == (403, "forbidden")
    assert error(close(operator, p["id"])) == (403, "forbidden")
    viewer = area_viewer(user_as)
    assert viewer.get(f"{EF}/payrolls/{p['id']}").status_code == 200
    assert error(open_payroll(viewer, pedro, today(), today(), kind="EXTRAORDINARY")) == (403, "forbidden")
    stranger = outsider(user_as)
    assert stranger.get(f"{EF}/payrolls/{p['id']}").status_code == 404
    assert stranger.get(f"{EF}/payrolls").status_code == 404


# --- the draft ----------------------------------------------------------------------------------------------------


def test_partial_amounts_and_whole_installments(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, funded: Any
) -> None:
    shortage(as_role, ids, pedro, "30000")
    admin = _admin(user_as)
    given(admin, pedro, funded["main_cash"], "100000", 2, on=days_ago(20), first_due_on=days_ago(10))
    p = opened(admin, pedro, _start())
    entry = leg(p, "SETTLEMENT_SHORTAGE")["entry_id"]
    inst = p["installments"][0]["installment_id"]
    assert error(choose(admin, p["id"], entry=entry, amount="30000.01")) == (422, "invalid_application_amount")
    assert error(choose(admin, p["id"], installment=inst, amount="10000")) == (422, "installment_whole")
    assert error(choose(admin, p["id"])) == (422, "one_item_required")
    p = chosen(admin, p["id"], entry=entry, amount="12000")
    p = chosen(admin, p["id"], installment=inst, included=False)
    assert [(i["installment_id"], i["included"]) for i in p["installments"]] == [(inst, False)]
    line(admin, p["id"], "SALARY", "100000")
    done = closed(admin, p["id"])
    assert sorted(a["amount"] for a in done["closing"]["applications"]) == ["12000.00"]
    nxt = opened(admin, pedro, today() + timedelta(days=1), today() + timedelta(days=1))
    assert draft_legs(nxt) == [("SETTLEMENT_SHORTAGE", "18000.00", "18000.00", True)]
    assert [i["installment_id"] for i in nxt["installments"]] == [inst]  # excluded, so still pending


def test_extraordinary_never_applies_installments(as_role: Any, user_as: Any, pedro: int, funded: Any) -> None:
    admin = _admin(user_as)
    loan = given(admin, pedro, funded["main_cash"], "100000", 2, on=days_ago(20), first_due_on=days_ago(10))
    p = opened(admin, pedro, _start(), kind="EXTRAORDINARY")
    assert p["installments"] == []
    inst = installment(loan, 1)["id"]
    assert error(choose(admin, p["id"], installment=inst)) == (422, "extraordinary_without_installments")
    assert all(x["origin"] != "LOAN" for x in p["legs"])  # a loan is applied only through its installments


def test_a_negative_net_is_never_cut_automatically(as_role: Any, user_as: Any, pedro: int, funded: Any) -> None:
    admin = _admin(user_as)
    registered(admin, "ADVANCE", pedro, "100000", on=days_ago(5), funding_account_id=funded["main_cash"])
    p = opened(admin, pedro, _start())
    p = line(admin, p["id"], "SALARY", "60000")
    assert totals(p) == ("60000.00", "100000.00", "0.00", "-40000.00")
    assert [b["code"] for b in p["preview"]["blockers"]] == ["net_negative"]
    assert error(close(admin, p["id"])) == (409, "net_negative")
    advance = leg(p, "ADVANCE")["entry_id"]
    p = chosen(admin, p["id"], entry=advance, amount="60000")
    assert totals(p)[3] == "0.00"
    closed(admin, p["id"])


def test_the_close_confirms_what_was_previewed(as_role: Any, user_as: Any, pedro: int) -> None:
    admin = _admin(user_as)
    p = opened(admin, pedro, _start())
    line(admin, p["id"], "SALARY", "500000")
    token = payroll(admin, p["id"])["preview"]["preview_token"]
    registered(admin, "CONSUMPTION", pedro, "1000", on=days_ago(2))
    assert error(close(admin, p["id"], token=token)) == (409, "payroll_preview_stale")
    assert error(close(admin, p["id"], version=2)) == (409, "payroll_version_changed")
    future = opened(admin, pedro, today() + timedelta(days=1), today() + timedelta(days=3), kind="EXTRAORDINARY")
    line(admin, future["id"], "BONUS", "1000")
    assert error(close(admin, future["id"])) == (422, "period_not_ended")
    no_confirm = admin.post(
        f"{EF}/payrolls/{p['id']}/close",
        json={"version": 1, "preview_token": payroll(admin, p["id"])["preview"]["preview_token"]},
    )
    assert error(no_confirm) == (422, "confirmation_required")
    first = close(admin, p["id"])
    retry = admin.post(
        f"{EF}/payrolls/{p['id']}/close",
        json={"version": 1, "preview_token": "0" * 64, "confirm": True},
    )
    assert (first.status_code, retry.status_code) == (201, 200)
    assert error(add_line(admin, p["id"], "BONUS", "1")) == (409, "payroll_not_open")


# --- reopening and correction -------------------------------------------------------------------------------------


def test_reopen_and_close_again_replaces_the_accrual(as_role: Any, user_as: Any, pedro: int, db: Session) -> None:
    admin = _admin(user_as)
    registered(admin, "CONSUMPTION", pedro, "20000", on=days_ago(5))
    p = opened(admin, pedro, _start())
    line(admin, p["id"], "SALARY", "800000")
    closed(admin, p["id"])
    assert reopen(admin, p["id"], 1, "  ").status_code == 422
    again = reopened(admin, p["id"], 1)
    assert (again["status"], again["version"], again["in_correction"]) == ("OPEN", 2, True)
    assert again["closing"]["version"] == 1  # still in effect
    assert again["reopenings"][0]["reason"] == "Faltó el bono"
    assert draft_legs(again) == [("CONSUMPTION", "20000.00", "20000.00", True)]  # its own application re-proposed
    assert applications(db, p["id"]) == [("20000.00", None)]
    line(admin, p["id"], "BONUS", "50000")
    done = closed(admin, p["id"])
    assert (done["version"], done["closing"]["version"], len(done["closings"])) == (2, 2, 2)
    assert done["closings"][1]["superseded_at"] is not None
    assert operations(db, p["id"]) == [
        ("PAYROLL_CLOSE", "-800000.00"),
        ("REVERSAL", "800000.00"),
        ("PAYROLL_CLOSE", "-850000.00"),
    ]
    assert applications(db, p["id"]) == [("20000.00", None)]
    assert applications(db, p["id"], in_effect=False) == [("20000.00", None)]
    assert balance(db, internal_account(db, pedro)) == Decimal("-830000")
    assert error(reopen(admin, p["id"], 1)) == (409, "payroll_version_changed")


def test_financial_to_no_accrual_and_back(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, db: Session
) -> None:
    shortage(as_role, ids, pedro, "-20000")
    admin = _admin(user_as)
    p = opened(admin, pedro, _start())
    salary = line(admin, p["id"], "SALARY", "800000")["lines"][0]["id"]
    closed(admin, p["id"])
    reopened(admin, p["id"], 1)
    admin.delete(f"{EF}/payroll-lines/{salary}")
    done = closed(admin, p["id"])
    assert (done["closing"]["kind"], done["closing"]["operation_id"]) == ("NO_ACCRUAL", None)
    assert done["closing"]["reversal_operation_id"] is not None  # FINANCIAL -> NO_ACCRUAL withdraws the accrual
    assert operations(db, p["id"]) == [("PAYROLL_CLOSE", "-800000.00"), ("REVERSAL", "800000.00")]
    reopened(admin, p["id"], 2)
    line(admin, p["id"], "BONUS", "10000")
    back = closed(admin, p["id"])
    assert (back["closing"]["kind"], back["closing"]["reversal_operation_id"]) == ("FINANCIAL", None)
    assert operations(db, p["id"])[-1] == ("PAYROLL_CLOSE", "-10000.00")  # nothing invented to reverse
    assert balance(db, internal_account(db, pedro)) == Decimal("-30000")


def test_cancel_only_a_payroll_never_closed(as_role: Any, user_as: Any, pedro: int) -> None:
    admin = _admin(user_as)
    p = opened(admin, pedro, _start())
    line(admin, p["id"], "BONUS", "1000")
    closed(admin, p["id"])
    assert error(cancel(admin, p["id"])) == (409, "payroll_not_open")
    reopened(admin, p["id"], 1)
    assert error(cancel(admin, p["id"])) == (409, "payroll_was_closed")
    other = opened(admin, pedro, today() + timedelta(days=1), today() + timedelta(days=2))
    assert error(cancel(area_operator(user_as), other["id"])) == (403, "forbidden")
    admin = _admin(user_as)
    assert cancel(admin, other["id"]).json()["status"] == "CANCELLED"
    assert error(add_line(admin, other["id"], "BONUS", "1")) == (409, "payroll_not_open")


# --- what a close applied is guarded ------------------------------------------------------------------------------


def test_what_a_close_applied_is_not_reversed_or_deferred(
    as_role: Any, user_as: Any, ids: dict[str, int], pedro: int, funded: Any, db: Session
) -> None:
    sid = shortage(as_role, ids, pedro, "30000")
    admin = _admin(user_as)
    consumption = registered(admin, "CONSUMPTION", pedro, "20000", on=days_ago(5))
    loan = given(admin, pedro, funded["main_cash"], "100000", 2, on=days_ago(20), first_due_on=days_ago(10))
    p = opened(admin, pedro, _start())
    line(admin, p["id"], "SALARY", "500000")
    closed(admin, p["id"])
    assert error(reverse(admin, consumption["id"])) == (409, "movement_applied")
    inst = installment(loan, 1)["id"]
    assert error(defer(admin, inst, [(today() + timedelta(days=40), "50000")])) == (409, "installment_applied")
    assert error(reverse_loan(admin, loan["id"])) == (409, "loan_applied")
    # A settlement correction (R2/R7) is never blocked: its reversal is a new leg for the next payroll.
    assert reopen_settlement(admin, sid, 1).status_code == 200
    operator = as_role("operator")
    count(operator, sid, ("ARS_10000", 10, ids["main_cash"]))
    assert close_settlement(operator, sid, version=2).status_code == 201
    # Reopened and the items excluded: they can be undone again.
    admin = _admin(user_as)
    reopened(admin, p["id"], 1)
    current = payroll(admin, p["id"])
    chosen(admin, p["id"], entry=leg(current, "CONSUMPTION")["entry_id"], included=False)
    chosen(admin, p["id"], installment=inst, included=False)
    closed(admin, p["id"])
    assert reverse(admin, consumption["id"]).status_code == 200
    assert defer(admin, inst, [(today() + timedelta(days=40), "50000")]).status_code == 200


def test_payroll_list_shows_totals(as_role: Any, user_as: Any, pedro: int, employee: Callable[[str], int]) -> None:
    admin = _admin(user_as)
    p = opened(admin, pedro, _start())
    line(admin, p["id"], "SALARY", "800000")
    closed(admin, p["id"])
    opened(admin, employee("Ana"), _start(), kind="EXTRAORDINARY")
    listed = admin.get(f"{EF}/payrolls").json()
    assert listed["total"] == 2
    row = next(r for r in listed["items"] if r["id"] == p["id"])
    assert (row["status"], row["totals"]["net"], row["totals_from_draft"], row["employee_name"]) == (
        "CLOSED",
        "800000.00",
        False,
        "Pedro Gómez",
    )
    assert admin.get(f"{EF}/payrolls", params={"employee_id": pedro}).json()["total"] == 1
    assert admin.get(f"{EF}/payrolls", params={"status": "OPEN"}).json()["total"] == 1
