"""Partial deliveries of a boca (R7-3, ADR 009): remittances, their reversal and the close that consolidates them,
through the API. The database rules are in `test_outlet_remittances_db_invariants.py`, the concurrent cases in
`test_outlet_remittances_concurrency.py`."""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.days import days_ago, today, yesterday
from tests.modules.settlements.outlet_support import (
    BASE,
    OUT,
    balances,
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
from tests.modules.settlements.remittance_support import (
    advances,
    at,
    legs,
    remit,
    remit_response,
    remittance_body,
    remittance_row,
    reverse,
    reverse_response,
)


@pytest.fixture
def centro(boca: Callable[[str], int]) -> int:
    return boca("CENTRO")


def _detail(r: Roles, sid: int) -> dict:  # type: ignore[type-arg]
    response = r.operator.get(f"{OUT}/{sid}")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def _close_operation(r: Roles, sid: int) -> int:
    return _detail(r, sid)["close_in_effect"]["operation_id"]  # type: ignore[no-any-return]


# --- recording ------------------------------------------------------------------------------------


def test_operator_hands_cash_over_to_a_cash_box(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    sid = create(r.operator, centro, "1000000")
    assert advances(db, centro) == "none"  # created on first use, never seeded
    rid = remit(r.operator, sid, "400000", ids["main_cash"], reference="Sobre 1")
    assert advances(db, centro) == "-400000.00"
    assert balances(r.admin)["main_cash"] == "400000.00"
    row = remittance_row(db, rid)
    assert (row["outlet_id"], row["business_date"]) == (centro, yesterday())
    assert legs(db, row["operation_id"]) == {f"advances:{centro}": "-400000.00", "main_cash": "400000.00"}
    operation = r.admin.get(f"{BASE}/operations/{row['operation_id']}").json()
    assert (operation["type"], operation["source_type"], operation["source_id"]) == (
        "OUTLET_REMITTANCE",
        "outlet_remittance",
        rid,
    )
    assert operation["business_date"] == yesterday().isoformat()
    account = next(e for e in operation["entries"] if e["account_kind"] == "OUTLET_ADVANCES")
    assert account["account_name"] == "Entregas a cuenta — Boca CENTRO"

    body = _detail(r, sid)
    [shown] = body["remittances"]
    assert shown["amount"] == "400000.00"
    assert shown["destination_account_name"] == "Caja principal"
    assert (shown["status"], shown["consolidated"], shown["reference"]) == ("IN_EFFECT", False, "Sobre 1")
    summary = body["summary"]
    assert summary["remitted"] == "400000.00"
    assert summary["justified"] == "400000.00"
    assert summary["remaining"] == "600000.00"
    assert summary["justified_above_expected"] is None
    # No difference while the settlement is open: only the close gives one.
    assert (summary["difference"], summary["difference_kind"]) == (None, None)


def test_a_retry_returns_the_same_remittance_and_other_content_conflicts(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000000")
    body = remittance_body("400000", ids["main_cash"])
    first = r.operator.post(f"{OUT}/{sid}/remittances", json=body)
    again = r.operator.post(f"{OUT}/{sid}/remittances", json=body)
    assert (first.status_code, again.status_code) == (201, 200)
    assert len(again.json()["remittances"]) == 1
    assert error(r.operator.post(f"{OUT}/{sid}/remittances", json=body | {"amount": "1"})) == (
        409,
        "idempotency_conflict",
    )
    assert advances(db, centro) == "-400000.00"


def test_destination_is_an_active_cash_box(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    sid = create(r.operator, centro, "1000")
    for account in ("bank", "outlet_collections", "expense", "adjustment"):
        assert error(remit_response(r.operator, sid, "100", ids[account])) == (422, "invalid_remittance_destination")
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE code = 'change_cash'"))
    db.commit()
    assert error(remit_response(r.operator, sid, "100", ids["change_cash"])) == (422, "invalid_remittance_destination")
    # Another active cash box is fine.
    assert remit_response(r.operator, sid, "100", ids["main_cash"]).status_code == 201


def test_delivered_at_is_on_the_business_date_and_not_in_the_future(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    other_day = at(13, on=days_ago(2))
    assert error(remit_response(r.operator, sid, "100", ids["main_cash"], delivered_at=other_day)) == (
        422,
        "delivered_at_not_on_business_date",
    )
    today_sid = create(r.operator, centro, "1000", business_date=today().isoformat())
    assert error(remit_response(r.operator, today_sid, "100", ids["main_cash"], delivered_at=future)) == (
        422,
        "future_delivered_at",
    )
    naive = datetime.combine(yesterday(), datetime.min.time()).isoformat()
    assert error(remit_response(r.operator, sid, "100", ids["main_cash"], delivered_at=naive)) == (
        422,
        "invalid_delivered_at",
    )
    # 23:30 in Buenos Aires is already the next day in UTC: it is still the business date.
    late = remit_response(r.operator, sid, "100", ids["main_cash"], delivered_at=at(23, 30))
    assert late.status_code == 201, late.text


def test_amount_must_be_positive(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    for amount in ("0", "-5", "10.001"):
        assert remit_response(r.operator, sid, amount, ids["main_cash"]).status_code == 422


def test_viewer_cannot_hand_cash_over(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    assert remit_response(r.viewer, sid, "100", ids["main_cash"]).status_code == 403


def test_a_delivery_settlement_takes_no_remittance(r: Roles, driver, ids: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    ana = driver("Ana Repartidora")
    created = r.operator.post(
        f"{BASE}/deliveries",
        json={
            "client_request_id": str(uuid4()),
            "business_date": yesterday().isoformat(),
            "employee_id": ana,
            "expected_amount": "1000",
        },
    )
    assert created.status_code == 201, created.text
    assert remit_response(r.operator, created.json()["id"], "100", ids["main_cash"]).status_code == 404


# --- above the expected amount --------------------------------------------------------------------


def test_going_above_the_expected_asks_for_confirmation_on_the_total(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000000")
    remit(r.operator, sid, "900000", ids["main_cash"])
    body = remittance_body("200000", ids["main_cash"])
    shown = r.operator.post(
        f"{OUT}/{sid}/remittances/preview", json={k: v for k, v in body.items() if k != "client_request_id"}
    )
    assert shown.status_code == 200, shown.text
    preview_body = shown.json()
    assert (preview_body["remitted_before"], preview_body["remitted_after"]) == ("900000.00", "1100000.00")
    assert preview_body["excess"] == "100000.00"
    assert preview_body["warning"] == "Con esta entrega lo entregado supera el importe esperado en $100000.00."
    assert [(leg["account_name"], leg["amount"]) for leg in preview_body["legs"]] == [
        ("Caja principal", "200000.00"),
        ("Entregas a cuenta — Boca CENTRO", "-200000.00"),
    ]
    refused = r.operator.post(f"{OUT}/{sid}/remittances", json=body)
    assert error(refused) == (409, "remittance_above_expected")
    assert refused.json()["details"] == {
        "excess": "100000.00",
        "remitted_total": "1100000.00",
        "expected": "1000000.00",
    }
    assert advances(db, centro) == "-900000.00"  # nothing was written
    confirmed = r.operator.post(f"{OUT}/{sid}/remittances", json=body | {"confirm_above_expected": True})
    assert confirmed.status_code == 201, confirmed.text
    summary = confirmed.json()["summary"]
    assert (summary["remaining"], summary["justified_above_expected"]) == ("0.00", "100000.00")


def test_without_expected_amount_a_remittance_is_allowed_as_is(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro)
    response = remit_response(r.operator, sid, "300000", ids["main_cash"])
    assert response.status_code == 201, response.text
    summary = response.json()["summary"]
    assert (summary["expected_missing"], summary["remaining"], summary["remitted"]) == (True, None, "300000.00")


# --- states and roles -----------------------------------------------------------------------------


def test_a_closed_settlement_takes_no_remittance_nor_reversal(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    assert close(r.operator, sid).status_code == 201
    assert error(remit_response(r.admin, sid, "1", ids["main_cash"])) == (409, "settlement_closed")
    assert error(reverse_response(r.admin, sid, rid)) == (409, "settlement_closed")


def test_being_corrected_only_an_admin_records_or_reverses(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    assert close(r.operator, sid).status_code == 201
    assert reopen(r.admin, sid, 1).status_code == 200
    assert error(remit_response(r.operator, sid, "100", ids["main_cash"])) == (403, "remittance_requires_admin")
    assert reverse_response(r.operator, sid, rid).status_code == 403
    assert remit_response(r.admin, sid, "100", ids["main_cash"], confirm_above_expected=True).status_code == 201


def test_only_an_admin_reverses_and_always_with_a_reason(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "500", ids["main_cash"])
    assert reverse_response(r.operator, sid, rid).status_code == 403
    assert reverse_response(r.admin, sid, rid, reason="").status_code == 422
    assert reverse_response(r.admin, sid, rid, reason="   ").status_code == 422


# --- reversal -------------------------------------------------------------------------------------


def test_reversal_undoes_both_legs_on_the_original_date(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000000")
    rid = remit(r.operator, sid, "350000", ids["main_cash"])
    body = reverse(r.admin, sid, rid, "Eran $300.000")
    assert advances(db, centro) == "0.00"
    assert balances(r.admin)["main_cash"] == "0.00"
    [shown] = body["remittances"]
    assert (shown["status"], shown["reversal_reason"], shown["reversed_by_name"]) == (
        "REVERSED",
        "Eran $300.000",
        "Usuario Test",
    )
    assert body["summary"]["remitted"] == "0.00"
    row = remittance_row(db, rid)
    assert legs(db, row["reversal_operation_id"]) == {f"advances:{centro}": "350000.00", "main_cash": "-350000.00"}
    reversal = r.admin.get(f"{BASE}/operations/{row['reversal_operation_id']}").json()
    assert (reversal["type"], reversal["business_date"]) == ("REVERSAL", yesterday().isoformat())
    # The amount never changed: a new remittance for the right amount.
    remit(r.operator, sid, "300000", ids["main_cash"])
    assert advances(db, centro) == "-300000.00"


def test_reversal_is_idempotent(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "500", ids["main_cash"])
    request = str(uuid4())
    first = reverse_response(r.admin, sid, rid, "Error de carga", client_request_id=request)
    again = reverse_response(r.admin, sid, rid, "Error de carga", client_request_id=request)
    assert (first.status_code, again.status_code) == (200, 200)
    assert error(reverse_response(r.admin, sid, rid, "Otro motivo", client_request_id=request)) == (
        409,
        "idempotency_conflict",
    )
    assert error(reverse_response(r.admin, sid, rid, "Error de carga")) == (409, "remittance_already_reversed")
    assert advances(db, centro) == "0.00"


def test_a_reversal_never_invents_cash_the_box_already_spent(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000000")
    rid = remit(r.operator, sid, "400000", ids["change_cash"])
    moved = r.operator.post(
        f"{BASE}/operations/cash-transfer",
        json={
            "client_request_id": str(uuid4()),
            "business_date": today().isoformat(),
            "from_account_id": ids["change_cash"],
            "to_account_id": ids["main_cash"],
            "amount": "300000",
        },
    )
    assert moved.status_code == 201, moved.text
    assert error(reverse_response(r.admin, sid, rid)) == (409, "insufficient_funds")
    assert remittance_row(db, rid)["reversal_operation_id"] is None
    assert advances(db, centro) == "-400000.00"


def test_cajas_never_reverses_a_remittance_nor_moves_its_account(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "500", ids["main_cash"])
    row = remittance_row(db, rid)
    refused = r.admin.post(
        f"{BASE}/operations/{row['operation_id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Desde Cajas"},
    )
    assert error(refused) == (409, "operation_owned_by_document")
    account = db.execute(text("SELECT id FROM settlements.accounts WHERE outlet_id = :o"), {"o": centro}).scalar_one()
    for path, payload in (
        ("adjustment", {"account_id": account, "amount": "100", "description": "Probar"}),
        ("cash-transfer", {"from_account_id": ids["main_cash"], "to_account_id": account, "amount": "100"}),
        ("opening-balance", {"account_id": account, "amount": "100"}),
    ):
        response = r.admin.post(f"{BASE}/operations/{path}", json={"client_request_id": str(uuid4()), **payload})
        assert response.status_code == 422, (path, response.text)
    assert advances(db, centro) == "-500.00"


# --- the close ------------------------------------------------------------------------------------


def test_example_1_remittances_consolidate_without_moving_the_cash_box_twice(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    """Expected 1.000.000; remittances 400.000 and 300.000; final count 200.000 and a transfer of 100.000."""
    sid = create(r.operator, centro, "1000000")
    first = remit(r.operator, sid, "400000", ids["main_cash"])
    second = remit(r.operator, sid, "300000", ids["change_cash"], delivered_at=at(17))
    count(r.operator, sid, ("ARS_20000", 10, ids["main_cash"]))  # 200.000 stayed in the boca
    transfer(r.operator, sid, "100000", "VERIFIED")
    shown = preview(r.operator, sid)
    assert (shown["difference"], shown["difference_kind"]) == ("0.00", "BALANCED")
    assert shown["summary"]["remitted"] == "700000.00"
    closed = close(r.operator, sid)
    assert closed.status_code == 201, closed.text
    operation = _close_operation(r, sid)
    assert legs(db, operation) == {
        "outlet_collections": "-1000000.00",
        f"advances:{centro}": "700000.00",
        "main_cash": "200000.00",
        "bank": "100000.00",
    }
    after = balances(r.admin)
    assert (after["main_cash"], after["change_cash"], after["bank"]) == ("600000.00", "300000.00", "100000.00")
    assert after["outlet_collections"] == "-1000000.00"
    assert advances(db, centro) == "0.00"
    frozen = db.execute(
        text(
            "SELECT remittance_id, amount FROM settlements.settlement_closing_remittances "
            "WHERE close_operation_id = :o ORDER BY remittance_id"
        ),
        {"o": operation},
    ).all()
    assert [(rid, f"{amount:.2f}") for rid, amount in frozen] == [(first, "400000.00"), (second, "300000.00")]
    body = closed.json()
    assert all(item["consolidated"] for item in body["remittances"])
    assert (body["summary"]["difference"], body["summary"]["difference_kind"]) == ("0.00", "BALANCED")


def test_example_2_shortage_split_between_two_employees(
    r: Roles, centro: int, ids: dict[str, int], employee: Callable[[str], int], db: Session
) -> None:
    """Expected 1.000.000; remittance 600.000; final cash 250.000, bank 100.000, expense 20.000: shortage 30.000,
    20.000 to employee A and 10.000 to employee B."""
    a, b = employee("Empleado A"), employee("Empleado B")
    sid = create(r.operator, centro, "1000000")
    remit(r.operator, sid, "600000", ids["main_cash"], delivered_by_employee_id=a)
    count(r.operator, sid, ("ARS_20000", 12, ids["main_cash"]), ("ARS_10000", 1, ids["main_cash"]))  # 250.000
    transfer(r.operator, sid, "100000", "VERIFIED")
    expense(r.operator, sid, category(r.admin), "20000")
    shown = preview(r.operator, sid)
    assert (shown["difference"], shown["difference_kind"]) == ("-30000.00", "SHORTAGE")
    assert error(close(r.operator, sid)) == (422, "difference_not_fully_allocated")
    closed = close(r.operator, sid, employee_part(a, "20000"), employee_part(b, "10000"))
    assert closed.status_code == 201, closed.text
    assert legs(db, _close_operation(r, sid)) == {
        "outlet_collections": "-1000000.00",
        f"advances:{centro}": "600000.00",
        "main_cash": "250000.00",
        "bank": "100000.00",
        "expense": "20000.00",
        f"employee:{a}": "20000.00",
        f"employee:{b}": "10000.00",
    }
    after = balances(r.admin)
    assert (after["main_cash"], after["bank"], after["expense"]) == ("850000.00", "100000.00", "20000.00")
    assert advances(db, centro) == "0.00"


def test_a_change_of_remittances_after_the_preview_is_stale(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "500", ids["main_cash"])
    count(r.operator, sid, ("ARS_500", 1, ids["main_cash"]))
    token = preview(r.operator, sid)["preview_token"]
    remit(r.operator, sid, "100", ids["main_cash"])
    assert error(close(r.operator, sid, token=token)) == (409, "settlement_preview_stale")
    token = preview(r.operator, sid)["preview_token"]
    reverse(r.admin, sid, rid)
    assert error(close(r.operator, sid, token=token)) == (409, "settlement_preview_stale")


def test_a_remittance_in_effect_is_never_an_administrative_zero_close(
    r: Roles, centro: int, ids: dict[str, int], employee: Callable[[str], int], db: Session
) -> None:
    sid = create(r.operator, centro, "0")
    rid = remit(r.operator, sid, "100", ids["main_cash"], confirm_above_expected=True)
    shown = preview(r.operator, sid)
    assert shown["administrative_zero"] is False
    assert (shown["difference"], shown["difference_kind"]) == ("100.00", "SURPLUS")
    # Reversed, it no longer counts: $0 without movements closes administratively.
    reverse(r.admin, sid, rid)
    shown = preview(r.operator, sid)
    assert shown["administrative_zero"] is True
    closed = close(r.operator, sid)
    assert closed.status_code == 201, closed.text
    assert closed.json()["close_in_effect"]["kind"] == "ADMINISTRATIVE_ZERO"
    snapshot = db.execute(
        text("SELECT snapshot FROM settlements.settlement_closings WHERE settlement_id = :s"), {"s": sid}
    ).scalar_one()
    assert snapshot["remittances"] == "0.00"
    assert advances(db, centro) == "0.00"


def test_two_settlements_of_the_same_boca_never_mix(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    """Rendición 1 hands over 700.000 and closes; Rendición 2 hands over 100.000 and stays open: the boca's account
    holds -100.000, never 0."""
    one = create(r.operator, centro, "700000")
    two = create(r.operator, centro, "500000", confirm_additional=True)
    remit(r.operator, one, "400000", ids["main_cash"])
    remit(r.operator, two, "100000", ids["main_cash"])
    remit(r.operator, one, "300000", ids["main_cash"])
    assert advances(db, centro) == "-800000.00"
    assert close(r.operator, one).status_code == 201
    assert advances(db, centro) == "-100000.00"
    assert legs(db, _close_operation(r, one))[f"advances:{centro}"] == "700000.00"
    assert _detail(r, two)["summary"]["remitted"] == "100000.00"


def test_correction_reverse_the_wrong_remittance_and_record_the_right_one(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    """Recorded $350.000 but it was $300.000: reopen, the admin reverses it, records $300.000 and closes again."""
    sid = create(r.operator, centro, "1000000")
    wrong = remit(r.operator, sid, "350000", ids["main_cash"])
    count(r.operator, sid, ("ARS_20000", 32, ids["main_cash"]), ("ARS_10000", 1, ids["main_cash"]))  # 650.000
    assert close(r.operator, sid).status_code == 201
    first_close = _close_operation(r, sid)
    assert advances(db, centro) == "0.00"

    assert reopen(r.admin, sid, 1, "La entrega fue de $300.000").status_code == 200
    reverse(r.admin, sid, wrong, "Eran $300.000")
    # The old close is still in effect: the account is positive for a while, explained by it.
    assert advances(db, centro) == "350000.00"
    body = _detail(r, sid)
    [old] = body["remittances"]
    assert (old["status"], old["consolidated"]) == ("REVERSED", True)
    right = remit(r.admin, sid, "300000", ids["main_cash"])
    assert advances(db, centro) == "50000.00"
    count(r.operator, sid, ("ARS_20000", 35, ids["main_cash"]))  # 700.000 stayed
    closed = close(r.operator, sid, version=2)
    assert closed.status_code == 201, closed.text
    second_close = _close_operation(r, sid)
    assert second_close != first_close
    assert legs(db, second_close)[f"advances:{centro}"] == "300000.00"
    assert advances(db, centro) == "0.00"
    states = {item["id"]: (item["status"], item["consolidated"]) for item in closed.json()["remittances"]}
    assert states == {wrong: ("REVERSED", False), right: ("IN_EFFECT", True)}
    assert balances(r.admin)["main_cash"] == "1000000.00"


def test_reopening_never_touches_the_remittances(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    sid = create(r.operator, centro, "1000")
    remit(r.operator, sid, "1000", ids["main_cash"])
    assert close(r.operator, sid).status_code == 201
    before = db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one()
    assert reopen(r.admin, sid, 1).status_code == 200
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one() == before
    [item] = _detail(r, sid)["remittances"]
    assert (item["status"], item["consolidated"]) == ("IN_EFFECT", True)
    assert advances(db, centro) == "0.00"
    # Closed again as it was: the new close consolidates the same remittance.
    assert close(r.operator, sid, version=2).status_code == 201
    assert advances(db, centro) == "0.00"


def test_financial_close_corrected_to_zero_after_reversing_its_remittances(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "1000", ids["main_cash"])
    assert close(r.operator, sid).status_code == 201
    assert reopen(r.admin, sid, 1, "No hubo ventas").status_code == 200
    reverse(r.admin, sid, rid, "No hubo ventas")
    set_expected(r.operator, sid, "0")
    closed = close(r.operator, sid, version=2)
    assert closed.status_code == 201, closed.text
    assert closed.json()["close_in_effect"]["kind"] == "ADMINISTRATIVE_ZERO"
    assert advances(db, centro) == "0.00"
    assert balances(r.admin)["main_cash"] == "0.00"


# --- who carried it -------------------------------------------------------------------------------


def test_who_delivered_is_a_fact_of_that_day(
    r: Roles, centro: int, ids: dict[str, int], employee: Callable[[str], int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000000")
    assert error(remit_response(r.operator, sid, "1", ids["main_cash"], delivered_by_employee_id=999999)) == (
        422,
        "employee_not_found",
    )
    hired_later = employee("Recién ingresado")
    db.execute(text("UPDATE employees SET hire_date = :d WHERE id = :e"), {"d": today(), "e": hired_later})
    db.commit()
    assert error(remit_response(r.operator, sid, "1", ids["main_cash"], delivered_by_employee_id=hired_later)) == (
        422,
        "employee_not_hired_yet",
    )
    gone, responsable = employee("Dado de baja"), employee("Ex encargada")
    granted = r.admin.post(
        f"/api/v1/settlements/outlets/{centro}/responsibilities",
        json={"employee_id": responsable, "valid_from": days_ago(10).isoformat()},
    )
    assert granted.status_code == 201, granted.text
    db.execute(text("UPDATE employees SET active = false WHERE id IN (:a, :b)"), {"a": gone, "b": responsable})
    db.commit()
    # Deactivated since: refused unless they were a responsable of the boca that day...
    assert error(remit_response(r.operator, sid, "1", ids["main_cash"], delivered_by_employee_id=gone)) == (
        422,
        "employee_inactive",
    )
    assert (
        remit_response(r.operator, sid, "1", ids["main_cash"], delivered_by_employee_id=responsable).status_code == 201
    )


def test_an_admin_documents_a_past_remittance_of_a_deactivated_employee(
    r: Roles, centro: int, ids: dict[str, int], employee: Callable[[str], int], db: Session
) -> None:
    gone = employee("Dado de baja")
    sid = create(r.operator, centro, "1000")
    count(r.operator, sid, ("ARS_1000", 1, ids["main_cash"]))
    assert close(r.operator, sid).status_code == 201
    assert reopen(r.admin, sid, 1, "Faltó una entrega").status_code == 200
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": gone})
    db.commit()
    response = remit_response(
        r.admin, sid, "1000", ids["main_cash"], delivered_by_employee_id=gone, confirm_above_expected=True
    )
    assert response.status_code == 201, response.text
    assert response.json()["remittances"][-1]["delivered_by_name"] == "Dado de baja"


def test_a_boca_deactivated_later_keeps_its_remittances(
    r: Roles, centro: int, ids: dict[str, int], db: Session
) -> None:
    sid = create(r.operator, centro, "1000")
    rid = remit(r.operator, sid, "300", ids["main_cash"])
    assert r.admin.patch(f"/api/v1/outlets/{centro}", json={"active": False}).status_code == 200
    remit(r.operator, sid, "200", ids["main_cash"])
    reverse(r.admin, sid, rid)
    assert advances(db, centro) == "-200.00"


def test_created_at_may_be_later_than_delivered_at(r: Roles, centro: int, ids: dict[str, int], db: Session) -> None:
    sid = create(r.operator, centro, "1000", business_date=days_ago(3).isoformat())
    rid = remit(r.operator, sid, "500", ids["main_cash"], delivered_at=at(13, on=days_ago(3)))
    row = remittance_row(db, rid)
    assert row["delivered_at"].astimezone(UTC) < row["created_at"].astimezone(UTC)
    assert row["business_date"] == days_ago(3)


def test_the_boca_account_is_listed_as_technical(r: Roles, centro: int, ids: dict[str, int]) -> None:
    sid = create(r.operator, centro, "1000")
    remit(r.operator, sid, "500", ids["main_cash"])
    accounts = {a["name"]: a for a in r.admin.get(f"{BASE}/accounts").json()}
    account = accounts["Entregas a cuenta — Boca CENTRO"]
    assert (account["kind"], account["code"], account["balance"]) == ("OUTLET_ADVANCES", None, "-500.00")
