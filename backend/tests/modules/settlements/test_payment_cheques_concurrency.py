"""Cheques delivered in payments under concurrency, against PostgreSQL (R6-2). The main thread holds the row
the workers need; once released, the database must end in one valid state, with every cheque whole and in
one place: one payment per cheque, never a cheque both paid and deposited or returned, one reversal, and a
payment that never creates nor deepens an overdraft even racing a cheque rejection on the bank (the race
pending from R6-1)."""

from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.cheques import cycle
from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.payments import schemas as s
from app.modules.settlements.payments import service
from app.platform.auth.models import User
from app.platform.third_parties.models import ThirdParty
from tests.modules.settlements.test_cheque_cycle_concurrency import (
    _bank_account,
    _check_whole,
    _deposit_body,
    _entry,
    _lock_cheques,
)
from tests.modules.settlements.test_direct_expenses_concurrency import _codes, _hold, _race, _scalar

T = business_today()


def _in_session(
    new_session: Callable[[], Session], actor: User, work: Callable[[Session, User], object]
) -> Callable[[], object]:
    """A worker in its own session that, like a request, rolls back what it left open when refused: a refused
    document must release the cheques it locked."""

    def _work() -> object:
        session = new_session()
        try:
            return work(session, session.merge(actor, load=False))
        except Exception:
            session.rollback()
            raise

    return _work


@pytest.fixture
def new_session(db: Session) -> Iterator[Callable[[], Session]]:
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    sessions: list[Session] = []

    def _new() -> Session:
        session = factory()
        sessions.append(session)
        return session

    yield _new
    for session in sessions:
        session.close()


