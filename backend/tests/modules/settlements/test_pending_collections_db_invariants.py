"""Rules of pending collections that the database enforces whoever writes (migrations 0009 and 0010), checked
with raw SQL: [18] append-only events and snapshots, a load frozen once its settlement is closed (but for its
responsible, [45] of R3-2), what a close published always equal to its "Pendientes de cobro" leg, receipts and
resolutions append-only and tied to their operations, and no orphan reversal ([34] of R3-2)."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.delivery import closing, schemas, service
from app.modules.settlements.pending import applications
from app.modules.settlements.pending import schemas as ps
from app.modules.settlements.pending import service as pending_service

DAY = business_today() - timedelta(days=1)


def _closed_with_pending(db: Session, actor, driver, accounts, name: str) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """$1.500 expected: $1.000 counted and a $500 pending, closed."""
    operator = actor("operator")
    pedro = driver(name)
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


@pytest.fixture
def closed(db: Session, actor, driver, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return _closed_with_pending(db, actor, driver, accounts, "Pedro")


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


# --- receipts and resolutions (migration 0010) ----------------------------------------------------


@pytest.fixture
def applied(db: Session, closed, actor, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """The $500 pending with a $200 cash receipt and a $100 company resolution."""
    receipt_id = applications.record_receipt(
        db,
        actor("operator"),
        closed["pending"],
        ps.ReceiptIn(
            client_request_id=uuid4(), amount=Decimal(200), method="CASH", destination_account_id=accounts["main_cash"]
        ),
    ).id
    admin = actor("admin")
    resolution_id = applications.resolve(
        db,
        admin,
        closed["pending"],
        ps.ResolutionIn(
            client_request_id=uuid4(),
            amount=Decimal(100),
            reason="Incobrable",
            allocations=[ps.ResolutionPartIn(target="COMPANY", amount=Decimal(100))],
        ),
    ).id
    rows = db.execute(
        text(
            "SELECT (SELECT operation_id FROM settlements.pending_collection_receipts WHERE id = :r), "
            "(SELECT operation_id FROM settlements.pending_collection_resolutions WHERE id = :s)"
        ),
        {"r": receipt_id, "s": resolution_id},
    ).one()
    db.rollback()
    return {
        **closed,
        "receipt": receipt_id,
        "resolution": resolution_id,
        "receipt_op": rows[0],
        "resolution_op": rows[1],
        "admin": admin.id,
    }


REVERSE_BY_HAND = (
    "WITH op AS (INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
    "request_fingerprint, reverses_operation_id, created_by) VALUES ('REVERSAL', :d, 'A mano', :k, :f, :o, :u) "
    "RETURNING id) "
    "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) "
    "SELECT op.id, e.account_id, -e.amount FROM op, settlements.ledger_entries e WHERE e.operation_id = :o"
)


def test_no_orphan_reversal_of_a_receipt_or_resolution(db: Session, applied) -> None:  # type: ignore[no-untyped-def]
    """[34] A REVERSAL written by hand, without its row marked reversed in the same commit, is refused."""
    for original in (applied["receipt_op"], applied["resolution_op"]):
        message = _refused(db, REVERSE_BY_HAND, d=DAY, k=uuid4(), f="a" * 64, o=original, u=applied["admin"])
        assert "without its row marked reversed" in message


def test_no_receipt_or_resolution_operation_without_its_row(db: Session, applied, accounts) -> None:  # type: ignore[no-untyped-def]
    message = _refused(
        db,
        "WITH op AS (INSERT INTO settlements.financial_operations (type, business_date, client_request_id, "
        "request_fingerprint, source_type, source_id, created_by) VALUES ('PENDING_COLLECTION', :d, :k, :f, "
        "'pending_collection', :p, :u) RETURNING id) "
        "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) "
        "SELECT op.id, a, v FROM op, (VALUES (:pc, -10), (:cash, 10)) AS legs(a, v)",
        d=DAY,
        k=uuid4(),
        f="a" * 64,
        p=applied["pending"],
        u=applied["admin"],
        pc=accounts["pending_collections"],
        cash=accounts["main_cash"],
    )
    assert "has no pending collection receipt" in message


def test_receipts_and_resolutions_are_append_only(db: Session, applied) -> None:  # type: ignore[no-untyped-def]
    for table, row in (
        ("pending_collection_receipts", applied["receipt"]),
        ("pending_collection_resolutions", applied["resolution"]),
    ):
        assert "never deleted" in _refused(db, f"DELETE FROM settlements.{table} WHERE id = :r", r=row)  # noqa: S608
        message = _refused(db, f"UPDATE settlements.{table} SET amount = 1 WHERE id = :r", r=row)  # noqa: S608
        assert "only changes to record its reversal" in message
    message = _refused(
        db,
        "UPDATE settlements.pending_resolution_allocations SET amount = 1 WHERE resolution_id = :r",
        r=applied["resolution"],
    )
    assert "never deleted" in message


def test_a_row_is_reversed_only_by_the_reversal_of_its_operation(db: Session, applied) -> None:  # type: ignore[no-untyped-def]
    """Marking a receipt reversed by some other operation is refused."""
    message = _refused(
        db,
        "UPDATE settlements.pending_collection_receipts SET reversal_operation_id = :o, reversed_by = :u, "
        "reversed_at = now(), reversal_reason = 'x' WHERE id = :r",
        o=applied["close"],
        u=applied["admin"],
        r=applied["receipt"],
    )
    assert "is not the reversal of" in message


def test_only_the_responsible_changes_after_the_close(db: Session, applied, driver) -> None:  # type: ignore[no-untyped-def]
    """[45] In SQL: with the settlement CLOSED, an update of only the responsible passes; anything else, alone or
    with it, is refused."""
    juan = driver("Juan")
    db.execute(
        text(
            "UPDATE settlements.pending_collections SET responsible_employee_id = :e, updated_at = now() WHERE id = :p"
        ),
        {"e": juan, "p": applied["pending"]},
    )
    db.commit()
    message = _refused(
        db,
        "UPDATE settlements.pending_collections SET responsible_employee_id = :e, reason = 'Otro' WHERE id = :p",
        e=juan,
        p=applied["pending"],
    )
    assert "CLOSED" in message
    for column, value in (("expected_date", DAY), ("customer_name", "Otro"), ("amount", 400)):
        message = _refused(
            db,
            f"UPDATE settlements.pending_collections SET {column} = :v WHERE id = :p",  # noqa: S608
            v=value,
            p=applied["pending"],
        )
        assert "CLOSED" in message


def test_applied_never_exceeds_the_amount_in_effect(db: Session, applied, actor, driver, accounts) -> None:  # type: ignore[no-untyped-def]
    """$300 of $500 applied: a $201 receipt written by hand, operation and row together, is refused at commit,
    even with room in "Pendientes de cobro" (another settlement's $500)."""
    _closed_with_pending(db, actor, driver, accounts, "Luis")
    params = {"d": DAY, "k": uuid4(), "f": "a" * 64, "p": applied["pending"], "u": applied["admin"]}
    operation_id = db.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, client_request_id, "
            "request_fingerprint, source_type, source_id, created_by) VALUES ('PENDING_COLLECTION', :d, :k, :f, "
            "'pending_collection', :p, :u) RETURNING id"
        ),
        params,
    ).scalar_one()
    db.execute(
        text(
            "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) "
            "VALUES (:o, :pc, -201), (:o, :cash, 201)"
        ),
        {"o": operation_id, "pc": accounts["pending_collections"], "cash": accounts["main_cash"]},
    )
    message = _refused(
        db,
        "INSERT INTO settlements.pending_collection_receipts (pending_id, amount, business_date, method, "
        "destination_account_id, operation_id, client_request_id, request_fingerprint, created_by) "
        "VALUES (:p, 201, :d, 'CASH', :cash, :o, :k, :f, :u)",
        **params,
        cash=accounts["main_cash"],
        o=operation_id,
    )
    assert "exceeds its amount in effect" in message
