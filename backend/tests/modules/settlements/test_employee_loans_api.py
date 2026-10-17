"""Loans to employees, their schedule, deferrals and reversal through the API (R8-3, ADR 010).

Signs: + the employee owes the company. The database repeats each rule (`test_employee_loans_db_invariants.py`);
races are in `test_employee_loans_concurrency.py`; Cajas and the internal account in `test_employee_loans_privacy.py`.
"""

import calendar
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.parties.models import Employee
from tests.modules.settlements.days import days_ago, today, yesterday
from tests.modules.settlements.employee_finance_support import BASE, EF, error
from tests.modules.settlements.employee_loan_support import (
    defer,
    deferred,
    funded,
    give,
    given,
    installment,
    preview,
    reverse_loan,
    schedule,
)
from tests.modules.settlements.employee_movement_support import (
    area_admin,
    area_admin_without_cash,
    area_operator,
    area_viewer,
    balance,
    internal_account,
    legs,
    outsider,
)

__all__ = ["funded"]


@pytest.fixture
def ana(employee: Callable[[str], int]) -> int:
    return employee("Ana Pérez")


def _audit(db: Session, event: str) -> list[Any]:
    db.rollback()
    return list(
        db.execute(
            text("SELECT payload->'after' FROM audit_events WHERE event_type = :e ORDER BY id"), {"e": event}
        ).scalars()
    )


def _count(db: Session, table: str) -> int:
    db.rollback()
    query = f"SELECT count(*) FROM settlements.{table}"  # noqa: S608
    return int(db.execute(text(query)).scalar_one())


# --- giving a loan --------------------------------------------------------------------------------


def test_a_loan_hands_cash_to_the_employee_with_its_schedule(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    on, first = days_ago(5), date(2026, 11, 30)
    created = given(
        client,
        ana,
        funded["main_cash"],
        "300000",
        3,
        on=on,
        first_due_on=first,
        description="  Préstamo   para la moto ",
        reference="P-1",
    )
    assert (created["status"], created["balance"], created["principal"]) == ("ACTIVE", "300000.00", "300000.00")
    assert (created["description"], created["reference"]) == ("Préstamo para la moto", "P-1")
    assert (created["funding_account_name"], created["installment_count"], created["first_due_on"]) == (
        "Caja principal",
        3,
        "2026-11-30",
    )
    assert legs(created) == [("CASH_BOX", "-300000.00"), ("EMPLOYEE", "300000.00")]
    assert {leg["account_name"] for leg in created["legs"]} == {"Caja principal", "Cuenta interna · Ana Pérez"}
    # Monthly from the first due date, on that day or the last of a shorter month.
    assert schedule(created) == [
        (1, "2026-11-30", "100000.00", "SCHEDULED"),
        (2, "2026-12-30", "100000.00", "SCHEDULED"),
        (3, "2027-01-30", "100000.00", "SCHEDULED"),
    ]
    assert (created["scheduled_count"], created["next_due_on"]) == (3, "2026-11-30")
    # One leg on the internal account, never one per installment.
    assert balance(db, funded["main_cash"]) == Decimal("200000")
    assert balance(db, internal_account(db, ana)) == Decimal("300000")
    db.rollback()
    operation = db.execute(
        text("SELECT type, business_date, source_type, source_id FROM settlements.financial_operations WHERE id = :o"),
        {"o": created["operation_id"]},
    ).one()
    assert tuple(operation) == ("EMPLOYEE_LOAN", on, "employee_loan", created["id"])
    audited = _audit(db, "settlements.employee_finance.employee_loan.created")
    assert len(audited) == 1
    assert (audited[0]["principal"], audited[0]["operation_id"], len(audited[0]["installments"])) == (
        "300000.00",
        created["operation_id"],
        3,
    )
    listed = client.get(f"{EF}/employees/{ana}/loans").json()
    assert [(r["id"], r["status"], r["balance"], r["scheduled_count"]) for r in listed] == [
        (created["id"], "ACTIVE", "300000.00", 3)
    ]
    assert client.get(f"{EF}/loans/{created['id']}").json() == created


def test_the_last_installment_absorbs_the_rounding(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]
) -> None:
    loan = given(area_admin(user_as), ana, funded["main_cash"], "100000", 3, first_due_on=date(2027, 1, 31))
    year = 2027
    feb = calendar.monthrange(year, 2)[1]
    assert schedule(loan) == [
        (1, "2027-01-31", "33333.33", "SCHEDULED"),
        (2, f"2027-02-{feb}", "33333.33", "SCHEDULED"),
        (3, "2027-03-31", "33333.34", "SCHEDULED"),
    ]
    assert sum(Decimal(i["amount"]) for i in loan["installments"]) == Decimal("100000")
    one = given(area_admin(user_as), ana, funded["main_cash"], "0.05", 4)
    assert [i["amount"] for i in one["installments"]] == ["0.01", "0.01", "0.01", "0.02"]
    assert error(give(area_admin(user_as), ana, funded["main_cash"], "0.03", 4)) == (422, "principal_too_small")


