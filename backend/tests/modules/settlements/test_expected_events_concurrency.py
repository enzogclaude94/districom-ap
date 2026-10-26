"""The expected amount under concurrency, against PostgreSQL (R9-1).

The main thread holds the settlement row, the workers block on it, and once it is released the database ends in one
valid state: the same record twice is one event, a record and a manual change are serialized, and a record against a
close either applies before it or is stored REJECTED_CLOSED, never half way.
"""

from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery import schemas, service
from app.modules.settlements.expected import schemas as es
from app.modules.settlements.expected import service as expected
from app.platform.auth.models import User
from tests.modules.settlements.days import yesterday
from tests.modules.settlements.store_sys_support import record, with_code
from tests.modules.settlements.test_settlement_close_concurrency import (
    _close,
    _count,
    _hold,
    _in_session,
    _race,
    _scalar,
    _settlement,
    new_session,
)

__all__ = ["new_session"]


@pytest.fixture
def operator(actor) -> User:  # type: ignore[no-untyped-def]
    return actor("operator")  # type: ignore[no-any-return]


@pytest.fixture
def pedro(driver, db: Session) -> int:  # type: ignore[no-untyped-def]
    pedro_id: int = driver("Pedro Gómez")
    with_code(db, pedro_id, "R-01")
    return pedro_id


def _import(*rows: dict[str, object]) -> Callable[[Session, User], object]:
    body = es.StoreSysImportIn(source="STORE_SYS", client_request_id=uuid4(), records=list(rows))
    return lambda session, user: expected.import_records(session, user, body)


def _outcomes(result: object) -> list[str | None]:
    assert isinstance(result, es.StoreSysImportOut), result
    return [r.outcome for r in result.results]


def test_the_same_record_twice_at_once_is_one_event(
    db: Session, new_session: Callable[[], Session], operator: User, pedro: int
) -> None:
    sid = service.create(
        db, operator, schemas.SettlementIn(client_request_id=uuid4(), business_date=yesterday(), employee_id=pedro)
    ).id
    holder = _hold(new_session, sid)
    work = _in_session(new_session, operator, _import(record("LIQ-1", "1000.00")))
    results = _race(holder, [work, work])
    assert [_outcomes(r) for r in results] == [["APPLIED"], ["APPLIED"]]
    assert _scalar(db, "SELECT count(*) FROM settlements.expected_amount_events") == 1
    assert _scalar(db, "SELECT expected_amount FROM settlements.settlements WHERE id = :s", s=sid) == Decimal("1000")


def test_a_record_and_a_manual_change_are_serialized(
    db: Session, new_session: Callable[[], Session], operator: User, pedro: int
) -> None:
    sid = _settlement(db, operator, pedro, "1500")
    holder = _hold(new_session, sid)

    def _manual(session: Session, user: User) -> object:
        service.update(session, user, sid, schemas.SettlementPatch(expected_amount=Decimal(1600)))
        return "manual"

    results = _race(
        holder,
        [
            _in_session(new_session, operator, _import(record("LIQ-1", "1400.00"))),
            _in_session(new_session, operator, _manual),
        ],
    )
    # Whatever came first, the settlement is at an event's amount and the history explains it: either the manual
    # change went first and the record waits against it, or the record waits and the manual change is refused.
    pending = _scalar(
        db, "SELECT count(*) FROM settlements.expected_amount_events WHERE outcome = 'PENDING_CONFIRMATION'"
    )
    assert pending == 1
    amount = _scalar(db, "SELECT expected_amount FROM settlements.settlements WHERE id = :s", s=sid)
    if isinstance(results[1], AppError):
        assert results[1].code == "expected_update_pending"
        assert amount == Decimal("1500")
    else:
        assert amount == Decimal("1600")
    assert (
        _scalar(
            db,
            "SELECT count(*) FROM settlements.settlements s JOIN settlements.expected_amount_events e "
            "ON e.id = s.expected_event_id WHERE s.id = :s AND e.expected_amount = s.expected_amount",
            s=sid,
        )
        == 1
    )


def test_a_record_against_a_close_is_never_half_way(
    db: Session, new_session: Callable[[], Session], operator: User, pedro: int, accounts: dict[str, int]
) -> None:
    sid = service.create(
        db, operator, schemas.SettlementIn(client_request_id=uuid4(), business_date=yesterday(), employee_id=pedro)
    ).id
    expected.import_records(
        db, operator, es.StoreSysImportIn(source="STORE_SYS", client_request_id=uuid4(), records=[record("A", "1000")])
    )
    _count(db, operator, sid, ("ARS_1000", 1, accounts["main_cash"]))
    close = _close(db, sid, 1)
    holder = _hold(new_session, sid)
    results = _race(
        holder,
        [
            _in_session(new_session, operator, close),
            _in_session(new_session, operator, _import(record("A", "900.00", revision=2))),
        ],
    )
    imported = _outcomes(results[1])
    status = _scalar(db, "SELECT status FROM settlements.settlements WHERE id = :s", s=sid)
    if imported == ["REJECTED_CLOSED"]:
        # The close went first: it froze $1.000 and the record changed nothing.
        frozen = _scalar(
            db, "SELECT expected_amount FROM settlements.settlement_closings WHERE settlement_id = :s", s=sid
        )
        assert (status, frozen) == ("CLOSED", Decimal("1000"))
    else:
        # The record went first: the confirmed preview is stale and the settlement stays open at $900.
        assert imported == ["APPLIED"]
        assert isinstance(results[0], AppError)
        assert results[0].code == "settlement_preview_stale"
        assert status == "OPEN"
