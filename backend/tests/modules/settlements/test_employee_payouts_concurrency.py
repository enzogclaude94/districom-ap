"""Payouts under concurrency, against PostgreSQL (R8-5, Enzo's point 11). The main thread holds the row the workers
need; once released, the database must end in one valid state: never paid beyond the net, never a negative cash box
nor a deepened overdraft, one payout and one operation per request, one reversal, never a reopened payroll with
payouts in effect, no deadlock.

Lock order of a payout and its reversal: the internal account, the payroll (FOR UPDATE), the cash boxes and banks in
id order, then the ledger's: the order of every R8 flow."""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.modules.settlements.employee_finance import payouts, payrolls
from app.modules.settlements.employee_finance.models import EmployeePayroll
from app.modules.settlements.expenses import direct
from app.modules.settlements.expenses import schemas as expense_schemas
from app.modules.settlements.payments import schemas as payment_schemas
from app.modules.settlements.payments import service as payment_service
from app.platform.auth.models import User
from app.platform.third_parties.models import ThirdParty
from tests.modules.settlements.days import today
from tests.modules.settlements.test_employee_movements_concurrency import (
    _balance,
    _cmd,
    _codes,
    _create,
    _hold_account,
    _internal,
    _race,
    _scalar,
    _unexpected,
    new_session,
    world,
)
from tests.modules.settlements.test_employee_payrolls_concurrency import _close, _do, _in_session, _open

__all__ = ["new_session", "world"]


def _closed(db: Session, w: dict[str, Any], salary: str = "100000") -> tuple[int, int]:
    """A payroll of Ana closed with that net: (payroll, close)."""
    pid = _open(db, w, salary=salary)
    _do(db, w["admin"], _close(db, pid))
    return pid, int(_scalar(db, "SELECT current_closing_id FROM settlements.employee_payrolls WHERE id = :p", p=pid))


def _cmd_pay(w: dict[str, Any], payroll: tuple[int, int], *items: tuple[str, str, str]) -> payouts.Command:
    return payouts.command(
        payroll[0], payroll[1], today(), [payouts.Item(m, w[box], Decimal(a)) for m, box, a in items], None
    )


def _pay(
    w: dict[str, Any], payroll: tuple[int, int], *items: tuple[str, str, str], request: UUID | None = None
) -> Callable[[Session, User], object]:
    cmd = _cmd_pay(w, payroll, *items)
    return lambda session, actor: payouts.create(session, actor, cmd, request or uuid4())


def _paid(db: Session, w: dict[str, Any], payroll: tuple[int, int], *items: tuple[str, str, str]) -> int:
    return int(_do(db, w["admin"], _pay(w, payroll, *items)).id)


def _reverse(payout_id: int, reason: str = "Error") -> Callable[[Session, User], object]:
    return lambda session, actor: payouts.reverse(session, actor, payout_id, reason, uuid4())


def _reopen(db: Session, payroll_id: int) -> Callable[[Session, User], object]:
    version = int(_scalar(db, "SELECT version FROM settlements.employee_payrolls WHERE id = :p", p=payroll_id))
    return lambda session, actor: payrolls.reopen(session, actor, payroll_id, version, "Corrección")


def _paid_total(db: Session, closing_id: int) -> Decimal:
    return Decimal(
        _scalar(
            db,
            "SELECT coalesce(sum(amount), 0) FROM settlements.employee_payouts "
            "WHERE payroll_closing_id = :c AND reversal_operation_id IS NULL",
            c=closing_id,
        )
    )


def _status(db: Session, payroll_id: int) -> str:
    return str(_scalar(db, "SELECT status FROM settlements.employee_payrolls WHERE id = :p", p=payroll_id))


def _account(db: Session, w: dict[str, Any]) -> int:
    return int(_scalar(db, "SELECT id FROM settlements.accounts WHERE employee_id = :e", e=w["ana"]))


def test_two_payouts_against_the_same_pending(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    payroll = _closed(db, world)
    holder = _hold_account(new_session, _account(db, world))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _pay(world, payroll, ("CASH", "main_cash", "60000"))),
            _in_session(new_session, world["admin"], _pay(world, payroll, ("TRANSFER", "bank", "60000"))),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["payout_exceeds_pending"], results
    assert _paid_total(db, payroll[1]) == Decimal("60000")
    assert _balance(db, world["main_cash"]) + _balance(db, world["bank"]) == Decimal("140000")


def test_two_final_payments_at_once(db: Session, world: dict[str, Any], new_session: Callable[[], Session]) -> None:
    payroll = _closed(db, world)
    _paid(db, world, payroll, ("CASH", "main_cash", "40000"))
    holder = _hold_account(new_session, _account(db, world))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _pay(world, payroll, ("CASH", "main_cash", "60000"))),
            _in_session(new_session, world["admin"], _pay(world, payroll, ("TRANSFER", "bank", "60000"))),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["payroll_fully_paid"], results
    assert _paid_total(db, payroll[1]) == Decimal("100000")
    assert _internal(db, world["ana"]) == Decimal("0")


def test_a_payout_racing_the_reopening(db: Session, world: dict[str, Any], new_session: Callable[[], Session]) -> None:
    payroll = _closed(db, world)
    holder = _hold_account(new_session, _account(db, world))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _pay(world, payroll, ("CASH", "main_cash", "50000"))),
            _in_session(new_session, world["admin"], _reopen(db, payroll[0])),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) in (["payroll_has_payouts"], ["payroll_not_closed"]), results
    if _codes(results) == ["payroll_has_payouts"]:
        assert (_status(db, payroll[0]), _paid_total(db, payroll[1])) == ("CLOSED", Decimal("50000"))
    else:
        assert (_status(db, payroll[0]), _paid_total(db, payroll[1])) == ("OPEN", Decimal("0"))