def test_the_first_installment_may_fall_in_the_loan_month_never_before_the_loan(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]
) -> None:
    """Enzo: loan on 02/11, first installment on 30/11 is fine; `first_due_on >= business_date`."""
    client = area_admin(user_as)
    on = days_ago(3)
    same_day = given(client, ana, funded["main_cash"], "1000", 1, on=on, first_due_on=on)
    assert schedule(same_day) == [(1, on.isoformat(), "1000.00", "SCHEDULED")]
    month_end = on.replace(day=calendar.monthrange(on.year, on.month)[1])
    assert given(client, ana, funded["main_cash"], "1000", 2, on=on, first_due_on=month_end)["first_due_on"] == (
        month_end.isoformat()
    )
    assert error(give(client, ana, funded["main_cash"], "1000", 2, on=on, first_due_on=on - timedelta(days=1))) == (
        422,
        "due_before_loan",
    )


def test_the_admin_edits_the_schedule_before_confirming(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    on = days_ago(2)
    d1, d2, d3 = on + timedelta(days=10), on + timedelta(days=45), on + timedelta(days=45)
    edited = given(
        client,
        ana,
        funded["main_cash"],
        "300000",
        3,
        on=on,
        first_due_on=d1,
        installments=[(d1, "50000"), (d2, "150000"), (d3, "100000")],
    )
    assert schedule(edited) == [
        (1, d1.isoformat(), "50000.00", "SCHEDULED"),
        (2, d2.isoformat(), "150000.00", "SCHEDULED"),
        (3, d3.isoformat(), "100000.00", "SCHEDULED"),
    ]
    bad: list[tuple[list[tuple[date, str]], int, date, str]] = [
        ([(d1, "50000"), (d2, "150000"), (d3, "99999.99")], 3, d1, "schedule_total_mismatch"),
        ([(d1, "50000"), (d2, "250000")], 3, d1, "schedule_mismatch"),
        ([(d2, "150000"), (d1, "50000"), (d3, "100000")], 3, d2, "schedule_out_of_order"),
        ([(d1, "150000"), (d2, "150000"), (d3, "0.00")], 3, d1, "validation_error"),
        ([(d2, "100000"), (d2, "100000"), (d3, "100000")], 3, d1, "schedule_mismatch"),
        (
            [(on - timedelta(days=1), "100000"), (d2, "100000"), (d3, "100000")],
            3,
            on - timedelta(days=1),
            "due_before_loan",
        ),
    ]
    for items, count, first, code in bad:
        response = give(
            client, ana, funded["main_cash"], "300000", count, on=on, first_due_on=first, installments=items
        )
        assert error(response) == (422, code), code
    assert _count(db, "employee_loans") == 1
    for count in (0, 121):
        assert error(give(client, ana, funded["main_cash"], "300000", count)) == (422, "validation_error"), count


def test_validations(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]) -> None:
    client = area_admin(user_as)
    assert error(give(client, 999999, funded["main_cash"])) == (404, "employee_not_found")
    future = today() + timedelta(days=1)
    assert error(give(client, ana, funded["main_cash"], on=future, first_due_on=future)) == (
        422,
        "future_business_date",
    )
    for amount in ("0", "-1", "1.234", "1234567890123"):
        assert error(give(client, ana, funded["main_cash"], amount)) == (422, "validation_error"), amount
    assert error(give(client, ana, funded["main_cash"], description="x" * 501)) == (422, "validation_error")
    assert error(give(client, ana, funded["main_cash"], reference="x" * 101)) == (422, "validation_error")
    # Interest does not exist: an unknown field is refused, never ignored.
    assert error(give(client, ana, funded["main_cash"], interest_rate="10")) == (422, "validation_error")
    # Without description nor reference is fine.
    assert given(client, ana, funded["main_cash"], "10", 1)["description"] is None


