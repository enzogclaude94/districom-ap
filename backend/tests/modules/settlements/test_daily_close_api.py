"""«Cierre del día» de Cajas y Bancos (R9-2): a calculated view of one business date over the ledger.

The numbers in brackets are the tests Enzo asked for R9-2; the printable view [18] and the phone layout [19] are
covered by the frontend tests and `e2e/daily-close.spec.ts`. Every test also checks the reconciliation of each
account (opening + net = closing, the closing summed on its own) through `_view`.
"""

from collections.abc import Callable
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.settlements.daily_close import service
from app.modules.settlements.ledger.service import AccountBalance
from tests.modules.settlements import outlet_support as boca_
from tests.modules.settlements import store_sys_support as sys_
from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.days import days_ago, today, yesterday
from tests.modules.settlements.direct_support import fund, spent
from tests.modules.settlements.employee_movement_support import area_admin, outsider, registered
from tests.modules.settlements.employee_movement_support import reverse as reverse_movement
from tests.modules.settlements.payment_support import paid, third_party
from tests.modules.settlements.payment_support import transfer as paid_by_transfer
from tests.modules.settlements.remittance_support import remit
from tests.modules.settlements.store_sys_support import as_user
from tests.modules.settlements.test_cheque_cycle_api import credited, deposited, entered, rejected

BASE = "/api/v1/settlements"
DAILY = f"{BASE}/daily-close"
D = Decimal

__all__ = ["as_user"]  # fixture


# --- helpers --------------------------------------------------------------------------------------


def _view(client: TestClient, on: date) -> dict[str, Any]:
    response = client.get(DAILY, params={"on": on.isoformat()})
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    assert body["business_date"] == on.isoformat()
    for account in body["accounts"]:
        assert D(account["opening_balance"]) + D(account["net"]) == D(account["closing_balance"]), account
        assert D(account["inflows"]) - D(account["outflows"]) == D(account["net"])
        assert account["reconciled"] is True
    assert body["reconciled"] is True
    return body


def _account(body: dict[str, Any], name: str) -> dict[str, Any]:
    return next(a for a in body["accounts"] if a["name"] == name)


def _flows(account: dict[str, Any], side: str) -> dict[str, tuple[int, str]]:
    return {c["key"]: (c["operations"], c["amount"]) for c in account[f"{side}_categories"]}


def _ids(client: TestClient) -> dict[str, int]:
    return {a["code"]: a["id"] for a in client.get(f"{BASE}/accounts").json() if a["code"]}


def _post(client: TestClient, path: str, **body: Any) -> dict[str, Any]:
    for key, value in list(body.items()):
        if isinstance(value, date):
            body[key] = value.isoformat()
    response = client.post(f"{BASE}/operations/{path}", json={"client_request_id": str(uuid4()), **body})
    assert response.status_code in (200, 201), response.text
    return response.json()  # type: ignore[no-any-return]


def _delivery(client: TestClient, driver_id: int, expected: str | None, bills: int, box: int, **extra: Any) -> int:
    sid = sys_.open_delivery(client, driver_id, expected, **extra)
    sys_.count_cash(client, sid, box, bills)
    return sid


def _closed_delivery(client: TestClient, driver_id: int, expected: str, bills: int, box: int, **extra: Any) -> int:
    sid = _delivery(client, driver_id, expected, bills, box, **extra)
    response = sys_.close(client, sid)
    assert response.status_code == 201, response.text
    return sid


@pytest.fixture
def juan(driver: Callable[..., int]) -> int:
    return driver("Juan Pérez")


@pytest.fixture
def category(r: Roles) -> int:
    response = r.admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"})
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


# --- access ---------------------------------------------------------------------------------------


def test_any_role_in_rendiciones_reads_it_and_nobody_else(r: Roles, user_as: Callable[..., TestClient]) -> None:
    assert r.viewer.get(DAILY).status_code == 200
    assert user_as("ajeno").get(DAILY).status_code == 403


