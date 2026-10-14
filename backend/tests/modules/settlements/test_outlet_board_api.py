"""Rendiciones > Bocas (R7-4): the daily board, the ficha of a boca and the history of a boca settlement.

The board reads; it never mixes the figures of two settlements of the same boca and day, never calls what is
missing while OPEN a difference, and never invents 0 for an expected amount that is not loaded.
"""

from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy.orm import Session

from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.days import days_ago, today, yesterday
from tests.modules.settlements.outlet_support import (
    BASE,
    OUT,
    category,
    close,
    count,
    create,
    employee_part,
    error,
    expense,
    preview,
    reopen,
    set_expected,
    transfer,
)
from tests.modules.settlements.remittance_support import advances, at, remit, reverse

BOARD = f"{OUT}/board"


@pytest.fixture
def centro(boca: Callable[[str], int]) -> int:
    return boca("CENTRO")


def _board(r: Roles, on: str | None = None) -> dict[str, Any]:
    response = r.viewer.get(BOARD, params={"business_date": on or yesterday().isoformat()})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _row(board: dict[str, Any], outlet_id: int) -> dict[str, Any]:
    return next(row for row in board["rows"] if row["outlet_id"] == outlet_id)


def _settlement(row: dict[str, Any], sid: int) -> dict[str, Any]:
    return next(item for item in row["settlements"] if item["id"] == sid)


# --- board ----------------------------------------------------------------------------------------


def test_every_active_boca_has_a_row_even_without_a_settlement(r: Roles, boca: Callable[[str], int]) -> None:
    norte, centro = boca("NORTE"), boca("CENTRO")
    board = _board(r)
    assert board["business_date"] == yesterday().isoformat()
    assert [row["outlet_code"] for row in board["rows"]] == ["CENTRO", "NORTE"]
    for outlet_id in (norte, centro):
        assert _row(board, outlet_id)["settlements"] == []
        assert _row(board, outlet_id)["current_id"] is None
    assert board["counts"] == {"without_settlement": 2, "open": 0, "being_corrected": 0, "closed": 0}


def test_the_board_defaults_to_today(r: Roles, centro: int) -> None:
    response = r.viewer.get(BOARD)
    assert response.status_code == 200, response.text
    assert response.json()["business_date"] == today().isoformat()


def test_normal_flow_remaining_is_zero_and_never_a_difference(r: Roles, centro: int, ids: dict[str, int]) -> None:
    """Expected 1.000.000; remittances 400.000 + 300.000; transfer 100.000; final cash 200.000."""
    sid = create(r.operator, centro, "1000000")
    remit(r.operator, sid, "400000", ids["main_cash"])
    remit(r.operator, sid, "300000", ids["main_cash"])
    transfer(r.operator, sid, "100000", "VERIFIED")
    count(r.operator, sid, ("ARS_20000", 10, ids["main_cash"]))
    item = _settlement(_row(_board(r), centro), sid)
    assert (item["status"], item["financial_state"]) == ("OPEN", "NOT_CLOSED")
    assert item["expected_amount"] == "1000000.00"
    assert (item["remitted"], item["cash_total"], item["transfers_verified"]) == (
        "700000.00",
        "200000.00",
        "100000.00",
    )
    assert (item["cheques"], item["expenses"], item["justified"]) == ("0.00", "0.00", "1000000.00")
    assert (item["remaining"], item["justified_above_expected"]) == ("0.00", None)
    assert (item["difference"], item["difference_kind"]) == (None, None)

    assert close(r.operator, sid).status_code == 201
    board = _board(r)
    item = _settlement(_row(board, centro), sid)
    assert (item["status"], item["difference"], item["difference_kind"]) == ("CLOSED", "0.00", "BALANCED")
    assert (item["remaining"], item["justified_above_expected"]) == (None, None)
    assert board["counts"]["closed"] == 1


