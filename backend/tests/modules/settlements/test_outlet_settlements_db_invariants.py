"""Rules of boca settlements that the database enforces whoever writes (migration 0023), checked with raw SQL.

The load is made through the API (as an operator would); each case then tries to break a rule directly.
"""

from collections.abc import Callable
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.days import yesterday
from tests.modules.settlements.fuel_support import fuel_category
from tests.modules.settlements.outlet_support import (
    BASE,
    OUT,
    ZERO_CLOSE_SQL,
    addition,
    category,
    close,
    count,
    create,
    expense,
    reopen,
    set_expected,
    transfer,
)
from tests.modules.settlements.test_cheques_api import bank, physical

FINGERPRINT = "a" * 64


@pytest.fixture
def centro(boca: Callable[[str], int]) -> int:
    return boca("CENTRO")


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def _insert(db: Session, **fields: Any) -> str:
    """Tries a raw settlement insert; the user is any existing one."""
    values = {
        "type": "OUTLET",
        "business_date": yesterday(),
        "employee_id": None,
        "outlet_id": None,
        "sequence_number": 1,
        "expected_amount": 1,
        **fields,
    }
    return _refused(
        db,
        "INSERT INTO settlements.settlements (type, business_date, employee_id, outlet_id, sequence_number, "
        "expected_amount, client_request_id, request_fingerprint, created_by) SELECT :type, :business_date, "
        ":employee_id, :outlet_id, :sequence_number, :expected_amount, :k, :f, id FROM users ORDER BY id LIMIT 1",
        k=uuid4(),
        f=FINGERPRINT,
        **values,
    )


def _zero_close(db: Session, sid: int, cash: str = "0.00") -> str:
    """Marks the settlement CLOSED by an administrative zero close written by hand."""
    return _refused(db, ZERO_CLOSE_SQL, s=sid, f=FINGERPRINT, cash=cash)


ZERO_REFUSED = "an administrative zero close needs a boca settlement of $0 without movements"


# --- identity by type -----------------------------------------------------------------------------


def test_a_boca_settlement_has_a_boca_and_no_employee(db: Session, centro: int, driver) -> None:  # type: ignore[no-untyped-def]
    pedro = driver("Pedro Gómez")
    # Without a boca, the active-boca trigger refuses it even before the check.
    assert "inactive_outlet" in _insert(db, outlet_id=None)
    assert "identity_by_type" in _insert(db, outlet_id=centro, employee_id=pedro)
    assert "identity_by_type" in _insert(db, type="DELIVERY", employee_id=None)
    assert "identity_by_type" in _insert(db, type="DELIVERY", employee_id=pedro, outlet_id=centro)
    assert "ck_settlements_" in _insert(db, type="OTHER", outlet_id=centro)


def test_expected_amount_by_type(db: Session, centro: int, driver) -> None:  # type: ignore[no-untyped-def]
    pedro = driver("Pedro Gómez")
    assert "expected_amount_by_type" in _insert(db, outlet_id=centro, expected_amount=-1)
    assert "expected_amount_by_type" in _insert(db, type="DELIVERY", employee_id=pedro, expected_amount=0)
    assert "expected_amount_by_type" in _insert(db, type="DELIVERY", employee_id=pedro, expected_amount=None)


def test_a_closed_boca_settlement_has_its_expected_amount(r: Roles, db: Session, centro: int) -> None:
    sid = create(r.operator, centro, "0")
    assert close(r.operator, sid).status_code == 201
    message = _refused(db, "UPDATE settlements.settlements SET expected_amount = NULL WHERE id = :s", s=sid)
    assert "CLOSED" in message or "closed_has_expected" in message
    open_one = create(r.operator, centro, None, confirm_additional=True)
    assert "closed_has_expected" in _zero_close(db, open_one)


def test_sequence_is_unique_per_boca_and_day_and_apart_from_deliveries(
    r: Roles, db: Session, centro: int, driver
) -> None:  # type: ignore[no-untyped-def]
    create(r.operator, centro, "10")
    assert "uq_settlements_outlet_sequence" in _insert(db, outlet_id=centro)
    # A delivery with the same date and number is another sequence.
    pedro = driver("Pedro Gómez")
    db.execute(
        text(
            "INSERT INTO settlements.settlements (type, business_date, employee_id, sequence_number, "
            "expected_amount, client_request_id, request_fingerprint, created_by) SELECT 'DELIVERY', :d, :e, 1, 1, "
            ":k, :f, id FROM users ORDER BY id LIMIT 1"
        ),
        {"d": yesterday(), "e": pedro, "k": uuid4(), "f": FINGERPRINT},
    )
    db.commit()


