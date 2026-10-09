"""Direct expenses under concurrency, against PostgreSQL (R5-3). The main thread holds the row the workers
need; once released, the database must end in one valid state: never a negative cash box, never an
overdraft deepened by an expense, one reversal, one expense and one operation per request, no deadlock."""

import threading
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.cash import schemas as cash_schemas
from app.modules.settlements.cash import use_cases
from app.modules.settlements.cheques import cycle
from app.modules.settlements.cheques import schemas as cheque_schemas
from app.modules.settlements.expenses import direct
from app.modules.settlements.expenses import schemas as s
from app.platform.auth.models import User
from tests.conftest import PASSWORD
from tests.modules.settlements.test_cheque_overdraft import credited_cheque

T = business_today()
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


def _hold(new_session: Callable[[], Session], sql: str, **params: object) -> Session:
    holder = new_session()
    holder.execute(text(sql), params)
    return holder


def _codes(results: list[object]) -> list[str]:
    return sorted(r.code for r in results if isinstance(r, AppError))


def _scalar(db: Session, sql: str, **params: object) -> Any:
    db.rollback()
    return db.execute(text(sql), params).scalar_one()


@pytest.fixture
def world(db: Session, actor, accounts, post) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Caja principal with $100.000 and an ordinary category."""
    post("OPENING_BALANCE", (accounts["main_cash"], "100000"), (accounts["opening_balance"], "-100000"))
    category = int(
        db.execute(
            text(
                "INSERT INTO settlements.expense_categories (name, name_key, active) "
                "VALUES ('Peajes', 'peajes', true) RETURNING id"
            )
        ).scalar_one()
    )
    db.commit()
    return {"operator": actor("operator"), "admin": actor("admin"), "category": category, **accounts}


def _expense(world: dict[str, Any], amount: str, account: str = "main_cash", request: Any = None) -> s.DirectExpenseIn:
    return s.DirectExpenseIn(
        client_request_id=request or uuid4(),
        business_date=T,
        category_id=world["category"],
        paid_from_account_id=world[account],
        amount=Decimal(amount),
    )


def _lock_account(new_session: Callable[[], Session], account_id: int) -> Session:
    return _hold(new_session, "SELECT 1 FROM settlements.accounts WHERE id = :a FOR NO KEY UPDATE", a=account_id)


def _cash_balance(db: Session, world: dict[str, Any]) -> Decimal:
    return Decimal(
        _scalar(
            db,
            "SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a",
            a=world["main_cash"],
        )
    )


def test_an_expense_racing_a_withdrawal_never_overdraws_the_box(db: Session, world, new_session) -> None:  # type: ignore[no-untyped-def]
    """$100.000 in the box; an expense of $70.000 and a transfer out of $70.000 at once: one wins."""
    holder = _lock_account(new_session, world["main_cash"])
    withdrawal = cash_schemas.CashTransferIn(
        client_request_id=uuid4(),
        from_account_id=world["main_cash"],
        to_account_id=world["change_cash"],
        amount=Decimal("70000"),
    )
    results = _race(
        holder,
        [
            _in_session(new_session, world["operator"], lambda ss, u: direct.create(ss, u, _expense(world, "70000"))),
            _in_session(new_session, world["operator"], lambda ss, u: use_cases.cash_transfer(ss, u, withdrawal)),
        ],
    )
    assert _codes(results) == ["insufficient_funds"], results
    assert _cash_balance(db, world) == Decimal("30000.00")
    in_effect = _scalar(db, "SELECT count(*) FROM settlements.expenses WHERE origin = 'DIRECT'")
    assert in_effect == (0 if isinstance(results[0], AppError) else 1)


def test_two_expenses_on_funds_for_one(db: Session, world, new_session) -> None:  # type: ignore[no-untyped-def]
    holder = _lock_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["operator"], lambda ss, u: direct.create(ss, u, _expense(world, "70000"))),
            _in_session(new_session, world["operator"], lambda ss, u: direct.create(ss, u, _expense(world, "70000"))),
        ],
    )
    assert _codes(results) == ["insufficient_funds"], results
    assert _cash_balance(db, world) == Decimal("30000.00")
    assert _scalar(db, "SELECT count(*) FROM settlements.expenses") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'DIRECT_EXPENSE'") == 1


def test_the_same_request_twice_at_once_records_one_expense(db: Session, world, new_session) -> None:  # type: ignore[no-untyped-def]
    request = uuid4()
    holder = _lock_account(new_session, world["main_cash"])
    results = _race(
        holder,
        [
            _in_session(
                new_session,
                world["operator"],
                lambda ss, u: direct.create(ss, u, _expense(world, "1000", request=request)),
            ),
            _in_session(
                new_session,
                world["operator"],
                lambda ss, u: direct.create(ss, u, _expense(world, "1000", request=request)),
            ),
        ],
    )
    assert not _codes(results), results
    created = [r for r in results if isinstance(r, direct.Created)]
    assert {r.id for r in created} == {created[0].id}
    assert sorted(r.created for r in created) == [False, True]
    assert _scalar(db, "SELECT count(*) FROM settlements.expenses") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'DIRECT_EXPENSE'") == 1
    assert _cash_balance(db, world) == Decimal("99000.00")


def test_two_reversals_at_once_reverse_once(db: Session, world, new_session) -> None:  # type: ignore[no-untyped-def]
    eid = direct.create(db, world["operator"], _expense(world, "5000")).id
    holder = _hold(new_session, "SELECT 1 FROM settlements.expenses WHERE id = :e FOR UPDATE", e=eid)

    def reversal(ss: Session, u: User) -> object:
        return direct.reverse(ss, u, eid, s.ExpenseReversalIn(client_request_id=uuid4(), reason="Duplicado"))

    results = _race(
        holder,
        [_in_session(new_session, world["admin"], reversal), _in_session(new_session, world["admin"], reversal)],
    )
    assert _codes(results) == ["expense_already_reversed"], results
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1
    assert _cash_balance(db, world) == Decimal("100000.00")


def test_an_expense_racing_a_rejection_never_deepens_the_overdraft(  # type: ignore[no-untyped-def]
    db: Session, world, new_session, client, driver
) -> None:
    """The bank holds a credited cheque of $100.000 and nothing else. A rejection of that cheque (which may
    overdraw it) and a $20.000 expense from the bank at once: if the rejection is first, the expense is
    refused (the bank is at $0); if the expense is first, the bank had the money and the rejection then
    overdraws it to -$20.000. Never an expense booked on an overdrawn bank."""
    admin = world["admin"]
    client.post("/api/v1/auth/login", json={"email": admin.email, "password": PASSWORD})
    cheque = credited_cheque(client)
    ana = driver("Ana Cobranzas")
    holder = _lock_account(new_session, world["bank"])
    rejection = cheque_schemas.RejectionIn(
        client_request_id=uuid4(), reason="Sin fondos", business_date=T, responsible_employee_id=ana
    )
    results = _race(
        holder,
        [
            _in_session(new_session, admin, lambda ss, u: direct.create(ss, u, _expense(world, "20000", "bank"))),
            _in_session(new_session, admin, lambda ss, u: cycle.reject(ss, u, cheque, rejection)),
        ],
    )
    spent, rejected = results
    assert not isinstance(rejected, Exception), rejected
    bank = Decimal(
        _scalar(
            db, "SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a", a=world["bank"]
        )
    )
    if isinstance(spent, AppError):
        assert spent.code == "insufficient_funds"
        assert bank == Decimal("0.00")
    else:
        # The expense was booked first, on a bank that had the money; then the external debit.
        expense_op, rejection_op = (
            _scalar(db, "SELECT id FROM settlements.financial_operations WHERE type = :t", t=t)
            for t in ("DIRECT_EXPENSE", "CHEQUE_REJECTION")
        )
        assert expense_op < rejection_op
        assert bank == Decimal("-20000.00")
