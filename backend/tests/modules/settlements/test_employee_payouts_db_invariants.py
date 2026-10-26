"""Rules of payouts that the database enforces whoever writes (migration 0029), checked with raw SQL (Enzo's R8-5
point 12, invariants 1-23). Valid payrolls and a payout are made through the API; each case then tries to break one
rule directly. The number in brackets is the invariant of Enzo's list."""

from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_loan_support import funded
from tests.modules.settlements.employee_movement_support import internal_account
from tests.modules.settlements.employee_payout_support import area_admin, cash_box, net_payroll, paid
from tests.modules.settlements.employee_payroll_support import opened

__all__ = ["funded"]

FINGERPRINT = "d" * 64


@pytest.fixture
def world(
    user_as: Callable[..., TestClient], employee: Callable[[str], int], funded: dict[str, int], db: Session
) -> dict[str, Any]:
    """Ana's payroll (net $100.000, period to 10 days ago) paid $30.000 in cash; Beto's (net $50.000) unpaid; an
    open payroll of Ana."""
    admin = area_admin(user_as)
    ana, beto = employee("Ana Pérez"), employee("Beto")
    p = net_payroll(admin, ana, "100000", days_ago(40), days_ago(10))
    payout = paid(admin, p, ("CASH", funded["main_cash"], "30000"), on=days_ago(10))
    q = net_payroll(admin, beto, "50000", days_ago(40), days_ago(10))
    draft = opened(admin, ana, days_ago(9), days_ago(5))
    db.rollback()
    return {
        "ana": ana,
        "ana_account": internal_account(db, ana),
        "beto": beto,
        "beto_account": internal_account(db, beto),
        "payroll": p["id"],
        "closing": p["closing"]["id"],
        "payout": payout["id"],
        "payout_operation": payout["operation_id"],
        "beto_payroll": q["id"],
        "beto_closing": q["closing"]["id"],
        "draft": draft["id"],
        **funded,
    }


def _refused(db: Session, write: Callable[[], None]) -> str:
    def _write_and_commit() -> None:
        write()
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write_and_commit()
    db.rollback()
    return str(caught.value.orig)


def _sql(db: Session, sql: str, **params: object) -> Callable[[], None]:
    return lambda: db.execute(text(sql), params) and None


def _operation(
    db: Session,
    type: str,
    legs: list[tuple[int, str]],
    *,
    source: tuple[str, int] | None = None,
    reverses: int | None = None,
    on: Any = None,
) -> int:
    operation = db.execute(
        text(
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
            "request_fingerprint, source_type, source_id, reverses_operation_id, created_by) VALUES "
            "(:t, :d, 'Escrito a mano', :k, :f, :st, :si, :rv, (SELECT id FROM users ORDER BY id LIMIT 1)) "
            "RETURNING id"
        ),
        {
            "t": type,
            "d": on or days_ago(10),
            "k": uuid4(),
            "f": FINGERPRINT,
            "st": source[0] if source else None,
            "si": source[1] if source else None,
            "rv": reverses,
        },
    ).scalar_one()
    for account_id, amount in legs:
        db.execute(
            text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)"),
            {"o": operation, "a": account_id, "m": Decimal(amount)},
        )
    return int(operation)


def _payout(db: Session, w: dict[str, Any], **fields: Any) -> int:
    """Inserts a payout by hand (no commit): $10.000 of Ana's payroll, dated 10 days ago, unless told otherwise."""
    values: dict[str, Any] = {
        "payroll_id": w["payroll"],
        "payroll_closing_id": w["closing"],
        "employee_id": w["ana"],
        "employee_account_id": w["ana_account"],
        "business_date": days_ago(10),
        "amount": Decimal("10000"),
        "client_request_id": uuid4(),
        "operation_id": None,
        **fields,
    }
    return int(
        db.execute(
            text(
                "INSERT INTO settlements.employee_payouts (payroll_id, payroll_closing_id, employee_id, "
                "employee_account_id, business_date, amount, client_request_id, request_fingerprint, created_by, "
                "operation_id) VALUES (:payroll_id, :payroll_closing_id, :employee_id, :employee_account_id, "
                ":business_date, :amount, :client_request_id, :f, (SELECT id FROM users ORDER BY id LIMIT 1), "
                ":operation_id) RETURNING id"
            ),
            values | {"f": FINGERPRINT},
        ).scalar_one()
    )


def _item(db: Session, payout_id: int, method: str, account_id: int, amount: str) -> None:
    db.execute(
        text(
            "INSERT INTO settlements.employee_payout_items (payout_id, method, account_id, amount) "
            "VALUES (:p, :m, :a, :x)"
        ),
        {"p": payout_id, "m": method, "a": account_id, "x": Decimal(amount)},
    )


