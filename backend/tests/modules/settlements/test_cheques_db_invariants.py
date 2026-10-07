"""Rules of cheques that the database enforces whoever writes (migration 0012), checked with raw SQL:
[36] append-only events and snapshots, [20] [22] a published cheque never changes its amount, identity or
who delivered it, [32] "Cartera de cheques" never negative and each cheque whole and in one place, a leg
names a cheque exactly on a cheque account, and a close's snapshot equal to its portfolio legs."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.cheques import service as cheques
from app.modules.settlements.delivery import closing, schemas, service

DAY = business_today() - timedelta(days=1)


def _bank(db: Session) -> int:
    return db.execute(text("SELECT id FROM financial_institutions WHERE bcra_code = 7")).scalar_one()  # type: ignore[no-any-return]


@pytest.fixture
def closed(db: Session, actor, driver) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """$50.000 expected, justified by one physical cheque, closed."""
    operator = actor("operator")
    sid = service.create(
        db,
        operator,
        schemas.SettlementIn(
            client_request_id=uuid4(), business_date=DAY, employee_id=driver("Pedro"), expected_amount=Decimal(50000)
        ),
    ).id
    cheques.create(
        db,
        operator,
        sid,
        cs.ChequeIn(
            client_request_id=uuid4(),
            kind="PHYSICAL",
            payment_type="COMMON",
            amount=Decimal(50000),
            issuer_bank_id=_bank(db),
            branch_code="123",
            account_ref="4567890",
            cheque_number="12345",
            issue_date=DAY - timedelta(days=3),
            drawer_name="Distribuidora Sur SA",
            delivered_by_name="Almacén Don José",
            physical_receipt_confirmed=True,
        ),
    )
    token = closing.preview(db, sid).token
    closing.close(db, operator, sid, schemas.CloseIn(version=1, preview_token=token, confirm=True))
    row = db.execute(
        text(
            "SELECT c.id, s.current_close_operation_id FROM settlements.cheques c "
            "JOIN settlements.settlements s ON s.id = c.settlement_id WHERE s.id = :s"
        ),
        {"s": sid},
    ).one()
    return {"settlement": sid, "cheque": row[0], "close": row[1], "user": operator.id}


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def test_a_cheque_is_never_deleted_and_its_history_is_append_only(db: Session, closed) -> None:  # type: ignore[no-untyped-def]  # [36]
    assert "never deleted" in _refused(db, "DELETE FROM settlements.cheques WHERE id = :c", c=closed["cheque"])
    assert "never deleted" in _refused(db, "UPDATE settlements.cheque_events SET reason = 'x'")
    assert "never deleted" in _refused(db, "DELETE FROM settlements.cheque_events")
    assert "never deleted" in _refused(db, "UPDATE settlements.settlement_closing_cheques SET amount = 1")
    assert "never deleted" in _refused(db, "DELETE FROM settlements.settlement_closing_cheques")


def test_a_published_cheque_keeps_amount_identity_and_who_delivered_it(db: Session, closed) -> None:  # type: ignore[no-untyped-def]  # [20] [22]
    # Even with its settlement reopened, as a correction would.
    db.execute(
        text("UPDATE settlements.settlements SET status = 'OPEN', version = 2 WHERE id = :s"),
        {"s": closed["settlement"]},
    )
    db.commit()
    for change in (
        "amount = 1",
        "cheque_number = '9'",
        "account_ref = '9'",
        "instrument_key = 'P:x'",
        "delivered_by_name = 'Otro'",
        "payment_type = 'DEFERRED', payment_date = issue_date + 10",
    ):
        message = _refused(db, f"UPDATE settlements.cheques SET {change} WHERE id = :c", c=closed["cheque"])  # noqa: S608
        assert "financial effect" in message, change
    assert "never change" in _refused(
        db, "UPDATE settlements.cheques SET client_request_id = :k WHERE id = :c", k=uuid4(), c=closed["cheque"]
    )
    # Descriptive data does change (the service asks for an admin and a reason).
    db.execute(text("UPDATE settlements.cheques SET notes = 'x' WHERE id = :c"), {"c": closed["cheque"]})
    db.commit()


def test_a_closed_settlement_freezes_its_cheques_load(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    message = _refused(
        db,
        "UPDATE settlements.cheques SET voided_at = now(), voided_by = :u, void_reason = 'x' WHERE id = :c",
        u=closed["user"],
        c=closed["cheque"],
    )
    assert "not OPEN" in message
    message = _refused(
        db,
        "INSERT INTO settlements.cheques (kind, payment_type, amount, issuer_bank_id, cheque_number, issue_date, "
        "drawer_name, delivered_by_name, origin_type, settlement_id, received_business_date, "
        "physical_receipt_confirmed_by, physical_receipt_confirmed_at, client_request_id, request_fingerprint, "
        "created_by) VALUES ('PHYSICAL', 'COMMON', 1, :b, '1', :d, 'x', 'x', 'SETTLEMENT', :s, :d, :u, now(), :k, "
        ":f, :u)",
        b=_bank(db),
        d=DAY,
        s=closed["settlement"],
        u=closed["user"],
        k=uuid4(),
        f="a" * 64,
    )
    assert "not OPEN" in message


_RAW_ADJUSTMENT = (
    "WITH op AS (INSERT INTO settlements.financial_operations (type, business_date, description, "
    "client_request_id, request_fingerprint, created_by) VALUES ('ADJUSTMENT', :d, 'A mano', :k, :f, :u) "
    "RETURNING id) "
    "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount, cheque_id) "
    "SELECT op.id, a.id, CAST(:amount AS numeric), :c FROM op, settlements.accounts a "
    "WHERE a.code = 'cheque_portfolio' "
    "UNION ALL SELECT op.id, a.id, -CAST(:amount AS numeric), NULL FROM op, settlements.accounts a "
    "WHERE a.code = 'adjustment'"
)


def _take_out(db: Session, closed: dict[str, int], *amounts: str) -> str:
    """Raw operations moving the cheque out of the portfolio, in one transaction: the error at commit."""

    def _write() -> None:
        for amount in amounts:
            db.execute(
                text(_RAW_ADJUSTMENT),
                {"d": DAY, "k": uuid4(), "f": "a" * 64, "u": closed["user"], "c": closed["cheque"], "amount": amount},
            )
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def test_portfolio_is_never_negative_nor_partial(db: Session, closed) -> None:  # type: ignore[no-untyped-def]  # [32]
    assert "whole" in _take_out(db, closed, "-20000")
    assert "whole" in _take_out(db, closed, "-100000")
    # Out twice: the second leaves the cheque, and the portfolio, negative.
    assert "negative" in _take_out(db, closed, "-50000", "-50000")
    db.rollback()
    balance = db.execute(
        text(
            "SELECT sum(e.amount) FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id "
            "WHERE a.kind = 'CHEQUE_PORTFOLIO'"
        )
    ).scalar_one()
    assert balance == Decimal("50000.00")


def test_a_leg_names_a_cheque_only_on_a_cheque_account(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    message = _refused(
        db,
        "WITH op AS (INSERT INTO settlements.financial_operations (type, business_date, description, "
        "client_request_id, request_fingerprint, created_by) VALUES ('ADJUSTMENT', :d, 'A mano', :k, :f, :u) "
        "RETURNING id) "
        "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount, cheque_id) "
        "SELECT op.id, a.id, CASE a.code WHEN 'main_cash' THEN 10 ELSE -10 END, "
        "CASE a.code WHEN 'main_cash' THEN :c END FROM op, settlements.accounts a "
        "WHERE a.code IN ('main_cash', 'adjustment')",
        d=DAY,
        k=uuid4(),
        f="a" * 64,
        u=closed["user"],
        c=closed["cheque"],
    )
    assert "iff its account is a cheque account" in message


def test_a_close_snapshot_equals_its_portfolio_legs(db: Session, closed) -> None:  # type: ignore[no-untyped-def]  # [24]
    """A snapshot row that is not a leg of its close (or a leg without its row) is refused at commit."""
    db.execute(
        text("UPDATE settlements.settlements SET status = 'OPEN', version = 2 WHERE id = :s"),
        {"s": closed["settlement"]},
    )
    db.commit()
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_closing_cheques (close_operation_id, settlement_id, settlement_version, "
        "cheque_id, amount) VALUES (:o, :s, 1, :c, 50000)",
        o=closed["close"],
        s=closed["settlement"],
        c=closed["cheque"],
    )
    assert "uq_settlement_closing_cheques_close_cheque" in message  # already frozen by that close
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_closing_cheques (close_operation_id, settlement_id, settlement_version, "
        "cheque_id, amount) VALUES (:o, :s, 1, :c, 1)",
        o=closed["close"],
        s=closed["settlement"],
        c=closed["cheque"],
    )
    assert "of another amount" in message
