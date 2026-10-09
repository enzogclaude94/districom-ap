"""Fuel loads under concurrency, against PostgreSQL (R5-2). The main thread holds the settlement row the
workers need; once released, the database must end in one valid state: a close that matches its fuel
(never one built on liters that changed after its preview), one load per expense, no deadlock."""

import threading
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.delivery import closing, expenses, schemas, service
from app.platform.auth.models import User
from tests.modules.settlements.fuel_support import fuel_category, vehicle

DAY = business_today() - timedelta(days=1)
WAIT = 0.5


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


def _race(holder: Session, workers: list[Callable[[], object]]) -> list[object]:
    results: list[object] = [None] * len(workers)

    def _call(i: int) -> None:
        try:
            results[i] = workers[i]()
        except Exception as exc:  # reported in the assertions
            results[i] = exc

    threads = [threading.Thread(target=_call, args=(i,)) for i in range(len(workers))]
    for thread in threads:
        thread.start()
    time.sleep(WAIT)
    holder.commit()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "a worker is still waiting: deadlock?"
    return results


def _in_session(
    new_session: Callable[[], Session], actor: User, work: Callable[[Session, User], object]
) -> Callable[[], object]:
    def _work() -> object:
        session = new_session()
        return work(session, session.merge(actor, load=False))

    return _work


def _hold(new_session: Callable[[], Session], sid: int) -> Session:
    holder = new_session()
    holder.execute(text("SELECT 1 FROM settlements.settlements WHERE id = :id FOR UPDATE"), {"id": sid})
    return holder


@pytest.fixture
def setup(db: Session, actor, driver) -> dict[str, object]:  # type: ignore[no-untyped-def]
    """An OPEN settlement of $10.000 with one fuel load of 10 l x $1.000: it closes balanced."""
    operator = actor("operator")
    pedro = driver("Pedro Gómez")
    truck = vehicle(db, "AB123CD")
    sid = service.create(
        db,
        operator,
        schemas.SettlementIn(
            client_request_id=uuid4(), business_date=DAY, employee_id=pedro, expected_amount=Decimal("10000")
        ),
    ).id
    body = schemas.ExpenseIn(
        client_request_id=uuid4(),
        category_id=fuel_category(db),
        fuel=schemas.FuelIn(vehicle_id=truck, liters=Decimal("10"), price_per_liter=Decimal("1000")),
    )
    eid = expenses.create(db, operator, sid, body).id
    return {"operator": operator, "sid": sid, "expense": eid, "truck": truck, "category": fuel_category(db)}


def _close_body(db: Session, sid: int) -> schemas.CloseIn:
    db.rollback()
    return schemas.CloseIn(version=1, preview_token=closing.preview(db, sid).token, allocations=[], confirm=True)


def _scalar(db: Session, sql: str, **params: object) -> object:
    db.rollback()
    return db.execute(text(sql), params).scalar_one()


CHANGES: dict[str, Callable[[int, int], Callable[[Session, User], object]]] = {
    "liters": lambda sid, eid: (
        lambda s, u: expenses.update(s, u, sid, eid, schemas.ExpensePatch(fuel=schemas.FuelPatch(liters=Decimal("12"))))
    ),
    "price": lambda sid, eid: (
        lambda s, u: expenses.update(
            s, u, sid, eid, schemas.ExpensePatch(fuel=schemas.FuelPatch(price_per_liter=Decimal("1100")))
        )
    ),
    "void": lambda sid, eid: lambda s, u: expenses.void(s, u, sid, eid, "Cargado dos veces"),
}


@pytest.mark.parametrize("change", list(CHANGES))
def test_a_close_racing_a_fuel_change_never_publishes_stale_fuel(  # type: ignore[no-untyped-def]
    db: Session, setup, new_session, change: str
) -> None:
    """Close vs liters, vs price per liter, vs void: exactly one wins. If the change wins, the close (built on
    the earlier preview) is stale; if the close wins, the change finds it closed. The close in effect always
    publishes the amount its fuel load computes."""
    sid, eid, operator = setup["sid"], setup["expense"], setup["operator"]
    body = _close_body(db, sid)  # type: ignore[arg-type]
    holder = _hold(new_session, sid)  # type: ignore[arg-type]
    results = _race(
        holder,
        [
            _in_session(new_session, operator, lambda s, u: closing.close(s, u, sid, body)),  # type: ignore[arg-type]
            _in_session(new_session, operator, CHANGES[change](sid, eid)),  # type: ignore[arg-type,arg-type]
        ],
    )
    closed, changed = results
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    (failure,) = errors
    assert isinstance(failure, AppError)
    if failure is closed:
        assert failure.code == "settlement_preview_stale"
        assert _scalar(db, "SELECT status FROM settlements.settlements WHERE id = :s", s=sid) == "OPEN"
    else:
        assert failure is changed
        assert failure.code == "settlement_closed"
        published = _scalar(
            db,
            "SELECT e.amount FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id "
            "WHERE a.code = 'expense'",
        )
        assert published == Decimal("10000.00")
    assert _scalar(db, "SELECT count(*) FROM settlements.fuel_loads") == 1


def test_two_concurrent_corrections_both_apply_in_order(db: Session, setup, new_session) -> None:  # type: ignore[no-untyped-def]
    sid, eid, operator = setup["sid"], setup["expense"], setup["operator"]
    holder = _hold(new_session, sid)  # type: ignore[arg-type]
    results = _race(
        holder,
        [
            _in_session(new_session, operator, CHANGES["liters"](sid, eid)),  # type: ignore[arg-type,arg-type]
            _in_session(new_session, operator, CHANGES["price"](sid, eid)),  # type: ignore[arg-type,arg-type]
        ],
    )
    assert results == [None, None]
    assert _scalar(db, "SELECT amount FROM settlements.expenses WHERE id = :e", e=eid) == Decimal("13200.00")
    assert _scalar(db, "SELECT count(*) FROM audit_events WHERE event_type = 'settlements.expense.updated'") == 2


def test_the_same_request_twice_at_once_loads_fuel_once(db: Session, setup, new_session) -> None:  # type: ignore[no-untyped-def]
    sid, operator = setup["sid"], setup["operator"]
    body = schemas.ExpenseIn(
        client_request_id=uuid4(),
        category_id=setup["category"],  # type: ignore[arg-type]
        fuel=schemas.FuelIn(vehicle_id=setup["truck"], liters=Decimal("5"), price_per_liter=Decimal("1000")),  # type: ignore[arg-type]
    )
    holder = _hold(new_session, sid)  # type: ignore[arg-type]
    work = lambda s, u: expenses.create(s, u, sid, body)  # noqa: E731
    results = _race(holder, [_in_session(new_session, operator, work), _in_session(new_session, operator, work)])  # type: ignore[arg-type]
    created = [r for r in results if isinstance(r, service.Created)]
    assert len(created) == 2, results
    assert sorted(r.created for r in created) == [False, True]
    assert created[0].id == created[1].id
    assert _scalar(db, "SELECT count(*) FROM settlements.fuel_loads") == 2  # the setup's and this one
    assert _scalar(db, "SELECT count(*) FROM settlements.expenses") == 2
