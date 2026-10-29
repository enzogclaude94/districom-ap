"""The database protects the ledger even from code that skips the service (migration 0005).

These tests write raw SQL on purpose: application code must never do this (single writer).
"""

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from random import Random
from uuid import uuid4

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from tests.modules.settlements.days import today

FINGERPRINT = "0" * 64


@pytest.fixture
def conn(db: Session, make_user) -> Iterator[Connection]:  # type: ignore[no-untyped-def]
    make_user()
    connection = db.get_bind().connect()  # type: ignore[union-attr]
    yield connection
    connection.close()


def _user_id(conn: Connection) -> int:
    return int(conn.execute(text("SELECT id FROM users LIMIT 1")).scalar_one())


def _account(conn: Connection, code: str) -> int:
    return int(conn.execute(text("SELECT id FROM settlements.accounts WHERE code = :c"), {"c": code}).scalar_one())


def _insert_operation(conn: Connection, *legs: tuple[str, str], type: str = "CASH_TRANSFER", **columns: object) -> int:
    values = {
        "type": type,
        "description": None,
        "reverses_operation_id": None,
        "client_request_id": uuid4(),
        "fingerprint": FINGERPRINT,
        "user": _user_id(conn),
        "business_date": today(),
        **columns,
    }
    op_id = int(
        conn.execute(
            text(
                "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id,"
                " request_fingerprint, reverses_operation_id, created_by) VALUES (:type, :business_date, :description,"
                " :client_request_id, :fingerprint, :reverses_operation_id, :user) RETURNING id"
            ),
            values,
        ).scalar_one()
    )
    for code, amount in legs:
        conn.execute(
            text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)"),
            {"o": op_id, "a": _account(conn, code), "m": amount},
        )
    return op_id


def _committed(conn: Connection, *legs: tuple[str, str], **kwargs: object) -> int:
    op_id = _insert_operation(conn, *legs, **kwargs)
    conn.commit()
    return op_id


def _fund(conn: Connection) -> None:
    _committed(conn, ("main_cash", "100"), ("bank", "100"), ("opening_balance", "-200"), type="OPENING_BALANCE")


def _fails_at_commit(conn: Connection, *legs: tuple[str, str], **kwargs: object) -> str:
    _insert_operation(conn, *legs, **kwargs)
    with pytest.raises(DBAPIError) as excinfo:
        conn.commit()
    conn.rollback()
    return str(excinfo.value.orig)


def test_unbalanced_operation_is_rejected_at_commit(conn: Connection) -> None:
    _fund(conn)
    message = _fails_at_commit(conn, ("main_cash", "-10"), ("change_cash", "9"))
    assert "not balanced" in message


def test_operation_without_legs_is_rejected_at_commit(conn: Connection) -> None:
    assert "legs: 0" in _fails_at_commit(conn)


def test_legs_cannot_be_added_to_a_committed_operation_unbalancing_it(conn: Connection) -> None:
    _fund(conn)
    op_id = _committed(conn, ("main_cash", "-10"), ("change_cash", "10"))
    conn.execute(
        text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, 5)"),
        {"o": op_id, "a": _account(conn, "change_cash")},
    )
    with pytest.raises(DBAPIError, match="not balanced"):
        conn.commit()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE settlements.ledger_entries SET amount = amount * 2",
        "DELETE FROM settlements.ledger_entries",
        "UPDATE settlements.financial_operations SET description = 'edited'",
        "DELETE FROM settlements.financial_operations",
    ],
)
def test_ledger_is_append_only(conn: Connection, statement: str) -> None:
    _fund(conn)
    with pytest.raises(DBAPIError, match="append-only"):
        conn.execute(text(statement))
    conn.rollback()


@pytest.mark.parametrize("code", ["main_cash", "change_cash", "bank"])
def test_cash_boxes_and_bank_cannot_go_negative(conn: Connection, code: str) -> None:
    _fund(conn)
    message = _fails_at_commit(conn, (code, "-100.01"), ("adjustment", "100.01"), type="ADJUSTMENT", description="x")
    assert "negative balance" in message


def test_negative_check_follows_allow_negative(conn: Connection) -> None:
    owner = int(
        conn.execute(
            text(
                "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative)"
                " VALUES ('Socio', 'socio', 'OWNER_EQUITY', true)"
                " RETURNING id"
            )
        ).scalar_one()
    )
    conn.commit()
    op_id = _insert_operation(conn, ("main_cash", "50"), type="OWNER_CONTRIBUTION")
    conn.execute(
        text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, -50)"),
        {"o": op_id, "a": owner},
    )
    conn.commit()  # partner accounts may be negative


def test_cash_box_can_never_allow_negative(conn: Connection) -> None:
    with pytest.raises(IntegrityError, match="ck_accounts_cash_box_not_negative"):
        conn.execute(text("UPDATE settlements.accounts SET allow_negative = true WHERE code = 'main_cash'"))
    conn.rollback()


def test_only_one_opening_balance_and_adjustment_account(conn: Connection) -> None:
    for kind in ("OPENING_BALANCE", "ADJUSTMENT"):
        with pytest.raises(IntegrityError, match="uq_accounts_technical_kind"):
            conn.execute(
                text(
                    "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative) VALUES (:n, :n, :k, true)"
                ),
                {"n": f"otra {kind}", "k": kind},
            )
        conn.rollback()


