"""CIERRE ADMINISTRATIVO CERO (Enzo's R7-2-Q1, ADR 009): a boca settlement of $0 without movements closes
without a ledger operation and without $0 entries, but with its close, snapshot, version, audit and history.

The twelve cases Enzo asked for, numbered as in his answer; the direct-SQL case (12) has its variants per
component in `test_outlet_settlements_db_invariants.py`.
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
from tests.modules.settlements.outlet_support import (
    BASE,
    OUT,
    ZERO_CLOSE_SQL,
    addition,
    balances,
    close,
    count,
    create,
    error,
    legs,
    operations_of,
    preview,
    reopen,
    set_expected,
    transfer,
    zero_entries,
)


@pytest.fixture
def centro(boca: Callable[[str], int]) -> int:
    return boca("CENTRO")


def _closings(db: Session, sid: int) -> list[tuple[int, str, int | None]]:
    rows = db.execute(
        text(
            "SELECT settlement_version, kind, operation_id FROM settlements.settlement_closings "
            "WHERE settlement_id = :s ORDER BY id"
        ),
        {"s": sid},
    )
    return [(int(v), str(k), o) for v, k, o in rows.all()]


def _history(r: Roles, sid: int) -> Any:
    response = r.viewer.get(f"{OUT}/{sid}/history")
    assert response.status_code == 200, response.text
    return response.json()


ZERO_SNAPSHOT_AMOUNTS = {
    "expected_amount": "0.00",
    "cash": "0.00",
    "additions": "0.00",
    "transfers_verified": "0.00",
    "cheques": "0.00",
    "expenses": "0.00",
    "pending": "0.00",
    "difference": "0.00",
}


# 1 ------------------------------------------------------------------------------------------------


def test_1_expected_null_does_not_close(r: Roles, centro: int, db: Session) -> None:
    sid = create(r.operator, centro)
    shown = preview(r.operator, sid)
    assert shown["administrative_zero"] is False
    assert [b["code"] for b in shown["blockers"]] == ["expected_amount_required"]
    assert error(close(r.operator, sid)) == (422, "expected_amount_required")
    assert _closings(db, sid) == []
    assert r.viewer.get(f"{OUT}/{sid}").json()["status"] == "OPEN"


# 2, 3, 4 ------------------------------------------------------------------------------------------


def test_2_3_4_expected_zero_with_everything_zero_closes_without_operation_nor_zero_entries(
    r: Roles, centro: int, db: Session
) -> None:
    before = balances(r.admin)
    sid = create(r.operator, centro, "0")
    shown = preview(r.operator, sid)
    assert (shown["blockers"], shown["administrative_zero"], shown["difference"]) == ([], True, "0.00")
    assert shown["legs"] == []
    closed = close(r.operator, sid)
    assert closed.status_code == 201, closed.text
    # 3: no ledger operation, neither for the close nor anything else of the settlement.
    assert closed.json()["close_in_effect"]["operation_id"] is None
    assert operations_of(db, sid) == []
    assert _closings(db, sid) == [(1, "ADMINISTRATIVE_ZERO", None)]
    # 4: no $0 entry anywhere, and no account moved.
    assert zero_entries(db) == 0
    assert balances(r.admin) == before


def test_one_cent_of_any_kind_is_a_financial_close(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    """A rejected transfer does not count; a single verified cent does, and then expected 0 needs allocation."""
    sid = create(r.operator, centro, "0")
    transfer(r.operator, sid, "0.01", "REJECTED")
    assert preview(r.operator, sid)["administrative_zero"] is True
    transfer(r.operator, sid, "0.01", "VERIFIED")
    shown = preview(r.operator, sid)
    assert shown["administrative_zero"] is False
    assert shown["difference"] == "0.01"
    closed = close(r.admin, sid, {"target": "COMPANY", "amount": "0.01", "reason": "Sobrante de un centavo"})
    assert closed.status_code == 201, closed.text
    operation = closed.json()["close_in_effect"]["operation_id"]
    assert closed.json()["close_in_effect"]["kind"] == "FINANCIAL"
    # Expected 0: there is no leg on «Cobranzas de boca».
    assert legs(db, operation) == {"bank": "0.01", "settlement_difference": "-0.01"}


def test_a_declared_transfer_is_not_a_zero_close(r: Roles, centro: int) -> None:
    """A transfer still waiting for verification blocks the close, it never vanishes into a zero close."""
    sid = create(r.operator, centro, "0")
    transfer(r.operator, sid, "100")
    shown = preview(r.operator, sid)
    assert shown["administrative_zero"] is False
    assert shown["blockers"] != []


# 5, 6 ---------------------------------------------------------------------------------------------


def test_5_6_zero_close_shows_closed_and_is_in_the_history_and_the_audit(r: Roles, centro: int, db: Session) -> None:
    sid = create(r.operator, centro, "0")
    assert close(r.operator, sid).status_code == 201
    body = r.viewer.get(f"{OUT}/{sid}").json()
    assert (body["status"], body["financial_state"], body["version"]) == ("CLOSED", "CLOSED", 1)
    assert body["close_in_effect"]["kind"] == "ADMINISTRATIVE_ZERO"
    assert body["close_in_effect"]["operation_id"] is None
    assert body["closed_by"] is not None
    listed = r.viewer.get(OUT, params={"financial_state": "CLOSED"}).json()
    assert [i["id"] for i in listed["items"]] == [sid]
    assert r.viewer.get(f"{BASE}/outlets/{centro}/settlements-summary").json()["open_count"] == 0
    history = _history(r, sid)
    assert history["financial_state"] == "CLOSED"
    [closing] = history["closings"]
    assert (closing["kind"], closing["operation_id"], closing["in_effect"], closing["legs"]) == (
        "ADMINISTRATIVE_ZERO",
        None,
        True,
        [],
    )
    assert closing["business_date"] == yesterday().isoformat()
    assert closing["closed_by_name"]
    assert closing["snapshot"] == {
        "type": "OUTLET",
        "outlet_id": str(centro),
        "version": "1",
        "business_date": yesterday().isoformat(),
        **ZERO_SNAPSHOT_AMOUNTS,
    }
    audit = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = 'settlements.settlement.closed' AND entity_id = :s"),
        {"s": str(sid)},
    ).scalar_one()
    assert audit["after"]["closing_kind"] == "ADMINISTRATIVE_ZERO"
    assert audit["after"]["close_operation_id"] is None


# 7, 8 ---------------------------------------------------------------------------------------------


def test_7_8_reopen_a_zero_close_and_close_it_again_at_zero(r: Roles, centro: int, db: Session) -> None:
    sid = create(r.operator, centro, "0")
    close(r.operator, sid)
    assert error(reopen(r.operator, sid, 1)) == (403, "forbidden")
    reopened = reopen(r.admin, sid, 1)
    assert reopened.status_code == 200, reopened.text
    body = reopened.json()
    assert (body["status"], body["financial_state"], body["version"]) == ("OPEN", "BEING_CORRECTED", 2)
    assert body["close_in_effect"]["kind"] == "ADMINISTRATIVE_ZERO"
    # Nothing to reverse: no operation existed.
    assert operations_of(db, sid) == []
    again = close(r.operator, sid, version=2)
    assert again.status_code == 201, again.text
    assert _closings(db, sid) == [(1, "ADMINISTRATIVE_ZERO", None), (2, "ADMINISTRATIVE_ZERO", None)]
    assert operations_of(db, sid) == []
    history = _history(r, sid)
    assert [(c["settlement_version"], c["in_effect"], c["reversal_operation_id"]) for c in history["closings"]] == [
        (1, False, None),
        (2, True, None),
    ]
    assert [(o["from_version"], o["close_operation_id"]) for o in history["reopenings"]] == [(1, None)]
    assert zero_entries(db) == 0


# 9 ------------------------------------------------------------------------------------------------


def test_9_zero_then_reopen_then_cash_is_a_financial_close(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "0")
    close(r.operator, sid)
    reopen(r.admin, sid, 1)
    set_expected(r.operator, sid, "5000")
    count(r.operator, sid, ("ARS_1000", 4, ids["main_cash"]))
    addition(r.operator, sid, "1000", ids["main_cash"])
    shown = preview(r.operator, sid)
    assert shown["administrative_zero"] is False
    closed = close(r.operator, sid, version=2)
    assert closed.status_code == 201, closed.text
    operation = closed.json()["close_in_effect"]["operation_id"]
    # A normal close; never a reversal of an operation that never existed.
    assert [t for _, t in operations_of(db, sid)] == ["SETTLEMENT_CLOSE"]
    assert legs(db, operation) == {"outlet_collections": "-5000.00", "main_cash": "5000.00"}
    assert _closings(db, sid) == [(1, "ADMINISTRATIVE_ZERO", None), (2, "FINANCIAL", operation)]
    assert _history(r, sid)["closings"][1]["reversal_operation_id"] is None


# 10 -----------------------------------------------------------------------------------------------


def test_10_financial_then_reopen_then_zero_reverses_and_closes_administratively(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "3000")
    count(r.operator, sid, ("ARS_1000", 3, ids["main_cash"]))
    first = close(r.operator, sid).json()["close_in_effect"]["operation_id"]
    reopen(r.admin, sid, 1)
    count(r.operator, sid)
    set_expected(r.operator, sid, "0")
    shown = preview(r.operator, sid)
    assert (shown["administrative_zero"], shown["blockers"]) == (True, [])
    closed = close(r.operator, sid, version=2)
    assert closed.status_code == 201, closed.text
    assert closed.json()["close_in_effect"] == {**closed.json()["close_in_effect"], "kind": "ADMINISTRATIVE_ZERO"}
    assert closed.json()["close_in_effect"]["operation_id"] is None
    operations = operations_of(db, sid)
    assert [t for _, t in operations] == ["SETTLEMENT_CLOSE", "REVERSAL"]
    reversal = operations[1][0]
    assert legs(db, reversal) == {"outlet_collections": "3000.00", "main_cash": "-3000.00"}
    assert balances(r.admin)["outlet_collections"] == "0.00"
    assert balances(r.admin)["main_cash"] == "0.00"
    history = _history(r, sid)
    assert [
        (c["kind"], c["operation_id"], c["reversal_operation_id"], c["reversed_by_operation_id"], c["in_effect"])
        for c in history["closings"]
    ] == [("FINANCIAL", first, None, reversal, False), ("ADMINISTRATIVE_ZERO", None, reversal, None, True)]
    assert zero_entries(db) == 0
    # And back: reopen the zero close, load cash, close financially; nothing more to reverse.
    reopen(r.admin, sid, 2)
    set_expected(r.operator, sid, "1000")
    count(r.operator, sid, ("ARS_1000", 1, ids["main_cash"]))
    third = close(r.operator, sid, version=3).json()["close_in_effect"]["operation_id"]
    assert [t for _, t in operations_of(db, sid)] == ["SETTLEMENT_CLOSE", "REVERSAL", "SETTLEMENT_CLOSE"]
    assert legs(db, third) == {"outlet_collections": "-1000.00", "main_cash": "1000.00"}


def test_zero_correction_withdraws_the_published_cheque(r: Roles, centro: int, db: Session) -> None:
    from tests.modules.settlements.test_cheques_api import bank, physical

    sid = create(r.operator, centro, "50000")
    cheque = r.operator.post(f"{OUT}/{sid}/cheques", json=physical(bank(r.operator))).json()["cheques"][-1]["id"]
    assert close(r.operator, sid).status_code == 201
    assert r.viewer.get(f"{BASE}/cheques/{cheque}").json()["in_effect"] is True
    reopen(r.admin, sid, 1)
    voided = r.operator.post(f"{OUT}/{sid}/cheques/{cheque}/void", json={"reason": "Cargado por error"})
    assert voided.status_code == 200, voided.text
    set_expected(r.operator, sid, "0")
    closed = close(r.operator, sid, version=2)
    assert closed.status_code == 201, closed.text
    assert r.viewer.get(f"{BASE}/cheques/{cheque}").json()["in_effect"] is False
    assert balances(r.admin)["cheque_portfolio"] == "0.00"


# 11 -----------------------------------------------------------------------------------------------


def test_11_delivery_does_not_get_the_exception(r: Roles, driver, db: Session) -> None:  # type: ignore[no-untyped-def]
    pedro = driver("Pedro Gómez")
    response = r.operator.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": yesterday().isoformat(),
            "employee_id": pedro,
            "expected_amount": "0",
        },
    )
    assert response.status_code == 422, response.text
    assert db.execute(text("SELECT count(*) FROM settlements.settlements")).scalar_one() == 0


# 12 -----------------------------------------------------------------------------------------------


def test_12_direct_sql_cannot_close_without_operation(r: Roles, centro: int, db: Session) -> None:
    """A $1 boca settlement marked CLOSED with an administrative closing is refused at commit."""
    sid = create(r.operator, centro, "1")
    db.execute(text(ZERO_CLOSE_SQL), {"s": sid, "f": "a" * 64, "cash": "0.00"})
    with pytest.raises(DBAPIError, match="administrative zero close"):
        db.commit()
    db.rollback()
    assert r.viewer.get(f"{OUT}/{sid}").json()["status"] == "OPEN"
