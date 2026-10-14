"""Rules of the remittances of a boca that the database enforces whoever writes (migration 0024), checked with raw
SQL. The load is made through the API; each case then tries to break one rule directly (Enzo's R7-3 point 16)."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.days import days_ago, yesterday
from tests.modules.settlements.outlet_support import ZERO_CLOSE_SQL, close, count, create, reopen
from tests.modules.settlements.remittance_support import at, remit, remittance_row, reverse

FINGERPRINT = "b" * 64
ZERO_REFUSED = "an administrative zero close needs a boca settlement of $0 without movements"


@pytest.fixture
def centro(boca: Callable[[str], int]) -> int:
    return boca("CENTRO")


@pytest.fixture
def norte(boca: Callable[[str], int]) -> int:
    return boca("NORTE")


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        for statement in sql.split(";\n"):
            db.execute(text(statement), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def _account(db: Session, outlet_id: int) -> int:
    return int(
        db.execute(text("SELECT id FROM settlements.accounts WHERE outlet_id = :o"), {"o": outlet_id}).scalar_one()
    )


def _user(db: Session) -> int:
    return int(db.execute(text("SELECT id FROM users ORDER BY id LIMIT 1")).scalar_one())


def _operation(
    db: Session,
    type: str,
    legs: list[tuple[int, Decimal | int | str]],
    *,
    source: tuple[str, int] | None = None,
    reverses: int | None = None,
    on: Any = None,
) -> int:
    """Writes an operation and its legs by hand (no commit); returns its id."""
    operation = db.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
            "request_fingerprint, source_type, source_id, reverses_operation_id, created_by) VALUES "
            "(:t, :d, 'Escrito a mano', :k, :f, :st, :si, :rv, :u) RETURNING id"
        ),
        {
            "t": type,
            "d": on or yesterday(),
            "k": uuid4(),
            "f": FINGERPRINT,
            "st": source[0] if source else None,
            "si": source[1] if source else None,
            "rv": reverses,
            "u": _user(db),
        },
    ).scalar_one()
    for account_id, amount in legs:
        db.execute(
            text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)"),
            {"o": operation, "a": account_id, "m": Decimal(str(amount))},
        )
    return int(operation)


def _raw_remittance(db: Session, sid: int, box: int, **fields: Any) -> int:
    """Inserts a remittance by hand (no commit); boca and date taken from the settlement unless given."""
    values = {"amount": Decimal("1000"), "delivered_at": at(13), **fields}
    row = db.execute(
        text(
            "INSERT INTO settlements.outlet_remittances (settlement_id, outlet_id, business_date, delivered_at, "
            "amount, destination_account_id, client_request_id, request_fingerprint, created_by) "
            "SELECT s.id, coalesce(CAST(:outlet AS bigint), s.outlet_id), "
            "coalesce(CAST(:day AS date), s.business_date), "
            ":delivered_at, :amount, :box, :k, :f, s.created_by FROM settlements.settlements s WHERE s.id = :s "
            "RETURNING id"
        ),
        {
            "s": sid,
            "box": box,
            "k": uuid4(),
            "f": FINGERPRINT,
            "outlet": values.pop("outlet_id", None),
            "day": values.pop("business_date", None),
            **values,
        },
    ).scalar_one()
    return int(row)


def _attempt(db: Session, write: Callable[[], None]) -> str:
    def _write_and_commit() -> None:
        write()
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write_and_commit()
    db.rollback()
    return str(caught.value.orig)


def _full_remittance(db: Session, sid: int, outlet_id: int, box: int, amount: str = "1000") -> Callable[[], None]:
    """A remittance with its exact operation, written by hand: the control case of several rules."""

    def _write() -> None:
        rid = _raw_remittance(db, sid, box, amount=Decimal(amount))
        op = _operation(
            db,
            "OUTLET_REMITTANCE",
            [(_account(db, outlet_id), f"-{amount}"), (box, amount)],
            source=("outlet_remittance", rid),
        )
        db.execute(
            text("UPDATE settlements.outlet_remittances SET operation_id = :o WHERE id = :r"), {"o": op, "r": rid}
        )

    return _write


# --- 1-6: what a remittance is --------------------------------------------------------------------


def test_control_a_remittance_written_by_hand_exactly_is_accepted(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "5000")
    remit(r.operator, sid, "1", ids["main_cash"])  # creates the boca's account
    _full_remittance(db, sid, centro, ids["main_cash"])()
    db.commit()


def test_1_remittances_are_only_of_boca_settlements(r: Roles, db: Session, driver, ids: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    pedro = driver("Pedro Gómez")
    created = r.operator.post(
        "/api/v1/settlements/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": yesterday().isoformat(),
            "employee_id": pedro,
            "expected_amount": "1000",
        },
    )
    sid = created.json()["id"]
    message = _attempt(db, lambda: _raw_remittance(db, sid, ids["main_cash"], outlet_id=1))
    assert "is not of a boca" in message


def test_2_amount_is_positive(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    for amount in (Decimal(0), Decimal(-1)):
        message = _attempt(db, lambda amount=amount: _raw_remittance(db, sid, ids["main_cash"], amount=amount))  # type: ignore[misc]
        assert "amount_positive" in message


def test_3_destination_is_an_active_cash_box(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    for account in ("bank", "outlet_collections", "expense"):
        message = _attempt(db, lambda account=account: _raw_remittance(db, sid, ids[account]))  # type: ignore[misc]
        assert "is not an active cash box" in message
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE code = 'change_cash'"))
    db.commit()
    assert "is not an active cash box" in _attempt(db, lambda: _raw_remittance(db, sid, ids["change_cash"]))


def test_4_5_boca_and_date_are_those_of_the_settlement(
    r: Roles, db: Session, centro: int, norte: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "5000")
    assert "boca and date are those" in _attempt(
        db, lambda: _raw_remittance(db, sid, ids["main_cash"], outlet_id=norte)
    )
    message = _attempt(db, lambda: _raw_remittance(db, sid, ids["main_cash"], business_date=days_ago(2)))
    assert "boca and date are those" in message


def test_6_delivered_at_is_not_future_and_on_the_business_date(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "5000")
    future = datetime.now(UTC) + timedelta(minutes=5)
    for when in (future, at(13, on=days_ago(2)), datetime.combine(yesterday(), datetime.min.time(), UTC)):
        message = _attempt(db, lambda when=when: _raw_remittance(db, sid, ids["main_cash"], delivered_at=when))  # type: ignore[misc]
        assert "is in the future or not on" in message


def test_a_closed_settlement_takes_no_remittance(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "0")
    assert close(r.operator, sid).status_code == 201
    assert "takes no remittance" in _attempt(db, lambda: _raw_remittance(db, sid, ids["main_cash"]))


# --- 7-11: the remittance and its operation are exactly each other --------------------------------


def test_7_9_the_operation_is_exact(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    remit(r.operator, sid, "1", ids["main_cash"])
    advances = _account(db, centro)
    for legs in (
        [(advances, "-999"), (ids["main_cash"], "999")],  # another amount
        [(advances, "-1000"), (ids["change_cash"], "1000")],  # another cash box
    ):

        def _write(legs: list[tuple[int, str]] = legs) -> None:  # type: ignore[assignment]
            rid = _raw_remittance(db, sid, ids["main_cash"])
            op = _operation(db, "OUTLET_REMITTANCE", legs, source=("outlet_remittance", rid))  # type: ignore[arg-type]
            db.execute(
                text("UPDATE settlements.outlet_remittances SET operation_id = :o WHERE id = :r"), {"o": op, "r": rid}
            )

        # Whichever deferred check runs first refuses it: the document's or the boca's balance.
        message = _attempt(db, _write)
        assert "does not match remittance" in message or "outlet_advances_inconsistent" in message


def test_7_the_operation_keeps_the_business_date(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    remit(r.operator, sid, "1", ids["main_cash"])

    def _write() -> None:
        rid = _raw_remittance(db, sid, ids["main_cash"])
        op = _operation(
            db,
            "OUTLET_REMITTANCE",
            [(_account(db, centro), "-1000"), (ids["main_cash"], "1000")],
            source=("outlet_remittance", rid),
            on=days_ago(2),
        )
        db.execute(
            text("UPDATE settlements.outlet_remittances SET operation_id = :o WHERE id = :r"), {"o": op, "r": rid}
        )

    assert "is not the OUTLET_REMITTANCE of remittance" in _attempt(db, _write)


def test_8_the_account_is_the_boca_s_own(r: Roles, db: Session, centro: int, norte: int, ids: dict[str, int]) -> None:
    one = create(r.operator, centro, "5000")
    other = create(r.operator, norte, "5000")
    remit(r.operator, one, "1", ids["main_cash"])
    remit(r.operator, other, "1", ids["main_cash"])

    def _write() -> None:
        rid = _raw_remittance(db, one, ids["main_cash"])
        _operation(
            db,
            "OUTLET_REMITTANCE",
            [(_account(db, norte), "-1000"), (ids["main_cash"], "1000")],
            source=("outlet_remittance", rid),
        )

    assert "outlet_advances_closed" in _attempt(db, _write)


def test_10_document_and_operation_need_each_other(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    remit(r.operator, sid, "1", ids["main_cash"])
    message = _attempt(db, lambda: _raw_remittance(db, sid, ids["main_cash"]))
    assert "has no operation" in message or "outlet_advances_inconsistent" in message

    def _orphan() -> None:
        rid = _raw_remittance(db, sid, ids["main_cash"])
        op = _operation(
            db,
            "OUTLET_REMITTANCE",
            [(_account(db, centro), "-1000"), (ids["main_cash"], "1000")],
            source=("outlet_remittance", rid),
        )
        del op  # the remittance never names it

    message = _attempt(db, _orphan)
    assert "has no operation" in message or "has no remittance" in message


def test_11_the_reversal_is_exact_and_marked(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    row = remittance_row(db, rid)
    advances = _account(db, centro)

    def _unmarked() -> None:
        _operation(db, "REVERSAL", [(advances, "1000"), (ids["main_cash"], "-1000")], reverses=row["operation_id"])

    assert "reversed exactly by the reversal of its operation" in _attempt(db, _unmarked)

    def _other_date() -> None:
        reversal = _operation(
            db,
            "REVERSAL",
            [(advances, "1000"), (ids["main_cash"], "-1000")],
            reverses=row["operation_id"],
            on=days_ago(0),
        )
        db.execute(
            text(
                "UPDATE settlements.outlet_remittances SET reversal_operation_id = :o, reversed_by = created_by, "
                "reversed_at = now(), reversal_reason = 'Error' WHERE id = :r"
            ),
            {"o": reversal, "r": rid},
        )

    assert "keeps its business date" in _attempt(db, _other_date)


# --- 12-13: never deleted, its money never changes ------------------------------------------------


def test_12_a_remittance_is_never_deleted(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    assert "never deleted" in _refused(db, "DELETE FROM settlements.outlet_remittances WHERE id = :r", r=rid)


def test_13_its_money_never_changes(r: Roles, db: Session, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "5000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    for change in (
        "amount = 999",
        "destination_account_id = :box",
        "delivered_at = delivered_at - interval '1 hour'",
        "reference = 'Otra'",
        "operation_id = NULL",
    ):
        sql = f"UPDATE settlements.outlet_remittances SET {change} WHERE id = :r"  # noqa: S608
        message = _refused(db, sql, r=rid, box=ids["change_cash"])
        assert "never changes" in message
    reverse(r.admin, sid, rid)
    message = _refused(db, "UPDATE settlements.outlet_remittances SET reversal_reason = 'Otro' WHERE id = :r", r=rid)
    assert "is reversed and does not change" in message


def test_a_closed_settlement_s_remittance_is_not_reversed(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    assert close(r.operator, sid).status_code == 201
    row = remittance_row(db, rid)

    def _write() -> None:
        reversal = _operation(
            db,
            "REVERSAL",
            [(_account(db, centro), "1000"), (ids["main_cash"], "-1000")],
            reverses=row["operation_id"],
        )
        db.execute(
            text(
                "UPDATE settlements.outlet_remittances SET reversal_operation_id = :o, reversed_by = created_by, "
                "reversed_at = now(), reversal_reason = 'Error' WHERE id = :r"
            ),
            {"o": reversal, "r": rid},
        )

    assert "reopen it to reverse" in _attempt(db, _write)


# --- 14-15: what a close consolidates -------------------------------------------------------------


def _raw_close(db: Session, sid: int, legs: list[tuple[int, str]], frozen: list[tuple[int, str]] = ()) -> None:  # type: ignore[assignment]
    op = _operation(db, "SETTLEMENT_CLOSE", legs, source=("settlement", sid))
    for rid, amount in frozen:
        db.execute(
            text(
                "INSERT INTO settlements.settlement_closing_remittances (close_operation_id, settlement_id, "
                "settlement_version, remittance_id, amount) VALUES (:o, :s, 1, :r, :m)"
            ),
            {"o": op, "s": sid, "r": rid, "m": Decimal(amount)},
        )


def test_14_a_close_consolidates_exactly_its_remittances_in_effect(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "600", ids["main_cash"])
    advances = _account(db, centro)
    collections = ids["outlet_collections"]
    cases = [
        # The leg without the frozen rows.
        ([(collections, "-1000"), (advances, "600"), (ids["main_cash"], "400")], []),
        # Neither: the remittance in effect is not consolidated (the cash box would get it twice).
        ([(collections, "-1000"), (ids["main_cash"], "1000")], []),
        # Rows and leg of another amount.
        ([(collections, "-1000"), (advances, "500"), (ids["main_cash"], "500")], [(rid, "500")]),
    ]
    for legs, frozen in cases:
        message = _attempt(db, lambda legs=legs, frozen=frozen: _raw_close(db, sid, legs, frozen))  # type: ignore[misc]
        assert "consolidates exactly the remittances in effect" in message or "of another amount" in message
    # The exact close is accepted (control).
    _raw_close(db, sid, [(collections, "-1000"), (advances, "600"), (ids["main_cash"], "400")], [(rid, "600")])
    db.commit()


def test_15_an_administrative_zero_close_admits_no_remittance(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "0")
    rid = remit(r.operator, sid, "100", ids["main_cash"], confirm_above_expected=True)
    assert ZERO_REFUSED in _refused(db, ZERO_CLOSE_SQL, s=sid, f=FINGERPRINT, cash="0.00")
    # A reversed one does not count.
    reverse(r.admin, sid, rid)
    db.execute(text(ZERO_CLOSE_SQL), {"s": sid, "f": FINGERPRINT, "cash": "0.00"})
    db.commit()


# --- 16-18: the boca's account --------------------------------------------------------------------


def test_16_the_global_balance_per_boca_is_checked_on_its_own(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    """With the per-document checks lifted (inside the transaction that fails), the rule per boca still refuses a
    balance its remittances do not explain, and a closed settlement that did not consolidate them."""
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])

    def _marked_without_reversal() -> None:
        db.execute(text("ALTER TABLE settlements.outlet_remittances DISABLE TRIGGER trg_outlet_remittances_check"))
        db.execute(text("ALTER TABLE settlements.outlet_remittances DISABLE TRIGGER trg_outlet_remittances_operation"))
        db.execute(
            text(
                "UPDATE settlements.outlet_remittances SET reversal_operation_id = operation_id, "
                "reversed_by = created_by, reversed_at = now(), reversal_reason = 'Sin reverso' WHERE id = :r"
            ),
            {"r": rid},
        )

    assert "outlet_advances_inconsistent" in _attempt(db, _marked_without_reversal)

    def _closed_without_consolidating() -> None:
        trigger = "trg_financial_operations_close_remittances"
        db.execute(text(f"ALTER TABLE settlements.financial_operations DISABLE TRIGGER {trigger}"))
        _raw_close(db, sid, [(ids["outlet_collections"], "-1000"), (ids["main_cash"], "1000")])
        db.execute(
            text(
                "INSERT INTO settlements.settlement_closings (settlement_id, settlement_version, kind, operation_id, "
                "request_fingerprint, closed_by) SELECT :s, 1, 'FINANCIAL', o.id, :f, o.created_by "
                "FROM settlements.financial_operations o WHERE o.source_type = 'settlement' AND o.source_id = :s"
            ),
            {"s": sid, "f": FINGERPRINT},
        )
        db.execute(
            text(
                "UPDATE settlements.settlements s SET status = 'CLOSED', closed_at = now(), closed_by = created_by, "
                "current_close_operation_id = c.operation_id, current_closing_id = c.id "
                "FROM settlements.settlement_closings c WHERE c.settlement_id = s.id AND s.id = :s"
            ),
            {"s": sid},
        )

    assert "outlet_advances_inconsistent" in _attempt(db, _closed_without_consolidating)


def test_17_two_settlements_of_the_same_boca_never_step_on_each_other(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    one = create(r.operator, centro, "1000")
    two = create(r.operator, centro, "1000", confirm_additional=True)
    remit(r.operator, one, "400", ids["main_cash"])
    theirs = remit(r.operator, two, "600", ids["main_cash"])
    advances = _account(db, centro)
    # Rendición 1 cannot consolidate a remittance of Rendición 2.
    message = _attempt(
        db,
        lambda: _raw_close(
            db,
            one,
            [(ids["outlet_collections"], "-1000"), (advances, "600"), (ids["main_cash"], "400")],
            [(theirs, "600")],
        ),
    )
    assert "not of settlement" in message
    # Closing Rendición 1 leaves Rendición 2's remittance not consolidated.
    count(r.operator, one, ("ARS_200", 3, ids["main_cash"]))
    assert close(r.operator, one).status_code == 201
    db.expire_all()
    balance = db.execute(
        text("SELECT sum(amount) FROM settlements.ledger_entries WHERE account_id = :a"), {"a": advances}
    ).scalar_one()
    assert balance == Decimal("-600.00")


def test_18_no_other_operation_touches_the_boca_s_account(
    r: Roles,
    db: Session,
    centro: int,
    norte: int,
    ids: dict[str, int],
    driver,  # type: ignore[no-untyped-def]
) -> None:
    sid = create(r.operator, centro, "1000")
    remit(r.operator, sid, "1", ids["main_cash"])
    advances = _account(db, centro)
    for type in ("ADJUSTMENT", "CASH_TRANSFER", "OPENING_BALANCE", "OWNER_CONTRIBUTION", "PAYMENT", "DIRECT_EXPENSE"):
        message = _attempt(
            db,
            lambda type=type: _operation(db, type, [(advances, "100"), (ids["main_cash"], "-100")]),  # type: ignore[misc]
        )
        assert "outlet_advances_closed" in message, type
    # Nor the close of another boca's settlement, nor of a delivery.
    other = create(r.operator, norte, "1000")
    message = _attempt(
        db,
        lambda: _raw_close(db, other, [(ids["outlet_collections"], "-1000"), (advances, "1000")]),
    )
    assert "outlet_advances_closed" in message
    # Nor a remittance's leg with the wrong sign.
    message = _attempt(
        db,
        lambda: _operation(
            db,
            "OUTLET_REMITTANCE",
            [(advances, "100"), (ids["main_cash"], "-100")],
            source=("outlet_remittance", 999_999),
        ),
    )
    assert "outlet_advances_closed" in message


def test_the_boca_s_account_is_one_per_boca_and_never_changes(
    r: Roles, db: Session, centro: int, norte: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "1000")
    remit(r.operator, sid, "1", ids["main_cash"])
    advances = _account(db, centro)
    for change in ("outlet_id = :n", "active = false", "allow_negative = false", "kind = 'CASH_BOX', outlet_id = NULL"):
        sql = f"UPDATE settlements.accounts SET {change} WHERE id = :a"  # noqa: S608
        message = _refused(db, sql, a=advances, n=norte)
        assert "never changes" in message or "ck_accounts_" in message
    duplicate = _refused(
        db,
        "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative, outlet_id) "
        "VALUES ('Otra', 'otra', 'OUTLET_ADVANCES', true, :o)",
        o=centro,
    )
    assert "uq_accounts_outlet_id" in duplicate
    without_boca = _refused(
        db,
        "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative) "
        "VALUES ('Sin boca', 'sin boca', 'OUTLET_ADVANCES', true)",
    )
    assert "outlet_link" in without_boca


def test_reopened_settlement_keeps_its_old_close_while_a_remittance_is_reversed(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    """The temporary positive balance during a correction is valid; at the new close it is reconciled again."""
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    assert close(r.operator, sid).status_code == 201
    assert reopen(r.admin, sid, 1).status_code == 200
    reverse(r.admin, sid, rid)
    db.expire_all()
    assert db.execute(text("SELECT settlements.assert_outlet_advances(:o)"), {"o": centro}).first() is not None
