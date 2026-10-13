"""Boca de expendio settlements (R7-2, ADR 009): opening, loading, closing and correcting through the API.

The administrative zero close has its own file (`test_outlet_zero_close.py`); the database rules are in
`test_outlet_settlements_db_invariants.py`. Delivery settlements are covered by their own tests, which must
keep passing unchanged.
"""

from collections.abc import Callable
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.pending import schemas as ps
from app.modules.settlements.pending import service as pending
from app.platform.auth.models import User
from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.days import days_ago, today, yesterday
from tests.modules.settlements.fuel_support import fuel, fuel_category, vehicle
from tests.modules.settlements.outlet_support import (
    BASE,
    OUT,
    addition,
    balances,
    category,
    close,
    company_part,
    count,
    create,
    employee_part,
    error,
    expense,
    legs,
    open_settlement,
    operations_of,
    preview,
    reopen,
    set_expected,
    transfer,
)
from tests.modules.settlements.test_cheques_api import bank, physical


@pytest.fixture
def centro(boca: Callable[[str], int]) -> int:
    return boca("CENTRO")


def _deactivate(r: Roles, outlet_id: int) -> None:
    response = r.admin.patch(f"/api/v1/outlets/{outlet_id}", json={"active": False})
    assert response.status_code == 200, response.text


# --- opening --------------------------------------------------------------------------------------


def test_operator_opens_a_boca_settlement_without_employee(r: Roles, centro: int) -> None:
    response = open_settlement(r.operator, centro, "150000")
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["type"] == "OUTLET"
    assert (body["outlet_id"], body["outlet_code"], body["outlet_name"]) == (centro, "CENTRO", "Boca CENTRO")
    assert "employee_id" not in body
    assert body["expected_amount"] == "150000.00"
    assert body["sequence_number"] == 1
    assert body["status"] == "OPEN"
    assert body["financial_state"] == "NOT_CLOSED"
    assert body["pending_collections"] == []


def test_viewer_cannot_open_a_boca_settlement(r: Roles, centro: int) -> None:
    assert open_settlement(r.viewer, centro, "100").status_code == 403