def test_a_loan_from_the_bank(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    loan = given(area_admin(user_as), ana, funded["bank"], "120000", 4)
    assert legs(loan) == [("BANK", "-120000.00"), ("EMPLOYEE", "120000.00")]
    assert [i["amount"] for i in loan["installments"]] == ["30000.00"] * 4
    assert balance(db, funded["bank"]) == Decimal("180000")
    assert balance(db, internal_account(db, ana)) == Decimal("120000")


def test_a_loan_needs_funds_on_its_date_and_every_later_day(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session, post
) -> None:  # type: ignore[no-untyped-def]
    client = area_admin(user_as)
    assert error(give(client, ana, funded["main_cash"], "500000.01")) == (409, "insufficient_funds")
    # The bank never goes into an overdraft for a loan, nor deeper into one.
    assert error(give(client, ana, funded["bank"], "300000.01")) == (409, "insufficient_funds")
    post(
        "CASH_TRANSFER", (funded["main_cash"], "-450000"), (funded["change_cash"], "450000"), business_date=yesterday()
    )
    late = give(client, ana, funded["main_cash"], "60000", on=days_ago(8))
    assert error(late) == (409, "insufficient_funds")
    assert late.json()["details"]["business_date"] == yesterday().isoformat()
    assert give(client, ana, funded["main_cash"], "50000", on=days_ago(8)).status_code == 201
    assert balance(db, funded["main_cash"]) == Decimal("0")
    assert _count(db, "employee_loans") == 1
    assert _count(db, "employee_loan_installments") == 3


def test_a_loan_comes_from_an_active_cash_box_or_bank_only(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], employee: Callable[[str], int], db: Session
) -> None:
    client = area_admin(user_as)
    pedro = employee("Pedro")
    given(client, pedro, funded["main_cash"], "10", 1)
    for account in (
        funded["opening_balance"],
        funded["employee_consumptions"],
        funded["employee_adjustments"],
        funded["cheque_portfolio"],
        internal_account(db, pedro),
        999999,
    ):
        assert error(give(client, ana, account, "10", 1)) == (422, "invalid_funding_account"), account
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :a"), {"a": funded["change_cash"]})
    db.commit()
    assert error(give(client, ana, funded["change_cash"], "10", 1)) == (422, "invalid_funding_account")


def test_who_gives_a_loan(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]) -> None:
    cash = funded["main_cash"]
    for client in (outsider(user_as), user_as("plataforma", platform="admin")):
        assert error(give(client, ana, cash)) == (404, "not_found")
        assert error(preview(client, ana, cash)) == (404, "not_found")
        assert error(client.get(f"{EF}/employees/{ana}/loans")) == (404, "not_found")
    for client in (area_viewer(user_as), area_operator(user_as)):
        assert error(give(client, ana, cash)) == (403, "forbidden")
        assert error(preview(client, ana, cash)) == (403, "forbidden")
    # It moves a cash box: the area is not enough without operator in Rendiciones (as an advance, D9).
    accountant = area_admin_without_cash(user_as)
    assert error(give(accountant, ana, cash)) == (403, "forbidden")
    assert error(preview(accountant, ana, cash)) == (403, "forbidden")
    loan = given(area_admin(user_as), ana, cash)
    assert area_viewer(user_as).get(f"{EF}/loans/{loan['id']}").json()["id"] == loan["id"]
    assert error(outsider(user_as).get(f"{EF}/loans/{loan['id']}")) == (404, "not_found")