def test_without_a_date_it_is_today(r: Roles) -> None:
    assert r.viewer.get(DAILY).json()["business_date"] == today().isoformat()


# --- [1] a day without movements --------------------------------------------------------------------


def test_1_a_day_without_movements_lists_the_active_accounts_at_zero(r: Roles) -> None:
    body = _view(r.viewer, yesterday())
    assert [(a["name"], a["kind"], a["active"]) for a in body["accounts"]] == [
        ("Caja de cambio", "CASH_BOX", True),
        ("Caja principal", "CASH_BOX", True),
        ("Banco", "BANK", True),
    ]
    for account in body["accounts"]:
        assert (account["opening_balance"], account["inflows"], account["outflows"], account["closing_balance"]) == (
            "0.00",
            "0.00",
            "0.00",
            "0.00",
        )
        assert account["inflow_categories"] == account["outflow_categories"] == []
    assert body["settlements"]["total"] == 0
    assert body["cheques"]["portfolio_count"] == 0
    assert [t["kind"] for t in body["totals"]] == ["CASH_BOX", "BANK"]


# --- [2] opening balance -------------------------------------------------------------------------


def test_2_the_opening_balance_is_the_ledger_up_to_the_day_before(r: Roles) -> None:
    fund(r.admin, "main_cash", "150000", on=days_ago(3))
    fund(r.admin, "bank", "40000", on=yesterday())
    body = _view(r.viewer, yesterday())
    main = _account(body, "Caja principal")
    assert (main["opening_balance"], main["net"], main["closing_balance"]) == ("150000.00", "0.00", "150000.00")
    bank = _account(body, "Banco")
    assert (bank["opening_balance"], bank["closing_balance"]) == ("0.00", "40000.00")
    assert _flows(bank, "inflow") == {"OPENING_BALANCE": (1, "40000.00")}
    # The opening of a day is the closing of the day before.
    before = _view(r.viewer, days_ago(2))
    assert _account(before, "Caja principal")["closing_balance"] == main["opening_balance"]


# --- [3] income and [4] expense --------------------------------------------------------------------


def test_3_an_income_is_grouped_by_its_operation(r: Roles, owner_account: Callable[[str], int]) -> None:
    ids = _ids(r.admin)
    partner = owner_account("Aportes Socio A")
    _post(
        r.admin,
        "owner-contribution",
        owner_account_id=partner,
        account_id=ids["bank"],
        amount="50000",
        business_date=yesterday(),
    )
    _post(
        r.admin,
        "owner-contribution",
        owner_account_id=partner,
        account_id=ids["bank"],
        amount="25000",
        business_date=yesterday(),
    )
    bank = _account(_view(r.viewer, yesterday()), "Banco")
    assert _flows(bank, "inflow") == {"OWNER_CONTRIBUTION": (2, "75000.00")}
    assert bank["inflow_categories"][0]["label"] == "Aportes de socios"
    assert (bank["inflows"], bank["outflows"], bank["closing_balance"]) == ("75000.00", "0.00", "75000.00")


def test_4_an_expense_leaves_the_box_as_an_outflow(r: Roles, category: int) -> None:
    fund(r.admin, "main_cash", "30000", on=days_ago(2))
    ids = _ids(r.admin)
    spent(r.operator, ids["main_cash"], category, "12000", business_date=yesterday())
    main = _account(_view(r.viewer, yesterday()), "Caja principal")
    assert _flows(main, "outflow") == {"DIRECT_EXPENSE": (1, "12000.00")}
    assert main["outflow_categories"][0]["label"] == "Gastos directos"
    assert (main["opening_balance"], main["outflows"], main["closing_balance"]) == ("30000.00", "12000.00", "18000.00")


# --- [5] transfers between accounts ----------------------------------------------------------------