def test_opening_is_idempotent(r: Roles, centro: int, db: Session) -> None:
    body = {
        "client_request_id": str(uuid4()),
        "business_date": yesterday().isoformat(),
        "outlet_id": centro,
        "expected_amount": "1000",
    }
    first = r.operator.post(OUT, json=body)
    again = r.operator.post(OUT, json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert first.json()["id"] == again.json()["id"]
    other = r.operator.post(OUT, json={**body, "expected_amount": "2000"})
    assert error(other) == (409, "idempotency_conflict")
    assert db.execute(text("SELECT count(*) FROM settlements.settlements WHERE type = 'OUTLET'")).scalar_one() == 1


def test_inactive_boca_does_not_open(r: Roles, centro: int) -> None:
    _deactivate(r, centro)
    assert error(open_settlement(r.operator, centro, "100")) == (422, "inactive_outlet")


def test_unknown_boca_does_not_open(r: Roles) -> None:
    assert error(open_settlement(r.operator, 999999, "100")) == (422, "inactive_outlet")


def test_future_date_is_refused(r: Roles, centro: int) -> None:
    response = open_settlement(r.operator, centro, "100", business_date=days_ago(-1).isoformat())
    assert error(response) == (422, "future_business_date")


def test_second_settlement_of_the_day_needs_confirmation(r: Roles, centro: int, boca: Callable[[str], int]) -> None:
    create(r.operator, centro, "100")
    refused = open_settlement(r.operator, centro, "200")
    assert error(refused) == (409, "additional_settlement_confirmation_required")
    assert refused.json()["details"] == {"existing": 1, "next_sequence_number": 2}
    second = open_settlement(r.operator, centro, "200", confirm_additional=True)
    assert second.status_code == 201, second.text
    assert second.json()["sequence_number"] == 2
    assert second.json()["same_day_settlement_ids"] == [second.json()["id"] - 1]
    # Another boca, or another day, starts at 1 without confirmation.
    assert create(r.operator, boca("NORTE"), "50") > 0
    other_day = open_settlement(r.operator, centro, "10", business_date=days_ago(2).isoformat())
    assert other_day.status_code == 201
    assert other_day.json()["sequence_number"] == 1


# --- expected amount ------------------------------------------------------------------------------


@pytest.mark.parametrize(("expected", "shown"), [(None, None), ("0", "0.00"), ("1500.50", "1500.50")])
def test_expected_may_be_null_zero_or_positive(r: Roles, centro: int, expected: str | None, shown: str | None) -> None:
    body = r.operator.get(f"{OUT}/{create(r.operator, centro, expected)}").json()
    assert body["expected_amount"] == shown
    assert body["summary"]["expected_missing"] is (expected is None)


def test_negative_expected_is_refused(r: Roles, centro: int) -> None:
    assert open_settlement(r.operator, centro, "-1").status_code == 422


def test_expected_is_loaded_later_and_may_be_cleared_while_open(r: Roles, centro: int) -> None:
    sid = create(r.operator, centro)
    assert set_expected(r.operator, sid, "8000")["expected_amount"] == "8000.00"
    assert set_expected(r.operator, sid, None)["expected_amount"] is None
    assert set_expected(r.operator, sid, "0")["expected_amount"] == "0.00"


def test_expected_null_blocks_the_close(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro)
    count(r.operator, sid, ("ARS_1000", 5, ids["main_cash"]))
    shown = preview(r.operator, sid)
    assert [b["code"] for b in shown["blockers"]] == ["expected_amount_required"]
    assert shown["blockers"][0]["message"] == "Cargá el importe esperado de Store Sys para cerrar la rendición."
    refused = close(r.operator, sid, employee_part(1, "5000"))
    assert error(refused) == (422, "expected_amount_required")
    assert refused.json()["message"] == "Cargá el importe esperado de Store Sys para cerrar la rendición."


# --- types never cross ----------------------------------------------------------------------------


def test_delivery_paths_never_reach_a_boca_settlement_nor_the_reverse(r: Roles, centro: int, driver) -> None:  # type: ignore[no-untyped-def]
    sid = create(r.operator, centro, "100")
    assert error(r.operator.get(f"{BASE}/deliveries/{sid}")) == (404, "settlement_not_found")
    assert error(r.operator.put(f"{BASE}/deliveries/{sid}/count", json={"lines": []})) == (404, "settlement_not_found")
    pedro = driver("Pedro Gómez")
    delivery = r.operator.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": yesterday().isoformat(),
            "employee_id": pedro,
            "expected_amount": "100",
        },
    ).json()["id"]
    assert error(r.operator.get(f"{OUT}/{delivery}")) == (404, "settlement_not_found")
    assert error(r.operator.get(f"{OUT}/{delivery}/closing-preview")) == (404, "settlement_not_found")
    # The delivery list shows only deliveries; the boca list only bocas.
    assert [i["id"] for i in r.operator.get(f"{BASE}/deliveries").json()["items"]] == [delivery]
    assert [i["id"] for i in r.operator.get(OUT).json()["items"]] == [sid]


# --- what a boca does not have --------------------------------------------------------------------


def test_fuel_is_not_allowed_for_a_boca(r: Roles, centro: int, db: Session) -> None:
    sid = create(r.operator, centro, "100")
    truck = vehicle(db, "AB123CD")
    response = r.operator.post(
        f"{OUT}/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": fuel_category(db), "fuel": fuel(truck, "10", "1000")},
    )
    assert error(response) == (422, "fuel_not_allowed_for_outlet")


def test_pending_collections_are_not_enabled_for_a_boca(r: Roles, centro: int) -> None:
    sid = create(r.operator, centro, "100")
    # There is no boca path for them, and the delivery path does not reach a boca settlement.
    body = {"client_request_id": str(uuid4()), "customer_name": "Almacén", "amount": "100", "reason": "Fiado"}
    assert r.operator.post(f"{OUT}/{sid}/pending-collections", json=body).status_code in (404, 405)
    assert error(r.operator.post(f"{BASE}/deliveries/{sid}/pending-collections", json=body)) == (
        404,
        "settlement_not_found",
    )


