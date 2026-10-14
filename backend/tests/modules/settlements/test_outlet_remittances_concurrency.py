"""Remittances of a boca under concurrency, against PostgreSQL (R7-3, Enzo's point 17).

Lock order, the same in every writer, so two of them never wait on each other in a cycle:
settlement → its pendings → its cheques → its remittances (by id) → the boca's «Entregas a cuenta»
(FOR NO KEY UPDATE) → the cash boxes the ledger moves (by id). A remittance takes settlement → account →
cash box; a reversal settlement → remittance → account → cash box; a close all of them. Two settlements of
the same boca only meet at the boca's account, which each takes once and after its own settlement.

The main thread holds the lock the workers need, every worker blocks on it, and once it is released the
database must end in a valid state: the boca's balance explained by its remittances, no deadlock.
"""

from collections.abc import Callable, Iterator
from datetime import datetime, time
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.modules.settlements.delivery import closing, service
from app.modules.settlements.delivery import schemas as ds
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.outlet_settlements import remittances
from app.modules.settlements.outlet_settlements import schemas as os
from app.modules.settlements.outlet_settlements import service as outlet_settlements
from app.platform.auth.models import User
from app.platform.outlets.models import Outlet
from tests.modules.settlements.days import yesterday
from tests.modules.settlements.remittance_support import BA
from tests.modules.settlements.test_settlement_close_concurrency import (
    _errors,
    _hold,
    _race,
    _scalar,
    _token,
)


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


def _in_session(
    new_session: Callable[[], Session], actor: User, work: Callable[[Session, User], object]
) -> Callable[[], object]:
    """Run `work` in its own session; on error, roll back as a request would, releasing its locks."""

    def _work() -> object:
        session = new_session()
        try:
            return work(session, session.merge(actor, load=False))
        except Exception:
            session.rollback()
            raise

    return _work


@pytest.fixture
def operator(actor) -> User:  # type: ignore[no-untyped-def]
    return actor("operator")  # type: ignore[no-any-return]


@pytest.fixture
def admin(actor) -> User:  # type: ignore[no-untyped-def]
    return actor("admin")  # type: ignore[no-any-return]


@pytest.fixture
def centro(db: Session, admin: User) -> int:
    outlet = Outlet(code="CENTRO", name="Boca Centro", name_key="boca centro", created_by=admin.id, updated_by=admin.id)
    db.add(outlet)
    db.commit()
    return outlet.id


def _settlement(db: Session, actor: User, outlet_id: int, expected: str) -> int:
    body = os.OutletSettlementIn(
        client_request_id=uuid4(),
        business_date=yesterday(),
        outlet_id=outlet_id,
        expected_amount=Decimal(expected),
        confirm_additional=True,
    )
    return outlet_settlements.create(db, actor, body).id


def _body(amount: str, box: int, request_id: UUID | None = None) -> os.RemittanceIn:
    return os.RemittanceIn(
        client_request_id=request_id or uuid4(),
        amount=Decimal(amount),
        destination_account_id=box,
        delivered_at=datetime.combine(yesterday(), time(13), BA),
        confirm_above_expected=True,
    )


def _remit_now(db: Session, actor: User, sid: int, amount: str, box: int) -> int:
    return remittances.create(db, actor, sid, _body(amount, box)).id


def _remit(sid: int, amount: str, box: int, request_id: UUID | None = None) -> Callable[[Session, User], object]:
    body = _body(amount, box, request_id)
    return lambda session, user: remittances.create(session, user, sid, body)


def _reverse(sid: int, rid: int, request_id: UUID | None = None) -> Callable[[Session, User], object]:
    body = os.RemittanceReversalIn(client_request_id=request_id or uuid4(), reason="Se cargó mal")
    return lambda session, user: remittances.reverse(session, user, sid, rid, body)


