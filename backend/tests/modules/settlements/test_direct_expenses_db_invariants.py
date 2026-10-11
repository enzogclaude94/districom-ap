"""The rules of direct expenses (R5-3) the database keeps whoever writes: direct SQL, no API. Enzo's
invariants 1-20, plus a reversed expense that never changes again."""

from collections.abc import Callable
from datetime import timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.days import today
from tests.modules.settlements.direct_support import BASE, account_id, fund, reverse, spent
from tests.modules.settlements.fuel_support import fuel_category, vehicle
from tests.modules.settlements.test_settlement_close_api import close, create

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


@pytest.fixture
def world(as_role, driver, db: Session) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Cash box with $100.000, bank with $0, a direct toll of $500 from the cash box and a direct fuel
    expense (10 l x $1.000) from the cash box."""
    admin = as_role("admin")
    fund(admin, "main_cash", "100000", today() - timedelta(days=5))
    juan = driver("Juan Pérez", function="settlements.cash_viewer")
    tolls = admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]
    truck = vehicle(db, "AB123CD")
    cash = account_id(admin, "main_cash")
    toll = spent(admin, cash, tolls, "500")
    fuel = spent(
        admin,
        cash,
        fuel_category(db),
        fuel={"vehicle_id": truck, "employee_id": juan, "liters": "10", "price_per_liter": "1000"},
    )
    return {
        "admin": admin,
        "cash": cash,
        "bank": account_id(admin, "bank"),
        "expense_account": account_id(admin, "expense"),
        "adjustment": account_id(admin, "adjustment"),
        "tolls": tolls,
        "FUEL": fuel_category(db),
        "truck": truck,
        "other": vehicle(db, "AC456DE"),
        "juan": juan,
        "toll": toll,
        "fuel": fuel,
        "user": int(db.execute(text("SELECT min(id) FROM users")).scalar_one()),
    }


def _expense(db: Session, w: dict[str, Any], **values: Any) -> int:
    """A DIRECT expense row (no operation yet) with sensible defaults, inside the current transaction."""
    row = {
        "origin": "DIRECT",
        "settlement_id": None,
        "category_id": w["tolls"],
        "amount": 100,
        "paid_from_account_id": w["cash"],
        "business_date": today(),
        "created_by": w["user"],
        **values,
    }
    columns = ", ".join(row)
    params = ", ".join(f":{c}" for c in row)
    return int(
        db.execute(
            text(
                f"INSERT INTO settlements.expenses ({columns}, client_request_id, request_fingerprint) "  # noqa: S608
                f"VALUES ({params}, gen_random_uuid(), '{FINGERPRINT}') RETURNING id"
            ),
            row,
        ).scalar_one()
    )


def _operation(
    db: Session,
    w: dict[str, Any],
    source_id: int,
    legs: list[tuple[int, int]],
    *,
    type: str = "DIRECT_EXPENSE",
    business_date: Any = None,
    reverses: int | None = None,
    source_type: str | None = "expense",
) -> int:
    if business_date is None:
        business_date = today()
    op_id = int(
        db.execute(
            text(
                "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id,"
                " request_fingerprint, source_type, source_id, reverses_operation_id, created_by) VALUES "
                "(:t, :d, :desc, gen_random_uuid(), :f, :st, :s, :r, :u) RETURNING id"
            ),
            {
                "t": type,
                "d": business_date,
                "desc": "motivo" if type == "REVERSAL" else None,
                "f": FINGERPRINT,
                "st": source_type if source_type and type != "REVERSAL" else None,
                "s": source_id if source_type and type != "REVERSAL" else None,
                "r": reverses,
                "u": w["user"],
            },
        ).scalar_one()
    )
    for account, amount in legs:
        db.execute(
            text("INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)"),
            {"o": op_id, "a": account, "m": amount},
        )
    return op_id


def _publish(db: Session, w: dict[str, Any], amount: int = 100, **expense: Any) -> int:
    """A whole direct expense in the current transaction, as the service writes it."""
    eid = _expense(db, w, amount=amount, **expense)
    account = expense.get("paid_from_account_id", w["cash"])
    op = _operation(
        db,
        w,
        eid,
        [(account, -amount), (w["expense_account"], amount)],
        business_date=expense.get("business_date", today()),
    )
    _sql(db, "UPDATE settlements.expenses SET operation_id = :o WHERE id = :e", o=op, e=eid)
    return eid


def test_a_whole_direct_expense_written_by_hand_is_accepted(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    eid = _publish(db, world)
    db.commit()
    assert _sql(db, "SELECT origin FROM settlements.expenses WHERE id = :e", e=eid).scalar_one() == "DIRECT"


# --- 1-5: the origin decides the columns ------------------------------------------------------------


def test_1_a_direct_expense_needs_its_operation(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert "has no operation" in _refused(db, lambda: _expense(db, world))


def test_2_and_3_a_direct_expense_needs_its_account_and_date(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert "ck_expenses_origin_fields" in _refused(db, lambda: _expense(db, world, paid_from_account_id=None))
    assert "ck_expenses_origin_fields" in _refused(db, lambda: _expense(db, world, business_date=None))


def test_4_a_direct_expense_has_no_settlement(as_role, world, driver, db: Session) -> None:  # type: ignore[no-untyped-def]
    sid = create(as_role("operator"), driver("Pedro Gómez"), "1000")
    assert "ck_expenses_origin_fields" in _refused(db, lambda: _publish(db, world, settlement_id=sid))


def test_5_a_settlement_expense_keeps_its_rules(as_role, world, driver, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    sid = create(operator, driver("Pedro Gómez"), "500")
    eid = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={
            "client_request_id": "00000000-0000-4000-8000-000000000001",
            "category_id": world["tolls"],
            "amount": "500",
        },
    ).json()["expenses"][-1]["id"]
    for column, value in (
        ("paid_from_account_id", world["cash"]),
        ("business_date", today()),
        ("operation_id", world["toll"]["operation_id"]),
        ("settlement_id", None),
    ):
        message = _refused(
            db,
            lambda c=column, v=value: _sql(db, f"UPDATE settlements.expenses SET {c} = :v WHERE id = :e", v=v, e=eid),  # noqa: S608
        )
        assert "ck_expenses_origin_fields" in message, column
    assert "origin never changes" in _refused(
        db, lambda: _sql(db, "UPDATE settlements.expenses SET origin = 'DIRECT' WHERE id = :e", e=eid)
    )
    closed = close(operator, sid)
    assert closed.status_code in (200, 201), closed.text
    assert "cannot change" in _refused(
        db, lambda: _sql(db, "UPDATE settlements.expenses SET description = 'x' WHERE id = :e", e=eid)
    )


# --- 6-11: the paying account and the operation are exactly the expense -----------------------------


def test_6_only_an_active_cash_box_or_bank_pays(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    for account in (world["expense_account"], world["adjustment"]):
        assert "not an active cash box or bank" in _refused(
            db, lambda a=account: _publish(db, world, paid_from_account_id=a)
        )
    _sql(db, "UPDATE settlements.accounts SET active = false WHERE code = 'change_cash'")
    db.commit()
    change = int(_sql(db, "SELECT id FROM settlements.accounts WHERE code = 'change_cash'").scalar_one())
    assert "not an active cash box or bank" in _refused(db, lambda: _publish(db, world, paid_from_account_id=change))


def test_7_the_operation_is_a_direct_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        eid = _expense(db, world)
        op = _operation(
            db, world, eid, [(world["cash"], -100), (world["expense_account"], 100)], type="BANK_WITHDRAWAL"
        )
        _sql(db, "UPDATE settlements.expenses SET operation_id = :o WHERE id = :e", o=op, e=eid)

    assert "is not the DIRECT_EXPENSE of expense" in _refused(db, write)


def test_8_the_source_is_the_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    def write(source_type: str, other: bool) -> Callable[[], None]:
        def _write() -> None:
            eid = _expense(db, world)
            source = world["toll"]["id"] if other else eid
            op = _operation(
                db, world, source, [(world["cash"], -100), (world["expense_account"], 100)], source_type=source_type
            )
            _sql(db, "UPDATE settlements.expenses SET operation_id = :o WHERE id = :e", o=op, e=eid)

        return _write

    assert "is not the DIRECT_EXPENSE" in _refused(db, write("settlement", False))
    # Pointing at another expense: that one already has its operation (unique per source).
    assert "uq_financial_operations_direct_expense" in _refused(db, write("expense", True))
    # A DIRECT_EXPENSE nobody points at.
    assert "has no direct expense" in _refused(
        db,
        lambda: _operation(db, world, 999999, [(world["cash"], -1), (world["expense_account"], 1)]),
    )


@pytest.mark.parametrize(
    "legs",
    [
        lambda w: [(w["cash"], -99), (w["expense_account"], 99)],  # 9: not the amount
        lambda w: [(w["bank"], -100), (w["expense_account"], 100)],  # 9: not the paying account
        lambda w: [(w["cash"], -100), (w["adjustment"], 100)],  # 10: not «Gastos operativos»
        lambda w: [(w["cash"], -100), (w["expense_account"], 60), (w["adjustment"], 40)],  # 10: a third leg
    ],
)
def test_9_and_10_the_legs_are_exactly_account_minus_and_expense_plus(world, db: Session, legs) -> None:  # type: ignore[no-untyped-def]
    _sql(db, "UPDATE settlements.accounts SET allow_negative = true WHERE code = 'bank'")  # the funds rule apart

    def write() -> None:
        eid = _expense(db, world)
        op = _operation(db, world, eid, legs(world))
        _sql(db, "UPDATE settlements.expenses SET operation_id = :o WHERE id = :e", o=op, e=eid)

    try:
        assert "does not match direct expense" in _refused(db, write)
    finally:
        _sql(db, "UPDATE settlements.accounts SET allow_negative = false WHERE code = 'bank'")
        db.commit()


def test_11_one_operation_per_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    toll = world["toll"]
    assert "uq_financial_operations_direct_expense" in _refused(
        db, lambda: _operation(db, world, toll["id"], [(world["cash"], -500), (world["expense_account"], 500)])
    )
    # Another expense taking the toll's operation; and an expense's operation is set once.
    assert "uq_expenses_operation_id" in _refused(
        db,
        lambda: _sql(
            db,
            "UPDATE settlements.expenses SET operation_id = :o WHERE id = :e",
            o=toll["operation_id"],
            e=_expense(db, world),
        ),
    )
    assert "never changes" in _refused(
        db,
        lambda: _sql(
            db,
            "UPDATE settlements.expenses SET operation_id = :o WHERE id = :e",
            o=world["fuel"]["operation_id"],
            e=toll["id"],
        ),
    )


# --- 12-15: published money never changes; reversal is the only way out ------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("amount", "400"),
        ("category_id", "FUEL"),
        ("paid_from_account_id", "bank"),
        ("business_date", "yesterday"),
        ("operation_id", "fuel_operation"),
        ("client_request_id", "uuid"),
    ],
)
def test_12_the_money_of_a_direct_expense_never_changes(world, db: Session, column: str, value: str) -> None:  # type: ignore[no-untyped-def]
    resolved = {
        "FUEL": world["FUEL"],
        "bank": world["bank"],
        "yesterday": today() - timedelta(days=1),
        "fuel_operation": world["fuel"]["operation_id"],
        "uuid": str(uuid4()),
    }.get(value, value)
    message = _refused(
        db,
        lambda: _sql(
            db,
            f"UPDATE settlements.expenses SET {column} = :v WHERE id = :e",  # noqa: S608
            v=resolved,
            e=world["toll"]["id"],
        ),
    )
    assert "never changes" in message or "uq_expenses_operation_id" in message, message


def test_12_the_description_does_change(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    _sql(db, "UPDATE settlements.expenses SET description = 'Peaje corregido' WHERE id = :e", e=world["toll"]["id"])
    db.commit()


def test_13_a_reversal_belongs_to_its_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    toll, fuel = world["toll"], world["fuel"]

    def reversal_of(op: int, amount: int, on: Any = None) -> int:
        return _operation(
            db,
            world,
            0,
            [(world["cash"], amount), (world["expense_account"], -amount)],
            type="REVERSAL",
            reverses=op,
            business_date=on,
        )

    # A REVERSAL of a DIRECT_EXPENSE without the expense marked reversed by it.
    assert "reversed exactly by the reversal" in _refused(db, lambda: reversal_of(toll["operation_id"], 500))

    # The expense marked with the reversal of ANOTHER expense's operation.
    def crossed() -> None:
        rid = reversal_of(fuel["operation_id"], 10000)
        _sql(
            db,
            "UPDATE settlements.expenses SET reversal_operation_id = :r, voided_by = :u, voided_at = now(), "
            "void_reason = 'x' WHERE id = :e",
            r=rid,
            u=world["user"],
            e=toll["id"],
        )

    assert "reversed exactly by the reversal" in _refused(db, crossed)

    # On another business date than the expense's.
    def other_day() -> None:
        rid = reversal_of(toll["operation_id"], 500, today() - timedelta(days=1))
        _sql(
            db,
            "UPDATE settlements.expenses SET reversal_operation_id = :r, voided_by = :u, voided_at = now(), "
            "void_reason = 'x' WHERE id = :e",
            r=rid,
            u=world["user"],
            e=toll["id"],
        )

    assert "keeps its business date" in _refused(db, other_day)


def test_14_reversed_if_and_only_if_it_has_its_reversal(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    eid = world["toll"]["id"]
    assert "ck_expenses_direct_reversal" in _refused(
        db,
        lambda: _sql(
            db,
            "UPDATE settlements.expenses SET voided_by = :u, voided_at = now(), void_reason = 'x' WHERE id = :e",
            u=world["user"],
            e=eid,
        ),
    )


def test_14_a_reversed_expense_never_changes_again(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert reverse(world["admin"], world["toll"]["id"]).status_code == 201
    db.expire_all()
    for sql in (
        "UPDATE settlements.expenses SET description = 'x' WHERE id = :e",
        "UPDATE settlements.expenses SET reversal_operation_id = NULL, voided_at = NULL, voided_by = NULL, "
        "void_reason = NULL WHERE id = :e",
    ):
        assert "is reversed and does not change" in _refused(db, lambda s=sql: _sql(db, s, e=world["toll"]["id"]))


def test_15_nothing_is_deleted(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert "never deleted" in _refused(
        db, lambda: _sql(db, "DELETE FROM settlements.expenses WHERE id = :e", e=world["toll"]["id"])
    )
    assert "never deleted" in _refused(
        db, lambda: _sql(db, "DELETE FROM settlements.fuel_loads WHERE expense_id = :e", e=world["fuel"]["id"])
    )


# --- 16-18: fuel ------------------------------------------------------------------------------------


def test_16_a_direct_fuel_expense_needs_its_load(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert "needs its fuel load" in _refused(db, lambda: _publish(db, world, category_id=world["FUEL"]))


def test_17_the_fuel_amount_is_liters_times_price(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        eid = _publish(db, world, amount=10001, category_id=world["FUEL"])
        _sql(
            db,
            "INSERT INTO settlements.fuel_loads (expense_id, vehicle_id, employee_id, liters, price_per_liter, "
            "created_by, updated_by) VALUES (:e, :v, :j, 10, 1000, :u, :u)",
            e=eid,
            v=world["truck"],
            j=world["juan"],
            u=world["user"],
        )

    assert "is not liters x price per liter" in _refused(db, write)


@pytest.mark.parametrize(
    ("column", "value"),
    [("liters", 11), ("price_per_liter", 999), ("vehicle_id", "other"), ("employee_id", "user_employee")],
)
def test_18_the_load_of_a_direct_expense_is_frozen(world, db: Session, driver, column: str, value: Any) -> None:  # type: ignore[no-untyped-def]
    resolved = {"other": world["other"], "user_employee": driver("Otro Empleado")}.get(value, value)
    message = _refused(
        db,
        lambda: _sql(
            db,
            f"UPDATE settlements.fuel_loads SET {column} = :v WHERE expense_id = :e",  # noqa: S608
            v=resolved,
            e=world["fuel"]["id"],
        ),
    )
    assert "only the odometer changes" in message


def test_18_the_odometer_does_change(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    _sql(db, "UPDATE settlements.fuel_loads SET odometer_km = 12345 WHERE expense_id = :e", e=world["fuel"]["id"])
    db.commit()


def test_18_no_second_load_and_none_on_a_reversed_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    insert = (
        "INSERT INTO settlements.fuel_loads (expense_id, vehicle_id, employee_id, liters, price_per_liter, "
        "created_by, updated_by) VALUES (:e, :v, :j, 10, 1000, :u, :u)"
    )
    params = {"v": world["truck"], "j": world["juan"], "u": world["user"]}
    assert "uq_fuel_loads_expense_id" in _refused(db, lambda: _sql(db, insert, e=world["fuel"]["id"], **params))
    assert reverse(world["admin"], world["fuel"]["id"]).status_code == 201
    assert "is reversed" in _refused(
        db,
        lambda: _sql(
            db, "UPDATE settlements.fuel_loads SET odometer_km = 1 WHERE expense_id = :e", e=world["fuel"]["id"]
        ),
    )


# --- 19-20: funds -----------------------------------------------------------------------------------


def test_19_the_bank_never_goes_into_an_overdraft_for_an_expense(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert "cannot have a negative balance" in _refused(
        db, lambda: _publish(db, world, amount=1, paid_from_account_id=world["bank"])
    )


def test_20_the_cash_box_is_never_negative(world, db: Session) -> None:  # type: ignore[no-untyped-def]
    # $100.000 - $500 - $10.000 left.
    assert "cannot have a negative balance" in _refused(db, lambda: _publish(db, world, amount=89501))
    # Nor retroactively, before the box was funded.
    assert "cannot have a negative balance" in _refused(
        db, lambda: _publish(db, world, amount=1, business_date=today() - timedelta(days=6))
    )


def test_atomicity_an_expense_and_its_operation_live_or_die_together(world, db: Session, client: TestClient) -> None:  # type: ignore[no-untyped-def]
    before = _sql(db, "SELECT count(*) FROM settlements.expenses").scalar_one()
    _refused(db, lambda: _publish(db, world, amount=10**9))
    assert _sql(db, "SELECT count(*) FROM settlements.expenses").scalar_one() == before
