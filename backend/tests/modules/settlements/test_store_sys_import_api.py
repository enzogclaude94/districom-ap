"""The Store Sys -> DistriCom canonical contract (R9-1, ADR 011): authority, idempotency, revisions, conflicts,
batches, identities and the decision on a pending update."""

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.outlet_support import OUT
from tests.modules.settlements.outlet_support import create as open_outlet
from tests.modules.settlements.store_sys_support import (
    DELIVERIES,
    accept,
    as_user,
    close,
    count_cash,
    delivery,
    history,
    integration,
    keep,
    open_delivery,
    outcome,
    pending_id,
    record,
    reopen,
    send,
    sent,
    with_code,
)

__all__ = ["as_user"]


@pytest.fixture
def pedro(driver: Callable[..., int], db: Session) -> int:
    pedro_id = driver("Pedro Gómez")
    with_code(db, pedro_id, "R-01")
    return pedro_id


@pytest.fixture
def store(as_user: Callable[..., TestClient]) -> Callable[[], TestClient]:
    return lambda: integration(as_user)


def _events(db: Session) -> int:
    return int(db.execute(text("SELECT count(*) FROM settlements.expected_amount_events")).scalar_one())


def _expected(r: Roles, sid: int) -> tuple[str | None, str]:
    body = delivery(r.operator, sid)
    return body["expected_amount"], body["expected_source"]


# --- authority ------------------------------------------------------------------------------------


def test_store_sys_over_empty_applies(r: Roles, pedro: int, store: Callable[[], TestClient]) -> None:
    sid = open_delivery(r.operator, pedro)
    results = sent(store(), record("LIQ-1", "1000.00"))
    assert results[0]["outcome"] == "APPLIED"
    assert results[0]["settlement"] == {
        "type": "DELIVERY",
        "business_date": days_ago(1).isoformat(),
        "sequence_number": 1,
    }
    assert "id" not in results[0]["settlement"]
    assert _expected(r, sid) == ("1000.00", "STORE_SYS")


def test_a_later_revision_of_the_same_reference_applies_with_history(
    r: Roles, pedro: int, store: Callable[[], TestClient]
) -> None:
    sid = open_delivery(r.operator, pedro)
    assert outcome(store(), record("LIQ-1", "1000000.00")) == ("APPLIED", None)
    assert outcome(store(), record("LIQ-1", "980000.00", revision=2)) == ("APPLIED", None)
    assert _expected(r, sid) == ("980000.00", "STORE_SYS")
    events = history(r.operator, sid)["events"]
    assert [(e["revision"], e["expected_amount"], e["previous_amount"], e["in_effect"]) for e in events] == [
        (1, "1000000.00", None, False),
        (2, "980000.00", "1000000.00", True),
    ]


def test_an_old_revision_arriving_late_is_superseded(r: Roles, pedro: int, store: Callable[[], TestClient]) -> None:
    sid = open_delivery(r.operator, pedro)
    sent(store(), record("LIQ-1", "1000.00", revision=2))
    assert outcome(store(), record("LIQ-1", "900.00", revision=1)) == ("SUPERSEDED", None)
    assert _expected(r, sid) == ("1000.00", "STORE_SYS")


def test_store_sys_equal_to_manual_matches(r: Roles, pedro: int, store: Callable[[], TestClient]) -> None:
    sid = open_delivery(r.operator, pedro, "1500")
    assert outcome(store(), record("LIQ-1", "1500.00")) == ("MATCHED", None)
    body = delivery(r.operator, sid)
    assert (body["expected_amount"], body["expected_source"]) == ("1500.00", "STORE_SYS")
    assert body["expected_origin"]["external_reference"] == "LIQ-1"


