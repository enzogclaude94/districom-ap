"""Rules of the cheque cycle that the database enforces whoever writes (migration 0013), checked with raw SQL:
documents are append-only and change only to record their reversal; each has exactly its operation; a
deposit never takes an expired, unpayable or unverified cheque; a pending born from a cheque has exactly one
origin and is voided only with the reversal of that origin; and only a rejection may overdraw a bank."""

from collections.abc import Iterator
from datetime import date, timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import Connection, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.cheques import cycle
from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.cheques import service as cheques
from app.platform.auth.models import User

T = business_today()
ISSUED = T - timedelta(days=12)


def _bank(db: Session) -> int:
    return db.execute(text("SELECT id FROM financial_institutions WHERE bcra_code = 7")).scalar_one()  # type: ignore[no-any-return]


def _account(db: Session, code: str) -> int:
    return db.execute(text("SELECT id FROM settlements.accounts WHERE code = :c"), {"c": code}).scalar_one()  # type: ignore[no-any-return]


_numbers = iter(range(20000, 40000))


def _entry(db: Session, admin: User, issued: object = ISSUED, entered: object = None) -> int:
    number = str(next(_numbers))
    body = cs.AdministrativeEntryIn(
        client_request_id=uuid4(),
        kind="PHYSICAL",
        payment_type="COMMON",
        amount=Decimal(100000),
        issuer_bank_id=_bank(db),
        branch_code="123",
        account_ref=f"66{number}",
        cheque_number=number,
        issue_date=issued,  # type: ignore[arg-type]
        drawer_name="Distribuidora Sur SA",
        delivered_by_name="Almacén Don José",
        physical_receipt_confirmed=True,
        business_date=entered or T - timedelta(days=10),  # type: ignore[arg-type]
    )
    return cheques.opening_balance(db, admin, body).id


def _deposit(db: Session, operator: User, cid: int, on: date = T - timedelta(days=8)) -> int:
    body = cs.DepositIn(
        client_request_id=uuid4(), bank_account_id=_account(db, "bank"), cheque_ids=[cid], business_date=on
    )
    return cycle.deposit(db, operator, body).id


@pytest.fixture
def conn(db: Session) -> Iterator[Connection]:
    connection = db.get_bind().connect()  # type: ignore[union-attr]
    yield connection
    connection.rollback()
    connection.close()


@pytest.fixture
def deposited(db: Session, actor) -> dict[str, int]:  # type: ignore[no-untyped-def]
    operator = actor("operator")
    cid = _entry(db, actor("admin"))
    deposit_id = _deposit(db, operator, cid)
    return {"cheque": cid, "deposit": deposit_id}


def _refused(conn: Connection, sql: str, params: dict[str, object] | None = None, *, at_commit: bool = False) -> str:
    def _write() -> None:
        conn.execute(text(sql), params or {})
        if at_commit:
            conn.commit()

    with pytest.raises(DBAPIError) as excinfo:
        _write()
    conn.rollback()
    return str(excinfo.value.orig)


def test_documents_are_never_deleted_and_items_never_change(conn: Connection, deposited: dict[str, int]) -> None:
    _refused(conn, "DELETE FROM settlements.cheque_deposits WHERE id = :d", {"d": deposited["deposit"]})
    _refused(conn, "DELETE FROM settlements.cheque_deposit_items")
    _refused(conn, "UPDATE settlements.cheque_deposit_items SET amount = 1")


def test_a_document_changes_only_to_record_its_reversal(conn: Connection, deposited: dict[str, int]) -> None:
    message = _refused(
        conn, "UPDATE settlements.cheque_deposits SET notes = 'otra cosa' WHERE id = :d", {"d": deposited["deposit"]}
    )
    assert "only changes to record its reversal" in message
    # Pointing at an operation that is not the reversal of its own.
    other = conn.execute(text("SELECT min(id) FROM settlements.financial_operations")).scalar_one()
    message = _refused(
        conn,
        "UPDATE settlements.cheque_deposits SET reversal_operation_id = :o, reversed_by = created_by, "
        "reversed_at = now(), reversal_reason = 'x' WHERE id = :d",
        {"o": other, "d": deposited["deposit"]},
    )
    assert "is not the reversal" in message


def test_a_document_without_its_operation_is_refused_at_commit(conn: Connection, deposited: dict[str, int]) -> None:
    message = _refused(
        conn,
        "INSERT INTO settlements.cheque_deposits (bank_account_id, business_date, client_request_id, "
        "request_fingerprint, created_by) SELECT bank_account_id, business_date, gen_random_uuid(), "
        "request_fingerprint, created_by FROM settlements.cheque_deposits WHERE id = :d",
        {"d": deposited["deposit"]},
        at_commit=True,
    )
    assert "has no operation" in message