def _count(db: Session, actor: User, sid: int, amount: int, box: int) -> None:
    """Count `amount` in $1.000 notes (a multiple of 1000)."""
    lines = [ds.CountLineIn(denomination_code="ARS_1000", quantity=amount // 1000, destination_account_id=box)]
    service.replace_count(db, actor, sid, ds.CountIn(lines=lines))


def _close(db: Session, sid: int, version: int = 1) -> Callable[[Session, User], object]:
    body = ds.CloseIn(version=version, preview_token=_token(db, sid), allocations=[], confirm=True)
    return lambda session, user: closing.close(session, user, sid, body)


def _close_now(db: Session, actor: User, sid: int, version: int = 1) -> None:
    _close(db, sid, version)(db, actor)


def _advances(db: Session, outlet_id: int) -> Decimal:
    """The boca's balance, after checking the rule per boca holds on what was committed."""
    db.rollback()
    db.execute(text("SELECT settlements.assert_outlet_advances(:o)"), {"o": outlet_id})
    balance = db.execute(
        text(
            "SELECT coalesce(sum(e.amount), 0) FROM settlements.accounts a "
            "LEFT JOIN settlements.ledger_entries e ON e.account_id = a.id WHERE a.outlet_id = :o"
        ),
        {"o": outlet_id},
    ).scalar_one()
    db.rollback()
    return Decimal(balance)


def _box(db: Session, account_id: int) -> Decimal:
    value = _scalar(
        db, "SELECT coalesce(sum(amount), 0) FROM settlements.ledger_entries WHERE account_id = :a", a=account_id
    )
    return Decimal(str(value))


def _advances_id(db: Session, outlet_id: int) -> int:
    return int(_scalar(db, "SELECT id FROM settlements.accounts WHERE outlet_id = :o", o=outlet_id))  # type: ignore[call-overload]


def _transfer(source: int, target: int, amount: str) -> Callable[[Session, User], object]:
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


def _live(db: Session, sid: int) -> int:
    return int(
        _scalar(  # type: ignore[call-overload]
            db,
            "SELECT count(*) FROM settlements.outlet_remittances WHERE settlement_id = :s "
            "AND reversal_operation_id IS NULL",
            s=sid,
        )
    )


def test_two_different_remittances_at_once_both_land(db, new_session, operator, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    sid = _settlement(db, operator, centro, "1000000")
    holder = _hold(new_session, sid)
    results = _race(
        holder,
        [
            _in_session(new_session, operator, _remit(sid, "400000", accounts["main_cash"])),
            _in_session(new_session, operator, _remit(sid, "300000", accounts["main_cash"])),
        ],
    )
    assert all(isinstance(r, service.Created) and r.created for r in results), results
    assert _advances(db, centro) == Decimal("-700000.00")
    assert _box(db, accounts["main_cash"]) == Decimal("700000.00")


def test_the_same_request_at_once_records_one(db, new_session, operator, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    sid = _settlement(db, operator, centro, "1000000")
    request_id = uuid4()
    holder = _hold(new_session, sid)
    results = _race(
        holder,
        [
            _in_session(new_session, operator, _remit(sid, "400000", accounts["main_cash"], request_id))
            for _ in range(2)
        ],
    )
    assert sorted(r.created for r in results if isinstance(r, service.Created)) == [False, True], results
    assert _live(db, sid) == 1
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'OUTLET_REMITTANCE'") == 1
    assert _advances(db, centro) == Decimal("-400000.00")


def test_remittance_against_close_never_closes_stale_totals(db, new_session, operator, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    """Either the remittance lands first and the close (confirming the old preview) is stale, or the close lands
    first and the remittance finds the settlement closed. Never a close that ignores a remittance."""
    sid = _settlement(db, operator, centro, "1000000")
    _remit_now(db, operator, sid, "400000", accounts["main_cash"])
    _count(db, operator, sid, 600000, accounts["main_cash"])
    holder = _hold(new_session, sid)
    results = _race(
        holder,
        [
            _in_session(new_session, operator, _close(db, sid)),
            _in_session(new_session, operator, _remit(sid, "100000", accounts["main_cash"])),
        ],
    )
    status = _scalar(db, "SELECT status FROM settlements.settlements WHERE id = :s", s=sid)
    if status == "CLOSED":
        assert _errors(results) == ["settlement_closed"], results
        assert _advances(db, centro) == Decimal("0.00")
    else:
        assert _errors(results) == ["settlement_preview_stale"], results
        assert _advances(db, centro) == Decimal("-500000.00")


def test_reversal_against_close_never_closes_stale_totals(db, new_session, operator, admin, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    sid = _settlement(db, operator, centro, "1000000")
    rid = _remit_now(db, operator, sid, "400000", accounts["main_cash"])
    _count(db, operator, sid, 600000, accounts["main_cash"])
    close = _close(db, sid)
    holder = _hold(new_session, sid)
    results = _race(
        holder,
        [_in_session(new_session, operator, close), _in_session(new_session, admin, _reverse(sid, rid))],
    )
    status = _scalar(db, "SELECT status FROM settlements.settlements WHERE id = :s", s=sid)
    if status == "CLOSED":
        assert _errors(results) == ["settlement_closed"], results
        assert _live(db, sid) == 1
    else:
        assert _errors(results) == ["settlement_preview_stale"], results
        assert _live(db, sid) == 0
    assert _advances(db, centro) == Decimal("0.00")


def test_two_reversals_at_once_reverse_once(db, new_session, operator, admin, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    sid = _settlement(db, operator, centro, "1000000")
    rid = _remit_now(db, operator, sid, "400000", accounts["main_cash"])
    holder = _hold(new_session, sid)
    results = _race(
        holder,
        [_in_session(new_session, admin, _reverse(sid, rid)) for _ in range(2)],
    )
    assert _errors(results) == ["remittance_already_reversed"], results
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1
    assert _advances(db, centro) == Decimal("0.00")
    assert _box(db, accounts["main_cash"]) == Decimal("0.00")


def test_the_same_reversal_at_once_answers_the_same(db, new_session, operator, admin, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    sid = _settlement(db, operator, centro, "1000000")
    rid = _remit_now(db, operator, sid, "400000", accounts["main_cash"])
    request_id = uuid4()
    holder = _hold(new_session, sid)
    results = _race(holder, [_in_session(new_session, admin, _reverse(sid, rid, request_id)) for _ in range(2)])
    assert results == [rid, rid], results
    assert _scalar(db, "SELECT count(*) FROM settlements.financial_operations WHERE type = 'REVERSAL'") == 1


def test_reversal_against_an_outflow_of_the_cash_box_never_leaves_it_negative(
    db, new_session, operator, admin, centro, accounts
) -> None:  # type: ignore[no-untyped-def]
    """The $400.000 the boca handed over are moved to Caja de cambio while an admin reverses the remittance:
    only one of them can take that money out of Caja principal."""
    sid = _settlement(db, operator, centro, "1000000")
    rid = _remit_now(db, operator, sid, "400000", accounts["main_cash"])
    holder = _hold(new_session, sid, accounts["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, admin, _reverse(sid, rid)),
            _in_session(new_session, operator, _transfer(accounts["main_cash"], accounts["change_cash"], "400000")),
        ],
    )
    assert _errors(results) == ["insufficient_funds"], results
    assert _box(db, accounts["main_cash"]) == Decimal("0.00")
    reversed_ = _live(db, sid) == 0
    assert _box(db, accounts["change_cash"]) == (Decimal("0.00") if reversed_ else Decimal("400000.00"))
    assert _advances(db, centro) == (Decimal("0.00") if reversed_ else Decimal("-400000.00"))


def test_remittance_against_an_outflow_of_the_cash_box(db, new_session, operator, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    """Caja principal is empty: a transfer out of it only goes through if the remittance landed first."""
    sid = _settlement(db, operator, centro, "1000000")
    _remit_now(db, operator, sid, "1", accounts["main_cash"])  # the boca's account exists before the race
    holder = _hold(new_session, sid, accounts["main_cash"])
    results = _race(
        holder,
        [
            _in_session(new_session, operator, _remit(sid, "400000", accounts["main_cash"])),
            _in_session(new_session, operator, _transfer(accounts["main_cash"], accounts["change_cash"], "400000")),
        ],
    )
    assert isinstance(results[0], service.Created), results
    moved = not isinstance(results[1], AppError)
    assert moved or results[1].code == "insufficient_funds", results  # type: ignore[union-attr]
    assert _box(db, accounts["main_cash"]) == (Decimal("1.00") if moved else Decimal("400001.00"))
    assert _advances(db, centro) == Decimal("-400001.00")


def test_remittance_against_deactivation_of_the_cash_box(db, new_session, operator, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    """The cash box is deactivated while a remittance to it waits: the remittance is refused, nothing is left
    half written (the database checks the box under its lock, 0024)."""
    sid = _settlement(db, operator, centro, "1000000")
    holder = new_session()
    holder.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :a"), {"a": accounts["change_cash"]})
    results = _race(holder, [_in_session(new_session, operator, _remit(sid, "1000", accounts["change_cash"]))])
    assert isinstance(results[0], AppError | DBAPIError), results
    if isinstance(results[0], AppError):
        assert results[0].code in ("invalid_outlet_remittance", "account_inactive", "invalid_remittance_destination")
    assert _live(db, sid) == 0
    assert _box(db, accounts["change_cash"]) == Decimal("0.00")


def test_settlement_1_closes_while_settlement_2_receives_remittances(
    db, new_session, operator, centro, accounts
) -> None:  # type: ignore[no-untyped-def]
    one = _settlement(db, operator, centro, "1000000")
    two = _settlement(db, operator, centro, "500000")
    _remit_now(db, operator, one, "700000", accounts["main_cash"])
    _remit_now(db, operator, two, "100000", accounts["main_cash"])
    _count(db, operator, one, 300000, accounts["main_cash"])
    close = _close(db, one)
    holder = _hold(new_session, one, _advances_id(db, centro))
    results = _race(
        holder,
        [
            _in_session(new_session, operator, close),
            _in_session(new_session, operator, _remit(two, "200000", accounts["main_cash"])),
        ],
    )
    assert _errors(results) == [], results
    # Settlement 1 consolidated its 700k; settlement 2's 300k stay handed over and not consolidated.
    assert _advances(db, centro) == Decimal("-300000.00")


def test_two_settlements_of_the_same_boca_close_at_once(db, new_session, operator, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    one = _settlement(db, operator, centro, "1000000")
    two = _settlement(db, operator, centro, "500000")
    _remit_now(db, operator, one, "700000", accounts["main_cash"])
    _remit_now(db, operator, two, "100000", accounts["main_cash"])
    _count(db, operator, one, 300000, accounts["main_cash"])
    _count(db, operator, two, 400000, accounts["main_cash"])
    closes = [_close(db, one), _close(db, two)]
    holder = _hold(new_session, one, _advances_id(db, centro))
    results = _race(holder, [_in_session(new_session, operator, close) for close in closes])
    assert _errors(results) == [], results
    assert _advances(db, centro) == Decimal("0.00")
    assert _box(db, accounts["main_cash"]) == Decimal("1500000.00")


def test_correcting_one_while_the_other_stays_open(db, new_session, operator, admin, centro, accounts) -> None:  # type: ignore[no-untyped-def]
    """Rendición 1 (700k, closed) is reopened and its 350k remittance reversed while Rendición 2 receives a
    remittance; Rendición 1 then records the correct 300k and closes again."""
    one = _settlement(db, operator, centro, "1000000")
    two = _settlement(db, operator, centro, "500000")
    _remit_now(db, operator, one, "350000", accounts["main_cash"])
    wrong = _remit_now(db, operator, one, "350000", accounts["main_cash"])
    _count(db, operator, one, 300000, accounts["main_cash"])
    _remit_now(db, operator, two, "100000", accounts["main_cash"])
    _close_now(db, operator, one)
    closing.reopen(db, admin, one, ds.ReopenIn(version=1, reason="La segunda entrega era de $300.000"))
    holder = _hold(new_session, one, _advances_id(db, centro))
    results = _race(
        holder,
        [
            _in_session(new_session, admin, _reverse(one, wrong)),
            _in_session(new_session, operator, _remit(two, "50000", accounts["main_cash"])),
        ],
    )
    assert _errors(results) == [], results
    # During the correction the old close stays in effect: +700k consolidated, 350k live in R1, 150k in R2.
    assert _advances(db, centro) == Decimal("200000.00")
    _remit_now(db, admin, one, "300000", accounts["main_cash"])
    _count(db, operator, one, 350000, accounts["main_cash"])
    _close_now(db, operator, one, version=2)
    assert _advances(db, centro) == Decimal("-150000.00")
