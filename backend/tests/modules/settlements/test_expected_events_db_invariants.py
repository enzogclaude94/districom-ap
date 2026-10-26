"""What the database guarantees about the expected amount and its events (migration 0030), whoever writes."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.store_sys_support import (
    as_user,
    close,
    count_cash,
    integration,
    open_delivery,
    pending_id,
    record,
    sent,
    with_code,
)

__all__ = ["as_user"]

FINGERPRINT = "a" * 64


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


@pytest.fixture
def pedro(driver: Callable[..., int], db: Session) -> int:
    pedro_id = driver("Pedro Gómez")
    with_code(db, pedro_id, "R-01")
    return pedro_id


def _event_of(db: Session, sid: int) -> int:
    return int(
        db.execute(text("SELECT expected_event_id FROM settlements.settlements WHERE id = :s"), {"s": sid}).scalar_one()
    )


def test_the_amount_changes_only_with_an_event(r: Roles, pedro: int, db: Session) -> None:
    sid = open_delivery(r.operator, pedro, "1000")
    message = _refused(db, "UPDATE settlements.settlements SET expected_amount = 2000 WHERE id = :s", s=sid)
    assert "expected_event_required" in message
    message = _refused(db, "UPDATE settlements.settlements SET expected_source = 'STORE_SYS' WHERE id = :s", s=sid)
    assert "expected_event_required" in message
    message = _refused(db, "UPDATE settlements.settlements SET expected_event_id = NULL WHERE id = :s", s=sid)
    assert "never loses its event" in message


def test_the_settlement_is_at_the_amount_of_its_event(
    r: Roles, pedro: int, db: Session, as_user: Callable[..., TestClient]
) -> None:
    one = open_delivery(r.operator, pedro, "1000")
    two = open_delivery(r.operator, pedro, "2000")
    sent(integration(as_user), record("LIQ-1", "1500.00", sequence=1))
    pending = pending_id(r.operator, one)
    # Another settlement's event, a pending event, an event of another amount.
    for event in (_event_of(db, two), pending):
        message = _refused(
            db, "UPDATE settlements.settlements SET expected_event_id = :e WHERE id = :s", e=event, s=one
        )
        assert "expected_event_mismatch" in message


def test_an_expected_amount_is_never_inserted_without_its_event(r: Roles, pedro: int, db: Session) -> None:
    assert r.operator  # a user to author it
    message = _refused(
        db,
        "INSERT INTO settlements.settlements (type, business_date, employee_id, sequence_number, expected_amount, "
        "client_request_id, request_fingerprint, created_by) SELECT 'DELIVERY', current_date - 1, :e, 9, 10, "
        "gen_random_uuid(), :f, id FROM users ORDER BY id LIMIT 1",
        e=pedro,
        f=FINGERPRINT,
    )
    assert "expected_event_required" in message


def test_events_are_never_deleted_and_decided_once(
    r: Roles, pedro: int, db: Session, as_user: Callable[..., TestClient]
) -> None:
    sid = open_delivery(r.operator, pedro, "1000")
    sent(integration(as_user), record("LIQ-1", "1500.00"))
    event = pending_id(r.operator, sid)
    message = _refused(db, "DELETE FROM settlements.expected_amount_events WHERE settlement_id = :s", s=sid)
    assert "expected_amount_events" in message
    message = _refused(db, "UPDATE settlements.expected_amount_events SET expected_amount = 1 WHERE id = :e", e=event)
    assert "decided once" in message
    keep = (
        "UPDATE settlements.expected_amount_events SET outcome = 'KEPT', decided_by = received_by, decided_at = now(), "
        "decision_reason = 'Motivo' WHERE id = :e"
    )
    db.execute(text(keep), {"e": event})
    db.commit()
    message = _refused(
        db,
        "UPDATE settlements.expected_amount_events SET outcome = 'CONFIRMED', decision_reason = NULL WHERE id = :e",
        e=event,
    )
    assert "decided once" in message
    # A manual event is never decided.
    manual = _event_of(db, sid)
    message = _refused(db, keep, e=manual)
    assert "decided once" in message


def test_a_store_sys_key_is_unique(r: Roles, pedro: int, db: Session, as_user: Callable[..., TestClient]) -> None:
    sid = open_delivery(r.operator, pedro)
    sent(integration(as_user), record("LIQ-1", "1500.00"))
    message = _refused(
        db,
        "INSERT INTO settlements.expected_amount_events (settlement_id, settlement_version, source, outcome, "
        "external_reference, revision, request_fingerprint, expected_amount, previous_amount, previous_source, "
        "previous_event_id, received_by) SELECT id, version, 'STORE_SYS', 'SUPERSEDED', 'LIQ-1', 1, :f, 1, "
        "expected_amount, expected_source, expected_event_id, created_by FROM settlements.settlements WHERE id = :s",
        f=FINGERPRINT,
        s=sid,
    )
    assert "uq_expected_amount_events_store_key" in message


def test_a_closed_settlement_takes_no_setting_event_and_does_not_close_with_a_pending(
    r: Roles, pedro: int, db: Session, as_user: Callable[..., TestClient], ids: dict[str, int]
) -> None:
    sid = open_delivery(r.operator, pedro, "10000")
    count_cash(r.operator, sid, ids["main_cash"], 1)
    sent(integration(as_user), record("LIQ-1", "15000.00"))
    message = _refused(
        db,
        "UPDATE settlements.settlements SET status = 'CLOSED', closed_at = now(), closed_by = created_by WHERE id = :s",
        s=sid,
    )
    assert "expected_update_pending" in message
    r.operator.post(
        f"/api/v1/settlements/expected-updates/{pending_id(r.operator, sid)}/keep", json={"reason": "Error de carga"}
    )
    assert close(r.operator, sid).status_code == 201
    insert = (
        "INSERT INTO settlements.expected_amount_events (settlement_id, settlement_version, source, outcome, "
        "expected_amount, previous_amount, previous_source, previous_event_id, received_by) SELECT id, version, "
        "'MANUAL', 'MANUAL', 1, expected_amount, expected_source, expected_event_id, created_by "
        "FROM settlements.settlements WHERE id = :s"
    )
    assert "is CLOSED" in _refused(db, insert, s=sid)


def test_an_event_is_written_against_the_amount_in_effect(r: Roles, pedro: int, db: Session) -> None:
    sid = open_delivery(r.operator, pedro, "1000")
    message = _refused(
        db,
        "INSERT INTO settlements.expected_amount_events (settlement_id, settlement_version, source, outcome, "
        "expected_amount, previous_amount, previous_source, received_by) SELECT id, version, 'MANUAL', 'MANUAL', 5, "
        "999, 'MANUAL', created_by FROM settlements.settlements WHERE id = :s",
        s=sid,
    )
    assert "previous amount is not the one in effect" in message


def test_a_close_freezes_the_expected_of_its_settlement(r: Roles, pedro: int, db: Session, ids: dict[str, int]) -> None:
    sid = open_delivery(r.operator, pedro, "10000")
    count_cash(r.operator, sid, ids["main_cash"], 1)
    assert close(r.operator, sid).status_code == 201
    frozen = db.execute(
        text(
            "SELECT expected_amount, expected_source, expected_event_id FROM settlements.settlement_closings "
            "WHERE settlement_id = :s"
        ),
        {"s": sid},
    ).one()
    assert (str(frozen[0]), frozen[1], frozen[2]) == ("10000.00", "MANUAL", _event_of(db, sid))
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_closings (settlement_id, settlement_version, kind, operation_id, "
        "request_fingerprint, closed_by, expected_amount, expected_source) SELECT id, 2, 'FINANCIAL', "
        "current_close_operation_id, :f, created_by, 1, 'MANUAL' FROM settlements.settlements WHERE id = :s",
        f=FINGERPRINT,
        s=sid,
    )
    assert "frozen is not the one" in message
