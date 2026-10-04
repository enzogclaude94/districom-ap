"""Concurrent publications against PostgreSQL: no overdraft, no duplicate, no deadlock.

Each test holds one transaction open in the main thread and runs a second one in another thread,
then checks how the second one ends once the first commits.
"""

import threading
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.modules.settlements.ledger.models import FinancialOperation
from app.modules.settlements.ledger.service import Leg, Posted, account_balances, post_operation
from app.platform.auth.models import User

WAIT = 0.5  # long enough for the second transaction to reach the lock


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
def admin(actor) -> User:  # type: ignore[no-untyped-def]
    return actor("admin")  # type: ignore[no-any-return]


@pytest.fixture
def funded(post, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    post("OPENING_BALANCE", (accounts["main_cash"], "100"), (accounts["opening_balance"], "-100"))
    return accounts


def _transfer(session: Session, admin: User, source: int, target: int, amount: str, key: UUID | None = None) -> Posted:
    return post_operation(
        session,
        actor=session.merge(admin, load=False),
        type="CASH_TRANSFER",
        legs=[Leg(source, -Decimal(amount)), Leg(target, Decimal(amount))],
        client_request_id=key or uuid4(),
    )


def _in_thread(work: Callable[[], object]) -> tuple[threading.Thread, dict[str, object]]:
    outcome: dict[str, object] = {}

    def run() -> None:
        try:
            outcome["result"] = work()
        except Exception as exc:  # the test inspects whatever happened
            outcome["error"] = exc

    thread = threading.Thread(target=run)
    thread.start()
    time.sleep(WAIT)
    return thread, outcome


def _waiting_on_lock(db: Session) -> bool:
    return bool(db.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE wait_event_type = 'Lock'")))


def test_two_withdrawals_cannot_overdraw_a_cash_box(db, new_session, admin, funded) -> None:  # type: ignore[no-untyped-def]
    first, second = new_session(), new_session()
    _transfer(first, admin, funded["main_cash"], funded["change_cash"], "80")

    def other() -> Posted:
        posted = _transfer(second, admin, funded["main_cash"], funded["change_cash"], "80")
        second.commit()
        return posted

    thread, outcome = _in_thread(other)
    assert _waiting_on_lock(db), "the second withdrawal should wait for the first one's lock"
    first.commit()
    thread.join(timeout=10)

    error = outcome.get("error")
    assert isinstance(error, AppError)
    assert error.code == "insufficient_funds"
    balances = {b.code: b.balance for b in account_balances(db)}
    assert (balances["main_cash"], balances["change_cash"]) == (Decimal("20.00"), Decimal("80.00"))


def test_two_withdrawals_that_fit_both_succeed(db, new_session, admin, funded) -> None:  # type: ignore[no-untyped-def]
    first, second = new_session(), new_session()
    _transfer(first, admin, funded["main_cash"], funded["change_cash"], "40")

    def other() -> Posted:
        posted = _transfer(second, admin, funded["main_cash"], funded["change_cash"], "60")
        second.commit()
        return posted

    thread, outcome = _in_thread(other)
    first.commit()
    thread.join(timeout=10)

    assert "error" not in outcome
    assert {b.code: b.balance for b in account_balances(db)}["main_cash"] == 0


@pytest.mark.parametrize(("amount", "expected"), [("30", "replay"), ("70", "idempotency_conflict")])
def test_same_request_id_in_flight_twice(db, new_session, admin, funded, amount, expected) -> None:  # type: ignore[no-untyped-def]
    key = uuid4()
    first, second = new_session(), new_session()
    original = _transfer(first, admin, funded["main_cash"], funded["change_cash"], "30", key)

    def other() -> Posted:
        posted = _transfer(second, admin, funded["main_cash"], funded["change_cash"], amount, key)
        second.commit()
        return posted

    thread, outcome = _in_thread(other)
    first.commit()
    thread.join(timeout=10)

    if expected == "replay":
        result = outcome["result"]
        assert isinstance(result, Posted)
        assert (result.created, result.operation.id) == (False, original.operation.id)
    else:
        error = outcome["error"]
        assert isinstance(error, AppError)
        assert error.code == "idempotency_conflict"
    assert db.scalar(select(func.count()).select_from(FinancialOperation)) == 2  # opening + one transfer


def test_opposite_transfers_do_not_deadlock(db, new_session, admin, funded, post) -> None:  # type: ignore[no-untyped-def]
    post("CASH_TRANSFER", (funded["main_cash"], "-50"), (funded["change_cash"], "50"))
    first, second = new_session(), new_session()
    _transfer(first, admin, funded["main_cash"], funded["change_cash"], "10")

    def other() -> Posted:
        posted = _transfer(second, admin, funded["change_cash"], funded["main_cash"], "20")
        second.commit()
        return posted

    thread, outcome = _in_thread(other)
    first.commit()
    thread.join(timeout=10)

    assert "error" not in outcome, outcome.get("error")
    balances = {b.code: b.balance for b in account_balances(db)}
    assert (balances["main_cash"], balances["change_cash"]) == (Decimal("60.00"), Decimal("40.00"))