def test_5_a_transfer_between_boxes_and_a_deposit_show_on_both_sides(r: Roles) -> None:
    fund(r.admin, "main_cash", "100000", on=days_ago(2))
    ids = _ids(r.admin)
    on = yesterday()
    _post(
        r.operator,
        "cash-transfer",
        from_account_id=ids["main_cash"],
        to_account_id=ids["change_cash"],
        amount="20000",
        business_date=on,
    )
    _post(
        r.operator,
        "bank-deposit",
        cash_account_id=ids["main_cash"],
        bank_account_id=ids["bank"],
        amount="50000",
        business_date=on,
    )
    body = _view(r.viewer, on)
    main, change, bank = (_account(body, n) for n in ("Caja principal", "Caja de cambio", "Banco"))
    assert _flows(main, "outflow") == {"BANK_DEPOSIT": (1, "50000.00"), "CASH_TRANSFER": (1, "20000.00")}
    assert _flows(change, "inflow") == {"CASH_TRANSFER": (1, "20000.00")}
    assert _flows(bank, "inflow") == {"BANK_DEPOSIT": (1, "50000.00")}
    cash_total, bank_total = body["totals"]
    # Between boxes the money only moves: the boxes together lose only what went to the bank.
    assert (cash_total["inflows"], cash_total["outflows"], cash_total["net"]) == ("20000.00", "70000.00", "-50000.00")
    assert (bank_total["net"], bank_total["closing_balance"]) == ("50000.00", "50000.00")


# --- [6] reversals ------------------------------------------------------------------------------------


def test_6_a_reversal_is_its_own_category_and_never_nets_the_original_away(r: Roles) -> None:
    ids = _ids(r.admin)
    on = yesterday()
    adjustment = _post(
        r.admin, "adjustment", account_id=ids["main_cash"], amount="8000", description="Sobrante", business_date=on
    )
    response = r.admin.post(
        f"{BASE}/operations/{adjustment['id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Se cargó dos veces", "business_date": on.isoformat()},
    )
    assert response.status_code == 201, response.text
    main = _account(_view(r.viewer, on), "Caja principal")
    assert _flows(main, "inflow") == {"ADJUSTMENT": (1, "8000.00")}
    assert _flows(main, "outflow") == {"REVERSAL:ADJUSTMENT": (1, "8000.00")}
    reversal = main["outflow_categories"][0]
    assert (reversal["label"], reversal["reverses"]) == ("Reverso de ajustes", "ADJUSTMENT")
    assert (main["inflows"], main["outflows"], main["net"], main["closing_balance"]) == (
        "8000.00",
        "8000.00",
        "0.00",
        "0.00",
    )


# --- [7] delivery and [8] boca ----------------------------------------------------------------------


def test_7_a_delivery_close_enters_the_box_and_shows_in_the_settlements(r: Roles, juan: int) -> None:
    ids = _ids(r.admin)
    sid = _closed_delivery(r.operator, juan, "100000", 10, ids["main_cash"])
    body = _view(r.viewer, yesterday())
    main = _account(body, "Caja principal")
    assert _flows(main, "inflow") == {"SETTLEMENT_CLOSE_DELIVERY": (1, "100000.00")}
    assert main["inflow_categories"][0]["label"] == "Cierres de rendición de reparto"
    settlements = body["settlements"]
    assert (settlements["total"], settlements["closed"], settlements["open"]) == (1, 1, 0)
    item = settlements["items"][0]
    assert (item["id"], item["type"], item["name"], item["financial_state"], item["close_kind"]) == (
        sid,
        "DELIVERY",
        "Juan Pérez",
        "CLOSED",
        "FINANCIAL",
    )
    assert (item["expected_amount"], item["justified"], item["difference"]) == ("100000.00", "100000.00", "0.00")
    assert item["expected_origin"] == {
        "source": "MANUAL",
        "legacy": False,
        "external_reference": None,
        "revision": None,
    }


