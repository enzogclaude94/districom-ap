"""Loans under concurrency, against PostgreSQL (R8-3, Enzo's point 15). The main thread holds the row the workers need;
once released, the database must end in one valid state: never a negative cash box nor a deepened overdraft, one loan
and one operation per request, one reversal, one deferral of an installment, one internal account per employee, the
scheduled installments adding up to the principal, no deadlock.

Lock order of a loan: the employee row (FOR SHARE), the internal account, the cash box or bank, then the ledger's
(R8-2's order). Deferring and reversing take the loan row first (FOR UPDATE)."""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.modules.settlements.employee_finance import loans
from app.modules.settlements.expenses import direct
from app.modules.settlements.expenses import schemas as expense_schemas
from app.platform.auth.models import User
from tests.modules.settlements.days import today
from tests.modules.settlements.test_employee_movements_concurrency import (
    _balance,
    _cmd,
    _codes,
    _create,
    _hold,
    _hold_account,
    _hold_employee,
    _in_session,
    _internal,
    _race,
    _scalar,
    _unexpected,
    new_session,
    world,
)

__all__ = ["new_session", "world"]

LATER = date(2027, 6, 30)


def _loan_cmd(world: dict[str, Any], principal: str, box: str = "main_cash", **fields: Any) -> loans.Command:
    return loans.command(
        fields.pop("employee_id", world["ana"]),
        today(),
        Decimal(principal),
        world[box],
        fields.pop("count", 2),
        date(2026, 11, 30),
        None,
        "Concurrente",
        None,
    )


def _give(world: dict[str, Any], cmd: loans.Command, request: UUID | None = None) -> Callable[[Session, User], object]:
    return lambda session, actor: loans.create(session, actor, cmd, request or uuid4())


def _published(world: dict[str, Any], db: Session, principal: str, **fields: Any) -> int:
    session = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    try:
        actor = session.merge(world["admin"], load=False)
        return loans.create(session, actor, _loan_cmd(world, principal, **fields), uuid4()).id
    finally:
        session.close()


def _installment_id(db: Session, loan_id: int, number: int) -> int:
    return int(
        _scalar(
            db,
            "SELECT id FROM settlements.employee_loan_installments WHERE loan_id = :l AND number = :n",
            l=loan_id,
            n=number,
        )
    )


def _scheduled_total(db: Session, loan_id: int) -> Decimal:
    return Decimal(
        _scalar(
            db,
            "SELECT coalesce(sum(amount), 0) FROM settlements.employee_loan_installments "
            "WHERE loan_id = :l AND status = 'SCHEDULED'",
            l=loan_id,
        )
    )


def test_two_loans_competing_for_the_same_cash_box(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    holder = _hold_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _give(world, _loan_cmd(world, "70000"))),
            _in_session(new_session, world["admin"], _give(world, _loan_cmd(world, "70000"))),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["main_cash"]) == Decimal("30000")
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_loans") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_loan_installments") == 2


def test_a_loan_racing_an_advance_from_the_bank(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    holder = _hold_account(new_session, world["bank"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _give(world, _loan_cmd(world, "70000", "bank"))),
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "70000", funding_account_id=world["bank"])),
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["bank"]) == Decimal("30000")
    assert _internal(db, world["ana"]) == Decimal("70000")


def test_a_loan_racing_an_expense_from_the_same_box(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    expense = expense_schemas.DirectExpenseIn(
        client_request_id=uuid4(),
        business_date=today(),
        category_id=world["category"],
        paid_from_account_id=world["main_cash"],
        amount=Decimal("70000"),
    )
    holder = _hold_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _give(world, _loan_cmd(world, "70000"))),
            _in_session(new_session, world["admin"], lambda session, actor: direct.create(session, actor, expense)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["main_cash"]) == Decimal("30000")


