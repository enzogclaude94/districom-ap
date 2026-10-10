"""The rules of payments (R6-1) the database keeps whoever writes: direct SQL, no API. Enzo's invariants 1-14
for R6-1: at least one item; amount = exact sum; no repeated account; one operation per payment; exact legs;
one counter-leg for the total; never an overdraft; an active third party; the right expense category; no
fuel; OTHER only by an admin; no DELETE; immutable money; the operation owned by its payment."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.direct_support import BASE, T, account_id, fund
from tests.modules.settlements.fuel_support import fuel_category
from tests.modules.settlements.payment_support import THIRD_PARTIES, cash, paid, reverse_payment, third_party

FINGERPRINT = "0" * 64


def _refused(db: Session, write: Callable[[], Any]) -> str:
    """Run `write` and commit in one transaction; return why the database refused it (at commit at the latest)."""
    with pytest.raises(DBAPIError) as caught:  # noqa: PT012 - refused at the write or, if deferred, at commit
        write()
        db.commit()
    db.rollback()
    return str(caught.value.orig)


def _sql(db: Session, sql: str, **params: object) -> Any:
    return db.execute(text(sql), params)


def _user(db: Session, email: str) -> int:
    return int(_sql(db, "SELECT id FROM users WHERE email = :e", e=email).scalar_one())


@pytest.fixture
def world(as_role, db: Session) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Cash box and bank with $100.000 each, an active and a deactivated third party, a toll category and a
    published payment of $500 from the cash box."""
    as_role("operator")
    admin: TestClient = as_role("admin")
    fund(admin, "main_cash", "100000", T - timedelta(days=5))
    fund(admin, "bank", "100000", T - timedelta(days=5))
    perez = third_party(admin)
    gone = third_party(admin, "Proveedor viejo", None)
    admin.patch(f"{THIRD_PARTIES}/{gone}", json={"active": False})
    tolls = admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]
    box = account_id(admin, "main_cash")
    payment = paid(admin, perez, [cash(box, "500")])
    return {
        "admin": admin,
        "cash": box,
        "change": account_id(admin, "change_cash"),
        "bank": account_id(admin, "bank"),
        "merchandise": account_id(admin, "merchandise_payments"),
        "expense": account_id(admin, "expense"),
        "refunds": account_id(admin, "third_party_refunds"),
        "other": account_id(admin, "other_payments"),
        "perez": perez,
        "gone": gone,
        "tolls": tolls,
        "FUEL": fuel_category(db),
        "payment": payment,
        "user": _user(db, "admin@districom.test"),
        "operator": _user(db, "operator@districom.test"),
    }


def _payment(db: Session, w: dict[str, Any], **values: Any) -> int:
    """A payment row (no items, no operation yet) with sensible defaults, inside the current transaction."""
    row = {
        "third_party_id": w["perez"],
        "business_date": T,
        "concept": "MERCHANDISE",
        "expense_category_id": None,
        "amount": 100,
        "description": None,
        "created_by": w["user"],
        **values,
    }
    columns = ", ".join(row)
    params = ", ".join(f":{c}" for c in row)
    return int(
        db.execute(
            text(
                f"INSERT INTO settlements.payments ({columns}, client_request_id, request_fingerprint) "  # noqa: S608
                f"VALUES ({params}, gen_random_uuid(), '{FINGERPRINT}') RETURNING id"
            ),
            row,
        ).scalar_one()
    )


def _item(db: Session, pid: int, account: int, amount: int, method: str = "CASH") -> int:
    return int(
        _sql(
            db,
            "INSERT INTO settlements.payment_items (payment_id, method, account_id, amount) "
            "VALUES (:p, :m, :a, :x) RETURNING id",
            p=pid,
            m=method,
            a=account,
            x=amount,
        ).scalar_one()
    )


def _operation(
    db: Session,
    w: dict[str, Any],
    source_id: int | None,
    legs: list[tuple[int, int]],
    *,
    type: str = "PAYMENT",
    business_date: Any = T,
    reverses: int | None = None,
) -> int:
    op_id = int(
        _sql(
            db,
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id,"
            " request_fingerprint, source_type, source_id, reverses_operation_id, created_by) VALUES "
            "(:t, :d, :desc, gen_random_uuid(), :f, :st, :s, :r, :u) RETURNING id",
            t=type,
            d=business_date,
            desc="motivo" if type == "REVERSAL" else None,
            f=FINGERPRINT,
            st="payment" if type != "REVERSAL" and source_id is not None else None,
            s=source_id if type != "REVERSAL" else None,
            r=reverses,
            u=w["user"],
        ).scalar_one()
    )
    for account, amount in legs:
        _sql(
            db,
            "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)",
            o=op_id,
            a=account,
            m=amount,
        )
    return op_id