def test_7_a_shortage_shows_the_difference_of_the_close(r: Roles, juan: int) -> None:
    ids = _ids(r.admin)
    sid = _delivery(r.operator, juan, "100000", 9, ids["main_cash"])
    response = sys_.close(r.admin, sid, {"target": "COMPANY", "amount": "10000", "reason": "Faltante absorbido"})
    assert response.status_code == 201, response.text
    item = _view(r.viewer, yesterday())["settlements"]["items"][0]
    assert (item["expected_amount"], item["justified"], item["difference"]) == ("100000.00", "90000.00", "-10000.00")


def test_8_a_boca_close_and_its_remittances(r: Roles, boca: Callable[[str], int]) -> None:
    ids = _ids(r.admin)
    centro = boca("CENTRO")
    sid = boca_.create(r.operator, centro, "80000")
    remit(r.operator, sid, "30000", ids["main_cash"])
    boca_.count(r.operator, sid, ("ARS_10000", 5, ids["main_cash"]))
    assert boca_.close(r.operator, sid).status_code == 201
    body = _view(r.viewer, yesterday())
    main = _account(body, "Caja principal")
    assert _flows(main, "inflow") == {
        "SETTLEMENT_CLOSE_OUTLET": (1, "50000.00"),
        "OUTLET_REMITTANCE": (1, "30000.00"),
    }
    labels = {c["key"]: c["label"] for c in main["inflow_categories"]}
    assert labels["SETTLEMENT_CLOSE_OUTLET"] == "Cierres de rendición de boca"
    item = body["settlements"]["items"][0]
    assert (item["type"], item["name"], item["expected_amount"], item["justified"], item["difference"]) == (
        "OUTLET",
        "Boca CENTRO",
        "80000.00",
        "80000.00",
        "0.00",
    )


# --- [9] pending collections ------------------------------------------------------------------------------


def test_9_a_pending_collected_enters_as_its_own_category(r: Roles, juan: int) -> None:
    ids = _ids(r.admin)
    sid = _delivery(r.operator, juan, "100000", 8, ids["main_cash"])
    pending = r.operator.post(
        f"{BASE}/deliveries/{sid}/pending-collections",
        json={
            "client_request_id": str(uuid4()),
            "customer_name": "Almacén Don José",
            "amount": "20000",
            "reason": "Paga mañana",
        },
    )
    assert pending.status_code == 201, pending.text
    pid = pending.json()["pending_collections"][-1]["id"]
    assert sys_.close(r.operator, sid).status_code == 201
    receipt = r.operator.post(
        f"{BASE}/pending-collections/{pid}/receipts",
        json={
            "client_request_id": str(uuid4()),
            "amount": "20000",
            "method": "CASH",
            "destination_account_id": ids["change_cash"],
            "business_date": today().isoformat(),
        },
    )
    assert receipt.status_code == 201, receipt.text
    # The close (yesterday) justified the pending without cash; the collection is today's.
    assert _flows(_account(_view(r.viewer, yesterday()), "Caja principal"), "inflow") == {
        "SETTLEMENT_CLOSE_DELIVERY": (1, "80000.00")
    }
    change = _account(_view(r.viewer, today()), "Caja de cambio")
    assert _flows(change, "inflow") == {"PENDING_COLLECTION": (1, "20000.00")}
    assert change["inflow_categories"][0]["label"] == "Cobro de pendientes"


# --- [10] payment to a third party -------------------------------------------------------------------


def test_10_a_payment_to_a_third_party_leaves_the_bank(r: Roles) -> None:
    fund(r.admin, "bank", "200000", on=days_ago(2))
    ids = _ids(r.admin)
    paid(r.operator, third_party(r.admin), [paid_by_transfer(ids["bank"], "70000")], on=yesterday())
    bank = _account(_view(r.viewer, yesterday()), "Banco")
    assert _flows(bank, "outflow") == {"PAYMENT": (1, "70000.00")}
    assert bank["outflow_categories"][0]["label"] == "Pagos a terceros"
    assert bank["closing_balance"] == "130000.00"


