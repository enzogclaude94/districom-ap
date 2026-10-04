"""Rules of closes, differences and internal accounts that the database enforces whoever writes
(migration 0008), checked with raw SQL."""

from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.delivery import closing, schemas, service

DAY = business_today() - timedelta(days=1)
FINGERPRINT = "f" * 64


@pytest.fixture
def closed(db: Session, actor, driver, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """A settlement closed with a $500 shortage charged to its driver."""
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
    charge = schemas.AllocationIn(target="EMPLOYEE", employee_id=pedro, amount=Decimal(500))
    closing.close(db, operator, sid, schemas.CloseIn(version=1, allocations=[charge], confirm=True))
    row = db.execute(
        text(
            "SELECT s.current_close_operation_id, d.id FROM settlements.settlements s "
            "JOIN settlements.settlement_differences d ON d.settlement_id = s.id WHERE s.id = :s"
        ),
        {"s": sid},
    ).one()
    return {"settlement": sid, "close": row[0], "difference": row[1], "user": operator.id, "pedro": pedro}


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def _operation(db: Session, type: str, user: int, source_id: int | None = None, reverses: int | None = None) -> int:
    return int(
        db.execute(
            text(
                "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
                "request_fingerprint, source_type, source_id, reverses_operation_id, created_by) VALUES "
                "(:t, :d, 'x', :k, :f, :st, :si, :r, :u) RETURNING id"
            ),
            {
                "t": type,
                "d": DAY,
                "k": uuid4(),
                "f": FINGERPRINT,
                "st": "settlement" if source_id else None,
                "si": source_id,
                "r": reverses,
                "u": user,
            },
        ).scalar_one()
    )


def _legs(db: Session, operation_id: int, *legs: tuple[int, str]) -> None:
    for account_id, amount in legs:
        db.execute(
            text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)"),
            {"o": operation_id, "a": account_id, "m": amount},
        )