@pytest.mark.parametrize(
    ("columns", "constraint"),
    [
        ({"type": "ADJUSTMENT"}, "ck_financial_operations_reason_required"),
        ({"type": "ADJUSTMENT", "description": "  "}, "ck_financial_operations_reason_required"),
        ({"type": "REVERSAL", "description": "x"}, "ck_financial_operations_reversal_link"),
        ({"type": "MAGIC"}, "ck_financial_operations_type"),
        ({"fingerprint": "not-a-hash"}, "ck_financial_operations_fingerprint_format"),
    ],
)
def test_operation_checks(conn: Connection, columns: dict[str, object], constraint: str) -> None:
    with pytest.raises(IntegrityError, match=constraint):
        _insert_operation(conn, **columns)
    conn.rollback()


def test_client_request_id_is_unique_and_reversal_happens_once(conn: Connection) -> None:
    _fund(conn)
    key = uuid4()
    original = _committed(conn, ("main_cash", "-10"), ("change_cash", "10"), client_request_id=key)
    with pytest.raises(IntegrityError, match="uq_financial_operations_client_request_id"):
        _insert_operation(conn, ("main_cash", "-10"), ("change_cash", "10"), client_request_id=key)
    conn.rollback()

    _committed(
        conn,
        ("main_cash", "10"),
        ("change_cash", "-10"),
        type="REVERSAL",
        description="x",
        reverses_operation_id=original,
    )
    with pytest.raises(IntegrityError, match="uq_financial_operations_reverses_operation_id"):
        _insert_operation(
            conn,
            ("main_cash", "10"),
            ("change_cash", "-10"),
            type="REVERSAL",
            description="x",
            reverses_operation_id=original,
        )
    conn.rollback()


def test_accounts_with_entries_cannot_be_deleted(conn: Connection) -> None:
    _fund(conn)
    with pytest.raises(IntegrityError):
        conn.execute(text("DELETE FROM settlements.accounts WHERE code = 'main_cash'"))
    conn.rollback()


def test_concurrent_raw_withdrawals_cannot_overdraw(db: Session, conn: Connection) -> None:
    """Two transactions that each see $100 and withdraw $80: the one that commits second fails."""
    _fund(conn)
    other = db.get_bind().connect()  # type: ignore[union-attr]
    try:
        _insert_operation(conn, ("main_cash", "-80"), ("adjustment", "80"), type="ADJUSTMENT", description="a")
        _insert_operation(other, ("main_cash", "-80"), ("adjustment", "80"), type="ADJUSTMENT", description="b")
        conn.commit()
        with pytest.raises(DBAPIError, match="negative balance"):
            other.commit()
    finally:
        other.close()


def test_retroactive_raw_withdrawal_cannot_leave_a_past_day_negative(conn: Connection) -> None:
    """Enzo's example: +100.000, -80.000, +200.000 on three days; -50.000 on day 2 is rejected."""
    day1, day2, day3 = (today() - timedelta(days=n) for n in (3, 2, 1))
    _committed(
        conn, ("main_cash", "100000"), ("opening_balance", "-100000"), type="OPENING_BALANCE", business_date=day1
    )
    _committed(conn, ("main_cash", "-80000"), ("change_cash", "80000"), business_date=day2)
    _committed(
        conn, ("main_cash", "200000"), ("adjustment", "-200000"), type="ADJUSTMENT", description="x", business_date=day3
    )
    message = _fails_at_commit(conn, ("main_cash", "-50000"), ("change_cash", "50000"), business_date=day2)
    assert "negative balance" in message
    assert day2.isoformat() in message
    _committed(conn, ("main_cash", "-20000"), ("change_cash", "20000"), business_date=day2)


def test_not_negative_trigger_matches_a_plain_end_of_day_check(conn: Connection) -> None:
    """0031 reads only the days after a recent debit and keeps 0013's single pass for older ones; either way it must
    refuse exactly what a plain check of every end-of-day balance from the debit's date on refuses, and name the
    same lowest balance and day (the earliest one on a tie). Most debits probe the edge: they take the lowest balance
    from their day on to exactly zero, a cent below or a little above."""
    rng = Random(31)  # noqa: S311 - a reproducible sequence, never a secret
    start = today() - timedelta(days=45)
    booked: list[tuple[date, Decimal]] = []

    def end_of(day: date, *extra: tuple[date, Decimal]) -> Decimal:
        return sum((amount for when, amount in [*booked, *extra] if when <= day), Decimal(0))

    _committed(
        conn, ("main_cash", "300.00"), ("opening_balance", "-300.00"), type="OPENING_BALANCE", business_date=start
    )
    booked.append((start, Decimal("300.00")))
    refused: set[tuple[bool, bool]] = set()
    for _ in range(150):
        day = start + timedelta(days=rng.randint(0, 45))
        days = sorted({when for when, _ in booked} | {day})
        if rng.random() < 0.3:
            amount = Decimal(rng.choice(["10.00", "25.50", "40.00", "60.00"]))
        else:
            floor = min(end_of(when) for when in days if when >= day)
            margin = Decimal(rng.choice(["-5.00", "0.00", "0.00", "0.01"]))
            amount = -(floor + margin) if floor > 5 else Decimal("-0.01")
        lowest, lowest_day = min((end_of(when, (day, amount)), when) for when in days if when >= day)
        legs = (("main_cash", str(amount)), ("adjustment", str(-amount)))
        if amount < 0 and lowest < 0:
            message = _fails_at_commit(conn, *legs, type="ADJUSTMENT", description="x", business_date=day)
            assert f"negative balance ({lowest} on {lowest_day.isoformat()})" in message
            refused.add((day < today() - timedelta(days=31), lowest_day == day))
        else:
            _committed(conn, *legs, type="ADJUSTMENT", description="x", business_date=day)
            booked.append((day, amount))
    # Refusals on the debit's own day and on a later one, for recent and for older debits.
    assert refused == {(False, False), (False, True), (True, False), (True, True)}