@pytest.fixture
def world(db: Session, actor, driver) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A cheque of $100.000 in the portfolio since T-10 and an active third party."""
    admin = actor("admin")
    perez = ThirdParty(name="Distribuidora Pérez", created_by=admin.id, updated_by=admin.id)
    db.add(perez)
    db.commit()
    return {
        "admin": admin,
        "operator": actor("operator"),
        "perez": perez.id,
        "cheque": _entry(db, admin),
        "ana": driver("Ana"),
    }


def _with_cheque(world: dict[str, Any], cheque_id: int, request: Any = None) -> s.PaymentIn:
    return s.PaymentIn(
        client_request_id=request or uuid4(),
        business_date=T,
        third_party_id=world["perez"],
        concept="MERCHANDISE",
        items=[s.PaymentItemIn(method="CHEQUE", cheque_id=cheque_id, amount=Decimal("100000"))],
    )


def _pay(world: dict[str, Any], cheque_id: int) -> Callable[[Session, User], object]:
    return lambda session, user: service.create(session, user, _with_cheque(world, cheque_id))


def _reversal(cheque_ids: list[int]) -> s.PaymentReversalIn:
    return s.PaymentReversalIn(client_request_id=uuid4(), reason="Pago equivocado", recovered_cheque_ids=cheque_ids)


def _count(db: Session, sql: str, **params: object) -> int:
    return int(_scalar(db, sql, **params))


def _live_payments_of(db: Session, cheque_id: int) -> int:
    return _count(
        db,
        "SELECT count(*) FROM settlements.payment_items i JOIN settlements.payments p ON p.id = i.payment_id "
        "WHERE i.cheque_id = :c AND p.reversal_operation_id IS NULL",
        c=cheque_id,
    )


def _live_deposits_of(db: Session, cheque_id: int) -> int:
    return _count(
        db,
        "SELECT count(*) FROM settlements.cheque_deposit_items i JOIN settlements.cheque_deposits d "
        "ON d.id = i.deposit_id WHERE i.cheque_id = :c AND d.reversal_operation_id IS NULL",
        c=cheque_id,
    )


def test_a_two_payments_with_the_same_cheque(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    holder = new_session()
    _lock_cheques(holder, [world["cheque"]])
    pay = _pay(world, world["cheque"])
    results = _race(holder, [_in_session(new_session, world["operator"], pay) for _ in range(2)])
    assert _codes(results) == ["cheque_not_in_portfolio"]
    assert _live_payments_of(db, world["cheque"]) == 1
    assert _count(db, "SELECT count(*) FROM settlements.cheque_events WHERE event_type = 'DELIVERED'") == 1
    _check_whole(db)


def test_the_same_payment_request_twice_at_once_is_one_delivery(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    request = uuid4()
    holder = new_session()
    _lock_cheques(holder, [world["cheque"]])
    pay = lambda ses, user: service.create(ses, user, _with_cheque(world, world["cheque"], request))  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["operator"], pay) for _ in range(2)])
    assert not [r for r in results if isinstance(r, Exception)], results
    assert sorted(r.created for r in results) == [False, True]  # type: ignore[union-attr]
    assert _count(db, "SELECT count(*) FROM settlements.cheque_events WHERE event_type = 'DELIVERED'") == 1
    _check_whole(db)


def test_b_a_payment_racing_a_deposit_of_the_cheque(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    holder = new_session()
    _lock_cheques(holder, [world["cheque"]])
    deposit = lambda ses, user: cycle.deposit(ses, user, _deposit_body(ses, [world["cheque"]]))  # noqa: E731
    results = _race(
        holder,
        [
            _in_session(new_session, world["operator"], _pay(world, world["cheque"])),
            _in_session(new_session, world["operator"], deposit),
        ],
    )
    assert _codes(results) == ["cheque_not_in_portfolio"]
    assert _live_payments_of(db, world["cheque"]) + _live_deposits_of(db, world["cheque"]) == 1
    _check_whole(db)


def test_c_a_payment_racing_a_return_of_the_cheque(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    holder = new_session()
    _lock_cheques(holder, [world["cheque"]])
    body = cs.ReturnIn(
        client_request_id=uuid4(), reason="Lo reemplaza", business_date=T, responsible_employee_id=world["ana"]
    )
    give_back = lambda ses, user: cycle.give_back(ses, user, world["cheque"], body)  # noqa: E731
    results = _race(
        holder,
        [
            _in_session(new_session, world["operator"], _pay(world, world["cheque"])),
            _in_session(new_session, world["admin"], give_back),
        ],
    )
    assert _codes(results) == ["cheque_not_in_portfolio"]
    returns = _count(db, "SELECT count(*) FROM settlements.cheque_returns WHERE reversal_operation_id IS NULL")
    assert _live_payments_of(db, world["cheque"]) + returns == 1
    _check_whole(db)


def test_d_two_reversals_of_a_payment_with_a_cheque(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    payment = service.create(db, world["operator"], _with_cheque(world, world["cheque"]))
    holder = new_session()
    _lock_cheques(holder, [world["cheque"]])
    reverse = lambda ses, user: service.reverse(ses, user, payment.id, _reversal([world["cheque"]]))  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["admin"], reverse) for _ in range(2)])
    assert _codes(results) == ["payment_already_reversed"]
    assert _count(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1
    recovered = "SELECT count(*) FROM settlements.cheque_events WHERE event_type = 'RECOVERED_FROM_PAYMENT'"
    assert _count(db, recovered) == 1
    _check_whole(db)


def test_e_a_reversal_racing_a_deposit_of_its_cheque(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """The deposit either runs first and finds the cheque delivered, or after the reversal and deposits it."""
    payment = service.create(db, world["operator"], _with_cheque(world, world["cheque"]))
    holder = new_session()
    _lock_cheques(holder, [world["cheque"]])
    reverse = lambda ses, user: service.reverse(ses, user, payment.id, _reversal([world["cheque"]]))  # noqa: E731

    def deposit(session: Session, user: User) -> object:
        body = cs.DepositIn(
            client_request_id=uuid4(), bank_account_id=_bank_account(session), cheque_ids=[world["cheque"]]
        )
        return cycle.deposit(session, user, body)

    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], reverse),
            _in_session(new_session, world["operator"], deposit),
        ],
    )
    assert not isinstance(results[0], Exception), results
    assert _codes(results) in ([], ["cheque_not_in_portfolio"])
    assert _live_payments_of(db, world["cheque"]) == 0
    assert _live_deposits_of(db, world["cheque"]) == (0 if _codes(results) else 1)
    _check_whole(db)


def test_f_a_payment_racing_a_rejection_after_crediting_never_deepens_the_overdraft(  # type: ignore[no-untyped-def]
    new_session, world, db: Session, post, accounts
) -> None:
    """The bank has $100.000 of its own plus a credited cheque of $100.000. A transfer of $150.000 and the
    rejection of that cheque race on the bank: if the payment goes first the rejection may still overdraw
    the bank (an external debit), and if the rejection goes first the payment is insufficient_funds. Never a
    payment that leaves the bank negative at any end of day from its date on."""
    bank = _bank_account(db)
    post(
        "OPENING_BALANCE",
        (bank, "100000"),
        (accounts["opening_balance"], "-100000"),
        business_date=T - timedelta(days=10),
    )
    cheque = world["cheque"]
    cycle.deposit(db, world["operator"], _deposit_body(db, [cheque]))
    credit = cs.CreditIn(
        client_request_id=uuid4(), bank_account_id=bank, cheque_ids=[cheque], business_date=T - timedelta(days=6)
    )
    cycle.credit(db, world["operator"], credit)
    holder = _hold(new_session, "SELECT 1 FROM settlements.accounts WHERE id = :a FOR NO KEY UPDATE", a=bank)

    def pay(session: Session, user: User) -> object:
        body = s.PaymentIn(
            client_request_id=uuid4(),
            business_date=T,
            third_party_id=world["perez"],
            concept="MERCHANDISE",
            items=[s.PaymentItemIn(method="TRANSFER", account_id=bank, amount=Decimal("150000"))],
        )
        return service.create(session, user, body)

    def reject(session: Session, user: User) -> object:
        body = cs.RejectionIn(
            client_request_id=uuid4(), reason="Sin fondos", business_date=T, responsible_employee_id=world["ana"]
        )
        return cycle.reject(session, user, cheque, body)

    results = _race(
        holder,
        [
            _in_session(new_session, world["operator"], pay),
            _in_session(new_session, world["operator"], reject),
        ],
    )
    assert not isinstance(results[1], Exception), results  # the bank's rejection is always booked
    balance = Decimal(_scalar(db, "SELECT sum(amount) FROM settlements.ledger_entries WHERE account_id = :a", a=bank))
    if isinstance(results[0], AppError):
        assert results[0].code == "insufficient_funds"
        assert balance == Decimal("100000.00")
    else:
        # The payment went first, with room for it; the rejection overdrew the bank afterwards.
        assert balance == Decimal("-50000.00")
    assert not [r for r in results if isinstance(r, Exception) and not isinstance(r, AppError)], results
    _check_whole(db)
