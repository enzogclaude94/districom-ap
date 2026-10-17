"""Employee movements under concurrency, against PostgreSQL (R8-2, Enzo's point 14). The main thread holds the row the
workers need; once released, the database must end in one valid state: never a negative cash box nor a deepened
overdraft, one movement and one operation per request, one reversal, one internal account per employee, no deadlock.

Lock order of a movement: the employee row (FOR SHARE), the employee's internal account, then (inside the ledger) the
accounts that lose value in id order. Payments, expenses and transfers lock only the latter, so they never wait in a
cycle with a movement."""

import threading
import time
from collections.abc import Callable, Iterator
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.modules.settlements.cash import schemas as cash_schemas
from app.modules.settlements.cash import use_cases
from app.modules.settlements.employee_finance import movements
from app.modules.settlements.expenses import direct
from app.modules.settlements.expenses import schemas as expense_schemas
from app.platform.auth.models import User
from app.platform.auth.module_roles import grant_module_role
from tests.modules.settlements.days import today

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
        thread.join(timeout=15)
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


def _hold_employee(new_session: Callable[[], Session], employee_id: int) -> Session:
    return _hold(new_session, "SELECT 1 FROM employees WHERE id = :e FOR UPDATE", e=employee_id)


def _hold_account(new_session: Callable[[], Session], account_id: int) -> Session:
    return _hold(new_session, "SELECT 1 FROM settlements.accounts WHERE id = :a FOR NO KEY UPDATE", a=account_id)


def _codes(results: list[object]) -> list[str]:
    return sorted(r.code for r in results if isinstance(r, AppError))


def _unexpected(results: list[object]) -> list[object]:
    return [r for r in results if isinstance(r, Exception) and not isinstance(r, AppError)]


def _scalar(db: Session, sql: str, **params: object) -> Any:
    db.rollback()
    return db.execute(text(sql), params).scalar_one()


def _balance(db: Session, account_id: int) -> Decimal:
    return Decimal(
        _scalar(
            db, "SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a", a=account_id
        )
    )


def _internal(db: Session, employee_id: int) -> Decimal:
    return Decimal(
        _scalar(
            db,
            "SELECT coalesce(sum(e.amount), 0) FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.employee_id = :e",
            e=employee_id,
        )
    )


@pytest.fixture
def world(db: Session, make_user, employee, post, accounts) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """An admin of the area, Ana, $100.000 in Caja principal and $100.000 in the bank."""
    admin = make_user(email="rrhh@districom.test", role="viewer")
    grant_module_role(db, "rrhh@districom.test", "settlements", "operator")
    grant_module_role(db, "rrhh@districom.test", "settlements.employee_finance", "admin")
    db.refresh(admin)
    post("OPENING_BALANCE", (accounts["main_cash"], "100000"), (accounts["opening_balance"], "-100000"))
    post("OPENING_BALANCE", (accounts["bank"], "100000"), (accounts["opening_balance"], "-100000"))
    category = int(
        db.execute(
            text(
                "INSERT INTO settlements.expense_categories (name, name_key, active) "
                "VALUES ('Peajes', 'peajes', true) RETURNING id"
            )
        ).scalar_one()
    )
    db.commit()
    return {"admin": admin, "ana": employee("Ana Pérez"), "category": category, **accounts}


def _cmd(world: dict[str, Any], kind: str, amount: str, **fields: Any) -> movements.Command:
    return movements.command(
        kind,  # type: ignore[arg-type]
        fields.pop("employee_id", world["ana"]),
        fields.pop("on", today()),
        Decimal(amount),
        "Concurrente",
        None,
        **fields,
    )


def _create(
    world: dict[str, Any], cmd: movements.Command, request: UUID | None = None
) -> Callable[[Session, User], object]:
    return lambda session, actor: movements.create(session, actor, cmd, request or uuid4())


def test_two_consumptions_and_two_adjustments_at_once_both_count(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    holder = _hold_employee(new_session, world["ana"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _create(world, _cmd(world, "CONSUMPTION", "1000"))),
            _in_session(new_session, world["admin"], _create(world, _cmd(world, "CONSUMPTION", "2000"))),
            _in_session(
                new_session, world["admin"], _create(world, _cmd(world, "ADJUSTMENT", "300", direction="CHARGE"))
            ),
            _in_session(
                new_session, world["admin"], _create(world, _cmd(world, "ADJUSTMENT", "500", direction="CREDIT"))
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    assert _internal(db, world["ana"]) == Decimal("2800")
    # The first movements of Ana created exactly one internal account.
    assert _scalar(db, "SELECT count(*) FROM settlements.accounts WHERE employee_id = :e", e=world["ana"]) == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_movements") == 4


def test_the_same_consumption_twice_at_once_is_one_movement(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    request = uuid4()
    cmd = _cmd(world, "CONSUMPTION", "1500")
    holder = _hold_employee(new_session, world["ana"])
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _create(world, cmd, request)),
            _in_session(new_session, world["admin"], _create(world, cmd, request)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    created = sorted(r.created for r in results if isinstance(r, movements.Created))
    assert created == [False, True]
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_movements") == 1
    assert _internal(db, world["ana"]) == Decimal("1500")


@pytest.mark.parametrize("box", ["main_cash", "bank"])
def test_two_advances_competing_for_the_same_funds(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session], employee: Callable[[str], int], box: str
) -> None:
    beto = employee("Beto")
    holder = _hold_account(new_session, world[box])
    results = _race(
        holder,
        [
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "70000", funding_account_id=world[box])),
            ),
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "70000", employee_id=beto, funding_account_id=world[box])),
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world[box]) == Decimal("30000")
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_movements") == 1