def _publish(
    db: Session,
    w: dict[str, Any],
    items: list[tuple[int, int]],
    *,
    legs: list[tuple[int, int]] | None = None,
    **payment: Any,
) -> int:
    """A whole payment in the current transaction, as the service writes it (or with other legs)."""
    total = sum(amount for _, amount in items)
    pid = _payment(db, w, **{"amount": total, **payment})
    for account, amount in items:
        _item(db, pid, account, amount, "TRANSFER" if account == w["bank"] else "CASH")
    counter = {"MERCHANDISE": "merchandise", "EXPENSE": "expense", "REFUND": "refunds", "OTHER": "other"}[
        payment.get("concept", "MERCHANDISE")
    ]
    expected = [(account, -amount) for account, amount in items] + [(w[counter], total)]
    op = _operation(db, w, pid, legs if legs is not None else expected, business_date=payment.get("business_date", T))
    _sql(db, "UPDATE settlements.payments SET operation_id = :o WHERE id = :p", o=op, p=pid)
    return pid


def test_the_helpers_write_what_the_service_writes(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    """The control: a payment written by hand exactly like the service is accepted."""
    _publish(db, world, [(world["cash"], 100), (world["bank"], 200)])
    db.commit()
    assert _sql(db, "SELECT count(*) FROM settlements.payments").scalar_one() == 2


# --- 1, 2, 3: items --------------------------------------------------------------------------------


def test_1_a_payment_has_at_least_one_item(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        pid = _payment(db, world, amount=100)
        op = _operation(db, world, pid, [(world["cash"], -100), (world["merchandise"], 100)])
        _sql(db, "UPDATE settlements.payments SET operation_id = :o WHERE id = :p", o=op, p=pid)

    assert "has no items" in _refused(db, write)


def test_2_the_amount_is_exactly_the_sum_of_the_items(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        _publish(
            db,
            world,
            [(world["cash"], 100)],
            amount=101,
            legs=[(world["cash"], -101), (world["merchandise"], 101)],
        )

    assert "is not the sum of its items" in _refused(db, write)


def test_3_an_account_appears_once_per_payment(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        pid = _payment(db, world, amount=200)
        _item(db, pid, world["cash"], 100)
        _item(db, pid, world["cash"], 100)

    assert "uq_payment_items_payment_id_account_id" in _refused(db, write)


def test_an_item_leaves_the_right_kind_of_active_account(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid_of = lambda: _payment(db, world)  # noqa: E731
    assert "not an active account of method" in _refused(db, lambda: _item(db, pid_of(), world["bank"], 100, "CASH"))
    assert "not an active account of method" in _refused(
        db, lambda: _item(db, pid_of(), world["cash"], 100, "TRANSFER")
    )
    assert "not an active account of method" in _refused(db, lambda: _item(db, pid_of(), world["merchandise"], 100))
    _sql(db, "UPDATE settlements.accounts SET active = false WHERE id = :a", a=world["change"])
    db.commit()
    assert "not an active account of method" in _refused(db, lambda: _item(db, pid_of(), world["change"], 100))


# --- 4, 5, 6, 14: the operation ------------------------------------------------------------------------


def test_4_a_payment_has_its_operation(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        pid = _payment(db, world)
        _item(db, pid, world["cash"], 100)

    assert "has no operation" in _refused(db, write)


def test_4_a_payment_has_one_operation(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid = world["payment"]["id"]
    message = _refused(db, lambda: _operation(db, world, pid, [(world["cash"], -1), (world["merchandise"], 1)]))
    assert "uq_financial_operations_payment" in message


def test_5_the_legs_are_exactly_the_items_and_the_counter_leg(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    cases = [
        # The item leaves another account.
        [(world["change"], -100), (world["merchandise"], 100)],
        # Another amount on the item's account.
        [(world["cash"], -90), (world["merchandise"], 90)],
        # An extra leg.
        [(world["cash"], -100), (world["bank"], -10), (world["merchandise"], 110)],
    ]
    for legs in cases:
        assert "does not match payment" in _refused(
            db, lambda legs=legs: _publish(db, world, [(world["cash"], 100)], legs=legs)
        )


def test_6_a_single_counter_leg_for_the_total_in_the_concept_account(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    split = [(world["cash"], -100), (world["merchandise"], 60), (world["refunds"], 40)]
    assert "does not match payment" in _refused(db, lambda: _publish(db, world, [(world["cash"], 100)], legs=split))
    wrong = [(world["cash"], -100), (world["merchandise"], 100)]
    message = _refused(
        db,
        lambda: _publish(
            db, world, [(world["cash"], 100)], legs=wrong, concept="EXPENSE", expense_category_id=world["tolls"]
        ),
    )
    assert "does not match payment" in message


def test_14_the_operation_is_owned_by_its_payment(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    # A PAYMENT nobody points at.
    orphan = [(world["cash"], -1), (world["merchandise"], 1)]
    assert "has no payment" in _refused(db, lambda: _operation(db, world, None, orphan))

    # The operation of another date.
    def other_date() -> None:
        pid = _payment(db, world, amount=100)
        _item(db, pid, world["cash"], 100)
        op = _operation(
            db, world, pid, [(world["cash"], -100), (world["merchandise"], 100)], business_date=T - timedelta(days=1)
        )
        _sql(db, "UPDATE settlements.payments SET operation_id = :o WHERE id = :p", o=op, p=pid)

    assert "is not the PAYMENT of payment" in _refused(db, other_date)


def test_14_a_reversal_is_recorded_on_its_payment(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    op = world["payment"]["operation_id"]
    reversal = [(world["cash"], 500), (world["merchandise"], -500)]
    message = _refused(db, lambda: _operation(db, world, None, reversal, type="REVERSAL", reverses=op))
    assert "is reversed exactly by the reversal of its operation" in message


# --- 7: funds ---------------------------------------------------------------------------------------


def test_7_never_an_overdraft(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    # The cash box has $99.500 left; the bank $100.000.
    assert "negative" in _refused(db, lambda: _publish(db, world, [(world["cash"], 99501)]))
    assert "negative" in _refused(db, lambda: _publish(db, world, [(world["bank"], 100001)]))


# --- 8, 9, 10, 11: who and what --------------------------------------------------------------------


def test_8_the_third_party_is_active(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    assert "is not active" in _refused(db, lambda: _payment(db, world, third_party_id=world["gone"]))


def test_9_and_10_the_expense_category_is_right_and_never_fuel(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    # EXPENSE without a category: the guard sees no active category before the CHECK does.
    assert "expense category <NULL> is not active" in _refused(db, lambda: _payment(db, world, concept="EXPENSE"))
    assert "ck_payments_expense_category" in _refused(
        db, lambda: _payment(db, world, concept="MERCHANDISE", expense_category_id=world["tolls"])
    )
    fuel = _refused(db, lambda: _payment(db, world, concept="EXPENSE", expense_category_id=world["FUEL"]))
    assert "fuel is not paid as a payment" in fuel
    _sql(db, "UPDATE settlements.expense_categories SET active = false WHERE id = :c", c=world["tolls"])
    db.commit()
    inactive = _refused(db, lambda: _payment(db, world, concept="EXPENSE", expense_category_id=world["tolls"]))
    assert "is not active" in inactive


def test_11_other_only_by_an_admin_and_with_a_description(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    by_operator = _refused(
        db, lambda: _payment(db, world, concept="OTHER", description="Algo", created_by=world["operator"])
    )
    assert "only by an admin" in by_operator
    assert "ck_payments_other_description" in _refused(
        db, lambda: _payment(db, world, concept="OTHER", description=" ")
    )
    pid = _publish(db, world, [(world["cash"], 10)], concept="OTHER", description="Flete compartido")
    db.commit()
    blank = _refused(db, lambda: _sql(db, "UPDATE settlements.payments SET description = '' WHERE id = :p", p=pid))
    assert "ck_payments_other_description" in blank


# --- 12, 13: history ------------------------------------------------------------------------------


def test_12_nothing_is_deleted(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid = world["payment"]["id"]
    assert "never deleted" in _refused(
        db, lambda: _sql(db, "DELETE FROM settlements.payment_items WHERE payment_id = :p", p=pid)
    )
    assert "never deleted" in _refused(db, lambda: _sql(db, "DELETE FROM settlements.payments WHERE id = :p", p=pid))


def test_13_money_and_items_never_change(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid = world["payment"]["id"]
    for change in (
        "amount = 600",
        f"third_party_id = {world['gone']}",
        "business_date = business_date - 1",
        "concept = 'REFUND'",
    ):
        message = _refused(
            db,
            lambda change=change: _sql(db, f"UPDATE settlements.payments SET {change} WHERE id = :p", p=pid),  # noqa: S608
        )
        assert "never changes" in message, change
    item = world["payment"]["items"][0]["id"]
    assert "only its reference changes" in _refused(
        db, lambda: _sql(db, "UPDATE settlements.payment_items SET amount = 1 WHERE id = :i", i=item)
    )
    assert "its items never change" in _refused(db, lambda: _item(db, pid, world["bank"], 1, "TRANSFER"))
    # The descriptive data does change.
    _sql(db, "UPDATE settlements.payments SET reference = 'FC 1' WHERE id = :p", p=pid)
    _sql(db, "UPDATE settlements.payment_items SET reference = 'R 1' WHERE id = :i", i=item)
    db.commit()


def test_13_a_reversed_payment_never_changes(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid = world["payment"]["id"]
    assert reverse_payment(world["admin"], pid).status_code == 201
    db.expire_all()
    assert "is reversed and does not change" in _refused(
        db, lambda: _sql(db, "UPDATE settlements.payments SET reference = 'x' WHERE id = :p", p=pid)
    )
    item = world["payment"]["items"][0]["id"]
    assert "is reversed and does not change" in _refused(
        db, lambda: _sql(db, "UPDATE settlements.payment_items SET reference = 'x' WHERE id = :i", i=item)
    )