def test_pending_service_refuses_a_boca_settlement(r: Roles, centro: int, db: Session) -> None:
    sid = create(r.operator, centro, "100")
    operator = db.scalars(select(User).where(User.email == "operator@districom.test")).one()
    with pytest.raises(AppError) as caught:
        pending.create(
            db,
            operator,
            sid,
            ps.PendingIn(client_request_id=uuid4(), customer_name="Almacén", amount=Decimal(100), reason="Fiado"),
        )
    assert (caught.value.status_code, caught.value.code) == (422, "pending_not_enabled_for_outlets")


# --- components and a normal close ----------------------------------------------------------------


def test_balanced_close_of_a_boca(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    """Cash to two boxes, an addition, a verified transfer to the bank, a cheque and an expense: the close
    takes the expected amount out of «Cobranzas de boca» and is exactly balanced."""
    sid = create(r.operator, centro, "200000")
    count(r.operator, sid, ("ARS_20000", 3, ids["main_cash"]), ("ARS_10000", 1, ids["change_cash"]))  # 70.000
    addition(r.operator, sid, "10000", ids["main_cash"])  # 80.000 cash
    transfer(r.operator, sid, "40000", "VERIFIED")
    transfer(r.operator, sid, "5000", "REJECTED")  # never justifies
    cheque = r.operator.post(f"{OUT}/{sid}/cheques", json=physical(bank(r.operator))).json()["cheques"][-1]["id"]
    expense(r.operator, sid, category(r.admin), "30000")
    shown = preview(r.operator, sid)
    assert shown["blockers"] == []
    assert shown["administrative_zero"] is False
    assert shown["difference"] == "0.00"
    assert shown["summary"]["justified"] == "200000.00"
    # Before the close, the cheque is not in the portfolio.
    assert r.operator.get(f"{BASE}/cheques/{cheque}").json()["in_effect"] is False
    closed = close(r.operator, sid)
    assert closed.status_code == 201, closed.text
    body = closed.json()
    assert (body["status"], body["financial_state"]) == ("CLOSED", "CLOSED")
    operation = body["close_in_effect"]["operation_id"]
    assert body["close_in_effect"]["kind"] == "FINANCIAL"
    assert legs(db, operation) == {
        "outlet_collections": "-200000.00",
        "main_cash": "70000.00",
        "change_cash": "10000.00",
        "bank": "40000.00",
        "expense": "30000.00",
        "cheque_portfolio": "50000.00",
    }
    after = balances(r.admin)
    assert after["outlet_collections"] == "-200000.00"
    assert after["delivery_collections"] == "0.00"
    assert r.operator.get(f"{BASE}/cheques/{cheque}").json()["in_effect"] is True


def test_shortage_and_surplus_are_allocated_explicitly(
    r: Roles, boca: Callable[[str], int], employee: Callable[[str], int], ids: dict[str, int], db: Session
) -> None:
    ana, luis = employee("Ana Cajera"), employee("Luis Encargado")
    short = create(r.operator, boca("SUR"), "100000")
    count(r.operator, short, ("ARS_20000", 4, ids["main_cash"]), ("ARS_1000", 19, ids["main_cash"]))  # 99.000
    assert preview(r.operator, short)["difference"] == "-1000.00"
    # Never split automatically: nothing allocated is refused.
    assert error(close(r.operator, short)) == (422, "difference_not_fully_allocated")
    closed = close(r.operator, short, employee_part(ana, "600"), employee_part(luis, "400")).json()
    assert closed["current_difference"]["kind"] == "SHORTAGE"
    assert legs(db, closed["close_in_effect"]["operation_id"]) == {
        "outlet_collections": "-100000.00",
        "main_cash": "99000.00",
        f"employee:{ana}": "600.00",
        f"employee:{luis}": "400.00",
    }
    over = create(r.operator, boca("ESTE"), "1000")
    count(r.operator, over, ("ARS_1000", 2, ids["main_cash"]))
    assert error(close(r.operator, over, company_part("1000"))) == (403, "company_allocation_requires_admin")
    closed = close(r.admin, over, company_part("1000")).json()
    assert closed["current_difference"]["kind"] == "SURPLUS"
    assert legs(db, closed["close_in_effect"]["operation_id"]) == {
        "outlet_collections": "-1000.00",
        "main_cash": "2000.00",
        "settlement_difference": "-1000.00",
    }


def test_responsables_of_the_day_are_suggestions_only(
    r: Roles, centro: int, employee: Callable[[str], int], ids: dict[str, int]
) -> None:
    ana, luis, otro = employee("Ana Cajera"), employee("Luis Encargado"), employee("Otro Empleado")
    for who, since in ((ana, days_ago(10)), (luis, yesterday()), (otro, today())):
        assigned = r.admin.post(
            f"{BASE}/outlets/{centro}/responsibilities", json={"employee_id": who, "valid_from": since.isoformat()}
        )
        assert assigned.status_code == 201, assigned.text
    sid = create(r.operator, centro, "1000")
    expected = [{"employee_id": ana, "full_name": "Ana Cajera"}, {"employee_id": luis, "full_name": "Luis Encargado"}]
    assert r.operator.get(f"{OUT}/{sid}").json()["responsible_suggestions"] == expected
    count(r.operator, sid, ("ARS_100", 9, ids["main_cash"]))  # 900: a $100 shortage
    shown = preview(r.operator, sid)
    assert shown["responsible_suggestions"] == expected
    assert shown["amount_to_allocate"] == "100.00"
    # Nothing is preloaded, and someone who is not a responsable of the day may still absorb it.
    closed = close(r.operator, sid, employee_part(otro, "100"))
    assert closed.status_code == 201, closed.text


def test_a_boca_without_responsables_still_renders(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    count(r.operator, sid, ("ARS_1000", 1, ids["main_cash"]))
    assert preview(r.operator, sid)["responsible_suggestions"] == []
    assert close(r.operator, sid).status_code == 201


def test_an_open_settlement_finishes_after_its_boca_is_deactivated(
    r: Roles, centro: int, ids: dict[str, int], employee: Callable[[str], int]
) -> None:
    ana = employee("Ana Cajera")
    sid = create(r.operator, centro, "5000")
    _deactivate(r, centro)
    assert r.operator.get(f"{OUT}/{sid}").json()["outlet_active"] is False
    count(r.operator, sid, ("ARS_1000", 4, ids["main_cash"]))
    set_expected(r.operator, sid, "4500")
    closed = close(r.operator, sid, employee_part(ana, "500"))
    assert closed.status_code == 201, closed.text
    assert reopen(r.admin, sid, 1).status_code == 200
    count(r.operator, sid, ("ARS_1000", 4, ids["main_cash"]), ("ARS_500", 1, ids["main_cash"]))
    again = close(r.operator, sid, version=2)
    assert again.status_code == 201, again.text
    # A new cycle of that boca does not start.
    assert error(open_settlement(r.operator, centro, "1", business_date=today().isoformat())) == (
        422,
        "inactive_outlet",
    )


def test_stale_preview_is_refused(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "2000")
    count(r.operator, sid, ("ARS_1000", 2, ids["main_cash"]))
    token = preview(r.operator, sid)["preview_token"]
    set_expected(r.operator, sid, "2000")  # same value: nothing changed, still valid
    assert preview(r.operator, sid)["preview_token"] == token
    count(r.operator, sid, ("ARS_1000", 2, ids["change_cash"]))  # same total, other box
    assert error(close(r.operator, sid, token=token)) == (409, "settlement_preview_stale")
    assert close(r.operator, sid).status_code == 201


def test_reopen_and_close_again_replaces_the_close(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    sid = create(r.operator, centro, "3000")
    count(r.operator, sid, ("ARS_1000", 3, ids["main_cash"]))
    first = close(r.operator, sid).json()["close_in_effect"]["operation_id"]
    reopened = reopen(r.admin, sid, 1).json()
    assert (reopened["status"], reopened["financial_state"], reopened["version"]) == ("OPEN", "BEING_CORRECTED", 2)
    assert reopened["close_in_effect"]["operation_id"] == first
    set_expected(r.operator, sid, "3500")
    addition(r.operator, sid, "500", ids["main_cash"])
    second = close(r.operator, sid, version=2).json()["close_in_effect"]["operation_id"]
    assert [t for _, t in operations_of(db, sid)] == ["SETTLEMENT_CLOSE", "REVERSAL", "SETTLEMENT_CLOSE"]
    assert legs(db, second) == {"outlet_collections": "-3500.00", "main_cash": "3500.00"}
    assert balances(r.admin)["outlet_collections"] == "-3500.00"
    history = r.operator.get(f"{OUT}/{sid}/history").json()
    assert [(c["settlement_version"], c["kind"], c["in_effect"]) for c in history["closings"]] == [
        (1, "FINANCIAL", False),
        (2, "FINANCIAL", True),
    ]
    assert history["reopenings"][0]["close_operation_id"] == first


def test_list_filters_and_open_count(r: Roles, centro: int, boca: Callable[[str], int], ids: dict[str, int]) -> None:
    norte = boca("NORTE")
    a = create(r.operator, centro, "100")
    create(r.operator, centro, None, confirm_additional=True)
    b = create(r.operator, norte, "0")
    count(r.operator, a, ("ARS_100", 1, ids["main_cash"]))
    close(r.operator, a)
    listed = r.viewer.get(OUT, params={"outlet_id": centro}).json()
    assert listed["total"] == 2
    missing = next(i for i in listed["items"] if i["expected_amount"] is None)
    assert missing["difference"] is None
    assert r.viewer.get(OUT, params={"financial_state": "CLOSED"}).json()["total"] == 1
    assert r.viewer.get(OUT, params={"financial_state": "NOT_CLOSED"}).json()["total"] == 2
    assert r.viewer.get(f"{BASE}/outlets/{centro}/settlements-summary").json() == {"outlet_id": centro, "open_count": 1}
    assert r.viewer.get(f"{BASE}/outlets/{norte}/settlements-summary").json()["open_count"] == 1
    assert b > 0
    assert error(r.viewer.get(f"{BASE}/outlets/999999/settlements-summary")) == (404, "outlet_not_found")


def test_outlet_collections_is_not_on_the_cajas_board(r: Roles, centro: int, ids: dict[str, int]) -> None:
    """Technical: listed among the accounts with its kind, never offered as a cash box or bank."""
    accounts = {a["code"]: a for a in r.admin.get(f"{BASE}/accounts").json() if a["code"]}
    assert accounts["outlet_collections"]["kind"] == "OUTLET_COLLECTIONS"
    response = r.admin.post(
        f"{BASE}/operations/adjustment",
        json={
            "client_request_id": str(uuid4()),
            "account_id": ids["outlet_collections"],
            "amount": "100",
            "description": "Probar la cuenta técnica",
        },
    )
    assert response.status_code == 422, response.text
    assert balances(r.admin)["outlet_collections"] == "0.00"


def test_a_rejected_boca_cheque_asks_who_collects_it(r: Roles, centro: int, employee: Callable[[str], int]) -> None:
    """A boca has no driver to default to: the rejection's new pending needs a responsible chosen by hand."""
    from tests.modules.settlements.test_cheque_cycle_api import deposited, pending, reject, rejected

    ana = employee("Ana Cajera")
    sid = create(r.operator, centro, "50000")
    cheque = r.operator.post(f"{OUT}/{sid}/cheques", json=physical(bank(r.operator))).json()["cheques"][-1]["id"]
    assert close(r.operator, sid).status_code == 201
    deposited(r.operator, [cheque], on=today())
    assert error(reject(r.operator, cheque, today())) == (422, "responsible_required")
    owed = pending(r.operator, rejected(r.operator, cheque, today(), responsible=ana)["pending_id"])
    assert (owed["responsible_employee_id"], owed["settlement_id"]) == (ana, None)