def test_store_sys_different_from_manual_waits_and_accept_applies_it(
    r: Roles, pedro: int, store: Callable[[], TestClient]
) -> None:
    sid = open_delivery(r.operator, pedro, "1500")
    assert outcome(store(), record("LIQ-1", "1400.00")) == ("PENDING_CONFIRMATION", None)
    body = delivery(r.operator, sid)
    assert (body["expected_amount"], body["expected_source"]) == ("1500.00", "MANUAL")
    assert body["expected_pending"]["expected_amount"] == "1400.00"
    assert body["expected_pending"]["requires_admin"] is False
    assert r.operator.get(DELIVERIES).json()["items"][0]["expected_update_pending"] is True
    event = pending_id(r.operator, sid)
    assert accept(r.operator, event).status_code == 200
    assert _expected(r, sid) == ("1400.00", "STORE_SYS")
    assert history(r.operator, sid)["events"][-1]["outcome"] == "CONFIRMED"
    again = accept(r.operator, event)
    assert (again.status_code, again.json()["code"]) == (409, "expected_update_already_decided")
    assert keep(r.operator, event).status_code == 409


def test_keep_takes_a_reason_and_changes_nothing(r: Roles, pedro: int, store: Callable[[], TestClient]) -> None:
    sid = open_delivery(r.operator, pedro, "1500")
    sent(store(), record("LIQ-1", "1400.00"))
    event = pending_id(r.operator, sid)
    assert keep(r.operator, event, reason=None).status_code == 422
    assert keep(r.operator, event, reason="   ").status_code == 422
    kept = keep(r.operator, event)
    assert kept.status_code == 200
    assert kept.json()["events"][-1]["outcome"] == "KEPT"
    assert kept.json()["events"][-1]["decision_reason"] == "El remito de Store Sys tiene un error"
    assert _expected(r, sid) == ("1500.00", "MANUAL")
    assert accept(r.operator, event).status_code == 409


def test_manual_over_store_sys_needs_a_reason_and_then_rules(
    r: Roles, pedro: int, store: Callable[[], TestClient]
) -> None:
    sid = open_delivery(r.operator, pedro)
    sent(store(), record("LIQ-1", "1000.00"))
    refused = r.operator.patch(f"{DELIVERIES}/{sid}", json={"expected_amount": "1100"})
    assert (refused.status_code, refused.json()["code"]) == (422, "expected_reason_required")
    corrected = r.operator.patch(
        f"{DELIVERIES}/{sid}", json={"expected_amount": "1100", "expected_reason": "Falta una factura en Store Sys"}
    )
    assert corrected.status_code == 200, corrected.text
    assert _expected(r, sid) == ("1100.00", "MANUAL")
    # From now on the manual amount rules: the next revision waits.
    assert outcome(store(), record("LIQ-1", "1050.00", revision=2)) == ("PENDING_CONFIRMATION", None)
    events = history(r.operator, sid)["events"]
    assert [(e["source"], e["outcome"]) for e in events] == [
        ("STORE_SYS", "APPLIED"),
        ("MANUAL", "MANUAL"),
        ("STORE_SYS", "PENDING_CONFIRMATION"),
    ]
    assert events[1]["reason"] == "Falta una factura en Store Sys"


def test_while_a_update_is_pending_the_amount_does_not_change_by_hand_nor_close(
    r: Roles, pedro: int, store: Callable[[], TestClient], ids: dict[str, int]
) -> None:
    sid = open_delivery(r.operator, pedro, "10000")
    count_cash(r.operator, sid, ids["main_cash"], 1)
    sent(store(), record("LIQ-1", "20000.00"))
    blocked = r.operator.patch(f"{DELIVERIES}/{sid}", json={"expected_amount": "30000"})
    assert (blocked.status_code, blocked.json()["code"]) == (409, "expected_update_pending")
    refused = close(r.operator, sid)
    assert (refused.status_code, refused.json()["code"]) == (409, "expected_update_pending")
    keep(r.operator, pending_id(r.operator, sid))
    assert close(r.operator, sid).status_code == 201


