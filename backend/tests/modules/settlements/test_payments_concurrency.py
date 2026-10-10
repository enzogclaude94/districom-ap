"""Payments under concurrency, against PostgreSQL (R6-1). The main thread holds the row the workers need; once
released, the database must end in one valid state: never a negative cash box, one payment and one operation
per request, one reversal, never a payment to a third party deactivated before it, no deadlock."""

from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.settlements.payments import schemas as s
from app.modules.settlements.payments import service
from app.platform.third_parties import service as third_parties
from app.platform.third_parties.models import ThirdParty
from app.platform.third_parties.schemas import ThirdPartyPatch
from tests.modules.settlements.days import today
from tests.modules.settlements.test_direct_expenses_concurrency import _codes, _hold, _in_session, _race, _scalar


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
def world(db: Session, actor, accounts, post) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Caja principal with $100.000 and an active third party."""
    post("OPENING_BALANCE", (accounts["main_cash"], "100000"), (accounts["opening_balance"], "-100000"))
    admin = actor("admin")
    perez = ThirdParty(name="Distribuidora Pérez", created_by=admin.id, updated_by=admin.id)
    db.add(perez)
    db.commit()
    return {"operator": actor("operator"), "admin": admin, "perez": perez.id, **accounts}


def _payment(world: dict[str, Any], amount: str, request: Any = None) -> s.PaymentIn:
    return s.PaymentIn(
        client_request_id=request or uuid4(),
        business_date=today(),
        third_party_id=world["perez"],
        concept="MERCHANDISE",
        items=[s.PaymentItemIn(method="CASH", account_id=world["main_cash"], amount=Decimal(amount))],
    )


def _payments(db: Session) -> int:
    return int(_scalar(db, "SELECT count(*) FROM settlements.payments"))


def test_two_payments_racing_for_the_same_cash(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """$100.000 in the box, two payments of $60.000: one is paid, the other is insufficient_funds."""
    holder = _hold(
        new_session, "SELECT 1 FROM settlements.accounts WHERE id = :a FOR NO KEY UPDATE", a=world["main_cash"]
    )
    pay = lambda session, actor: service.create(session, actor, _payment(world, "60000"))  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["operator"], pay) for _ in range(2)])
    assert _codes(results) == ["insufficient_funds"]
    assert _payments(db) == 1
    balance = _scalar(
        db, "SELECT sum(amount) FROM settlements.ledger_entries WHERE account_id = :a", a=world["main_cash"]
    )
    assert balance == Decimal("40000.00")


def test_the_same_request_twice_at_once_is_one_payment(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    request = uuid4()
    holder = _hold(
        new_session, "SELECT 1 FROM settlements.accounts WHERE id = :a FOR NO KEY UPDATE", a=world["main_cash"]
    )
    pay = lambda session, actor: service.create(session, actor, _payment(world, "1000", request))  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["operator"], pay) for _ in range(2)])
    assert not [r for r in results if isinstance(r, Exception)], results
    assert sorted(r.created for r in results) == [False, True]  # type: ignore[union-attr]
    assert {r.id for r in results} == {_scalar(db, "SELECT id FROM settlements.payments")}  # type: ignore[union-attr]
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'PAYMENT'") == 1


def test_a_deactivation_racing_a_payment(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """A payment holds the third party FOR SHARE until its commit: the deactivation waits for it, and a
    payment that starts after the deactivation sees it and is refused. Never a payment to a third party
    that was already deactivated."""
    # 1. A payment in flight holds the third party: the deactivation waits for its commit.
    holder = new_session()
    third_parties.lock_active(holder, world["perez"])
    deactivate = lambda ses, actor: third_parties.update(ses, actor, world["perez"], ThirdPartyPatch(active=False))  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["admin"], deactivate)])
    assert not [r for r in results if isinstance(r, Exception)], results

    # 2. Deactivated (committed) before: the next payment is refused, nothing written.
    db.expire_all()
    assert _scalar(db, "SELECT active FROM third_parties WHERE id = :t", t=world["perez"]) is False
    holder = _hold(new_session, "SELECT 1 FROM third_parties WHERE id = :t FOR NO KEY UPDATE", t=world["perez"])
    pay = lambda ses, actor: service.create(ses, actor, _payment(world, "1000"))  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["operator"], pay)])
    assert _codes(results) == ["third_party_inactive"]
    assert _payments(db) == 0


def test_a_payment_waits_for_a_deactivation_in_flight(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    """The deactivation holds the row (uncommitted): the payment waits, then sees it deactivated."""
    holder = new_session()
    holder.execute(text("UPDATE third_parties SET active = false WHERE id = :t"), {"t": world["perez"]})
    pay = lambda ses, actor: service.create(ses, actor, _payment(world, "1000"))  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["operator"], pay)])
    assert _codes(results) == ["third_party_inactive"]
    assert _payments(db) == 0


def test_two_reversals_at_once_are_one(new_session, world, db: Session) -> None:  # type: ignore[no-untyped-def]
    payment = service.create(db, world["operator"], _payment(world, "1000"))
    holder = _hold(new_session, "SELECT 1 FROM settlements.payments WHERE id = :p FOR UPDATE", p=payment.id)
    body = lambda: s.PaymentReversalIn(client_request_id=uuid4(), reason="Duplicado")  # noqa: E731
    rev = lambda ses, actor: service.reverse(ses, actor, payment.id, body())  # noqa: E731
    results = _race(holder, [_in_session(new_session, world["admin"], rev) for _ in range(2)])
    assert _codes(results) == ["payment_already_reversed"]
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1