def test_the_same_loan_twice_at_once_is_one_loan(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    request = uuid4()
    cmd = _loan_cmd(world, "1500", count=3)
    holder = _hold_employee(new_session, world["ana"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _give(world, cmd, request)),
            _in_session(new_session, world["admin"], _give(world, cmd, request)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    assert sorted(r.created for r in results if isinstance(r, loans.Created)) == [False, True]
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_loans") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_loan_installments") == 3
    assert _internal(db, world["ana"]) == Decimal("1500")


def test_two_reversals_of_the_same_loan_at_once_are_one(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    loan = _published(world, db, "40000")
    holder = _hold(new_session, "SELECT 1 FROM settlements.employee_loans WHERE id = :l FOR UPDATE", l=loan)
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], lambda s, a: loans.reverse(s, a, loan, "Uno", uuid4())),
            _in_session(new_session, world["admin"], lambda s, a: loans.reverse(s, a, loan, "Otro", uuid4())),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["employee_loan_already_reversed"], results
    assert _balance(db, world["main_cash"]) == Decimal("100000")
    assert _internal(db, world["ana"]) == Decimal("0")
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_loan_installments WHERE status = 'CANCELLED'") == 2


def test_a_reversal_racing_a_debit_of_its_cash_box(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    """The box has $40.000 left; the reversal puts $60.000 back while an expense of $80.000 waits: whichever goes
    first, the box never goes negative."""
    loan = _published(world, db, "60000")
    expense = expense_schemas.DirectExpenseIn(
        client_request_id=uuid4(),
        business_date=today(),
        category_id=world["category"],
        paid_from_account_id=world["main_cash"],
        amount=Decimal("80000"),
    )
    holder = _hold_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], lambda s, a: loans.reverse(s, a, loan, "Error", uuid4())),
            _in_session(new_session, world["admin"], lambda session, actor: direct.create(session, actor, expense)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) in ([], ["insufficient_funds"]), results
    final = _balance(db, world["main_cash"])
    assert final >= 0
    assert final == (Decimal("20000") if _codes(results) == [] else Decimal("100000"))
    assert _scalar(db, "SELECT reversal_operation_id IS NOT NULL FROM settlements.employee_loans") is True


def test_two_deferrals_of_the_same_installment_at_once_are_one(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    loan = _published(world, db, "40000")
    second = _installment_id(db, loan, 2)
    replacement = [loans.Installment(LATER, Decimal("20000"))]
    holder = _hold(new_session, "SELECT 1 FROM settlements.employee_loans WHERE id = :l FOR UPDATE", l=loan)
    results = _race(
        holder,
        [
            _in_session(
                new_session, world["admin"], lambda s, a: loans.defer(s, a, second, "Uno", replacement, uuid4())
            ),
            _in_session(
                new_session, world["admin"], lambda s, a: loans.defer(s, a, second, "Otro", replacement, uuid4())
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["installment_not_scheduled"], results
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_loan_installments") == 3
    assert _scheduled_total(db, loan) == Decimal("40000")


def test_a_deferral_racing_the_reversal_of_its_loan(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    loan = _published(world, db, "40000")
    second = _installment_id(db, loan, 2)
    replacement = [loans.Installment(LATER, Decimal("5000")), loans.Installment(LATER, Decimal("15000"))]
    holder = _hold(new_session, "SELECT 1 FROM settlements.employee_loans WHERE id = :l FOR UPDATE", l=loan)
    results = _race(
        holder,
        [
            _in_session(
                new_session, world["admin"], lambda s, a: loans.defer(s, a, second, "Pide", replacement, uuid4())
            ),
            _in_session(new_session, world["admin"], lambda s, a: loans.reverse(s, a, loan, "Error", uuid4())),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) in ([], ["employee_loan_reversed"]), results
    # Reversed in the end, nothing scheduled; if the deferral went first its replacements were cancelled with it.
    assert _scheduled_total(db, loan) == Decimal("0")
    statuses = _scalar(
        db,
        "SELECT string_agg(status, ',' ORDER BY number) FROM settlements.employee_loan_installments WHERE loan_id = :l",
        l=loan,
    )
    assert statuses in ("CANCELLED,CANCELLED", "CANCELLED,DEFERRED,CANCELLED,CANCELLED")
    assert _balance(db, world["main_cash"]) == Decimal("100000")


def test_the_first_loans_of_an_employee_create_one_internal_account(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    holder = _hold_employee(new_session, world["ana"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _give(world, _loan_cmd(world, "1000"))),
            _in_session(new_session, world["admin"], _give(world, _loan_cmd(world, "2000", "bank"))),
            _in_session(new_session, world["admin"], _create(world, _cmd(world, "CONSUMPTION", "300"))),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    assert _scalar(db, "SELECT count(*) FROM settlements.accounts WHERE employee_id = :e", e=world["ana"]) == 1
    assert _internal(db, world["ana"]) == Decimal("3300")


def test_loans_to_several_employees_from_the_same_box(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session], employee: Callable[[str], int]
) -> None:
    people = [employee(f"Empleado {i}") for i in range(4)]
    holder = _hold_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _give(world, _loan_cmd(world, "30000", employee_id=p)))
            for p in people
        ],
    )
    assert _unexpected(results) == [], results
    # $100.000 in the box: three loans of $30.000 fit, the fourth does not.
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["main_cash"]) == Decimal("10000")
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_loans") == 3