def _publish(db: Session, w: dict[str, Any], payout_id: int, legs: list[tuple[int, str]], **kw: Any) -> int:
    operation = _operation(db, kw.pop("type", "EMPLOYEE_PAYOUT"), legs, source=("employee_payout", payout_id), **kw)
    db.execute(
        text("UPDATE settlements.employee_payouts SET operation_id = :o WHERE id = :p"),
        {"o": operation, "p": payout_id},
    )
    return operation


def _whole(db: Session, w: dict[str, Any], amount: str = "10000", **fields: Any) -> Callable[[], None]:
    """A complete payout in cash, written by hand."""

    def write() -> None:
        pid = _payout(db, w, amount=Decimal(amount), **fields)
        _item(db, pid, "CASH", w["main_cash"], amount)
        _publish(
            db, w, pid, [(w["main_cash"], f"-{amount}"), (fields.get("employee_account_id", w["ana_account"]), amount)]
        )

    return write


def test_a_complete_payout_written_by_hand_is_accepted(world: dict[str, Any], db: Session) -> None:
    _whole(db, world)()
    db.commit()


def test_1_the_payout_is_of_the_payrolls_employee(world: dict[str, Any], db: Session) -> None:
    w = world
    assert "is not of employee" in _refused(
        db, _whole(db, w, employee_id=w["beto"], employee_account_id=w["beto_account"])
    )
    assert "is not of employee" in _refused(db, _whole(db, w, employee_account_id=w["beto_account"]))


def test_2_3_4_the_close_in_effect_of_a_closed_payroll(world: dict[str, Any], db: Session) -> None:
    w = world
    assert "close in effect" in _refused(db, _whole(db, w, payroll_closing_id=w["beto_closing"]))
    message = _refused(db, _whole(db, w, payroll_id=w["draft"], payroll_closing_id=w["closing"]))
    assert "close in effect" in message


def test_5_amount_positive(world: dict[str, Any], db: Session) -> None:
    assert "ck_employee_payouts_amount_positive" in _refused(
        db, lambda: _payout(db, world, amount=Decimal("0")) and None
    )
    pid = _payout(db, world)
    assert "ck_employee_payout_items_amount_positive" in _refused(
        db, lambda: _item(db, pid, "CASH", world["main_cash"], "-1")
    )


def test_6_7_dates(world: dict[str, Any], db: Session) -> None:
    assert "before the period end" in _refused(db, _whole(db, world, business_date=days_ago(11)))
    assert "in the future" in _refused(db, _whole(db, world, business_date=today() + timedelta(days=1)))


def test_8_at_least_one_item(world: dict[str, Any], db: Session) -> None:
    def write() -> None:
        pid = _payout(db, world)
        _publish(db, world, pid, [(world["main_cash"], "-10000"), (world["ana_account"], "10000")])

    assert "do not add up" in _refused(db, write)


def test_9_10_only_cash_boxes_and_banks_that_are_active(world: dict[str, Any], db: Session) -> None:
    w = world
    for method, account in (("CHEQUE", w["main_cash"]), ("ECHEQ", w["bank"])):
        assert "is not an active" in _refused(db, lambda m=method, a=account: _item(db, _payout(db, w), m, a, "10000"))
    for method, account in (
        ("CASH", w["bank"]),
        ("TRANSFER", w["main_cash"]),
        ("CASH", w["cheque_portfolio"]),
        ("CASH", w["ana_account"]),
        ("TRANSFER", w["payroll_expense"]),
    ):
        assert "is not an active" in _refused(db, lambda m=method, a=account: _item(db, _payout(db, w), m, a, "10000"))
    closed_box = cash_box(db, "Caja vieja", active=False)
    assert "is not an active" in _refused(db, lambda: _item(db, _payout(db, w), "CASH", closed_box, "10000"))


def test_11_items_add_up_to_the_amount(world: dict[str, Any], db: Session) -> None:
    def write() -> None:
        pid = _payout(db, world)
        _item(db, pid, "CASH", world["main_cash"], "9999.99")
        _publish(db, world, pid, [(world["main_cash"], "-9999.99"), (world["ana_account"], "9999.99")])

    assert "do not add up" in _refused(db, write)


def test_12_never_more_than_the_net(world: dict[str, Any], db: Session) -> None:
    assert "payroll_overpaid" in _refused(db, _whole(db, world, "70000.01"))
    _whole(db, world, "70000")()
    db.commit()