# --- [11] employee movements without the area ---------------------------------------------------------


def test_11_employee_movements_are_reserved_without_the_area_and_still_count(
    r: Roles, user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    fund(r.admin, "main_cash", "300000", on=days_ago(2))
    ids = _ids(r.admin)
    pedro = employee("Pedro Gómez")
    admin = area_admin(user_as)
    on = yesterday()
    registered(
        admin, "ADVANCE", pedro, "40000", funding_account_id=ids["main_cash"], on=on, description="Adelanto quincena"
    )
    reversed_ = registered(admin, "ADVANCE", pedro, "5000", funding_account_id=ids["main_cash"], on=on)
    assert reverse_movement(admin, reversed_["id"]).status_code == 200

    # With the area: what it is, the reversal next to it.
    seen = _account(_view(area_admin(user_as), on), "Caja principal")
    assert _flows(seen, "outflow") == {"EMPLOYEE_ADVANCE": (2, "45000.00")}
    assert _flows(seen, "inflow") == {"REVERSAL:EMPLOYEE_ADVANCE": (1, "5000.00")}

    # Without it: one reserved group on each side, no type, no employee, the same totals.
    client = outsider(user_as)
    response = client.get(DAILY, params={"on": on.isoformat()})
    for hidden in ("Pedro", "Adelanto", "EMPLOYEE_ADVANCE"):
        assert hidden not in response.text
    body = _view(client, on)
    assert body["employee_movements_reserved"] is True
    main = _account(body, "Caja principal")
    assert _flows(main, "outflow") == {"EMPLOYEE_RESERVED": (2, "45000.00")}
    assert _flows(main, "inflow") == {"EMPLOYEE_RESERVED": (1, "5000.00")}
    assert main["outflow_categories"][0]["label"] == "Movimiento de empleado (reservado)"
    assert (
        (main["net"], main["closing_balance"]) == (seen["net"], seen["closing_balance"]) == ("-40000.00", "260000.00")
    )
    assert all(a["kind"] in ("CASH_BOX", "BANK") for a in body["accounts"])


# --- [12] [13] Store Sys --------------------------------------------------------------------------------


def test_12_a_store_sys_expected_shows_its_origin_and_never_a_ledger_category(
    r: Roles,
    juan: int,
    as_user: Callable[..., TestClient],
    db: Session,
) -> None:
    ids = _ids(r.admin)
    sys_.with_code(db, juan, "R-01")
    sid = sys_.open_delivery(r.operator, juan)
    assert sys_.outcome(sys_.integration(as_user), sys_.record("LIQ-77", "100000")) == ("APPLIED", None)
    sys_.count_cash(r.operator, sid, ids["main_cash"], 10)
    assert sys_.close(r.operator, sid).status_code == 201
    body = _view(r.viewer, yesterday())
    item = body["settlements"]["items"][0]
    assert item["expected_origin"] == {
        "source": "STORE_SYS",
        "legacy": False,
        "external_reference": "LIQ-77",
        "revision": 1,
    }
    assert item["expected_amount"] == "100000.00"
    keys = {c["key"] for a in body["accounts"] for c in a["inflow_categories"] + a["outflow_categories"]}
    assert keys == {"SETTLEMENT_CLOSE_DELIVERY"}


def test_13_a_store_sys_update_waiting_and_a_settlement_without_expected(
    r: Roles,
    juan: int,
    driver: Callable[..., int],
    as_user: Callable[..., TestClient],
    db: Session,
) -> None:
    sys_.with_code(db, juan, "R-01")
    waiting = sys_.open_delivery(r.operator, juan, "80000")
    assert sys_.outcome(sys_.integration(as_user), sys_.record("LIQ-9", "100000")) == ("PENDING_CONFIRMATION", None)
    missing = sys_.open_delivery(r.operator, driver("Ana Ruiz"))
    settlements = _view(r.viewer, yesterday())["settlements"]
    assert (
        settlements["total"],
        settlements["open"],
        settlements["store_sys_pending"],
        settlements["without_expected"],
    ) == (
        2,
        2,
        1,
        1,
    )
    items = {i["id"]: i for i in settlements["items"]}
    assert items[waiting]["expected_update_pending"] is True
    assert (items[waiting]["expected_amount"], items[waiting]["expected_origin"]["source"]) == ("80000.00", "MANUAL")
    assert (items[waiting]["justified"], items[waiting]["difference"]) == (None, None)
    assert items[missing]["expected_missing"] is True
    assert (items[missing]["expected_amount"], items[missing]["expected_origin"]) == (None, None)


# --- [14] a settlement being corrected -------------------------------------------------------------------


def test_14_a_settlement_in_correction_shows_the_close_in_effect_never_its_draft(r: Roles, juan: int) -> None:
    ids = _ids(r.admin)
    sid = _closed_delivery(r.operator, juan, "100000", 10, ids["main_cash"])
    assert sys_.reopen(r.admin, sid).status_code == 200
    sys_.count_cash(r.operator, sid, ids["main_cash"], 7)  # the draft: a shortage of 30.000
    body = _view(r.viewer, yesterday())
    item = body["settlements"]["items"][0]
    assert (item["financial_state"], item["being_corrected"]) == ("BEING_CORRECTED", True)
    assert (item["expected_amount"], item["justified"], item["difference"]) == ("100000.00", "100000.00", "0.00")
    assert (body["settlements"]["being_corrected"], body["settlements"]["closed"]) == (1, 0)
    # Cajas still holds the close in effect.
    assert _flows(_account(body, "Caja principal"), "inflow") == {"SETTLEMENT_CLOSE_DELIVERY": (1, "100000.00")}


def test_14_a_corrected_close_shows_the_replaced_one_its_reversal_and_the_new_one(r: Roles, juan: int) -> None:
    ids = _ids(r.admin)
    sid = _closed_delivery(r.operator, juan, "100000", 10, ids["main_cash"])
    assert sys_.reopen(r.admin, sid).status_code == 200
    sys_.count_cash(r.operator, sid, ids["change_cash"], 10)
    assert sys_.close(r.operator, sid, version=2).status_code == 201
    body = _view(r.viewer, yesterday())
    main, change = _account(body, "Caja principal"), _account(body, "Caja de cambio")
    assert _flows(main, "inflow") == {"SETTLEMENT_CLOSE_DELIVERY": (1, "100000.00")}
    assert _flows(main, "outflow") == {"REVERSAL:SETTLEMENT_CLOSE_DELIVERY": (1, "100000.00")}
    assert main["outflow_categories"][0]["label"] == "Reverso de cierres de rendición de reparto"
    assert _flows(change, "inflow") == {"SETTLEMENT_CLOSE_DELIVERY": (1, "100000.00")}
    assert body["settlements"]["items"][0]["financial_state"] == "CLOSED"


# --- [15] an account inactive today --------------------------------------------------------------------


def test_15_an_account_inactive_today_shows_on_the_days_it_moved(r: Roles, db: Session) -> None:
    ids = _ids(r.admin)
    fund(r.admin, "change_cash", "5000", on=days_ago(3))
    _post(
        r.operator,
        "cash-transfer",
        from_account_id=ids["change_cash"],
        to_account_id=ids["main_cash"],
        amount="5000",
        business_date=days_ago(2),
    )
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE code = 'change_cash'"))
    db.commit()
    moved = _account(_view(r.viewer, days_ago(2)), "Caja de cambio")
    assert (moved["active"], moved["opening_balance"], moved["outflows"], moved["closing_balance"]) == (
        False,
        "5000.00",
        "5000.00",
        "0.00",
    )
    assert _account(_view(r.viewer, days_ago(3)), "Caja de cambio")["closing_balance"] == "5000.00"
    # A day it neither held nor moved anything: not listed.
    assert "Caja de cambio" not in {a["name"] for a in _view(r.viewer, yesterday())["accounts"]}


# --- [16] business date, never created_at ----------------------------------------------------------------


def test_16_the_business_date_decides_the_day_never_when_it_was_recorded(r: Roles, juan: int) -> None:
    ids = _ids(r.admin)
    # Recorded today, both dated two days ago: a settlement closed late and a transfer loaded late.
    sid = _closed_delivery(r.operator, juan, "100000", 10, ids["main_cash"], business_date=days_ago(2).isoformat())
    _post(
        r.operator,
        "cash-transfer",
        from_account_id=ids["main_cash"],
        to_account_id=ids["change_cash"],
        amount="1000",
        business_date=days_ago(2),
    )
    day = _view(r.viewer, days_ago(2))
    assert [i["id"] for i in day["settlements"]["items"]] == [sid]
    assert _flows(_account(day, "Caja principal"), "inflow") == {"SETTLEMENT_CLOSE_DELIVERY": (1, "100000.00")}
    for later in (yesterday(), today()):
        other = _view(r.viewer, later)
        assert other["settlements"]["total"] == 0
        assert all(a["inflow_categories"] == a["outflow_categories"] == [] for a in other["accounts"])
        assert _account(other, "Caja principal")["opening_balance"] == "99000.00"


# --- [17] CSV ----------------------------------------------------------------------------------------------


def test_17_the_csv_is_the_same_view_for_a_spanish_spreadsheet(
    r: Roles, juan: int, user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    ids = _ids(r.admin)
    _closed_delivery(r.operator, juan, "100000", 10, ids["main_cash"])
    registered(
        area_admin(user_as),
        "ADVANCE",
        employee("Pedro Gómez"),
        "4000",
        funding_account_id=ids["main_cash"],
        on=yesterday(),
    )
    client = outsider(user_as)
    response = client.get(f"{DAILY}.csv", params={"on": yesterday().isoformat()})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert f'filename="cierre-del-dia-{yesterday().isoformat()}.csv"' in response.headers["content-disposition"]
    content = response.content.decode("utf-8")
    assert content.startswith("﻿Cierre del día;")
    lines = content.splitlines()
    assert "Arqueo físico;No registrado" in lines
    assert "Sección;Cuenta o rendición;Tipo;Estado;Concepto;Operaciones;Importe" in lines
    assert "Saldos;Caja principal;Caja;Activa;Saldo final contable;;96000,00" in lines
    assert "Ingresos;Caja principal;Caja;Activa;Cierres de rendición de reparto;1;100000,00" in lines
    assert "Egresos;Caja principal;Caja;Activa;Movimiento de empleado (reservado);1;4000,00" in lines
    assert "Rendiciones;Reparto · Juan Pérez · Rendición 1;Reparto;Cerrada;Origen del esperado;;Manual" in lines
    assert "Cheques;;;;En cartera al final del día;0;0,00" in lines
    assert "Pedro" not in content
    assert "Adelanto" not in content


def test_17_a_name_never_starts_a_formula_in_the_csv(r: Roles, db: Session) -> None:
    # A second bank, active: removed after the test like every account that is not seeded.
    db.execute(
        text(
            "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative) "
            "VALUES ('=HYPERLINK(1)', '=hyperlink(1)', 'BANK', true)"
        )
    )
    db.commit()
    content = r.viewer.get(f"{DAILY}.csv", params={"on": yesterday().isoformat()}).content.decode("utf-8")
    assert "Saldos;'=HYPERLINK(1);Banco;Activa;Saldo inicial contable;;0,00" in content.splitlines()


def test_17_the_csv_needs_access_too(user_as: Callable[..., TestClient]) -> None:
    assert user_as("ajeno").get(f"{DAILY}.csv").status_code == 403


# --- [20] reconciliation -------------------------------------------------------------------------------


def test_20_every_day_reconciles_and_chains_into_the_next(
    r: Roles, juan: int, category: int, owner_account: Callable[[str], int]
) -> None:
    ids = _ids(r.admin)
    fund(r.admin, "main_cash", "50000", on=days_ago(4))
    _closed_delivery(r.operator, juan, "100000", 10, ids["main_cash"], business_date=days_ago(3).isoformat())
    _post(
        r.operator,
        "bank-deposit",
        cash_account_id=ids["main_cash"],
        bank_account_id=ids["bank"],
        amount="120000",
        business_date=days_ago(2),
    )
    spent(r.operator, ids["bank"], category, "3000", business_date=days_ago(2))
    _post(
        r.admin,
        "owner-contribution",
        owner_account_id=owner_account("Socio"),
        account_id=ids["change_cash"],
        amount="7000",
        business_date=yesterday(),
    )
    previous: dict[str, str] | None = None
    for n in (5, 4, 3, 2, 1, 0):
        body = _view(r.viewer, days_ago(n))
        closing = {a["name"]: a["closing_balance"] for a in body["accounts"]}
        if previous is not None:
            assert {a["name"]: a["opening_balance"] for a in body["accounts"]} == previous
        previous = closing
    assert previous == {"Caja de cambio": "7000.00", "Caja principal": "30000.00", "Banco": "117000.00"}


def test_20_a_mismatch_is_an_invariant_error_never_an_arqueo_difference() -> None:
    balance = AccountBalance(1, "main_cash", "Caja principal", "CASH_BOX", False, True, D("100.00"))
    day = service._AccountDay()
    day.add("CASH_TRANSFER", 7, D("10.00"))
    assert service._account(balance, D("90.00"), day).reconciled is True
    assert service._account(balance, D("80.00"), day).reconciled is False


# --- cheques at the end of the day -----------------------------------------------------------------------


def test_the_cheques_held_at_the_end_of_each_day_and_the_documents_of_the_day(
    r: Roles, employee: Callable[[str], int]
) -> None:
    a = entered(r.admin, "100000", on=days_ago(10))
    b = entered(r.admin, "50000", on=days_ago(10))
    entered(r.admin, "30000", on=days_ago(10))
    deposited(r.operator, [a, b], on=days_ago(8))
    credited(r.operator, [a], on=days_ago(6))
    rejected(r.operator, b, on=days_ago(6), responsible=employee("Juan Pérez"))

    def cheques(n: int) -> dict[str, Any]:
        return _view(r.viewer, days_ago(n))["cheques"]  # type: ignore[no-any-return]

    assert {k: v for k, v in cheques(9).items() if k.startswith(("portfolio", "in_transit"))} == {
        "portfolio_count": 3,
        "portfolio_amount": "180000.00",
        "in_transit_count": 0,
        "in_transit_amount": "0.00",
    }
    day = cheques(8)
    assert (day["portfolio_count"], day["in_transit_count"], day["in_transit_amount"]) == (1, 2, "150000.00")
    assert (day["deposited_count"], day["deposited_amount"], day["credited_count"]) == (2, "150000.00", 0)
    day = cheques(6)
    assert (day["portfolio_count"], day["portfolio_amount"], day["in_transit_count"]) == (1, "30000.00", 0)
    assert (day["credited_count"], day["credited_amount"], day["rejected_count"], day["rejected_amount"]) == (
        1,
        "100000.00",
        1,
        "50000.00",
    )
    # Only the crediting moves the bank: a cheque rejected before crediting never touched it.
    bank = _account(_view(r.viewer, days_ago(6)), "Banco")
    assert _flows(bank, "inflow") == {"CHEQUE_CREDIT": (1, "100000.00")}
    assert _flows(bank, "outflow") == {}
