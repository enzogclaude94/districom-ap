"""Close, reopen and new close under concurrency, against PostgreSQL (R2-2).

The main thread holds the lock the workers need (the settlement row and, when money leaves a cash
box, that account), every worker blocks on it, and once it is released the database must end in one
of the valid states: never two closes, never a stale close, never a negative cash box, no deadlock.
"""

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
from app.modules.settlements.delivery import closing, schemas, service
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.pending import schemas as ps
from app.modules.settlements.pending import service as pending_service
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


def _race(holder: Session, workers: list[Callable[[], object]]) -> list[object]:
    """Start the workers while `holder` keeps its locks, release them, and return each result or error."""
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


def _hold(new_session: Callable[[], Session], settlement_id: int, *account_ids: int) -> Session:
    holder = new_session()
    holder.execute(text("SELECT 1 FROM settlements.settlements WHERE id = :id FOR UPDATE"), {"id": settlement_id})
    for account_id in account_ids:
        holder.execute(text("SELECT 1 FROM settlements.accounts WHERE id = :id FOR NO KEY UPDATE"), {"id": account_id})
    return holder


def _in_session(
    new_session: Callable[[], Session], actor: User, work: Callable[[Session, User], object]
) -> Callable[[], object]:
    def _work() -> object:
        session = new_session()
        return work(session, session.merge(actor, load=False))

    return _work


def _token(db: Session, sid: int) -> str:
    db.rollback()  # see the latest committed state
    return closing.preview(db, sid).token


def close_now(db: Session, actor: User, sid: int, version: int, *allocations: schemas.AllocationIn) -> None:
    body = schemas.CloseIn(version=version, preview_token=_token(db, sid), allocations=list(allocations), confirm=True)
    closing.close(db, actor, sid, body)


def _close(
    db: Session, sid: int, version: int, *allocations: schemas.AllocationIn
) -> Callable[[Session, User], object]:
    """A close confirming the preview taken now, before the race starts."""
    body = schemas.CloseIn(version=version, preview_token=_token(db, sid), allocations=list(allocations), confirm=True)
    return lambda session, user: closing.close(session, user, sid, body)


def _cash_transfer(source: int, target: int, amount: str) -> Callable[[Session, User], object]:
    def _work(session: Session, user: User) -> object:
        posted = ledger.post_operation(
            session,
            actor=user,
            type="CASH_TRANSFER",
            legs=[ledger.Leg(source, -Decimal(amount)), ledger.Leg(target, Decimal(amount))],
            client_request_id=uuid4(),
        )
        session.commit()
        return posted

    return _work


def _count(db: Session, actor: User, sid: int, *lines: tuple[str, int, int]) -> None:
    service.replace_count(
        db,
        actor,
        sid,
        schemas.CountIn(
            lines=[
                schemas.CountLineIn(denomination_code=c, quantity=q, destination_account_id=box) for c, q, box in lines
            ]
        ),
    )


def _settlement(db: Session, actor: User, employee_id: int, expected: str) -> int:
    body = schemas.SettlementIn(
        client_request_id=uuid4(), business_date=DAY, employee_id=employee_id, expected_amount=Decimal(expected)
    )
    return service.create(db, actor, body).id


def _scalar(db: Session, sql: str, **params: object) -> object:
    db.rollback()  # see what the workers committed
    return db.execute(text(sql), params).scalar_one()


def _errors(results: list[object]) -> list[str]:
    return sorted(r.code for r in results if isinstance(r, AppError))


@pytest.fixture
def operator(actor) -> User:  # type: ignore[no-untyped-def]
    return actor("operator")  # type: ignore[no-any-return]


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def short(db: Session, operator: User, pedro: int, accounts) -> int:  # type: ignore[no-untyped-def]
    """$1.500 expected, $1.000 counted in Caja principal: $500 short."""
    sid = _settlement(db, operator, pedro, "1500")
    _count(db, operator, sid, ("ARS_1000", 1, accounts["main_cash"]))
    return sid


