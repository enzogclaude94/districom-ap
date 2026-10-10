"""The rules of cheques delivered in payments (R6-2) the database keeps whoever writes: direct SQL, no API.
Enzo's barriers 1-10 for R6-2: a CHEQUE item names its cheque and no account, for its face amount; the cheque
belongs to one payment in effect and leaves the portfolio once; the PAYMENT has an exact leg per cheque,
naming it; never a cheque that was not in the portfolio on the payment's date; a reversal only with every
cheque recovered and back in the portfolio; no DELETE."""

import importlib
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.modules.settlements.direct_support import T, account_id, fund
from tests.modules.settlements.payment_support import third_party
from tests.modules.settlements.test_cheque_cycle_api import deposited, entered
from tests.modules.settlements.test_payments_db_invariants import FINGERPRINT, _payment, _refused, _sql, _user

Leg = tuple[int, int | None, int]


@pytest.fixture
def world(as_role, db: Session) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Cash box with $100.000, a third party and three cheques of the portfolio (since T-10): A $1.000,
    B $2.000 and C $3.000."""
    admin: TestClient = as_role("admin")
    fund(admin, "main_cash", "100000", T - timedelta(days=10))
    return {
        "admin": admin,
        "cash": account_id(admin, "main_cash"),
        "portfolio": account_id(admin, "cheque_portfolio"),
        "merchandise": account_id(admin, "merchandise_payments"),
        "perez": third_party(admin),
        "A": entered(admin, "1000"),
        "B": entered(admin, "2000"),
        "C": entered(admin, "3000"),
        "user": _user(db, "admin@districom.test"),
    }


def _cheque_item(db: Session, pid: int, cheque_id: int | None, amount: int, account: int | None = None) -> int:
    return int(
        _sql(
            db,
            "INSERT INTO settlements.payment_items (payment_id, method, account_id, cheque_id, amount) "
            "VALUES (:p, 'CHEQUE', :a, :c, :x) RETURNING id",
            p=pid,
            a=account,
            c=cheque_id,
            x=amount,
        ).scalar_one()
    )


def _operation(
    db: Session, w: dict[str, Any], legs: list[Leg], *, source: int | None, on: date = T, reverses: int | None = None
) -> int:
    op_id = int(
        _sql(
            db,
            "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id,"
            " request_fingerprint, source_type, source_id, reverses_operation_id, created_by) VALUES "
            "(:t, :d, :desc, gen_random_uuid(), :f, :st, :s, :r, :u) RETURNING id",
            t="REVERSAL" if reverses else "PAYMENT",
            d=on,
            desc="motivo" if reverses else None,
            f=FINGERPRINT,
            st=None if reverses else "payment",
            s=None if reverses else source,
            r=reverses,
            u=w["user"],
        ).scalar_one()
    )
    for account, cheque, amount in legs:
        _sql(
            db,
            "INSERT INTO settlements.ledger_entries (operation_id, account_id, cheque_id, amount) "
            "VALUES (:o, :a, :c, :m)",
            o=op_id,
            a=account,
            c=cheque,
            m=amount,
        )
    return op_id


def _event(
    db: Session, w: dict[str, Any], cheque: int, event: str, pid: int, op: int, reason: str | None = None
) -> None:
    _sql(
        db,
        "INSERT INTO settlements.cheque_events (cheque_id, event_type, payment_id, operation_id, reason, created_by) "
        "VALUES (:c, :e, :p, :o, :r, :u)",
        c=cheque,
        e=event,
        p=pid,
        o=op,
        r=reason,
        u=w["user"],
    )


def _publish(
    db: Session,
    w: dict[str, Any],
    cheques: list[tuple[int, int]],
    *,
    cash: int = 0,
    legs: list[Leg] | None = None,
    events: list[int] | None = None,
    on: date = T,
) -> tuple[int, int]:
    """A payment with cheques (and cash) in the current transaction, as the service writes it; or with other
    legs or DELIVERED events. Returns the payment and its operation."""
    total = sum(amount for _, amount in cheques) + cash
    pid = _payment(db, w, amount=total, business_date=on)
    if cash:
        _sql(
            db,
            "INSERT INTO settlements.payment_items (payment_id, method, account_id, amount) "
            "VALUES (:p, 'CASH', :a, :x)",
            p=pid,
            a=w["cash"],
            x=cash,
        )
    for cheque, amount in cheques:
        _cheque_item(db, pid, cheque, amount)
    expected: list[Leg] = [(w["portfolio"], cheque, -amount) for cheque, amount in cheques]
    if cash:
        expected.append((w["cash"], None, -cash))
    expected.append((w["merchandise"], None, total))
    op = _operation(db, w, legs if legs is not None else expected, source=pid, on=on)
    _sql(db, "UPDATE settlements.payments SET operation_id = :o WHERE id = :p", o=op, p=pid)
    for cheque in events if events is not None else [c for c, _ in cheques]:
        _event(db, w, cheque, "DELIVERED", pid, op)
    return pid, op


def _reverse(db: Session, w: dict[str, Any], pid: int, op: int, recovered: list[int]) -> int:
    legs = [
        (account, cheque, -amount)
        for account, cheque, amount in _sql(
            db, "SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = :o", o=op
        ).all()
    ]
    on = _sql(db, "SELECT business_date FROM settlements.payments WHERE id = :p", p=pid).scalar_one()
    reversal = _operation(db, w, legs, source=None, on=on, reverses=op)
    _sql(
        db,
        "UPDATE settlements.payments SET reversal_operation_id = :r, reversed_by = :u, reversed_at = now(), "
        "reversal_reason = 'Pago equivocado' WHERE id = :p",
        r=reversal,
        u=w["user"],
        p=pid,
    )
    for cheque in recovered:
        _event(db, w, cheque, "RECOVERED_FROM_PAYMENT", pid, reversal, "Pago equivocado")
    return reversal


def _position(db: Session, cheque: int) -> Any:
    return _sql(
        db,
        "SELECT coalesce(sum(e.amount), 0) FROM settlements.ledger_entries e "
        "JOIN settlements.accounts a ON a.id = e.account_id WHERE e.cheque_id = :c AND a.kind = 'CHEQUE_PORTFOLIO'",
        c=cheque,
    ).scalar_one()


def test_the_helpers_write_what_the_service_writes(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    """The control: cash and two cheques written by hand exactly like the service, and their reversal with
    both recovered, are accepted."""
    pid, op = _publish(db, world, [(world["A"], 1000), (world["B"], 2000)], cash=500)
    db.commit()
    assert (_position(db, world["A"]), _position(db, world["B"])) == (0, 0)
    _reverse(db, world, pid, op, [world["A"], world["B"]])
    db.commit()
    assert (_position(db, world["A"]), _position(db, world["B"])) == (1000, 2000)


# --- 1, 2, 3: the item -----------------------------------------------------------------------------------


def test_1_a_cheque_item_names_its_cheque(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    message = _refused(db, lambda: _cheque_item(db, _payment(db, world), None, 1000))
    assert "ck_payment_items_method_fields" in message or "cheque <NULL>" in message


def test_2_a_cheque_item_names_no_account(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    message = _refused(db, lambda: _cheque_item(db, _payment(db, world), world["A"], 1000, account=world["cash"]))
    assert "ck_payment_items_method_fields" in message


def test_3_the_amount_is_the_face_amount(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    message = _refused(db, lambda: _cheque_item(db, _payment(db, world), world["A"], 500))
    assert "not paid for its whole amount" in message


def test_3_a_cheque_appears_once_per_payment(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        pid = _payment(db, world, amount=2000)
        _cheque_item(db, pid, world["A"], 1000)
        _cheque_item(db, pid, world["A"], 1000)

    assert "uq_payment_items_payment_id_cheque_id" in _refused(db, write)


# --- 4, 5: one payment in effect, one exit from the portfolio --------------------------------------------


def test_4_and_5_a_cheque_in_two_payments_in_effect_is_refused(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    _publish(db, world, [(world["A"], 1000)])
    db.commit()
    message = _refused(db, lambda: _publish(db, world, [(world["A"], 1000)]))
    assert "partial or negative position" in message or "another payment in effect" in message


def test_5_a_deposited_cheque_is_never_delivered_too(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    """«Depositado» and «Entregado» at once is impossible: the cheque would leave the portfolio twice."""
    deposited(world["admin"], [world["C"]])
    message = _refused(db, lambda: _publish(db, world, [(world["C"], 3000)]))
    assert "partial or negative position" in message or "was not in the portfolio" in message


# --- 6, 7: the legs ----------------------------------------------------------------------------------------


def test_6_the_operation_has_the_exact_leg_of_each_cheque(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    w = world
    # The cheque paid with cash instead of leaving the portfolio.
    cash_instead = [(w["cash"], None, -1000), (w["merchandise"], None, 1000)]
    assert "does not match payment" in _refused(db, lambda: _publish(db, w, [(w["A"], 1000)], legs=cash_instead))
    # Another cheque leaves instead of the one of the item.
    swapped = [(w["portfolio"], w["B"], -2000), (w["merchandise"], None, 2000)]
    assert "not paid for its whole amount" in _refused(db, lambda: _publish(db, w, [(w["A"], 2000)], legs=swapped))


def test_7_the_portfolio_leg_names_its_cheque(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    w = world
    nameless = [(w["portfolio"], None, -1000), (w["merchandise"], None, 1000)]
    message = _refused(db, lambda: _publish(db, w, [(w["A"], 1000)], legs=nameless))
    assert "names a cheque iff its account is a cheque account" in message


def test_each_cheque_has_its_delivered_event(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    w = world
    assert "exactly one DELIVERED" in _refused(db, lambda: _publish(db, w, [(w["A"], 1000)], events=[]))
    twice = [w["A"], w["A"]]
    assert "exactly one DELIVERED" in _refused(db, lambda: _publish(db, w, [(w["A"], 1000)], events=twice))
    other = [w["A"], w["B"]]
    assert "exactly one DELIVERED" in _refused(db, lambda: _publish(db, w, [(w["A"], 1000)], events=other))


def test_a_delivered_event_names_its_payment(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    def write() -> None:
        _sql(
            db,
            "INSERT INTO settlements.cheque_events (cheque_id, event_type, operation_id, created_by) "
            "VALUES (:c, 'DELIVERED', NULL, :u)",
            c=world["A"],
            u=world["user"],
        )

    message = _refused(db, write)
    assert "ck_cheque_events_payment_events" in message or "ck_cheque_events_cycle_operation" in message


# --- 8: never a cheque that was not in the portfolio on the payment's date -------------------------------


def test_8_never_a_cheque_received_after_the_payment_date(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    late = entered(world["admin"], "4000", on=T - timedelta(days=3))
    message = _refused(db, lambda: _publish(db, world, [(late, 4000)], on=T - timedelta(days=5)))
    assert "was not in the portfolio" in message


def test_8_never_a_cheque_out_of_the_portfolio(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    deposited(world["admin"], [world["B"]])
    message = _refused(db, lambda: _publish(db, world, [(world["B"], 2000)]))
    assert "partial or negative position" in message or "was not in the portfolio" in message


# --- 9: a reversal only with every cheque recovered and back -----------------------------------------------


def test_9_a_reversal_without_the_recovery_is_refused(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid, op = _publish(db, world, [(world["A"], 1000), (world["B"], 2000)])
    db.commit()
    message = _refused(db, lambda: _reverse(db, world, pid, op, []))
    assert "reversed only with every cheque recovered" in message
    message = _refused(db, lambda: _reverse(db, world, pid, op, [world["A"]]))
    assert "reversed only with every cheque recovered" in message


def test_9_no_recovery_while_the_payment_is_in_effect(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid, op = _publish(db, world, [(world["A"], 1000)])
    db.commit()
    message = _refused(db, lambda: _event(db, world, world["A"], "RECOVERED_FROM_PAYMENT", pid, op, "Volvió"))
    assert "reversed only with every cheque recovered" in message


def test_9_the_recovery_carries_its_reason(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid, op = _publish(db, world, [(world["A"], 1000)])
    db.commit()
    message = _refused(db, lambda: _event(db, world, world["A"], "RECOVERED_FROM_PAYMENT", pid, op, None))
    assert "ck_cheque_events_reason_required" in message


def test_9_a_reversal_puts_every_cheque_back(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    """A reversal that is not the mirror of the payment (a cheque left out) is not its reversal."""
    w = world
    pid, op = _publish(db, w, [(w["A"], 1000)], cash=500)
    db.commit()

    def partial() -> None:
        reversal = _operation(db, w, [(w["cash"], None, 500), (w["merchandise"], None, -500)], source=None, reverses=op)
        _sql(
            db,
            "UPDATE settlements.payments SET reversal_operation_id = :r, reversed_by = :u, reversed_at = now(), "
            "reversal_reason = 'x' WHERE id = :p",
            r=reversal,
            u=w["user"],
            p=pid,
        )
        _event(db, w, w["A"], "RECOVERED_FROM_PAYMENT", pid, reversal, "x")

    assert "is not exactly its operation undone" in _refused(db, partial)
    assert _position(db, w["A"]) == 0


# --- 10: nothing is deleted -------------------------------------------------------------------------------


def test_10_nothing_is_deleted_nor_rewritten(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    pid, _ = _publish(db, world, [(world["A"], 1000)])
    db.commit()
    assert _refused(db, lambda: _sql(db, "DELETE FROM settlements.payment_items WHERE payment_id = :p", p=pid))
    assert _refused(db, lambda: _sql(db, "DELETE FROM settlements.cheque_events WHERE payment_id = :p", p=pid))
    assert _refused(
        db, lambda: _sql(db, "UPDATE settlements.cheque_events SET reason = 'x' WHERE payment_id = :p", p=pid)
    )
    assert _refused(
        db,
        lambda: _sql(
            db, "UPDATE settlements.payment_items SET cheque_id = :c WHERE payment_id = :p", c=world["B"], p=pid
        ),
    )
    assert _sql(db, "SELECT count(*) FROM settlements.payment_items WHERE payment_id = :p", p=pid).scalar_one() == 1


def test_downgrade_is_refused_once_a_cheque_was_delivered(db: Session, world) -> None:  # type: ignore[no-untyped-def]
    """0020's downgrade would lose to whom each cheque was delivered: it refuses while one exists."""
    migration = importlib.import_module("migrations.versions.0020_settlements_cheque_payments")
    _sql(db, migration.REFUSE_DOWNGRADE)  # nothing delivered yet: the guard lets it through
    db.rollback()
    _publish(db, world, [(world["A"], 1000)])
    db.commit()
    assert "would lose their history" in _refused(db, lambda: _sql(db, migration.REFUSE_DOWNGRADE))
