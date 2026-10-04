"""Rules of settlements that the database enforces whoever writes (migration 0007), checked with raw SQL."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.delivery import additions, schemas, service, transfers

DAY = business_today() - timedelta(days=1)


@pytest.fixture
def loaded(db: Session, actor, driver, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """An OPEN settlement with one count line, one addition, one transfer and one expense."""
    operator = actor("operator")
    created = service.create(
        db,
        operator,
        schemas.SettlementIn(
            client_request_id=uuid4(), business_date=DAY, employee_id=driver("Pedro"), expected_amount=Decimal(100)
        ),
    )
    sid = created.id
    service.replace_count(
        db,
        operator,
        sid,
        schemas.CountIn(
            lines=[
                schemas.CountLineIn(
                    denomination_code="ARS_100", quantity=1, destination_account_id=accounts["main_cash"]
                )
            ]
        ),
    )
    addition = additions.create(
        db,
        operator,
        sid,
        schemas.AdditionIn(client_request_id=uuid4(), amount=Decimal(5), destination_account_id=accounts["main_cash"]),
    )
    transfer = transfers.create(db, operator, sid, schemas.TransferIn(client_request_id=uuid4(), amount=Decimal(7)))
    category = db.execute(
        text("INSERT INTO settlements.expense_categories (name, name_key) VALUES ('Peajes', 'peajes') RETURNING id")
    ).scalar_one()
    expense = db.execute(
        text(
            "INSERT INTO settlements.expenses (settlement_id, category_id, amount, client_request_id, "
            "request_fingerprint, created_by) VALUES (:s, :c, 3, :k, repeat('a', 64), :u) RETURNING id"
        ),
        {"s": sid, "c": category, "k": uuid4(), "u": operator.id},
    ).scalar_one()
    db.commit()
    return {"settlement": sid, "addition": addition.id, "transfer": transfer.id, "expense": expense}


def close_in_database(db: Session, settlement_id: int) -> None:
    """Close a settlement with raw SQL: a SETTLEMENT_CLOSE of it in effect, as the close flow leaves it."""
    operation = db.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
            "request_fingerprint, source_type, source_id, created_by) SELECT 'SETTLEMENT_CLOSE', business_date, "
            "'Cierre', gen_random_uuid(), repeat('a', 64), 'settlement', id, created_by "
            "FROM settlements.settlements WHERE id = :s RETURNING id"
        ),
        {"s": settlement_id},
    ).scalar_one()
    db.execute(
        text(
            "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) SELECT :o, id, "
            "CASE code WHEN 'main_cash' THEN 1 ELSE -1 END FROM settlements.accounts "
            "WHERE code IN ('main_cash', 'delivery_collections')"
        ),
        {"o": operation},
    )
    db.execute(
        text("UPDATE settlements.settlements SET status = 'CLOSED', current_close_operation_id = :o WHERE id = :s"),
        {"o": operation, "s": settlement_id},
    )
    db.commit()


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def test_nothing_of_a_settlement_is_deleted(db: Session, loaded) -> None:  # type: ignore[no-untyped-def]
    for table, key in (
        ("settlements", "settlement"),
        ("cash_additions", "addition"),
        ("settlement_transfers", "transfer"),
        ("expenses", "expense"),
    ):
        message = _refused(db, f"DELETE FROM settlements.{table} WHERE id = :id", id=loaded[key])  # noqa: S608
        assert "never deleted" in message, table
    assert "never deleted" in _refused(db, "DELETE FROM settlements.expense_categories")
    assert "never deleted" in _refused(db, "UPDATE settlements.transfer_status_events SET reason = 'x'")


def test_an_addition_is_only_voided(db: Session, loaded, actor) -> None:  # type: ignore[no-untyped-def]
    """[11] [12] at the database level: no edit, and a void keeps the row."""
    addition = loaded["addition"]
    assert "only voided" in _refused(
        db, "UPDATE settlements.cash_additions SET amount = 50 WHERE id = :id", id=addition
    )
    assert "void_complete" in _refused(
        db, "UPDATE settlements.cash_additions SET voided_at = now() WHERE id = :id", id=addition
    )
    db.execute(
        text(
            "UPDATE settlements.cash_additions SET voided_at = now(), voided_by = :u, void_reason = 'error' "
            "WHERE id = :id"
        ),
        {"u": actor("operator").id, "id": addition},
    )
    db.commit()
    assert "only voided" in _refused(
        db, "UPDATE settlements.cash_additions SET void_reason = 'otro' WHERE id = :id", id=addition
    )
    row = db.execute(
        text("SELECT amount, void_reason FROM settlements.cash_additions WHERE id = :id"), {"id": addition}
    )
    assert tuple(row.one()) == (Decimal("5.00"), "error")


def test_a_closed_settlement_is_frozen(db: Session, loaded, actor, accounts) -> None:  # type: ignore[no-untyped-def]
    """[18] at the database level."""
    sid = loaded["settlement"]
    close_in_database(db, sid)
    user = actor("operator").id
    statements = [
        ("UPDATE settlements.settlements SET expected_amount = 1 WHERE id = :s", {}),
        ("UPDATE settlements.cash_count_lines SET quantity = 9 WHERE settlement_id = :s", {}),
        ("DELETE FROM settlements.cash_count_lines WHERE settlement_id = :s", {}),
        (
            "INSERT INTO settlements.cash_count_lines (settlement_id, denomination_code, denomination_value, quantity, "
            "destination_account_id, created_by) VALUES (:s, 'ARS_200', 200, 1, :box, :u)",
            {"box": accounts["main_cash"], "u": user},
        ),
        (
            "UPDATE settlements.cash_additions SET voided_at = now(), voided_by = :u, void_reason = 'x' "
            "WHERE settlement_id = :s",
            {"u": user},
        ),
        ("UPDATE settlements.settlement_transfers SET status = 'VERIFIED' WHERE settlement_id = :s", {}),
        ("UPDATE settlements.expenses SET amount = 1 WHERE settlement_id = :s", {}),
    ]
    for sql, params in statements:
        message = _refused(db, sql, s=sid, **params)
        assert "CLOSED" in message, sql


def test_driver_date_and_number_never_change(db: Session, loaded) -> None:  # type: ignore[no-untyped-def]
    sid = loaded["settlement"]
    for change in ("sequence_number = 2", "business_date = business_date - 1"):
        message = _refused(db, f"UPDATE settlements.settlements SET {change} WHERE id = :s", s=sid)  # noqa: S608
        assert "never change" in message, change


def test_sequence_is_unique_per_driver_and_day(db: Session, loaded) -> None:  # type: ignore[no-untyped-def]
    message = _refused(
        db,
        "INSERT INTO settlements.settlements (type, business_date, employee_id, sequence_number, expected_amount, "
        "client_request_id, request_fingerprint, created_by) SELECT type, business_date, employee_id, "
        "sequence_number, 1, :k, request_fingerprint, created_by FROM settlements.settlements WHERE id = :s",
        k=uuid4(),
        s=loaded["settlement"],
    )
    assert "uq_settlements_sequence" in message


def test_a_rejected_transfer_has_a_reason(db: Session, loaded) -> None:  # type: ignore[no-untyped-def]
    message = _refused(
        db, "UPDATE settlements.settlement_transfers SET status = 'REJECTED' WHERE id = :t", t=loaded["transfer"]
    )
    assert "rejection_reason" in message
