"""Rules of pending collections that the database enforces whoever writes (migration 0009), checked with raw
SQL: [18] append-only events and snapshots, a load frozen once its settlement is closed, and what a close
published always equal to its "Pendientes de cobro" leg."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.delivery import closing, schemas, service
from app.modules.settlements.pending import schemas as ps
from app.modules.settlements.pending import service as pending_service

DAY = business_today() - timedelta(days=1)


@pytest.fixture
def closed(db: Session, actor, driver, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """$1.500 expected: $1.000 counted and a $500 pending, closed."""
    operator = actor("operator")
    pedro = driver("Pedro")
    sid = service.create(
        db,
        operator,
        schemas.SettlementIn(
            client_request_id=uuid4(), business_date=DAY, employee_id=pedro, expected_amount=Decimal(1500)
        ),
    ).id
    line = schemas.CountLineIn(denomination_code="ARS_1000", quantity=1, destination_account_id=accounts["main_cash"])
    service.replace_count(db, operator, sid, schemas.CountIn(lines=[line]))
    pid = pending_service.create(
        db,
        operator,
        sid,
        ps.PendingIn(client_request_id=uuid4(), customer_name="Kiosco", amount=Decimal(500), reason="Paga mañana"),
    ).id
    token = closing.preview(db, sid).token
    closing.close(db, operator, sid, schemas.CloseIn(version=1, preview_token=token, confirm=True))
    close_id = db.execute(
        text("SELECT current_close_operation_id FROM settlements.settlements WHERE id = :s"), {"s": sid}
    ).scalar_one()
    return {"settlement": sid, "pending": pid, "close": close_id, "user": operator.id}


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def test_a_pending_is_never_deleted(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    message = _refused(db, "DELETE FROM settlements.pending_collections WHERE id = :p", p=closed["pending"])
    assert "never deleted" in message


def test_a_closed_settlement_freezes_its_pendings(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    message = _refused(db, "UPDATE settlements.pending_collections SET amount = 1 WHERE id = :p", p=closed["pending"])
    assert "CLOSED" in message
    message = _refused(
        db,
        "INSERT INTO settlements.pending_collections (settlement_id, customer_name, amount, reason, "
        "responsible_employee_id, client_request_id, request_fingerprint, created_by) "
        "SELECT :s, 'X', 1, 'x', employee_id, :k, :f, :u FROM settlements.settlements WHERE id = :s",
        s=closed["settlement"],
        k=uuid4(),
        f="a" * 64,
        u=closed["user"],
    )
    assert "CLOSED" in message


def test_identity_never_changes_and_a_voided_pending_is_frozen(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    db.execute(
        text("UPDATE settlements.settlements SET status = 'OPEN', version = 2 WHERE id = :s"),
        {"s": closed["settlement"]},
    )
    db.commit()
    message = _refused(
        db,
        "UPDATE settlements.pending_collections SET client_request_id = :k WHERE id = :p",
        k=uuid4(),
        p=closed["pending"],
    )
    assert "identity" in message
    db.execute(
        text(
            "UPDATE settlements.pending_collections SET voided_at = now(), voided_by = :u, void_reason = 'x' "
            "WHERE id = :p"
        ),
        {"u": closed["user"], "p": closed["pending"]},
    )
    db.commit()
    message = _refused(db, "UPDATE settlements.pending_collections SET amount = 1 WHERE id = :p", p=closed["pending"])
    assert "voided" in message


def test_events_are_append_only(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    """[18]"""
    assert "never deleted" in _refused(db, "UPDATE settlements.pending_collection_events SET reason = 'x'")
    assert "never deleted" in _refused(db, "DELETE FROM settlements.pending_collection_events")
    message = _refused(
        db,
        "INSERT INTO settlements.pending_collection_events (pending_id, settlement_id, settlement_version, "
        "event_type, created_by) VALUES (:p, :s, 1, 'PUBLISHED', :u)",
        p=closed["pending"],
        s=closed["settlement"],
        u=closed["user"],
    )
    assert "close_events" in message


def test_snapshots_are_append_only(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    assert "never deleted" in _refused(db, "UPDATE settlements.settlement_closing_pendings SET amount = 1")
    assert "never deleted" in _refused(db, "DELETE FROM settlements.settlement_closing_pendings")


def test_a_close_publishes_only_live_pendings_of_its_settlement(db: Session, closed, actor, driver) -> None:  # type: ignore[no-untyped-def]
    """A snapshot row of a pending of another settlement, or under an operation that is not its close, is refused."""
    operator = actor("operator")
    other = service.create(
        db,
        operator,
        schemas.SettlementIn(
            client_request_id=uuid4(), business_date=DAY, employee_id=driver("Juan"), expected_amount=Decimal(100)
        ),
    ).id
    foreign = pending_service.create(
        db,
        operator,
        other,
        ps.PendingIn(client_request_id=uuid4(), customer_name="Otro", amount=Decimal(100), reason="x"),
    ).id
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_closing_pendings (close_operation_id, settlement_id, settlement_version, "
        "pending_id, amount, customer_name) VALUES (:o, :s, 1, :p, 100, 'Otro')",
        o=closed["close"],
        s=closed["settlement"],
        p=foreign,
    )
    assert "not of settlement" in message
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_closing_pendings (close_operation_id, settlement_id, settlement_version, "
        "pending_id, amount, customer_name) VALUES (:o, :s, 1, :p, 100, 'Otro')",
        o=closed["close"],
        s=other,
        p=foreign,
    )
    assert "is not a close of settlement" in message


def test_published_amounts_match_the_ledger_leg(db: Session, closed, actor) -> None:  # type: ignore[no-untyped-def]
    """At commit, a close's snapshot rows add up exactly to its "Pendientes de cobro" leg."""
    admin = actor("admin")
    closing.reopen(db, admin, closed["settlement"], schemas.ReopenIn(version=1, reason="Corregir"))
    operator = actor("operator")
    extra = pending_service.create(
        db,
        operator,
        closed["settlement"],
        ps.PendingIn(client_request_id=uuid4(), customer_name="Otro", amount=Decimal(1), reason="x"),
    ).id
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_closing_pendings (close_operation_id, settlement_id, settlement_version, "
        "pending_id, amount, customer_name) VALUES (:o, :s, 1, :p, 1, 'Otro')",
        o=closed["close"],
        s=closed["settlement"],
        p=extra,
    )
    assert "differ from its pending collections leg" in message


def test_the_pending_account_never_goes_negative(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    row = db.execute(
        text("SELECT allow_negative, kind FROM settlements.accounts WHERE code = 'pending_collections'")
    ).one()
    assert tuple(row) == (False, "PENDING_COLLECTIONS")