def test_an_inactive_boca_starts_no_settlement(r: Roles, db: Session, centro: int) -> None:
    assert r.admin.patch(f"/api/v1/outlets/{centro}", json={"active": False}).status_code == 200
    assert "inactive_outlet" in _insert(db, outlet_id=centro)


def test_boca_of_a_settlement_never_changes(r: Roles, db: Session, centro: int, boca: Callable[[str], int]) -> None:
    sid = create(r.operator, centro, "10")
    norte = boca("NORTE")
    message = _refused(db, "UPDATE settlements.settlements SET outlet_id = :o WHERE id = :s", o=norte, s=sid)
    assert "never change" in message


# --- what a boca does not have --------------------------------------------------------------------


def test_no_pending_collections_on_a_boca(r: Roles, db: Session, centro: int, driver) -> None:  # type: ignore[no-untyped-def]
    sid = create(r.operator, centro, "10")
    pedro = driver("Pedro Gómez")
    message = _refused(
        db,
        "INSERT INTO settlements.pending_collections (settlement_id, customer_name, amount, reason, "
        "responsible_employee_id, client_request_id, request_fingerprint, created_by) "
        "SELECT :s, 'X', 1, 'x', :e, :k, :f, created_by FROM settlements.settlements WHERE id = :s",
        s=sid,
        e=pedro,
        k=uuid4(),
        f=FINGERPRINT,
    )
    assert "pending_not_enabled_for_outlets" in message


def test_no_fuel_on_a_boca(r: Roles, db: Session, centro: int) -> None:
    sid = create(r.operator, centro, "10")
    message = _refused(
        db,
        "INSERT INTO settlements.expenses (settlement_id, category_id, amount, client_request_id, "
        "request_fingerprint, created_by) SELECT :s, :c, 3, :k, :f, created_by FROM settlements.settlements "
        "WHERE id = :s",
        s=sid,
        c=fuel_category(db),
        k=uuid4(),
        f=FINGERPRINT,
    )
    assert "fuel_not_allowed_for_outlet" in message
    other = expense(r.operator, sid, category(r.admin), "100")
    message = _refused(
        db,
        "UPDATE settlements.expenses SET category_id = :c WHERE id = :e",
        c=fuel_category(db),
        e=other,
    )
    assert "fuel_not_allowed_for_outlet" in message


# --- «Cobranzas de boca» is closed ----------------------------------------------------------------


def _operation(db: Session, type: str, source_id: int | None = None, reverses: int | None = None) -> int:
    return int(
        db.execute(
            text(
                "INSERT INTO settlements.financial_operations (type, business_date, description, client_request_id, "
                "request_fingerprint, source_type, source_id, reverses_operation_id, created_by) SELECT "
                ":t, :d, 'x', :k, :f, :st, :si, :r, id FROM users ORDER BY id LIMIT 1 RETURNING id"
            ),
            {
                "t": type,
                "d": yesterday(),
                "k": uuid4(),
                "f": FINGERPRINT,
                "st": "settlement" if source_id else None,
                "si": source_id,
                "r": reverses,
            },
        ).scalar_one()
    )


def _leg(db: Session, operation_id: int, account_id: int, amount: str) -> str:
    return _refused(
        db,
        "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, :m)",
        o=operation_id,
        a=account_id,
        m=amount,
    )


def test_outlet_collections_moves_only_with_a_boca_close(
    r: Roles, db: Session, centro: int, ids: dict[str, int], driver
) -> None:  # type: ignore[no-untyped-def]
    oc = ids["outlet_collections"]
    for type in ("CASH_TRANSFER", "ADJUSTMENT", "OPENING_BALANCE"):
        assert "outlet_collections_closed" in _leg(db, _operation(db, type), oc, "-1"), type
    # The close of a delivery never touches it.
    pedro = driver("Pedro Gómez")
    delivery = r.operator.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": yesterday().isoformat(),
            "employee_id": pedro,
            "expected_amount": "1",
        },
    ).json()["id"]
    assert "outlet_collections_closed" in _leg(db, _operation(db, "SETTLEMENT_CLOSE", delivery), oc, "-1")
    # A boca close takes value out, never puts it in; its reversal does the opposite.
    sid = create(r.operator, centro, "1")
    assert "outlet_collections_closed" in _leg(db, _operation(db, "SETTLEMENT_CLOSE", sid), oc, "1")
    addition(r.operator, sid, "1", ids["main_cash"])
    closed = close(r.operator, sid).json()["close_in_effect"]["operation_id"]
    assert "outlet_collections_closed" in _leg(db, _operation(db, "REVERSAL", reverses=closed), oc, "-1")