def test_open_with_money_remaining_is_not_a_shortage(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000000")
    remit(r.operator, sid, "600000", ids["main_cash"])
    item = _settlement(_row(_board(r), centro), sid)
    assert (item["remaining"], item["difference"], item["difference_kind"]) == ("400000.00", None, None)


def test_expected_not_loaded_is_never_zero(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, None)
    remit(r.operator, sid, "50000", ids["main_cash"])
    item = _settlement(_row(_board(r), centro), sid)
    assert item["expected_amount"] is None
    assert (item["remaining"], item["justified_above_expected"], item["difference"]) == (None, None, None)
    assert item["justified"] == "50000.00"


def test_justified_above_expected_while_open_is_not_a_surplus(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "100000")
    remit(r.operator, sid, "130000", ids["main_cash"], confirm_above_expected=True)
    item = _settlement(_row(_board(r), centro), sid)
    assert (item["remaining"], item["justified_above_expected"]) == ("0.00", "30000.00")
    assert (item["difference"], item["difference_kind"]) == (None, None)


def test_two_settlements_of_the_day_are_never_mixed(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    """R1: remittance 700.000, closed. R2: remittance 100.000, open. OUTLET_ADVANCES = -100.000."""
    first = create(r.operator, centro, "700000")
    remit(r.operator, first, "700000", ids["main_cash"])
    assert close(r.operator, first).status_code == 201
    second = create(r.operator, centro, "500000", confirm_additional=True)
    remit(r.operator, second, "100000", ids["main_cash"])
    assert advances(db, centro) == "-100000.00"

    row = _row(_board(r), centro)
    assert [(i["sequence_number"], i["status"]) for i in row["settlements"]] == [(1, "CLOSED"), (2, "OPEN")]
    assert row["current_id"] == second
    one, two = _settlement(row, first), _settlement(row, second)
    assert (one["remitted"], one["justified"], one["difference"]) == ("700000.00", "700000.00", "0.00")
    assert (two["remitted"], two["justified"], two["remaining"], two["difference"]) == (
        "100000.00",
        "100000.00",
        "400000.00",
        None,
    )
    assert _board(r)["counts"] == {"without_settlement": 0, "open": 1, "being_corrected": 0, "closed": 0}


def test_current_is_the_last_when_every_settlement_is_closed(r: Roles, centro: int, ids: dict[str, int]) -> None:
    first = create(r.operator, centro, "100")
    count(r.operator, first, ("ARS_100", 1, ids["main_cash"]))
    assert close(r.operator, first).status_code == 201
    second = create(r.operator, centro, "200", confirm_additional=True)
    count(r.operator, second, ("ARS_200", 1, ids["main_cash"]))
    assert close(r.operator, second).status_code == 201
    assert _row(_board(r), centro)["current_id"] == second


def test_being_corrected_shows_remaining_not_the_old_difference(
    r: Roles, centro: int, ids: dict[str, int], employee: Callable[[str], int]
) -> None:
    a = employee("Empleado A")
    sid = create(r.operator, centro, "1000000")
    remit(r.operator, sid, "900000", ids["main_cash"])
    assert close(r.operator, sid, employee_part(a, "100000")).status_code == 201
    assert reopen(r.admin, sid, 1).status_code == 200
    board = _board(r)
    item = _settlement(_row(board, centro), sid)
    assert (item["status"], item["financial_state"]) == ("OPEN", "BEING_CORRECTED")
    assert (item["remaining"], item["difference"]) == ("100000.00", None)
    assert board["counts"]["being_corrected"] == 1


def test_another_day_and_inactive_bocas(r: Roles, centro: int, boca: Callable[[str], int]) -> None:
    norte, sur = boca("NORTE"), boca("SUR")
    create(r.operator, norte, "100")
    for outlet_id in (norte, sur):
        assert r.admin.patch(f"/api/v1/outlets/{outlet_id}", json={"active": False}).status_code == 200
    board = _board(r)
    # NORTE was deactivated after its settlement opened: it stays on that day's board; SUR does not.
    assert [(row["outlet_code"], row["outlet_active"]) for row in board["rows"]] == [
        ("CENTRO", True),
        ("NORTE", False),
    ]
    other = _board(r, days_ago(2).isoformat())
    assert [row["outlet_code"] for row in other["rows"]] == ["CENTRO"]


def test_the_board_needs_module_access(as_role: Callable[[str], Any], centro: int) -> None:
    assert as_role("viewer").get(BOARD).status_code == 200
    assert as_role("outsider").get(BOARD).status_code == 403


# --- ficha of the boca ----------------------------------------------------------------------------


def test_ficha_summary_open_last_and_not_consolidated(r: Roles, centro: int, ids: dict[str, int]) -> None:
    summary_url = f"{BASE}/outlets/{centro}/settlements-summary"
    empty = r.viewer.get(summary_url).json()
    assert empty == {
        "outlet_id": centro,
        "open_count": 0,
        "open_settlements": [],
        "last_settlement": None,
        "remitted_not_consolidated": "0.00",
    }
    old = create(r.operator, centro, "300000", business_date=days_ago(3).isoformat())
    remit(r.operator, old, "300000", ids["main_cash"], delivered_at=at(13, on=days_ago(3)))
    assert close(r.operator, old).status_code == 201
    sid = create(r.operator, centro, "500000")
    remit(r.operator, sid, "200000", ids["main_cash"])
    summary = r.viewer.get(summary_url).json()
    assert summary["open_count"] == 1
    assert [s["id"] for s in summary["open_settlements"]] == [sid]
    assert (summary["last_settlement"]["id"], summary["last_settlement"]["financial_state"]) == (sid, "NOT_CLOSED")
    assert summary["remitted_not_consolidated"] == "200000.00"
    assert error(r.viewer.get(f"{BASE}/outlets/999999/settlements-summary")) == (404, "outlet_not_found")


def test_ficha_during_a_correction_counts_only_the_new_remittance(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000000")
    wrong = remit(r.operator, sid, "350000", ids["main_cash"])
    count(r.operator, sid, ("ARS_20000", 32, ids["main_cash"]), ("ARS_10000", 1, ids["main_cash"]))
    assert close(r.operator, sid).status_code == 201
    assert reopen(r.admin, sid, 1).status_code == 200
    reverse(r.admin, sid, wrong, "Eran $300.000")
    remit(r.admin, sid, "300000", ids["main_cash"])
    summary = r.viewer.get(f"{BASE}/outlets/{centro}/settlements-summary").json()
    # The reversed one stays consolidated by the close in effect; the new one waits for the new close.
    assert summary["remitted_not_consolidated"] == "300000.00"
    assert summary["open_settlements"][0]["financial_state"] == "BEING_CORRECTED"
    # The detail shows the close in effect next to the current proposal.
    board = r.viewer.get(f"{OUT}/{sid}").json()["summary"]
    assert (board["remitted_in_effect_close"], board["remitted"]) == ("350000.00", "300000.00")
    assert board["justified_other"] == "650000.00"


def test_detail_splits_delivered_and_other_justified(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000000")
    remit(r.operator, sid, "700000", ids["main_cash"])
    transfer(r.operator, sid, "100000", "VERIFIED")
    count(r.operator, sid, ("ARS_20000", 10, ids["main_cash"]))
    board = r.viewer.get(f"{OUT}/{sid}").json()["summary"]
    assert (board["remitted"], board["justified_other"], board["remaining"]) == ("700000.00", "300000.00", "0.00")
    assert board["remitted_in_effect_close"] is None


# --- history --------------------------------------------------------------------------------------


def test_history_lists_consolidated_remittances_and_expected_changes(
    r: Roles, centro: int, ids: dict[str, int], employee: Callable[[str], int]
) -> None:
    """Expected 1.000.000; remittance 600.000; cash 250.000; transfer 100.000; expense 20.000: shortage 30.000
    split A 20.000 and B 10.000. Then reopen, correct the remittance and close again."""
    a, b = employee("Empleado A"), employee("Empleado B")
    sid = create(r.operator, centro, None)
    set_expected(r.operator, sid, "900000")
    set_expected(r.operator, sid, "1000000")
    wrong = remit(r.operator, sid, "600000", ids["main_cash"])
    count(r.operator, sid, ("ARS_20000", 12, ids["main_cash"]), ("ARS_10000", 1, ids["main_cash"]))
    transfer(r.operator, sid, "100000", "VERIFIED")
    expense(r.operator, sid, category(r.admin), "20000")
    assert preview(r.operator, sid)["difference"] == "-30000.00"
    assert close(r.operator, sid, employee_part(a, "20000"), employee_part(b, "10000")).status_code == 201

    assert reopen(r.admin, sid, 1, "La entrega fue de $630.000").status_code == 200
    reverse(r.admin, sid, wrong, "Eran $630.000")
    right = remit(r.admin, sid, "630000", ids["main_cash"])
    assert close(r.operator, sid, version=2).status_code == 201

    history = r.viewer.get(f"{OUT}/{sid}/history").json()
    assert [(c["settlement_version"], c["in_effect"]) for c in history["closings"]] == [(1, False), (2, True)]
    assert [c["remittances"] for c in history["closings"]] == [
        [{"remittance_id": wrong, "amount": "600000.00"}],
        [{"remittance_id": right, "amount": "630000.00"}],
    ]
    assert history["closings"][1]["reversal_operation_id"] == history["closings"][0]["reversed_by_operation_id"]
    assert [(c["before"], c["after"]) for c in history["expected_changes"]] == [
        (None, "900000.00"),
        ("900000.00", "1000000.00"),
    ]
    assert history["expected_changes"][0]["changed_by_name"]
    assert len(history["differences"]) == 1
    assert {(x["employee_id"], x["amount"]) for x in history["differences"][0]["allocations"]} == {
        (a, "20000.00"),
        (b, "10000.00"),
    }


def test_history_of_a_zero_close_has_no_remittances(r: Roles, centro: int) -> None:
    sid = create(r.operator, centro, "0")
    assert close(r.operator, sid).status_code == 201
    [only] = r.viewer.get(f"{OUT}/{sid}/history").json()["closings"]
    assert (only["kind"], only["remittances"]) == ("ADMINISTRATIVE_ZERO", [])


def test_detail_says_when_closing_has_no_financial_movements(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "0")
    assert r.viewer.get(f"{OUT}/{sid}").json()["summary"]["without_movements"] is True
    missing = create(r.operator, centro, None, confirm_additional=True)
    assert r.viewer.get(f"{OUT}/{missing}").json()["summary"]["without_movements"] is False
    remit(r.operator, sid, "100", ids["main_cash"], confirm_above_expected=True)
    assert r.viewer.get(f"{OUT}/{sid}").json()["summary"]["without_movements"] is False