def test_a_newer_record_supersedes_the_undecided_pending(r: Roles, pedro: int, store: Callable[[], TestClient]) -> None:
    sid = open_delivery(r.operator, pedro, "1500")
    sent(store(), record("LIQ-1", "1400.00"))
    first = pending_id(r.operator, sid)
    assert outcome(store(), record("LIQ-1", "1450.00", revision=2)) == ("PENDING_CONFIRMATION", None)
    events = history(r.operator, sid)["events"]
    assert [(e["id"] == first, e["outcome"]) for e in events] == [
        (False, "MANUAL"),
        (True, "SUPERSEDED"),
        (False, "PENDING_CONFIRMATION"),
    ]
    assert accept(r.operator, first).status_code == 409
    # A newer record equal to the manual amount ends the disagreement.
    assert outcome(store(), record("LIQ-1", "1500.00", revision=3)) == ("MATCHED", None)
    assert history(r.operator, sid)["pending"] is None


def test_another_reference_over_store_sys_waits(r: Roles, pedro: int, store: Callable[[], TestClient]) -> None:
    sid = open_delivery(r.operator, pedro)
    sent(store(), record("LIQ-1", "1000.00"))
    assert outcome(store(), record("LIQ-2", "1200.00")) == ("PENDING_CONFIRMATION", None)
    assert _expected(r, sid) == ("1000.00", "STORE_SYS")


def test_a_closed_settlement_is_never_touched(
    r: Roles, pedro: int, store: Callable[[], TestClient], ids: dict[str, int], db: Session
) -> None:
    sid = open_delivery(r.operator, pedro, "10000")
    count_cash(r.operator, sid, ids["main_cash"], 1)
    assert close(r.operator, sid).status_code == 201
    closing = db.execute(text("SELECT id, expected_amount FROM settlements.settlement_closings")).one()
    assert outcome(store(), record("LIQ-1", "9800.00")) == ("REJECTED_CLOSED", None)
    body = delivery(r.operator, sid)
    assert (body["status"], body["expected_amount"], body["expected_source"]) == ("CLOSED", "10000.00", "MANUAL")
    assert db.execute(text("SELECT id, expected_amount FROM settlements.settlement_closings")).one() == closing
    assert history(r.operator, sid)["events"][-1]["outcome"] == "REJECTED_CLOSED"


def test_being_corrected_always_waits_and_takes_an_admin(
    r: Roles, pedro: int, store: Callable[[], TestClient], ids: dict[str, int]
) -> None:
    sid = open_delivery(r.operator, pedro)
    sent(store(), record("LIQ-1", "10000.00"))
    count_cash(r.operator, sid, ids["main_cash"], 1)
    assert close(r.operator, sid).status_code == 201
    assert reopen(r.admin, sid).status_code == 200
    # Even a later revision of the reference in effect: the correction of a financial document is never automatic.
    assert outcome(store(), record("LIQ-1", "9000.00", revision=2)) == ("PENDING_CONFIRMATION", None)
    event = pending_id(r.operator, sid)
    assert history(r.operator, sid)["pending"]["requires_admin"] is True
    assert accept(r.operator, event).status_code == 403
    assert keep(r.operator, event).status_code == 403
    assert accept(r.admin, event).status_code == 200
    assert _expected(r, sid) == ("9000.00", "STORE_SYS")


# --- idempotency and conflicts --------------------------------------------------------------------


def test_a_retry_returns_the_same_event_and_outcome(
    r: Roles, pedro: int, store: Callable[[], TestClient], db: Session
) -> None:
    open_delivery(r.operator, pedro, "1500")
    batch = (record("LIQ-1", "1500.00"), record("LIQ-2", "1.00", code="NADIE"))
    first = sent(store(), *batch)
    events = _events(db)
    again = sent(store(), *batch)
    assert again == first
    assert _events(db) == events == 2  # the manual load and the match


def test_a_retry_after_the_decision_reports_the_event_as_it_stands(
    r: Roles, pedro: int, store: Callable[[], TestClient], db: Session
) -> None:
    sid = open_delivery(r.operator, pedro, "1500")
    sent(store(), record("LIQ-1", "1400.00"))
    accept(r.operator, pending_id(r.operator, sid))
    assert outcome(store(), record("LIQ-1", "1400.00")) == ("CONFIRMED", None)
    assert _events(db) == 2


