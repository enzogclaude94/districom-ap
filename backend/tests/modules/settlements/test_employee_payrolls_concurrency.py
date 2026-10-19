"""Internal payrolls under concurrency, against PostgreSQL (R8-4, Enzo's point 17). The main thread holds the internal
account the workers need; once released, the database must end in one valid state: one close per version, one
REGULAR payroll per period, never a leg applied beyond itself, nothing applied that was undone meanwhile, no deadlock.

Lock order of every R8 flow, starting with the internal account: (the employee FOR SHARE when a document is created)
the internal account, the payroll, the loans of its installments (FOR SHARE), the document a flow undoes (a movement,
a loan, an installment), then the ledger's. A settlement's correction (R2/R7) locks its settlement and the accounts
the ledger moves; it is never blocked by a payroll, which then sees a changed preview."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.modules.settlements.delivery import closing as settlement_closing
from app.modules.settlements.delivery import schemas as settlement_schemas
from app.modules.settlements.employee_finance import loans, movements, payrolls
from app.modules.settlements.employee_finance.models import EmployeePayroll
from app.platform.auth.models import User
from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.test_employee_movements_concurrency import (
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
from tests.modules.settlements.test_settlement_close_concurrency import _count, _settlement, close_now

__all__ = ["new_session", "world"]


def _in_session(
    new_session: Callable[[], Session], actor: User, work: Callable[[Session, User], object]
) -> Callable[[], object]:
    """A worker as a request: its session ends (rolled back on an error), so a loser never keeps its locks."""

    def _work() -> object:
        session = new_session()
        try:
            return work(session, session.merge(actor, load=False))
        except Exception:
            session.rollback()
            raise

    return _work


def _session(db: Session) -> Session:
    return sessionmaker(bind=db.get_bind(), expire_on_commit=False)()


def _do(db: Session, actor: User, work: Callable[[Session, User], Any]) -> Any:
    """Run a step in its own committed session (as a request would)."""
    session = _session(db)
    try:
        return work(session, session.merge(actor, load=False))
    finally:
        session.close()


def _open(
    db: Session, w: dict[str, Any], kind: str = "REGULAR", employee_id: int | None = None, salary: str = "500000"
) -> int:
    def _work(session: Session, actor: User) -> int:
        pid = payrolls.create(
            session,
            actor,
            employee_id or w["ana"],
            kind,
            days_ago(30),
            today(),
            None,
            None,
            uuid4(),  # type: ignore[arg-type]
        ).id
        if Decimal(salary) > 0:
            payrolls.add_line(session, actor, pid, "BONUS", Decimal(salary), None)
        return pid

    return int(_do(db, w["admin"], _work))


def _token(db: Session, payroll_id: int) -> tuple[int, str]:
    session = _session(db)
    try:
        payroll = session.get(EmployeePayroll, payroll_id)
        assert payroll is not None
        return payroll.version, payrolls.preview_token(session, payrolls.draft(session, payroll))
    finally:
        session.close()


def _close(db: Session, payroll_id: int) -> Callable[[Session, User], object]:
    """A close confirming the preview taken now, before the race starts."""
    version, token = _token(db, payroll_id)
    return lambda session, actor: payrolls.close(session, actor, payroll_id, version, token, True)


def _entry_of(db: Session, w: dict[str, Any], operation_id: int) -> int:
    return int(
        _scalar(
            db,
            "SELECT e.id FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id "
            "WHERE e.operation_id = :o AND a.kind = 'EMPLOYEE'",
            o=operation_id,
        )
    )


def _account(db: Session, employee_id: int) -> int:
    return int(
        _scalar(db, "SELECT id FROM settlements.accounts WHERE kind = 'EMPLOYEE' AND employee_id = :e", e=employee_id)
    )


def _consumption(db: Session, w: dict[str, Any], amount: str = "20000") -> Any:
    return _do(db, w["admin"], _create(w, _cmd(w, "CONSUMPTION", amount, on=days_ago(5))))


def _applied_beyond(db: Session) -> int:
    return int(
        _scalar(
            db,
            "SELECT count(*) FROM (SELECT e.id FROM settlements.employee_payroll_applications a "
            "JOIN settlements.ledger_entries e ON e.id = a.ledger_entry_id WHERE a.superseded_at IS NULL "
            "GROUP BY e.id, e.amount HAVING sum(abs(a.amount)) > abs(e.amount)) x",
        )
    )


def test_two_closes_of_the_same_payroll_are_one(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    _consumption(db, world)
    pid = _open(db, world)
    holder = _hold_account(new_session, _account(db, world["ana"]))
    results = _race(holder, [_in_session(new_session, world["admin"], _close(db, pid)) for _ in range(2)])
    assert _unexpected(results) == [], results
    assert sorted(r.created for r in results if isinstance(r, payrolls.Created)) == [False, True], results
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_payroll_closings") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'PAYROLL_CLOSE'") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_payroll_applications") == 1


def test_two_regular_payrolls_of_one_period_at_once(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    _consumption(db, world)  # the account exists
    holder = _hold_account(new_session, _account(db, world["ana"]))

    def _create_one(session: Session, actor: User) -> object:
        return payrolls.create(session, actor, world["ana"], "REGULAR", days_ago(30), today(), None, None, uuid4())

    results = _race(holder, [_in_session(new_session, world["admin"], _create_one) for _ in range(2)])
    assert _unexpected(results) == [], results
    assert _codes(results) == ["regular_payroll_overlap"], results
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_payrolls") == 1


def test_the_same_leg_from_two_payrolls(db: Session, world: dict[str, Any], new_session: Callable[[], Session]) -> None:
    consumption = _consumption(db, world)
    entry = _entry_of(db, world, _scalar(db, "SELECT operation_id FROM settlements.employee_movements"))
    first, second = _open(db, world, "EXTRAORDINARY"), _open(db, world, "EXTRAORDINARY")
    for pid in (first, second):
        _do(
            db,
            world["admin"],
            lambda s, a, p=pid: payrolls.choose(
                s, a, p, ledger_entry_id=entry, installment_id=None, included=True, amount=None
            ),
        )
    holder = _hold_account(new_session, _account(db, world["ana"]))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _close(db, first)),
            _in_session(new_session, world["admin"], _close(db, second)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["payroll_preview_stale"], results
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_payroll_applications") == 1
    assert _applied_beyond(db) == 0
    assert consumption.created


def test_concurrent_partial_applications_never_exceed_the_leg(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    _consumption(db, world)
    entry = _entry_of(db, world, _scalar(db, "SELECT operation_id FROM settlements.employee_movements"))
    first, second = _open(db, world, "EXTRAORDINARY"), _open(db, world, "EXTRAORDINARY")
    for pid in (first, second):
        _do(
            db,
            world["admin"],
            lambda s, a, p=pid: payrolls.choose(
                s, a, p, ledger_entry_id=entry, installment_id=None, included=True, amount=Decimal("12000")
            ),
        )
    holder = _hold_account(new_session, _account(db, world["ana"]))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _close(db, first)),
            _in_session(new_session, world["admin"], _close(db, second)),
        ],
    )
    assert _unexpected(results) == [], results
    assert _codes(results) == ["payroll_preview_stale"], results
    assert _applied_beyond(db) == 0
    assert _scalar(db, "SELECT sum(amount) FROM settlements.employee_payroll_applications") == Decimal("12000")


def _close_against(
    db: Session,
    w: dict[str, Any],
    new_session: Callable[[], Session],
    pid: int,
    other: Callable[[Session, User], object],
) -> list[object]:
    holder = _hold_account(new_session, _account(db, w["ana"]))
    results = _race(
        holder, [_in_session(new_session, w["admin"], _close(db, pid)), _in_session(new_session, w["admin"], other)]
    )
    assert _unexpected(results) == [], results
    assert _applied_beyond(db) == 0
    return results


def test_close_against_a_new_consumption_advance_or_adjustment(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    """Whoever goes first, the close never applies what it did not preview: it is stale or it never saw it."""
    _consumption(db, world)
    for kind, fields in (
        ("CONSUMPTION", {}),
        ("ADVANCE", {"funding_account_id": world["main_cash"], "on": today()}),
        ("ADJUSTMENT", {"direction": "CHARGE"}),
    ):
        pid = _open(db, world, "EXTRAORDINARY", salary="100000")
        _do(
            db,
            world["admin"],
            lambda s, a, p=pid: payrolls.choose(
                s,
                a,
                p,
                ledger_entry_id=_entry_of(
                    db,
                    world,
                    _scalar(db, "SELECT operation_id FROM settlements.employee_movements ORDER BY id LIMIT 1"),
                ),
                installment_id=None,
                included=False,
                amount=None,
            ),
        )
        cmd = _cmd(world, kind, "1000", **({"on": days_ago(1)} | fields))
        results = _close_against(db, world, new_session, pid, _create(world, cmd))
        assert set(_codes(results)) <= {"payroll_preview_stale"}, results
        closes = [r for r in results if isinstance(r, payrolls.Created)]
        assert len(closes) == 1 - len(_codes(results))


def test_close_against_a_deferral_of_an_included_installment(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    loan = _do(
        db,
        world["admin"],
        lambda s, a: loans.create(
            s,
            a,
            loans.command(world["ana"], today(), Decimal("40000"), world["main_cash"], 2, today(), None, "P", None),
            uuid4(),
        ),
    )
    first = int(
        _scalar(
            db, "SELECT id FROM settlements.employee_loan_installments WHERE loan_id = :l AND number = 1", l=loan.id
        )
    )
    pid = _open(db, world)

    def _defer(session: Session, actor: User) -> object:
        return loans.defer(
            session, actor, first, "Pidió", [loans.Installment(today() + timedelta(days=40), Decimal("20000"))], uuid4()
        )

    results = _close_against(db, world, new_session, pid, _defer)
    codes = _codes(results)
    assert len(codes) == 1, results
    assert codes[0] in {"payroll_preview_stale", "installment_applied"}, results
    applied = _scalar(
        db, "SELECT count(*) FROM settlements.employee_payroll_applications WHERE installment_id = :i", i=first
    )
    status = _scalar(db, "SELECT status FROM settlements.employee_loan_installments WHERE id = :i", i=first)
    assert (applied, status) in {(1, "SCHEDULED"), (0, "DEFERRED")}


def test_close_against_reversing_an_included_movement(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    consumption = _consumption(db, world)
    pid = _open(db, world)

    def _reverse(session: Session, actor: User) -> object:
        return movements.reverse(session, actor, consumption.id, "Cargado por error", uuid4())

    results = _close_against(db, world, new_session, pid, _reverse)
    codes = _codes(results)
    assert len(codes) == 1, results
    assert codes[0] in {"payroll_preview_stale", "movement_applied"}, results


def test_close_against_a_settlement_correction(
    db: Session,
    world: dict[str, Any],
    new_session: Callable[[], Session],
    actor: Callable[[str], User],
    driver: Callable[..., int],
) -> None:
    """A settlement's correction is never blocked: the payroll either applied the original (the reversal is for the
    next payroll) or sees a changed preview."""
    pedro = driver("Pedro Gómez")
    operator = actor("operator")
    sid = _settlement(db, operator, pedro, "1500")
    _count(db, operator, sid, ("ARS_1000", 1, world["main_cash"]))
    charge = settlement_schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))
    close_now(db, operator, sid, 1, charge)
    settlement_closing.reopen(db, actor("admin"), sid, settlement_schemas.ReopenIn(version=1, reason="Recontado"))
    _count(db, operator, sid, ("ARS_1000", 1, world["main_cash"]), ("ARS_500", 1, world["main_cash"]))
    pid = _open(db, world, employee_id=pedro)
    db.rollback()
    body = settlement_schemas.CloseIn(
        version=2, preview_token=settlement_closing.preview(db, sid).token, allocations=[], confirm=True
    )
    holder = _hold_account(new_session, _account(db, pedro))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _close(db, pid)),
            _in_session(new_session, operator, lambda s, u: settlement_closing.close(s, u, sid, body)),
        ],
    )
    assert _unexpected(results) == [], results
    assert set(_codes(results)) <= {"payroll_preview_stale"}, results
    assert _scalar(db, "SELECT version FROM settlements.settlements WHERE id = :s", s=sid) == 2
    assert _scalar(db, "SELECT status FROM settlements.settlements WHERE id = :s", s=sid) == "CLOSED"
    assert _applied_beyond(db) == 0


def test_reopen_against_another_reopen_or_close(
    db: Session, world: dict[str, Any], new_session: Callable[[], Session]
) -> None:
    _consumption(db, world)
    pid = _open(db, world)
    retry = _close(db, pid)
    _do(db, world["admin"], retry)

    def _reopen(reason: str) -> Callable[[Session, User], object]:
        return lambda session, actor: payrolls.reopen(session, actor, pid, 1, reason)

    holder = _hold_account(new_session, _account(db, world["ana"]))
    results = _race(
        holder,
        [
            _in_session(new_session, world["admin"], _reopen("Motivo A")),
            _in_session(new_session, world["admin"], _reopen("Motivo B")),
            _in_session(new_session, world["admin"], retry),
        ],
    )
    assert _unexpected(results) == [], results
    # One reopening; the other is refused, and the retried close is either the same close or a stale version.
    assert [c for c in _codes(results) if c != "payroll_version_changed"] == ["payroll_not_closed"], results
    assert _scalar(db, "SELECT version FROM settlements.employee_payrolls WHERE id = :p", p=pid) == 2
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_payroll_reopenings") == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.employee_payroll_closings") == 1
    assert _internal(db, world["ana"]) == Decimal("-480000")
    assert isinstance(results[0], (payrolls.Created, AppError))
