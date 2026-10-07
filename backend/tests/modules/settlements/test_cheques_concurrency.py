"""Cheques under concurrency, against PostgreSQL (R4-1): [12] the same instrument loaded at once in two
settlements is registered once, and [40] a correction close racing a change of its cheques ends in a
valid state: never a cheque in the portfolio twice, never a close whose snapshot differs from its legs."""

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
from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.cheques import service as cheques
from app.modules.settlements.delivery import closing, schemas, service
from app.platform.auth.models import User

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


def _bank(db: Session) -> int:
    return db.execute(text("SELECT id FROM financial_institutions WHERE bcra_code = 7")).scalar_one()  # type: ignore[no-any-return]


def _settlement(db: Session, actor: User, employee_id: int, expected: str) -> int:
    body = schemas.SettlementIn(
        client_request_id=uuid4(), business_date=DAY, employee_id=employee_id, expected_amount=Decimal(expected)
    )
    return service.create(db, actor, body).id


def _cheque(bank_id: int, number: str = "12345", amount: str = "50000") -> cs.ChequeIn:
    return cs.ChequeIn(
        client_request_id=uuid4(),
        kind="PHYSICAL",
        payment_type="COMMON",
        amount=Decimal(amount),
        issuer_bank_id=bank_id,
        branch_code="123",
        account_ref="4567890",
        cheque_number=number,
        issue_date=DAY - timedelta(days=3),
        drawer_name="Distribuidora Sur SA",
        delivered_by_name="Almacén Don José",
        physical_receipt_confirmed=True,
    )


def _check_consistent(db: Session) -> None:
    """Each close's snapshot equals its portfolio legs, and the portfolio is the sum of whole cheques."""
    db.rollback()
    mismatched = db.execute(
        text(
            "SELECT count(*) FROM settlements.financial_operations o WHERE o.type = 'SETTLEMENT_CLOSE' AND EXISTS ("
            " (SELECT cheque_id, amount FROM settlements.settlement_closing_cheques WHERE close_operation_id = o.id"
            "  EXCEPT SELECT e.cheque_id, e.amount FROM settlements.ledger_entries e"
            "  JOIN settlements.accounts a ON a.id = e.account_id"
            "  WHERE e.operation_id = o.id AND a.kind = 'CHEQUE_PORTFOLIO'))"
        )
    ).scalar_one()
    assert mismatched == 0
    positions = db.execute(
        text(
            "SELECT e.cheque_id, sum(e.amount) FROM settlements.ledger_entries e "
            "JOIN settlements.accounts a ON a.id = e.account_id WHERE a.kind = 'CHEQUE_PORTFOLIO' "
            "GROUP BY e.cheque_id"
        )
    ).all()
    amounts = dict(db.execute(text("SELECT id, amount FROM settlements.cheques")).all())
    assert all(total in (0, amounts[cid]) for cid, total in positions)


def test_concurrent_loads_of_the_same_cheque_register_it_once(  # [12]
    db: Session, new_session, actor, driver
) -> None:  # type: ignore[no-untyped-def]
    operator = actor("operator")
    bank_id = _bank(db)
    first = _settlement(db, operator, driver("Pedro"), "50000")
    second = _settlement(db, operator, driver("Ana"), "50000")
    holder = new_session()
    holder.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"settlements.cheque:{bank_id}:12345"})

    def _load(sid: int) -> Callable[[Session, User], object]:
        return lambda session, user: cheques.create(session, user, sid, _cheque(bank_id))

    results = _race(holder, [_in_session(new_session, operator, _load(sid)) for sid in (first, second)])
    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1, results
    assert isinstance(errors[0], AppError)
    assert errors[0].code == "cheque_duplicate"
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM settlements.cheques")).scalar_one() == 1


def test_correction_close_racing_a_cheque_change_ends_valid(  # [40]
    db: Session, new_session, actor, driver
) -> None:  # type: ignore[no-untyped-def]
    operator = actor("operator")
    admin = actor("admin")
    bank_id = _bank(db)
    sid = _settlement(db, operator, driver("Pedro"), "50000")
    first = cheques.create(db, operator, sid, _cheque(bank_id)).id
    closing.close(
        db, operator, sid, schemas.CloseIn(version=1, preview_token=closing.preview(db, sid).token, confirm=True)
    )
    closing.reopen(db, admin, sid, schemas.ReopenIn(version=1, reason="Cambiar el cheque"))
    second = cheques.create(db, operator, sid, _cheque(bank_id, "777", "50000")).id
    db.rollback()
    close_body = schemas.CloseIn(
        version=2,
        preview_token=closing.preview(db, sid).token,
        confirm=True,
    )
    holder = new_session()
    holder.execute(text("SELECT 1 FROM settlements.settlements WHERE id = :id FOR UPDATE"), {"id": sid})

    def _close(session: Session, user: User) -> object:
        # The surplus of the second cheque goes to the driver: what the preview showed.
        plan = closing.preview(session, sid).plan
        body = close_body.model_copy(
            update={
                "allocations": [
                    schemas.AllocationIn(
                        target="EMPLOYEE", employee_id=plan.settlement.employee_id, amount=abs(plan.difference)
                    )
                ]
            }
        )
        return closing.close(session, user, sid, body)

    def _void(session: Session, user: User) -> object:
        cheques.void(session, user, sid, first, "Era otro cheque")
        return "voided"

    results = _race(holder, [_in_session(new_session, admin, _close), _in_session(new_session, operator, _void)])
    db.rollback()
    status = db.execute(text("SELECT status FROM settlements.settlements WHERE id = :s"), {"s": sid}).scalar_one()
    voided = db.execute(
        text("SELECT voided_at IS NOT NULL FROM settlements.cheques WHERE id = :c"), {"c": first}
    ).scalar_one()
    if status == "CLOSED":
        # The close won: the void came too late, and the first cheque stays in the portfolio.
        assert isinstance(results[1], AppError), results
        assert results[1].code == "settlement_closed"
        assert voided is False
    else:
        # The void won: the close confirmed a preview that no longer holds.
        assert isinstance(results[0], AppError), results
        assert results[0].code == "settlement_preview_stale"
        assert voided is True
    assert second
    _check_consistent(db)