def test_a_boca_close_takes_exactly_its_expected_amount(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    sid = create(r.operator, centro, "10")
    operation = _operation(db, "SETTLEMENT_CLOSE", sid)
    db.execute(
        text(
            "INSERT INTO settlements.ledger_entries (operation_id, account_id, amount) VALUES (:o, :a, -9), (:o, :b, 9)"
        ),
        {"o": operation, "a": ids["outlet_collections"], "b": ids["main_cash"]},
    )
    with pytest.raises(DBAPIError) as caught:
        db.commit()
    db.rollback()
    assert "takes exactly its expected amount" in str(caught.value.orig)


# --- the administrative zero close (Enzo's R7-2-Q1), with $1 of any component ---------------------


def test_zero_close_by_hand_of_a_clean_boca_settlement_holds(r: Roles, db: Session, centro: int) -> None:
    """The control case: the same raw statement as below, on a settlement of $0 without movements, commits."""
    sid = create(r.operator, centro, "0")
    db.execute(text(ZERO_CLOSE_SQL), {"s": sid, "f": FINGERPRINT, "cash": "0.00"})
    db.commit()
    assert r.viewer.get(f"{OUT}/{sid}").json()["financial_state"] == "CLOSED"


LOADS: dict[str, Callable[[Roles, int, dict[str, int]], object]] = {
    "expected": lambda r, sid, ids: set_expected(r.operator, sid, "1"),
    # The smallest note counted; every other component with $1.
    "cash": lambda r, sid, ids: count(r.operator, sid, ("ARS_100", 1, ids["main_cash"])),
    "addition": lambda r, sid, ids: addition(r.operator, sid, "1", ids["main_cash"]),
    "verified_transfer": lambda r, sid, ids: transfer(r.operator, sid, "1", "VERIFIED"),
    "declared_transfer": lambda r, sid, ids: transfer(r.operator, sid, "1"),
    "cheque": lambda r, sid, ids: r.operator.post(f"{OUT}/{sid}/cheques", json=physical(bank(r.operator), amount="1")),
    "expense": lambda r, sid, ids: expense(r.operator, sid, category(r.admin), "1"),
}


@pytest.mark.parametrize("component", list(LOADS))
def test_zero_close_with_one_peso_of_any_component_is_refused(
    r: Roles, db: Session, centro: int, ids: dict[str, int], component: str
) -> None:
    sid = create(r.operator, centro, "0")
    LOADS[component](r, sid, ids)
    assert ZERO_REFUSED in _zero_close(db, sid)
    assert r.viewer.get(f"{OUT}/{sid}").json()["status"] == "OPEN"


def test_a_difference_never_exists_without_its_close_operation(r: Roles, db: Session, centro: int) -> None:
    """Invariant 8 by construction: a difference belongs to a financial close, so a zero close has none."""
    sid = create(r.operator, centro, "0")
    message = _refused(
        db,
        "INSERT INTO settlements.settlement_differences (settlement_id, settlement_version, kind, amount, "
        "created_by) SELECT id, version, 'SURPLUS', 1, created_by FROM settlements.settlements WHERE id = :s",
        s=sid,
    )
    assert "close_operation_id" in message


def test_zero_close_whose_snapshot_lies_is_refused(r: Roles, db: Session, centro: int) -> None:
    sid = create(r.operator, centro, "0")
    assert ZERO_REFUSED in _zero_close(db, sid, cash="1.00")


def test_zero_close_with_a_close_still_in_the_ledger_is_refused(
    r: Roles, db: Session, centro: int, ids: dict[str, int]
) -> None:
    """Reopened after a financial close and corrected to $0: closing at zero without reversing it is refused."""
    sid = create(r.operator, centro, "1")
    addition(r.operator, sid, "1", ids["main_cash"])
    assert close(r.operator, sid).status_code == 201
    assert reopen(r.admin, sid, 1).status_code == 200
    void = r.operator.get(f"{OUT}/{sid}").json()["additions"][0]["id"]
    voided = r.operator.post(f"{OUT}/{sid}/additions/{void}/void", json={"reason": "Cargada por error"})
    assert voided.status_code == 200, voided.text
    set_expected(r.operator, sid, "0")
    message = _zero_close(db, sid)
    assert ZERO_REFUSED in message or "does not match" in message or "closed_has_close" in message


def test_a_delivery_never_closes_without_an_operation(r: Roles, db: Session, driver, ids: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    pedro = driver("Pedro Gómez")
    delivery = r.operator.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": yesterday().isoformat(),
            "employee_id": pedro,
            "expected_amount": "1",
        },
    ).json()["id"]
    message = _zero_close(db, delivery)
    assert "delivery_close_financial" in message or ZERO_REFUSED in message or "closed_has_close" in message
    message = _refused(
        db,
        "UPDATE settlements.settlements SET status = 'CLOSED', closed_at = now(), closed_by = created_by WHERE id = :s",
        s=delivery,
    )
    assert "closed_has_close" in message