def test_a_document_has_one_operation(conn: Connection, deposited: dict[str, int]) -> None:
    message = _refused(
        conn,
        "INSERT INTO settlements.financial_operations (type, business_date, client_request_id, request_fingerprint, "
        "created_by, source_type, source_id) SELECT type, business_date, gen_random_uuid(), request_fingerprint, "
        "created_by, source_type, source_id FROM settlements.financial_operations WHERE type = 'CHEQUE_DEPOSIT'",
    )
    assert "uq_financial_operations_cheque_document" in message


def test_a_deposit_never_takes_an_expired_cheque(db: Session, conn: Connection, actor) -> None:  # type: ignore[no-untyped-def]
    issued = T - timedelta(days=35)
    cid = _entry(db, actor("admin"), issued=issued, entered=T - timedelta(days=34))
    deposit_id = _deposit(db, actor("operator"), _entry(db, actor("admin")), on=T)
    message = _refused(
        conn,
        "INSERT INTO settlements.cheque_deposit_items (deposit_id, cheque_id, amount) VALUES (:d, :c, 100000)",
        {"d": deposit_id, "c": cid},
    )
    assert "presentation expired" in message


def test_only_a_rejection_overdraws_a_bank(db: Session, conn: Connection, actor, driver) -> None:  # type: ignore[no-untyped-def]
    """The bank stays `allow_negative = false`: the trigger lets through the rejection of a credited cheque and
    nothing else, whatever writes it."""
    operator = actor("operator")
    cid = _entry(db, actor("admin"))
    _deposit(db, operator, cid)
    credit = cs.CreditIn(
        client_request_id=uuid4(), bank_account_id=_account(db, "bank"), cheque_ids=[cid], business_date=T
    )
    cycle.credit(db, operator, credit)
    # Spend the money, then the bank rejects the cheque: overdrawn.
    user_id = db.execute(text("SELECT id FROM users LIMIT 1")).scalar_one()
    withdrawal = conn.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, client_request_id, "
            "request_fingerprint, created_by) VALUES ('BANK_WITHDRAWAL', :d, gen_random_uuid(), :f, :u) RETURNING id"
        ),
        {"d": T, "f": "0" * 64, "u": user_id},
    ).scalar_one()
    for code, amount in (("bank", "-100000"), ("main_cash", "100000")):
        conn.execute(
            text(
                "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) "
                "SELECT :o, id, :m FROM settlements.accounts WHERE code = :c"
            ),
            {"o": withdrawal, "m": amount, "c": code},
        )
    conn.commit()
    body = cs.RejectionIn(
        client_request_id=uuid4(), reason="Sin fondos", business_date=T, responsible_employee_id=driver("Ana")
    )
    cycle.reject(db, operator, cid, body)
    balance = conn.execute(
        text("SELECT sum(amount) FROM settlements.ledger_entries WHERE account_id = :a"), {"a": _account(db, "bank")}
    ).scalar_one()
    assert balance == Decimal("-100000.00")
    assert (
        conn.execute(text("SELECT allow_negative FROM settlements.accounts WHERE code = 'bank'")).scalar_one() is False
    )


def test_a_pending_born_from_a_cheque_has_one_origin_and_is_voided_only_with_it(
    db: Session,
    conn: Connection,
    actor,
    driver,  # type: ignore[no-untyped-def]
) -> None:
    operator = actor("operator")
    cid = _entry(db, actor("admin"))
    _deposit(db, operator, cid)
    body = cs.RejectionIn(
        client_request_id=uuid4(), reason="Sin fondos", business_date=T, responsible_employee_id=driver("Ana")
    )
    rejection_id = cycle.reject(db, operator, cid, body).id
    pending_id = conn.execute(
        text("SELECT id FROM settlements.pending_collections WHERE origin_cheque_rejection_id = :r"),
        {"r": rejection_id},
    ).scalar_one()
    settlement_like = _refused(
        conn, "UPDATE settlements.pending_collections SET amount = 1 WHERE id = :p", {"p": pending_id}
    )
    assert "only changes its responsible" in settlement_like
    voided = _refused(
        conn,
        "UPDATE settlements.pending_collections SET voided_at = now(), voided_by = created_by, void_reason = 'x' "
        "WHERE id = :p",
        {"p": pending_id},
    )
    assert "voided with the reversal of its origin" in voided
    two_origins = _refused(
        conn,
        "UPDATE settlements.pending_collections SET origin_cheque_return_id = origin_cheque_rejection_id WHERE id = :p",
        {"p": pending_id},
    )
    assert "pending_collections" in two_origins