def test_closings_and_allocations_are_append_only(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    for sql in (
        "UPDATE settlements.settlement_closings SET request_fingerprint = repeat('0', 64)",
        "DELETE FROM settlements.settlement_closings",
        "UPDATE settlements.difference_allocations SET amount = 1",
        "DELETE FROM settlements.difference_allocations",
        "DELETE FROM settlements.settlement_differences",
    ):
        assert "never deleted" in _refused(db, sql), sql


def test_a_difference_is_only_superseded(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    """[27] at the database level: never edited, superseded once and kept."""
    diff = closed["difference"]
    assert "only superseded" in _refused(
        db, "UPDATE settlements.settlement_differences SET amount = 1 WHERE id = :d", d=diff
    )
    assert "only superseded" in _refused(
        db, "UPDATE settlements.settlement_differences SET superseded_at = now() WHERE id = :d", d=diff
    )
    db.execute(
        text("UPDATE settlements.settlement_differences SET superseded_at = now(), superseded_by = :u WHERE id = :d"),
        {"u": closed["user"], "d": diff},
    )
    db.commit()
    assert "only superseded" in _refused(
        db, "UPDATE settlements.settlement_differences SET superseded_at = now() WHERE id = :d", d=diff
    )


def test_allocations_add_up_to_the_difference(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    """Deferred: checked at commit, once every allocation is in."""
    insert = (
        "INSERT INTO settlements.settlement_differences (settlement_id, settlement_version, kind, amount, "
        "close_operation_id, created_by) VALUES (:s, 9, 'SHORTAGE', 100, :o, :u) RETURNING id"
    )
    params = {"s": closed["settlement"], "o": closed["close"], "u": closed["user"]}
    db.execute(text("UPDATE settlements.settlement_differences SET superseded_at = now(), superseded_by = :u"), params)
    db.commit()
    assert "not fully allocated" in _refused(db, insert, **params)

    def _with_allocation(amount: str) -> str:
        diff = db.execute(text(insert), params).scalar_one()
        return _refused(
            db,
            "INSERT INTO settlements.difference_allocations (difference_id, target, employee_id, amount, decided_by) "
            "VALUES (:d, 'EMPLOYEE', :e, :m, :u)",
            d=diff,
            e=closed["pedro"],
            m=amount,
            u=closed["user"],
        )

    assert "not fully allocated" in _with_allocation("60")
    assert "not fully allocated" in _with_allocation("120")


def test_company_part_needs_a_reason(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    message = _refused(
        db,
        "INSERT INTO settlements.difference_allocations (difference_id, target, amount, reason, decided_by) "
        "VALUES (:d, 'COMPANY', 1, '  ', :u)",
        d=closed["difference"],
        u=closed["user"],
    )
    assert "company_reason_required" in message


def test_one_close_in_effect_per_settlement(db: Session, closed, accounts) -> None:  # type: ignore[no-untyped-def]
    second = _operation(db, "SETTLEMENT_CLOSE", closed["user"], source_id=closed["settlement"])
    _legs(db, second, (accounts["main_cash"], "1"), (accounts["delivery_collections"], "-1"))
    with pytest.raises(DBAPIError) as caught:
        db.commit()
    db.rollback()
    assert "more than one close in effect" in str(caught.value.orig)


def test_the_close_in_effect_cannot_be_reversed_behind_the_settlements_back(db: Session, closed, accounts) -> None:  # type: ignore[no-untyped-def]
    """A raw reversal of the close a CLOSED settlement points to is refused at commit."""
    reversal = _operation(db, "REVERSAL", closed["user"], reverses=closed["close"])
    _legs(
        db,
        reversal,
        (accounts["main_cash"], "-1000"),
        (accounts["delivery_collections"], "1500"),
        (db.execute(text("SELECT id FROM settlements.accounts WHERE kind = 'EMPLOYEE'")).scalar_one(), "-500"),
    )
    with pytest.raises(DBAPIError) as caught:
        db.commit()
    db.rollback()
    assert "is not a close of it in effect" in str(caught.value.orig)


def test_a_closed_settlement_points_to_its_own_close(db: Session, closed, accounts) -> None:  # type: ignore[no-untyped-def]
    other = _operation(db, "CASH_TRANSFER", closed["user"])
    _legs(db, other, (accounts["main_cash"], "-1"), (accounts["change_cash"], "1"))
    db.commit()
    message = _refused(
        db,
        "UPDATE settlements.settlements SET current_close_operation_id = :o WHERE id = :s",
        o=other,
        s=closed["settlement"],
    )
    assert "not a close of it in effect" in message
    assert "closed_has_close" in _refused(
        db, "UPDATE settlements.settlements SET current_close_operation_id = NULL WHERE id = :s", s=closed["settlement"]
    )


def test_reopen_data_is_complete(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    message = _refused(
        db,
        "UPDATE settlements.settlements SET status = 'OPEN', version = 2, reopened_at = now() WHERE id = :s",
        s=closed["settlement"],
    )
    assert "reopen_complete" in message


def test_one_internal_account_per_employee(db: Session, closed) -> None:  # type: ignore[no-untyped-def]
    """[16] at the database level, and an EMPLOYEE account always has its employee."""
    duplicate = _refused(
        db,
        "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative, employee_id) "
        "VALUES ('Otra', 'otra', 'EMPLOYEE', true, :e)",
        e=closed["pedro"],
    )
    assert "uq_accounts_employee_id" in duplicate
    orphan = _refused(
        db,
        "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative) "
        "VALUES ('Sin', 'sin', 'EMPLOYEE', true)",
    )
    assert "employee_link" in orphan
    linked_box = _refused(
        db,
        "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative, employee_id) "
        "VALUES ('Caja', 'caja', 'CASH_BOX', false, :e)",
        e=closed["pedro"],
    )
    assert "employee_link" in linked_box
    technical = _refused(
        db,
        "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative) "
        "VALUES ('Otra cobranza', 'otra cobranza', 'DELIVERY_COLLECTIONS', true)",
    )
    assert "uq_accounts_technical_kind" in technical