def test_same_reference_and_revision_with_other_content_is_a_conflict(
    r: Roles, pedro: int, store: Callable[[], TestClient], db: Session
) -> None:
    sid = open_delivery(r.operator, pedro)
    sent(store(), record("LIQ-1", "1000.00"))
    assert outcome(store(), record("LIQ-1", "1001.00")) == (None, "external_reference_conflict")
    assert _expected(r, sid) == ("1000.00", "STORE_SYS")
    assert _events(db) == 1


def test_a_reference_stays_with_its_settlement(
    r: Roles, pedro: int, store: Callable[[], TestClient], driver: Callable[..., int], db: Session
) -> None:
    juan = driver("Juan Díaz")
    with_code(db, juan, "R-02")
    open_delivery(r.operator, pedro)
    open_delivery(r.operator, juan)
    sent(store(), record("LIQ-1", "1000.00"))
    assert outcome(store(), record("LIQ-1", "1000.00", revision=2, code="R-02")) == (
        None,
        "external_reference_conflict",
    )


def test_settlement_not_found_stores_nothing_and_is_evaluated_again(
    r: Roles, pedro: int, store: Callable[[], TestClient], db: Session
) -> None:
    row = record("LIQ-1", "1000.00")
    assert outcome(store(), row) == (None, "settlement_not_found")
    assert _events(db) == 0
    sid = open_delivery(r.operator, pedro)
    assert outcome(store(), row) == ("APPLIED", None)
    assert _expected(r, sid) == ("1000.00", "STORE_SYS")


# --- identities and settlements -------------------------------------------------------------------


def test_identities_are_resolved_by_code_never_by_name(
    r: Roles, pedro: int, store: Callable[[], TestClient], boca: Callable[[str], int]
) -> None:
    open_delivery(r.operator, pedro)
    boca("CEN")
    client = store()
    assert outcome(client, record("A", "1", code="Pedro Gómez")) == (None, "invalid_external_identity")
    assert outcome(client, record("B", "1", outlet="NOPE")) == (None, "invalid_external_identity")
    assert outcome(client, record("C", "1", code=None)) == (None, "invalid_record")
    both = record("D", "1") | {"outlet_code": "CEN"}
    assert outcome(client, both) == (None, "invalid_record")


def test_two_settlements_of_the_day_need_the_number(r: Roles, pedro: int, store: Callable[[], TestClient]) -> None:
    open_delivery(r.operator, pedro)
    second = open_delivery(r.operator, pedro)
    assert outcome(store(), record("LIQ-1", "1000.00")) == (None, "ambiguous_settlement")
    assert outcome(store(), record("LIQ-1", "1000.00", sequence=2)) == ("APPLIED", None)
    assert _expected(r, second) == ("1000.00", "STORE_SYS")
    # A later revision finds its settlement by the reference, without the number.
    assert outcome(store(), record("LIQ-1", "900.00", revision=2)) == ("APPLIED", None)
    assert _expected(r, second) == ("900.00", "STORE_SYS")


def test_an_outlet_record_with_zero_applies(
    r: Roles, store: Callable[[], TestClient], boca: Callable[[str], int]
) -> None:
    sid = open_outlet(r.operator, boca("CEN"))
    assert outcome(store(), record("BOC-1", "0.00", outlet="cen")) == ("APPLIED", None)
    body = r.operator.get(f"{OUT}/{sid}").json()
    assert (body["expected_amount"], body["expected_source"]) == ("0.00", "STORE_SYS")
    assert body["expected_origin"]["external_reference"] == "BOC-1"