def test_a_payout_racing_the_close_of_another_payroll(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    payroll = _closed(db, world)
    other = _open(db, world, kind="EXTRAORDINARY", salary="30000")
    holder = _hold_account(new_session, _account(db, world))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _pay(world, payroll, ("CASH", "main_cash", "100000"))),
            _in_session(new_session, world["admin"], _close(db, other)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    assert _internal(db, world["ana"]) == Decimal("-30000")  # -100.000 -30.000 +100.000
    assert _status(db, other) == "CLOSED"


def test_a_payout_racing_an_expense_from_the_same_box(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    payroll = _closed(db, world)
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
            _in_session(new_session, world["admin"], _pay(world, payroll, ("CASH", "main_cash", "70000"))),
            _in_session(new_session, world["admin"], lambda session, actor: direct.create(session, actor, expense)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["main_cash"]) == Decimal("30000")


def test_a_payout_racing_a_payment_from_the_same_box(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    payroll = _closed(db, world)
    perez = ThirdParty(name="Distribuidora Pérez", created_by=world["admin"].id, updated_by=world["admin"].id)
    db.add(perez)
    db.commit()
    payment = payment_schemas.PaymentIn(
        client_request_id=uuid4(),
        business_date=today(),
        third_party_id=perez.id,
        concept="MERCHANDISE",
        items=[payment_schemas.PaymentItemIn(method="CASH", account_id=world["main_cash"], amount=Decimal("70000"))],
    )
    holder = _hold_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _pay(world, payroll, ("CASH", "main_cash", "70000"))),
            _in_session(
                new_session, world["admin"], lambda session, actor: payment_service.create(session, actor, payment)
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["main_cash"]) == Decimal("30000")


def test_a_payout_racing_an_advance(db: Session, world: dict[str, Any], new_session: Callable[[], Session]) -> None:
    payroll = _closed(db, world)
    holder = _hold_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _pay(world, payroll, ("CASH", "main_cash", "70000"))),
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "70000", funding_account_id=world["main_cash"])),
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["main_cash"]) == Decimal("30000")


def test_a_mixed_payout_racing_another_bank_debit(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session], employee: Callable[[str], int]
) -> None:
    payroll = _closed(db, world)
    beto = employee("Beto")
    holder = _hold_account(new_session, world["bank"])
    results = _race(
        holder,
        [
            _in_session(
                new_session,
                world["admin"],
                _pay(world, payroll, ("CASH", "main_cash", "30000"), ("TRANSFER", "bank", "70000")),
            ),
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "70000", employee_id=beto, funding_account_id=world["bank"])),
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["bank"]) == Decimal("30000")
    # All or nothing: when the payout lost, its cash box was not touched either.
    paid = _paid_total(db, payroll[1])
    assert _balance(db, world["main_cash"]) == (Decimal("70000") if paid else Decimal("100000"))


def test_two_reversals_of_the_same_payout_are_one(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    payroll = _closed(db, world)
    payout = _paid(db, world, payroll, ("CASH", "main_cash", "40000"))
    holder = _hold_account(new_session, _account(db, world))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _reverse(payout, "Uno")),
            _in_session(new_session, world["admin"], _reverse(payout, "Otro")),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["employee_payout_already_reversed"], results
    assert _balance(db, world["main_cash"]) == Decimal("100000")
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1


def test_a_reversal_racing_the_reopening(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    payroll = _closed(db, world)
    payout = _paid(db, world, payroll, ("CASH", "main_cash", "40000"))
    holder = _hold_account(new_session, _account(db, world))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _reverse(payout)),
            _in_session(new_session, world["admin"], _reopen(db, payroll[0])),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) in ([], ["payroll_has_payouts"]), results
    assert _paid_total(db, payroll[1]) == Decimal("0")
    assert _status(db, payroll[0]) == ("OPEN" if _codes(results) == [] else "CLOSED")


def test_the_same_payout_twice_at_once_is_one(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    payroll = _closed(db, world)
    request = uuid4()
    holder = _hold_account(new_session, _account(db, world))
    work = _pay(world, payroll, ("CASH", "main_cash", "25000"), ("TRANSFER", "bank", "5000"), request=request)
    results = _race(
        holder, [_in_session(new_session, world["admin"], work), _in_session(new_session, world["admin"], work)]
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    assert sorted(r.created for r in results if isinstance(r, payouts.Created)) == [False, True]
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_payouts") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'EMPLOYEE_PAYOUT'") == 1
    assert _paid_total(db, payroll[1]) == Decimal("30000")


def test_payroll_row_is_untouched_by_a_payout(db: Session, world: dict[str, Any]) -> None:
    """A payout never writes the payroll: its status and close stay as they were."""
    payroll = _closed(db, world)
    before = _scalar(db, "SELECT (status, version, current_closing_id)::text FROM settlements.employee_payrolls")
    _paid(db, world, payroll, ("CASH", "main_cash", "1000"))
    assert (
        _scalar(db, "SELECT (status, version, current_closing_id)::text FROM settlements.employee_payrolls") == before
    )
    assert db.get(EmployeePayroll, payroll[0]) is not None