def test_two_simultaneous_closes_publish_one(db, new_session, operator, short, pedro) -> None:  # type: ignore[no-untyped-def]
    """[15] Same close twice at once: one publishes, the other gets it back; one operation, one difference."""
    charge = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))
    holder = _hold(new_session, short)
    results = _race(holder, [_in_session(new_session, operator, _close(db, short, 1, charge)) for _ in range(2)])
    assert sorted(r.created for r in results if isinstance(r, service.Created)) == [False, True], results
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.settlement_differences") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.accounts WHERE kind = 'EMPLOYEE'") == 1


def test_close_against_a_load_change_never_closes_stale_totals(
    db, new_session, operator, short, pedro, accounts
) -> None:  # type: ignore[no-untyped-def]
    """A count change and a close race: either the change lands first and the close (confirming the old
    preview) is refused as stale, or the close lands first and the change is refused. Never both."""
    charge = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))

    def _recount(session: Session, user: User) -> object:
        _count(session, user, short, ("ARS_1000", 1, accounts["main_cash"]), ("ARS_500", 1, accounts["main_cash"]))
        return "recounted"

    holder = _hold(new_session, short)
    results = _race(
        holder,
        [
            _in_session(new_session, operator, _close(db, short, 1, charge)),
            _in_session(new_session, operator, _recount),
        ],
    )
    status = _scalar(db, "SELECT status FROM settlements.settlements WHERE id = :id", id=short)
    if status == "CLOSED":
        assert _errors(results) == ["settlement_closed"], results
        main = _scalar(
            db,
            "SELECT e.amount FROM settlements.ledger_entries e WHERE e.account_id = :a",
            a=accounts["main_cash"],
        )
        assert main == Decimal("1000.00")
    else:
        assert _errors(results) == ["settlement_preview_stale"], results
        assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations") == 0


def test_close_against_a_cash_transfer_stays_consistent(
    db, new_session, operator, short, pedro, accounts, post
) -> None:  # type: ignore[no-untyped-def]
    """[29] Closing brings $1.000 to Caja principal while $2.000 already there move to Caja de cambio."""
    post("OPENING_BALANCE", (accounts["main_cash"], "2000"), (accounts["opening_balance"], "-2000"))
    charge = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))
    holder = _hold(new_session, short, accounts["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, operator, _close(db, short, 1, charge)),
            _in_session(new_session, operator, _cash_transfer(accounts["main_cash"], accounts["change_cash"], "2000")),
        ],
    )
    assert _errors(results) == [], results
    balances = {b.code: b.balance for b in ledger.account_balances(db)}
    assert (balances["main_cash"], balances["change_cash"]) == (Decimal("1000.00"), Decimal("2000.00"))
    assert _scalar(db, "SELECT sum(amount) FROM settlements.ledger_entries") == 0


def test_concurrent_reopen_happens_once(db, new_session, operator, actor, short, pedro) -> None:  # type: ignore[no-untyped-def]
    charge = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))
    close_now(db, operator, short, 1, charge)
    admin = actor("admin")

    def _reopen(reason: str) -> Callable[[Session, User], object]:
        return lambda session, user: closing.reopen(session, user, short, schemas.ReopenIn(version=1, reason=reason))

    holder = _hold(new_session, short)
    results = _race(
        holder,
        [_in_session(new_session, admin, _reopen("Motivo A")), _in_session(new_session, admin, _reopen("Motivo B"))],
    )
    assert _errors(results) == ["settlement_not_closed"], results
    assert _scalar(db, "SELECT version FROM settlements.settlements WHERE id = :id", id=short) == 2
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations") == 1


