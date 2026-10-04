"""Concurrent creation of settlements against PostgreSQL: distinct numbers, no duplicate by retry.

[1] in Enzo's list of R2-1 tests. The main thread holds the sequence lock of a driver and day, every
worker blocks on it, and once it is released they must get 1, 2, 3... in some order, never the same
number nor a unique-constraint error.
"""

import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import business_today
from app.modules.settlements.delivery import schemas, service
from app.platform.auth.models import User

DAY = business_today() - timedelta(days=1)
WAIT = 0.5  # long enough for every worker to reach the lock


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


def _body(employee_id: int, key: UUID | None = None, expected: str = "1000") -> schemas.SettlementIn:
    return schemas.SettlementIn(
        client_request_id=key or uuid4(),
        business_date=DAY,
        employee_id=employee_id,
        expected_amount=Decimal(expected),
    )


def _run(workers: list[Callable[[], object]]) -> list[object]:
    results: list[object] = [None] * len(workers)

    def _call(i: int) -> None:
        try:
            results[i] = workers[i]()
        except Exception as exc:  # reported in the assertion
            results[i] = exc

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(len(workers))]
    for thread in threads:
        thread.start()
    return [results, threads]  # type: ignore[list-item]


def _join(started: list[object]) -> list[object]:
    results, threads = started
    for thread in threads:  # type: ignore[attr-defined]
        thread.join(timeout=10)
        assert not thread.is_alive(), "a worker is still waiting: deadlock?"
    return results  # type: ignore[return-value]


def _create(new_session: Callable[[], Session], actor: User, body: schemas.SettlementIn) -> Callable[[], object]:
    def _work() -> object:
        session = new_session()
        return service.create(session, session.merge(actor, load=False), body)

    return _work


def test_simultaneous_creations_get_distinct_numbers(db: Session, new_session, actor, driver) -> None:  # type: ignore[no-untyped-def]
    operator = actor("operator")
    pedro = driver("Pedro Gómez")
    holder = new_session()
    lock_key = f"settlements.sequence:DELIVERY:{pedro}:{DAY.isoformat()}"
    holder.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))

    started = _run([_create(new_session, operator, _body(pedro)) for _ in range(5)])
    time.sleep(WAIT)
    holder.commit()  # releases the lock: the workers go one after the other
    results = _join(started)

    assert all(isinstance(r, service.Created) and r.created for r in results), results
    numbers = db.execute(
        text("SELECT sequence_number FROM settlements.settlements WHERE employee_id = :e ORDER BY 1"), {"e": pedro}
    ).scalars()
    assert list(numbers) == [1, 2, 3, 4, 5]


def test_simultaneous_creations_without_a_held_lock_never_collide(db: Session, new_session, actor, driver) -> None:  # type: ignore[no-untyped-def]
    operator = actor("operator")
    pedro = driver("Pedro Gómez")
    results = _join(_run([_create(new_session, operator, _body(pedro)) for _ in range(8)]))
    assert all(isinstance(r, service.Created) and r.created for r in results), results
    numbers = db.execute(
        text("SELECT sequence_number FROM settlements.settlements WHERE employee_id = :e ORDER BY 1"), {"e": pedro}
    ).scalars()
    assert list(numbers) == list(range(1, 9))


def test_simultaneous_retries_of_one_request_create_one_settlement(db: Session, new_session, actor, driver) -> None:  # type: ignore[no-untyped-def]
    operator = actor("operator")
    pedro = driver("Pedro Gómez")
    key = uuid4()
    results = _join(_run([_create(new_session, operator, _body(pedro, key)) for _ in range(4)]))
    assert all(isinstance(r, service.Created) for r in results), results
    assert sorted(r.created for r in results) == [False, False, False, True]  # type: ignore[attr-defined]
    assert len({r.id for r in results}) == 1  # type: ignore[attr-defined]
    count = db.execute(text("SELECT count(*) FROM settlements.settlements")).scalar_one()
    assert count == 1