def test_giving_twice_is_one_loan_and_a_reused_id_is_a_conflict(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    request = uuid4()
    first = give(client, ana, funded["main_cash"], "1500", 3, request=request)
    again = give(client, ana, funded["main_cash"], "1500", 3, request=request)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json() == first.json()
    assert error(give(client, ana, funded["main_cash"], "1600", 3, request=request)) == (409, "idempotency_conflict")
    assert error(give(client, ana, funded["main_cash"], "1500", 2, request=request)) == (409, "idempotency_conflict")
    assert (_count(db, "employee_loans"), _count(db, "employee_loan_installments")) == (1, 3)
    assert balance(db, internal_account(db, ana)) == Decimal("1500")


def test_an_inactive_employee_takes_only_a_loan_that_documents_the_past(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    row = db.get(Employee, ana)
    assert row is not None
    row.active = False
    db.commit()
    client = area_admin(user_as)
    assert error(give(client, ana, funded["main_cash"], "100", 1)) == (422, "inactive_employee")
    assert give(client, ana, funded["main_cash"], "100", 1, on=yesterday()).status_code == 201


def test_the_preview_shows_legs_schedule_and_total_without_writing(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    on = days_ago(1)
    first = on + timedelta(days=20)
    response = preview(client, ana, funded["main_cash"], "300000", 3, on=on, first_due_on=first)
    assert response.status_code == 200, response.text
    out = response.json()
    assert [(leg["account_name"], leg["amount"]) for leg in out["legs"]] == [
        ("Caja principal", "-300000.00"),
        ("Cuenta interna · Ana Pérez", "300000.00"),
    ]
    assert (out["funding_account_name"], out["total"], out["balance_before"], out["balance_after"]) == (
        "Caja principal",
        "300000.00",
        "0.00",
        "300000.00",
    )
    assert [(i["number"], i["amount"]) for i in out["installments"]] == [
        (1, "100000.00"),
        (2, "100000.00"),
        (3, "100000.00"),
    ]
    assert out["installments"][0]["due_on"] == first.isoformat()
    edited = preview(
        client,
        ana,
        funded["main_cash"],
        "300000",
        2,
        on=on,
        first_due_on=first,
        installments=[(first, "100000"), (first, "200000")],
    ).json()
    assert [i["amount"] for i in edited["installments"]] == ["100000.00", "200000.00"]
    mismatch = preview(
        client, ana, funded["main_cash"], "300000", 2, on=on, first_due_on=first, installments=[(first, "1")] * 2
    )
    assert error(mismatch) == (422, "schedule_total_mismatch")
    assert error(preview(client, ana, funded["bank"], "900000")) == (409, "insufficient_funds")
    assert _count(db, "employee_loans") == 0
    assert _count(db, "employee_loan_installments") == 0


# --- deferring an installment ---------------------------------------------------------------------


def _loan(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]) -> dict[str, Any]:
    """$300.000 in 3 x $100.000 on 30/11, 30/12 and 30/01."""
    return given(area_admin(user_as), ana, funded["main_cash"], "300000", 3, first_due_on=date(2026, 11, 30))


def test_deferring_an_installment_replaces_it_and_keeps_the_history(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    """Enzo's example: 300k in 3; deferring #2 gives 1 SCHEDULED, 2 DEFERRED, 3 SCHEDULED, 4 SCHEDULED = 300k."""
    loan = _loan(user_as, ana, funded)
    entries_before = _count(db, "ledger_entries")
    after = deferred(
        area_admin(user_as), installment(loan, 2)["id"], [(date(2027, 2, 28), "100000")], reason="Vacaciones"
    )
    assert schedule(after) == [
        (1, "2026-11-30", "100000.00", "SCHEDULED"),
        (2, "2026-12-30", "100000.00", "DEFERRED"),
        (3, "2027-01-30", "100000.00", "SCHEDULED"),
        (4, "2027-02-28", "100000.00", "SCHEDULED"),
    ]
    two, four = installment(after, 2), installment(after, 4)
    assert (two["deferred_reason"], two["deferred_by_name"]) == ("Vacaciones", "Usuario Test")
    assert two["deferred_at"] is not None
    assert two["replaced_by"] == [{"number": 4, "due_on": "2027-02-28", "amount": "100000.00"}]
    assert (four["deferred_from_id"], four["deferred_from_number"]) == (two["id"], 2)
    active = sum(Decimal(i["amount"]) for i in after["installments"] if i["status"] == "SCHEDULED")
    assert active == Decimal("300000")
    assert (after["balance"], after["scheduled_count"], after["installment_count"]) == ("300000.00", 3, 3)
    # No money moved.
    assert _count(db, "ledger_entries") == entries_before
    assert balance(db, internal_account(db, ana)) == Decimal("300000")
    audited = _audit(db, "settlements.employee_finance.employee_loan.deferred")
    assert audited == [
        {"reason": "Vacaciones", "replaced_by": [{"number": 4, "due_on": "2027-02-28", "amount": "100000.00"}]}
    ]


def test_an_installment_may_be_replaced_by_several(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]
) -> None:
    """Enzo's example: #2 ($100.000) becomes #4 $40.000 and #5 $60.000, both deferred from 2."""
    loan = _loan(user_as, ana, funded)
    two = installment(loan, 2)
    after = deferred(area_admin(user_as), two["id"], [(date(2026, 12, 31), "40000"), (date(2027, 1, 31), "60000")])
    assert schedule(after)[3:] == [
        (4, "2026-12-31", "40000.00", "SCHEDULED"),
        (5, "2027-01-31", "60000.00", "SCHEDULED"),
    ]
    assert {installment(after, n)["deferred_from_number"] for n in (4, 5)} == {2}
    assert [r["number"] for r in installment(after, 2)["replaced_by"]] == [4, 5]


def test_a_deferred_installment_is_deferred_again_through_its_replacement(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]
) -> None:
    """A chain 2 → 4 → 6: each link stays as history."""
    client = area_admin(user_as)
    loan = _loan(user_as, ana, funded)
    loan = deferred(client, installment(loan, 2)["id"], [(date(2027, 2, 28), "100000")])
    # Deferring #2 again is refused: it is history; its replacement is what moves.
    again = defer(client, installment(loan, 2)["id"], [(date(2027, 3, 31), "100000")])
    assert error(again) == (409, "installment_not_scheduled")
    loan = deferred(client, installment(loan, 4)["id"], [(date(2027, 3, 31), "30000"), (date(2027, 4, 30), "70000")])
    loan = deferred(client, installment(loan, 5)["id"], [(date(2027, 5, 31), "30000")])
    assert schedule(loan) == [
        (1, "2026-11-30", "100000.00", "SCHEDULED"),
        (2, "2026-12-30", "100000.00", "DEFERRED"),
        (3, "2027-01-30", "100000.00", "SCHEDULED"),
        (4, "2027-02-28", "100000.00", "DEFERRED"),
        (5, "2027-03-31", "30000.00", "DEFERRED"),
        (6, "2027-04-30", "70000.00", "SCHEDULED"),
        (7, "2027-05-31", "30000.00", "SCHEDULED"),
    ]
    assert [(i["number"], i["deferred_from_number"]) for i in loan["installments"] if i["deferred_from_number"]] == [
        (4, 2),
        (5, 4),
        (6, 4),
        (7, 5),
    ]
    assert sum(Decimal(i["amount"]) for i in loan["installments"] if i["status"] == "SCHEDULED") == Decimal("300000")


def test_a_deferral_adds_up_exactly_with_a_reason_and_dates_after_the_loan(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    loan = _loan(user_as, ana, funded)
    two = installment(loan, 2)["id"]
    later = date(2027, 2, 28)
    assert error(defer(client, two, [(later, "99999.99")])) == (422, "replacement_mismatch")
    mismatch = defer(client, two, [(later, "50000"), (later, "60000")])
    assert error(mismatch) == (422, "replacement_mismatch")
    assert mismatch.json()["details"] == {"replaced_amount": "100000.00", "new_total": "110000.00"}
    assert error(defer(client, two, [(later, "100000")], reason="")) == (422, "validation_error")
    assert error(defer(client, two, [(later, "100000")], reason=" \t ")) == (422, "reason_required")
    assert error(defer(client, two, [])) == (422, "validation_error")
    assert error(defer(client, two, [(later, "0")])) == (422, "validation_error")
    assert error(defer(client, two, [(days_ago(400), "100000")])) == (422, "due_before_loan")
    assert error(defer(client, 999999, [(later, "100000")])) == (404, "installment_not_found")
    assert _count(db, "employee_loan_installments") == 3


def test_who_defers(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]) -> None:
    loan = _loan(user_as, ana, funded)
    two = installment(loan, 2)["id"]
    replacement = [(date(2027, 2, 28), "100000")]
    assert error(defer(outsider(user_as), two, replacement)) == (404, "not_found")
    assert error(defer(area_viewer(user_as), two, replacement)) == (403, "forbidden")
    assert error(defer(area_operator(user_as), two, replacement)) == (403, "forbidden")
    # Deferring moves no money: the area's admin is enough, without operator in Rendiciones.
    assert defer(area_admin_without_cash(user_as), two, replacement).status_code == 200


def test_deferring_twice_is_one_deferral_and_a_reused_id_is_a_conflict(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    loan = _loan(user_as, ana, funded)
    two, three = installment(loan, 2)["id"], installment(loan, 3)["id"]
    request = uuid4()
    replacement = [(date(2027, 2, 28), "100000")]
    first = defer(client, two, replacement, request=request)
    again = defer(client, two, replacement, request=request)
    assert (first.status_code, again.status_code) == (200, 200)
    assert again.json() == first.json()
    assert error(defer(client, two, replacement, reason="Otro motivo", request=request)) == (
        409,
        "idempotency_conflict",
    )
    assert error(defer(client, three, replacement, request=request)) == (409, "idempotency_conflict")
    assert _count(db, "employee_loan_installments") == 4


# --- reversing a loan -----------------------------------------------------------------------------


def test_reversing_a_loan_returns_the_money_and_cancels_what_was_scheduled(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session, post
) -> None:  # type: ignore[no-untyped-def]
    client = area_admin(user_as)
    loan = given(client, ana, funded["main_cash"], "300000", 3, on=days_ago(3), first_due_on=date(2026, 11, 30))
    loan = deferred(client, installment(loan, 2)["id"], [(date(2027, 2, 28), "100000")])
    # Everything left in the box is moved out afterwards: it is empty now; the reversal still credits it.
    post("CASH_TRANSFER", (funded["main_cash"], "-200000"), (funded["change_cash"], "200000"))
    assert balance(db, funded["main_cash"]) == Decimal("0")
    request = uuid4()
    response = reverse_loan(client, loan["id"], "Se cargó a otro empleado", request)
    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["status"], out["balance"], out["scheduled_count"], out["next_due_on"]) == ("REVERSED", "0.00", 0, None)
    assert (out["reversal_reason"], out["reversed_by_name"]) == ("Se cargó a otro empleado", "Usuario Test")
    # Scheduled installments are cancelled; the deferred one stays exactly as it was.
    assert schedule(out) == [
        (1, "2026-11-30", "100000.00", "CANCELLED"),
        (2, "2026-12-30", "100000.00", "DEFERRED"),
        (3, "2027-01-30", "100000.00", "CANCELLED"),
        (4, "2027-02-28", "100000.00", "CANCELLED"),
    ]
    assert installment(out, 2)["deferred_reason"] == installment(loan, 2)["deferred_reason"]
    assert balance(db, funded["main_cash"]) == Decimal("300000")
    assert balance(db, internal_account(db, ana)) == Decimal("0")
    db.rollback()
    reversal = db.execute(
        text("SELECT type, business_date, reverses_operation_id FROM settlements.financial_operations WHERE id = :o"),
        {"o": out["reversal_operation_id"]},
    ).one()
    assert tuple(reversal) == ("REVERSAL", days_ago(3), loan["operation_id"])
    # A retry is the same reversal; another one is refused; nothing doubles.
    assert reverse_loan(client, loan["id"], "Se cargó a otro empleado", request).status_code == 200
    assert error(reverse_loan(client, loan["id"])) == (409, "employee_loan_already_reversed")
    assert error(reverse_loan(client, loan["id"], "Otro motivo", request)) == (409, "idempotency_conflict")
    assert balance(db, funded["main_cash"]) == Decimal("300000")
    # A reversed loan's installments are not deferred any more.
    assert error(defer(client, installment(out, 4)["id"], [(date(2027, 3, 31), "100000")])) == (
        409,
        "employee_loan_reversed",
    )
    audited = _audit(db, "settlements.employee_finance.employee_loan.reversed")
    assert (audited[0]["reason"], audited[0]["cancelled_installments"]) == ("Se cargó a otro empleado", [1, 3, 4])


def test_who_reverses_a_loan(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session) -> None:
    loan = given(area_admin(user_as), ana, funded["main_cash"], "1000", 1)
    assert error(reverse_loan(outsider(user_as), loan["id"])) == (404, "not_found")
    assert error(reverse_loan(area_viewer(user_as), loan["id"])) == (403, "forbidden")
    assert error(reverse_loan(area_operator(user_as), loan["id"])) == (403, "forbidden")
    assert error(reverse_loan(area_admin_without_cash(user_as), loan["id"])) == (403, "forbidden")
    admin = area_admin(user_as)
    assert error(reverse_loan(admin, loan["id"], "   ")) == (422, "reason_required")
    assert error(reverse_loan(admin, 999999)) == (404, "employee_loan_not_found")
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :a"), {"a": funded["main_cash"]})
    db.commit()
    assert error(reverse_loan(admin, loan["id"])) == (409, "account_inactive")
    db.execute(text("UPDATE settlements.accounts SET active = true WHERE id = :a"), {"a": funded["main_cash"]})
    db.commit()
    assert reverse_loan(admin, loan["id"]).json()["status"] == "REVERSED"


def test_cajas_never_reverses_a_loan(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]) -> None:
    loan = given(area_admin(user_as), ana, funded["main_cash"], "1000", 1)
    boss = user_as("jefe", settlements="admin", area="admin")
    response = boss.post(
        f"{BASE}/operations/{loan['operation_id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Desde Cajas"},
    )
    assert error(response) == (409, "operation_owned_by_document")