def test_13_14_15_16_the_operation_is_exactly_the_payout(world: dict[str, Any], db: Session) -> None:
    w = world

    def wrong(legs: list[tuple[int, str]], **kw: Any) -> Callable[[], None]:
        def write() -> None:
            pid = _payout(db, w)
            _item(db, pid, "CASH", w["main_cash"], "10000")
            _publish(db, w, pid, legs, **kw)

        return write

    right = [(w["main_cash"], "-10000"), (w["ana_account"], "10000")]
    assert "is not the operation" in _refused(db, wrong(right, type="EMPLOYEE_ADVANCE"))
    assert "is not the operation" in _refused(db, wrong(right, on=days_ago(9)))
    assert "does not match" in _refused(
        db, wrong([(w["main_cash"], "-10000"), (w["ana_account"], "9000"), (w["opening_balance"], "1000")])
    )
    assert "does not match" in _refused(db, wrong([(w["bank"], "-10000"), (w["ana_account"], "10000")]))
    # An EMPLOYEE_PAYOUT without its payout, or pointing at another one.
    assert "has no employee payout" in _refused(
        db, lambda: _operation(db, "EMPLOYEE_PAYOUT", right, source=("employee_payout", 999999)) and None
    )


def test_17_a_reversal_is_exact_and_marks_the_payout(world: dict[str, Any], db: Session) -> None:
    w = world
    legs = [(w["main_cash"], "30000"), (w["ana_account"], "-30000")]
    assert "reversed exactly" in _refused(
        db, lambda: _operation(db, "REVERSAL", legs, reverses=w["payout_operation"]) and None
    )

    def other_date() -> None:
        reversal = _operation(db, "REVERSAL", legs, reverses=w["payout_operation"], on=days_ago(2))
        db.execute(
            text(
                "UPDATE settlements.employee_payouts SET reversal_operation_id = :r, reversed_by = "
                "(SELECT id FROM users ORDER BY id LIMIT 1), reversed_at = now(), reversal_reason = 'x' WHERE id = :p"
            ),
            {"r": reversal, "p": w["payout"]},
        )

    assert "keeps its business date" in _refused(db, other_date)


def test_18_19_20_nothing_is_deleted_nor_rewritten(world: dict[str, Any], db: Session) -> None:
    w = world
    for sql in (
        "DELETE FROM settlements.employee_payouts WHERE id = :p",
        "DELETE FROM settlements.employee_payout_items WHERE payout_id = :p",
        "UPDATE settlements.employee_payout_items SET amount = 1 WHERE payout_id = :p",
        "UPDATE settlements.employee_payouts SET amount = 1 WHERE id = :p",
        "UPDATE settlements.employee_payouts SET business_date = business_date + 1 WHERE id = :p",
        "UPDATE settlements.employee_payouts SET reference = 'otro' WHERE id = :p",
        "UPDATE settlements.employee_payouts SET operation_id = NULL WHERE id = :p",
    ):
        _refused(db, _sql(db, sql, p=w["payout"]))
    # An item is not added to a published payout.
    assert "already published" in _refused(db, lambda: _item(db, w["payout"], "TRANSFER", w["bank"], "1"))


def test_20_a_reversed_payout_never_changes(user_as: Any, world: dict[str, Any], db: Session) -> None:
    admin = area_admin(user_as)
    assert (
        admin.post(
            f"/api/v1/settlements/employee-finance/payouts/{world['payout']}/reverse",
            json={"client_request_id": str(uuid4()), "reason": "Error"},
        ).status_code
        == 201
    )
    message = _refused(
        db,
        _sql(db, "UPDATE settlements.employee_payouts SET reversal_reason = 'otro' WHERE id = :p", p=world["payout"]),
    )
    assert "is reversed and does not change" in message


def test_21_a_paid_payroll_does_not_reopen(world: dict[str, Any], db: Session) -> None:
    sql = "UPDATE settlements.employee_payrolls SET status = 'OPEN', version = version + 1 WHERE id = :p"
    assert "payroll_has_payouts" in _refused(db, _sql(db, sql, p=world["payroll"]))


def test_22_one_payout_per_request(world: dict[str, Any], db: Session) -> None:
    request = db.execute(
        text("SELECT client_request_id FROM settlements.employee_payouts WHERE id = :p"), {"p": world["payout"]}
    ).scalar_one()
    assert "uq_employee_payouts_client_request_id" in _refused(db, _whole(db, world, client_request_id=request))
    assert "uq_financial_operations_employee_payout" in _refused(
        db,
        lambda: (
            _operation(
                db,
                "EMPLOYEE_PAYOUT",
                [(world["main_cash"], "-1"), (world["ana_account"], "1")],
                source=("employee_payout", world["payout"]),
            )
            and None
        ),
    )


def test_23_only_its_types_move_the_internal_account(world: dict[str, Any], db: Session) -> None:
    w = world
    assert "employee_account_closed" in _refused(
        db, lambda: _operation(db, "CASH_TRANSFER", [(w["main_cash"], "-1"), (w["ana_account"], "1")]) and None
    )

    def into_payroll_expense() -> None:
        pid = _payout(db, w)
        _item(db, pid, "CASH", w["main_cash"], "10000")
        _publish(db, w, pid, [(w["main_cash"], "-10000"), (w["payroll_expense"], "10000")])

    assert "payroll_expense_closed" in _refused(db, into_payroll_expense)