def test_an_outlet_manual_amount_over_store_sys_can_be_cleared_with_a_reason(
    r: Roles, store: Callable[[], TestClient], boca: Callable[[str], int]
) -> None:
    sid = open_outlet(r.operator, boca("CEN"))
    sent(store(), record("BOC-1", "500.00", outlet="CEN"))
    assert r.operator.patch(f"{OUT}/{sid}", json={"expected_amount": None}).status_code == 422
    cleared = r.operator.patch(f"{OUT}/{sid}", json={"expected_amount": None, "expected_reason": "Era de otra boca"})
    assert cleared.status_code == 200, cleared.text
    assert (cleared.json()["expected_amount"], cleared.json()["expected_source"]) == (None, "MANUAL")
    assert outcome(store(), record("BOC-1", "500.00", revision=2, outlet="CEN")) == ("APPLIED", None)


def test_amounts_and_records_are_validated_one_by_one(
    r: Roles, pedro: int, store: Callable[[], TestClient], boca: Callable[[str], int]
) -> None:
    open_delivery(r.operator, pedro)
    open_outlet(r.operator, boca("CEN"))
    rows: list[dict[str, Any]] = [
        record("A", "0"),
        record("B", "-1"),
        record("C", "1.234"),
        record("D", "-1", outlet="CEN"),
        record("E", "1", on=today().replace(year=today().year + 1)),
        {"external_reference": "F"},
        record("G", "1", revision=0),
        record("H", "10.00"),
    ]
    results = sent(store(), *rows)
    assert [(x["index"], x["outcome"], x["code"]) for x in results] == [
        (0, None, "invalid_expected_amount"),
        (1, None, "invalid_expected_amount"),
        (2, None, "invalid_expected_amount"),
        (3, None, "invalid_expected_amount"),
        (4, None, "invalid_record"),
        (5, None, "invalid_record"),
        (6, None, "invalid_record"),
        (7, "APPLIED", None),
    ]


# --- batches --------------------------------------------------------------------------------------


def test_a_mixed_batch_has_one_result_per_record(
    r: Roles, pedro: int, store: Callable[[], TestClient], ids: dict[str, int], driver: Callable[..., int], db: Session
) -> None:
    juan = driver("Juan Díaz")
    with_code(db, juan, "R-02")
    closed = open_delivery(r.operator, juan, "10000")
    count_cash(r.operator, closed, ids["main_cash"], 1)
    close(r.operator, closed)
    open_delivery(r.operator, pedro)
    response = send(
        store(),
        record("OK", "1000.00"),
        record("NONE", "1", on=days_ago(5)),
        record("CLOSED", "9000.00", code="R-02"),
        record("OK", "1000.00"),
    )
    assert response.status_code == 200
    body = response.json()
    assert [(x["outcome"], x["code"]) for x in body["results"]] == [
        ("APPLIED", None),
        (None, "settlement_not_found"),
        ("REJECTED_CLOSED", None),
        (None, "duplicate_in_batch"),
    ]
    assert body["counts"] == {
        "received": 4,
        "stored": 2,
        "refused": 2,
        "by_outcome": {"APPLIED": 1, "REJECTED_CLOSED": 1},
    }


def test_the_envelope_is_validated_whole(pedro: int, store: Callable[[], TestClient]) -> None:
    client = store()
    row = record("A", "1")
    assert send(client).status_code == 422
    assert send(client, *[row] * 201).status_code == 422
    other = client.post(
        "/api/v1/settlements/imports/store-sys/expected-amounts",
        json={"source": "AXUM", "client_request_id": "6f1c2b9e-0d4e-4b8a-9b1f-3c2d1e0f9a8b", "records": [row]},
    )
    assert other.status_code == 422


def test_the_import_is_audited_without_the_employee_code(
    r: Roles, pedro: int, store: Callable[[], TestClient], db: Session
) -> None:
    open_delivery(r.operator, pedro)
    sent(store(), record("LIQ-1", "1000.00"))
    payload = db.execute(
        text("SELECT payload::text FROM audit_events WHERE event_type = 'settlements.expected.imported'")
    ).scalar_one()
    assert "LIQ-1" in payload
    assert "R-01" not in payload