def test_two_simultaneous_new_closes_publish_one_replacement(
    db, new_session, operator, actor, short, pedro, accounts
) -> None:  # type: ignore[no-untyped-def]
    charge = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))
    close_now(db, operator, short, 1, charge)
    closing.reopen(db, actor("admin"), short, schemas.ReopenIn(version=1, reason="Faltaban $500"))
    _count(db, operator, short, ("ARS_1000", 1, accounts["main_cash"]), ("ARS_500", 1, accounts["main_cash"]))

    holder = _hold(new_session, short)
    results = _race(holder, [_in_session(new_session, operator, _close(db, short, 2)) for _ in range(2)])
    assert sorted(r.created for r in results if isinstance(r, service.Created)) == [False, True], results
    types = _scalar(db, "SELECT string_agg(type, ',' ORDER BY id) FROM settlements.financial_operations")
    assert types == "SETTLEMENT_CLOSE,REVERSAL,SETTLEMENT_CLOSE"


def test_replacement_against_another_movement_never_overdraws(
    db, new_session, operator, actor, short, pedro, accounts
) -> None:  # type: ignore[no-untyped-def]
    """A correction that takes $500 out of Caja principal (net) and a transfer of $800 from it race.
    The box holds $1.000: both together would leave it at -$300, so exactly one of them is refused."""
    charge = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))
    close_now(db, operator, short, 1, charge)
    closing.reopen(db, actor("admin"), short, schemas.ReopenIn(version=1, reason="Eran $500, no $1.000"))
    _count(db, operator, short, ("ARS_500", 1, accounts["main_cash"]))
    more = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(1000))

    holder = _hold(new_session, short, accounts["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, operator, _close(db, short, 2, more)),
            _in_session(new_session, operator, _cash_transfer(accounts["main_cash"], accounts["change_cash"], "800")),
        ],
    )
    assert _errors(results) == ["insufficient_funds"], results
    balances = {b.code: b.balance for b in ledger.account_balances(db)}
    assert balances["main_cash"] >= 0
    assert balances["main_cash"] in (Decimal("500.00"), Decimal("200.00"))


def test_close_against_a_pending_change_never_publishes_a_stale_pending(
    db, new_session, operator, pedro, accounts
) -> None:  # type: ignore[no-untyped-def]
    """R3-1: a pending's amount changes while the settlement is being closed. Either the change lands first
    and the close (confirming the old preview) is stale, or the close publishes the old amount and the
    change is refused. The snapshot always equals the ledger leg."""
    sid = _settlement(db, operator, pedro, "1500")
    _count(db, operator, sid, ("ARS_1000", 1, accounts["main_cash"]))
    pid = pending_service.create(
        db,
        operator,
        sid,
        ps.PendingIn(client_request_id=uuid4(), customer_name="Kiosco", amount=Decimal(500), reason="Paga mañana"),
    ).id

    def _lower(session: Session, user: User) -> object:
        pending_service.update(session, user, sid, pid, ps.PendingPatch(amount=Decimal(400)))
        return "lowered"

    holder = _hold(new_session, sid)
    results = _race(
        holder,
        [_in_session(new_session, operator, _close(db, sid, 1)), _in_session(new_session, operator, _lower)],
    )
    status = _scalar(db, "SELECT status FROM settlements.settlements WHERE id = :id", id=sid)
    if status == "CLOSED":
        assert _errors(results) == ["settlement_closed"], results
        published = _scalar(
            db, "SELECT amount FROM settlements.settlement_closing_pendings WHERE pending_id = :p", p=pid
        )
        assert published == Decimal("500.00")
    else:
        assert _errors(results) == ["settlement_preview_stale"], results
        assert _scalar(db, "SELECT count(*) FROM settlements.settlement_closing_pendings") == 0
    leg = _scalar(
        db,
        "SELECT coalesce(sum(e.amount), 0) FROM settlements.ledger_entries e "
        "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.code = 'pending_collections'",
    )
    snapshot = _scalar(db, "SELECT coalesce(sum(amount), 0) FROM settlements.settlement_closing_pendings")
    assert leg == snapshot