def test_an_advance_racing_an_expense_from_the_same_box(
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
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "70000", funding_account_id=world["main_cash"])),
            ),
            _in_session(new_session, world["admin"], lambda session, actor: direct.create(session, actor, expense)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["main_cash"]) == Decimal("30000")


def test_an_advance_racing_a_bank_withdrawal(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    withdrawal = cash_schemas.BankWithdrawalIn(
        client_request_id=uuid4(),
        bank_account_id=world["bank"],
        cash_account_id=world["change_cash"],
        amount=Decimal("70000"),
    )
    holder = _hold_account(new_session, world["bank"])
    results = _race(
        holder,
        [
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "70000", funding_account_id=world["bank"])),
            ),
            _in_session(
                new_session,
                world["admin"],
                lambda session, actor: use_cases.bank_withdrawal(session, actor, withdrawal),
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["insufficient_funds"], results
    assert _balance(db, world["bank"]) == Decimal("30000")


def _published(world: dict[str, Any], db: Session, kind: str, amount: str, **fields: Any) -> int:
    session = sessionmaker(bind=db.get_bind(), expire_on_commit=False)()
    try:
        actor = session.merge(world["admin"], load=False)
        return movements.create(session, actor, _cmd(world, kind, amount, **fields), uuid4()).id
    finally:
        session.close()


def test_two_reversals_of_the_same_movement_at_once_are_one(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    advance = _published(world, db, "ADVANCE", "40000", funding_account_id=world["main_cash"])
    holder = _hold(new_session, "SELECT 1 FROM settlements.employee_movements WHERE id = :m FOR UPDATE", m=advance)
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], lambda s, a: movements.reverse(s, a, advance, "Uno", uuid4())),
            _in_session(new_session, world["admin"], lambda s, a: movements.reverse(s, a, advance, "Otro", uuid4())),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["employee_movement_already_reversed"], results
    assert _balance(db, world["main_cash"]) == Decimal("100000")
    assert _internal(db, world["ana"]) == Decimal("0")
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1


def test_a_new_movement_while_another_is_reversed(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    consumption = _published(world, db, "CONSUMPTION", "5000")
    holder = _hold_employee(new_session, world["ana"])
    holder.execute(
        text("SELECT 1 FROM settlements.accounts WHERE employee_id = :e FOR NO KEY UPDATE"), {"e": world["ana"]}
    )
    results = _race(
        holder,
        [
            _in_session(
                new_session, world["admin"], lambda s, a: movements.reverse(s, a, consumption, "Error", uuid4())
            ),
            _in_session(
                new_session,
                world["admin"],
                _create(world, _cmd(world, "ADVANCE", "10000", funding_account_id=world["main_cash"])),
            ),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    assert _internal(db, world["ana"]) == Decimal("10000")
    assert _balance(db, world["main_cash"]) == Decimal("90000")


def test_first_movements_of_many_employees_while_a_payment_waits(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session], employee: Callable[[str], int]
) -> None:
    """Lazy creation of several internal accounts and advances on one box, with a transfer out of it: no deadlock."""
    people = [employee(f"Empleado {i}") for i in range(4)]
    transfer = cash_schemas.CashTransferIn(
        client_request_id=uuid4(),
        from_account_id=world["main_cash"],
        to_account_id=world["change_cash"],
        amount=Decimal("10000"),
    )
    holder = _hold_account(new_session, world["main_cash"])
    workers = [
        _in_session(
            new_session,
            world["admin"],
            _create(world, _cmd(world, "ADVANCE", "20000", employee_id=p, funding_account_id=world["main_cash"])),
        )
        for p in people
    ] + [_in_session(new_session, world["admin"], lambda s, a: use_cases.cash_transfer(s, a, transfer))]
    results = _race(holder, workers)
    assert _unexpected(results) == [], results
    assert _codes(results) == [], results
    assert _balance(db, world["main_cash"]) == Decimal("10000")
    assert _scalar(db, "SELECT count(*) FROM settlements.accounts WHERE kind = 'EMPLOYEE'") == 4
