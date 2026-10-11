"""The consolidated fuel history (R5-4) over HTTP: one row per real load, its state, the filters, the page and
the summary of the fuel in effect. The numbers in brackets are the tests Enzo asked for R5-4. The table each
close freezes (0017) is checked in `test_fuel_history_db_invariants.py`."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.conftest import PASSWORD
from tests.modules.settlements.days import today, yesterday
from tests.modules.settlements.direct_support import account_id, fund, reverse, spent
from tests.modules.settlements.fuel_support import (
    BASE,
    add_fuel,
    fuel,
    fuel_category,
    last_expense,
    make_legacy,
    vehicle,
)
from tests.modules.settlements.test_settlement_close_api import close, create, reopen

URL = f"{BASE}/fuel-loads"


@pytest.fixture
def admin(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("admin")  # type: ignore[no-any-return]


@pytest.fixture
def operator(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("operator")  # type: ignore[no-any-return]


@pytest.fixture
def fuel_id(db: Session) -> int:
    return fuel_category(db)


@pytest.fixture
def pedro(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Pedro Gómez")  # type: ignore[no-any-return]


@pytest.fixture
def juan(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Juan Pérez", function="settlements.cash_viewer")  # type: ignore[no-any-return]


@pytest.fixture
def truck(admin: TestClient, db: Session) -> int:
    return vehicle(db, "AB123CD")


@pytest.fixture
def van(admin: TestClient, db: Session) -> int:
    return vehicle(db, "AC456DE")


@pytest.fixture
def cash(admin: TestClient) -> int:
    fund(admin, "main_cash", "5000000", today() - timedelta(days=10))
    return account_id(admin, "main_cash")


def loaded(client: TestClient, fuel_id: int, driver_id: int, truck: int, liters: str, price: str) -> dict[str, int]:
    """A settlement whose expected amount is exactly its one fuel load: it closes balanced, without counting."""
    amount = (Decimal(liters) * Decimal(price)).quantize(Decimal("0.01"))
    sid = create(client, driver_id, str(amount))
    item = last_expense(add_fuel(client, sid, fuel_id, fuel(truck, liters, price)))
    return {"settlement": sid, "expense": item["id"]}


def direct(client: TestClient, cash: int, fuel_id: int, truck: int, who: int, liters: str, price: str) -> int:
    body = {"vehicle_id": truck, "employee_id": who, "liters": liters, "price_per_liter": price}
    return spent(client, cash, fuel_id, fuel=body)["id"]  # type: ignore[no-any-return]


def page(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get(URL, params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def summary(client: TestClient, **params: Any) -> tuple[str, str, str | None]:
    s = page(client, **params)["summary"]
    return s["liters"], s["amount"], s["weighted_price_per_liter"]


def states(client: TestClient, **params: Any) -> list[tuple[int, str]]:
    return [(r["expense_id"], r["state"]) for r in page(client, **params)["items"]]


def closed(client: TestClient, sid: int, version: int = 1) -> None:
    response = close(client, sid, version=version)
    assert response.status_code == 201, response.text


# --- states and the summary ------------------------------------------------------------------------


def test_an_open_settlement_load_is_listed_and_not_summed(operator, fuel_id, pedro, truck) -> None:  # type: ignore[no-untyped-def]
    """[1] [2] [19] With nothing in effect, the weighted price is null: no division by zero."""
    s = loaded(operator, fuel_id, pedro, truck, "10", "2000")
    body = page(operator)
    assert body["total"] == 1
    row = body["items"][0]
    assert (row["state"], row["origin"], row["settlement_id"], row["liters"], row["amount"]) == (
        "OPEN_SETTLEMENT",
        "SETTLEMENT",
        s["settlement"],
        "10.000",
        "20000.00",
    )
    assert body["summary"] == {"liters": "0", "amount": "0.00", "weighted_price_per_liter": None}


def test_a_closed_settlement_load_is_published_and_summed(operator, fuel_id, pedro, truck) -> None:  # type: ignore[no-untyped-def]
    """[3]"""
    s = loaded(operator, fuel_id, pedro, truck, "10", "2000")
    closed(operator, s["settlement"])
    assert states(operator) == [(s["expense"], "PUBLISHED")]
    assert summary(operator) == ("10.000", "20000.00", "2000.00")


def test_a_direct_load_in_effect_is_published_and_summed(operator, cash, fuel_id, truck, juan) -> None:  # type: ignore[no-untyped-def]
    """[4] Its origin is the paying box, not a settlement."""
    eid = direct(operator, cash, fuel_id, truck, juan, "40", "1250")
    row = page(operator)["items"][0]
    assert (row["expense_id"], row["state"], row["origin"], row["settlement_id"], row["paid_from_account_name"]) == (
        eid,
        "PUBLISHED",
        "DIRECT",
        None,
        "Caja principal",
    )
    assert row["business_date"] == today().isoformat()
    assert summary(operator) == ("40.000", "50000.00", "1250.00")


def test_a_voided_settlement_load_is_listed_and_not_summed(operator, fuel_id, pedro, truck) -> None:  # type: ignore[no-untyped-def]
    """[5] Voided before the close (here, a second load of the same settlement)."""
    s = loaded(operator, fuel_id, pedro, truck, "10", "2000")
    extra = last_expense(add_fuel(operator, s["settlement"], fuel_id, fuel(truck, "5", "2000")))["id"]
    voided = operator.post(
        f"{BASE}/deliveries/{s['settlement']}/expenses/{extra}/void", json={"reason": "Cargado dos veces"}
    )
    assert voided.status_code == 200, voided.text
    assert dict(states(operator))[extra] == "VOIDED"  # also before the close
    closed(operator, s["settlement"])
    assert dict(states(operator)) == {s["expense"]: "PUBLISHED", extra: "VOIDED"}
    assert summary(operator) == ("10.000", "20000.00", "2000.00")


def test_a_reversed_direct_load_is_kept_and_not_summed(operator, admin, cash, fuel_id, truck, juan) -> None:  # type: ignore[no-untyped-def]
    """[6]"""
    eid = direct(operator, cash, fuel_id, truck, juan, "40", "1250")
    assert reverse(admin, eid).status_code == 201
    assert states(operator) == [(eid, "REVERSED")]
    assert summary(operator) == ("0", "0.00", None)


def test_totals_and_the_weighted_price_of_both_origins(operator, admin, cash, fuel_id, pedro, truck, juan) -> None:  # type: ignore[no-untyped-def]
    """[16] [17] [18] Enzo's example: 1.250,500 l for $1.875.000 is $1.499,40 per liter, weighted by liters
    (not the mean of the two prices, 1.499,625). Reversed and open loads stay out."""
    s = loaded(operator, fuel_id, pedro, truck, "250.5", "1500")  # $375.750
    closed(operator, s["settlement"])
    direct(operator, cash, fuel_id, truck, juan, "1000", "1499.25")  # $1.499.250
    gone = direct(operator, cash, fuel_id, truck, juan, "100", "900")
    assert reverse(admin, gone).status_code == 201
    loaded(operator, fuel_id, pedro, truck, "77", "1000")  # open
    assert summary(operator) == ("1250.500", "1875000.00", "1499.40")


# --- filters ---------------------------------------------------------------------------------------


@pytest.fixture
def history(operator, cash, fuel_id, pedro, juan, truck, van) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """A closed settlement load of Pedro in the truck (yesterday), an open one of Pedro in the van
    (yesterday) and a direct load of Juan in the van (today)."""
    first = loaded(operator, fuel_id, pedro, truck, "10", "2000")
    closed(operator, first["settlement"])
    second = loaded(operator, fuel_id, pedro, van, "20", "1000")
    third = direct(operator, cash, fuel_id, van, juan, "30", "1000")
    return {"closed": first["expense"], "open": second["expense"], "direct": third}


def ids(client: TestClient, **params: Any) -> set[int]:
    return {r["expense_id"] for r in page(client, **params)["items"]}


def test_filter_by_vehicle(operator, history, truck, van) -> None:  # type: ignore[no-untyped-def]
    """[7]"""
    assert ids(operator, vehicle_id=truck) == {history["closed"]}
    assert ids(operator, vehicle_id=van) == {history["open"], history["direct"]}
    assert summary(operator, vehicle_id=van) == ("30.000", "30000.00", "1000.00")


def test_an_inactive_vehicle_stays_visible_and_filterable(operator, history, truck, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[8]"""
    db.execute(text("UPDATE vehicles SET active = false WHERE id = :v"), {"v": truck})
    db.commit()
    rows = page(operator, vehicle_id=truck)["items"]
    assert [(r["expense_id"], r["vehicle_plate"], r["vehicle_active"]) for r in rows] == [
        (history["closed"], "AB 123 CD", False)
    ]


def test_filter_by_employee(operator, history, pedro, juan) -> None:  # type: ignore[no-untyped-def]
    """[9]"""
    assert ids(operator, employee_id=pedro) == {history["closed"], history["open"]}
    assert ids(operator, employee_id=juan) == {history["direct"]}
    assert summary(operator, employee_id=pedro) == ("10.000", "20000.00", "2000.00")


def test_an_inactive_employee_stays_visible_and_findable(operator, history, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[10] Also in the list of employees the filter offers."""
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": juan})
    db.commit()
    rows = page(operator, employee_id=juan)["items"]
    assert [(r["employee_name"], r["employee_active"]) for r in rows] == [("Juan Pérez", False)]
    offered = operator.get(f"{URL}/employees").json()
    assert {(e["full_name"], e["active"]) for e in offered} == {("Pedro Gómez", True), ("Juan Pérez", False)}


def test_date_range_on_business_dates(operator, history) -> None:  # type: ignore[no-untyped-def]
    """[11] The settlement's business date for its loads, the expense's for a direct one."""
    assert ids(operator, date_from=today().isoformat()) == {history["direct"]}
    assert ids(operator, date_to=yesterday().isoformat()) == {history["closed"], history["open"]}
    assert ids(operator, date_from=yesterday().isoformat(), date_to=today().isoformat()) == set(history.values())
    assert ids(operator, date_to=(yesterday() - timedelta(days=1)).isoformat()) == set()
    assert operator.get(URL, params={"date_from": "no-date"}).status_code == 422


def test_filter_by_origin(operator, history) -> None:  # type: ignore[no-untyped-def]
    """[12]"""
    assert ids(operator, origin="DIRECT") == {history["direct"]}
    assert ids(operator, origin="SETTLEMENT") == {history["closed"], history["open"]}
    assert operator.get(URL, params={"origin": "OTHER"}).status_code == 422


def test_filter_by_state(operator, history) -> None:  # type: ignore[no-untyped-def]
    """[13] The summary follows the filter: open loads never add up."""
    assert ids(operator, state="PUBLISHED") == {history["closed"], history["direct"]}
    assert ids(operator, state="OPEN_SETTLEMENT") == {history["open"]}
    assert summary(operator, state="OPEN_SETTLEMENT") == ("0", "0.00", None)
    assert ids(operator, state="REVERSED") == set()
    assert operator.get(URL, params={"state": "DRAFT"}).status_code == 422


def test_pagination_is_done_by_the_backend(operator, history) -> None:  # type: ignore[no-untyped-def]
    """[14] The summary covers every filtered load, not just the page."""
    first = page(operator, limit=2, offset=0)
    second = page(operator, limit=2, offset=2)
    assert (first["total"], first["limit"], first["offset"], len(first["items"])) == (3, 2, 0, 2)
    assert (second["total"], len(second["items"])) == (3, 1)
    assert {r["expense_id"] for r in first["items"] + second["items"]} == set(history.values())
    assert first["summary"] == second["summary"] == page(operator)["summary"]
    assert operator.get(URL, params={"limit": 0}).status_code == 422
    assert operator.get(URL, params={"limit": 201}).status_code == 422


def test_order_is_business_date_then_id_descending(operator, history) -> None:  # type: ignore[no-untyped-def]
    """[15] Today's direct load first, then yesterday's settlement loads, newest load first."""
    rows = page(operator)["items"]
    assert [r["expense_id"] for r in rows] == [history["direct"], history["open"], history["closed"]]
    assert [r["business_date"] for r in rows] == [today().isoformat(), yesterday().isoformat(), yesterday().isoformat()]


def test_no_loads_at_all(operator) -> None:  # type: ignore[no-untyped-def]
    assert page(operator) == {
        "items": [],
        "total": 0,
        "limit": 50,
        "offset": 0,
        "summary": {"liters": "0", "amount": "0.00", "weighted_price_per_liter": None},
    }


def test_a_legacy_fuel_expense_without_load_makes_no_row(operator, admin, fuel_id, pedro, db: Session) -> None:  # type: ignore[no-untyped-def]
    """[20] Its money counts as an expense, but there is no load to show and none is made up."""
    tolls = admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]
    sid = create(operator, pedro, "5000")
    response = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": "00000000-0000-4000-8000-000000000020", "category_id": tolls, "amount": "5000"},
    )
    make_legacy(db, last_expense(response)["id"])
    closed(operator, sid)
    assert page(operator)["total"] == 0
    assert summary(operator) == ("0", "0.00", None)


# --- a settlement in correction ---------------------------------------------------------------------


@pytest.fixture
def reopened(operator, admin, fuel_id, pedro, truck) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """10 l x $2.000 closed and then reopened."""
    s = loaded(operator, fuel_id, pedro, truck, "10", "2000")
    closed(operator, s["settlement"])
    assert reopen(admin, s["settlement"], 1).status_code == 200
    return s


def test_a_reopened_settlement_keeps_the_summary_of_its_close_in_effect(operator, reopened) -> None:  # type: ignore[no-untyped-def]
    """[21]"""
    row = page(operator)["items"][0]
    assert (row["state"], row["proposed_change"], row["proposed_void"]) == ("IN_CORRECTION", False, False)
    assert row["in_effect"]["liters"] == "10.000"
    assert summary(operator) == ("10.000", "20000.00", "2000.00")


def test_a_change_proposed_in_correction_does_not_move_the_summary_until_the_close(operator, reopened) -> None:  # type: ignore[no-untyped-def]
    """[22] [23] 8 l x $2.500 keeps the expected amount, so it closes balanced again."""
    sid, eid = reopened["settlement"], reopened["expense"]
    patched = operator.patch(
        f"{BASE}/deliveries/{sid}/expenses/{eid}", json={"fuel": {"liters": "8", "price_per_liter": "2500"}}
    )
    assert patched.status_code == 200, patched.text
    row = page(operator)["items"][0]
    assert (row["state"], row["liters"], row["price_per_liter"], row["proposed_change"]) == (
        "IN_CORRECTION",
        "8.000",
        "2500.0000",
        True,
    )
    assert (row["in_effect"]["liters"], row["in_effect"]["price_per_liter"], row["in_effect"]["amount"]) == (
        "10.000",
        "2000.0000",
        "20000.00",
    )
    assert summary(operator) == ("10.000", "20000.00", "2000.00")

    closed(operator, sid, version=2)
    row = page(operator)["items"][0]
    assert (row["state"], row["in_effect"], row["proposed_change"]) == ("PUBLISHED", None, False)
    assert summary(operator) == ("8.000", "20000.00", "2500.00")


def test_a_load_voided_in_correction_counts_until_the_new_close(operator, reopened, fuel_id, truck) -> None:  # type: ignore[no-untyped-def]
    """A void is a proposal like any change: the close in effect still holds the load."""
    sid, eid = reopened["settlement"], reopened["expense"]
    add_fuel(operator, sid, fuel_id, fuel(truck, "4", "5000"))  # keeps the expected $20.000
    voided = operator.post(f"{BASE}/deliveries/{sid}/expenses/{eid}/void", json={"reason": "Era de otro reparto"})
    assert voided.status_code == 200, voided.text
    rows = {r["expense_id"]: r for r in page(operator)["items"]}
    assert (rows[eid]["state"], rows[eid]["proposed_void"]) == ("IN_CORRECTION", True)
    added = next(r for i, r in rows.items() if i != eid)
    assert (added["state"], added["in_effect"], added["proposed_change"]) == ("IN_CORRECTION", None, True)
    assert summary(operator) == ("10.000", "20000.00", "2000.00")

    closed(operator, sid, version=2)
    assert {r["expense_id"]: r["state"] for r in page(operator)["items"]} == {
        eid: "VOIDED",
        added["expense_id"]: "PUBLISHED",
    }
    assert summary(operator) == ("4.000", "20000.00", "5000.00")


def test_a_vehicle_changed_in_correction_sums_under_the_one_in_effect(operator, reopened, truck, van) -> None:  # type: ignore[no-untyped-def]
    """The row shows under both vehicles while the change is a proposal; the summary only under the one the
    close in effect holds, and moves with the new close."""
    sid, eid = reopened["settlement"], reopened["expense"]
    patched = operator.patch(f"{BASE}/deliveries/{sid}/expenses/{eid}", json={"fuel": {"vehicle_id": van}})
    assert patched.status_code == 200, patched.text
    assert ids(operator, vehicle_id=truck) == ids(operator, vehicle_id=van) == {eid}
    assert summary(operator, vehicle_id=truck) == ("10.000", "20000.00", "2000.00")
    assert summary(operator, vehicle_id=van) == ("0", "0.00", None)

    closed(operator, sid, version=2)
    assert ids(operator, vehicle_id=truck) == set()
    assert summary(operator, vehicle_id=van) == ("10.000", "20000.00", "2000.00")


# --- the row and access -----------------------------------------------------------------------------


def test_a_row_carries_what_the_screen_shows(operator, history, db: Session) -> None:  # type: ignore[no-untyped-def]
    rows = {r["expense_id"]: r for r in page(operator)["items"]}
    settlement = rows[history["closed"]]
    assert (
        settlement["vehicle_plate"],
        settlement["vehicle_description"],
        settlement["employee_name"],
        settlement["settlement_driver_name"],
        settlement["settlement_sequence"],
        settlement["price_per_liter"],
        settlement["paid_from_account_name"],
        settlement["created_by_name"],
    ) == ("AB 123 CD", "Camión AB123CD", "Pedro Gómez", "Pedro Gómez", 1, "2000.0000", None, "Usuario Test")
    assert rows[history["direct"]]["settlement_sequence"] is None


def test_every_settlements_role_reads_it_and_nobody_else(as_role, make_user, client) -> None:  # type: ignore[no-untyped-def]
    assert as_role("viewer").get(URL).status_code == 200
    assert as_role("viewer").get(f"{URL}/employees").status_code == 200
    make_user(email="outsider@districom.test", role="viewer")
    login = client.post("/api/v1/auth/login", json={"email": "outsider@districom.test", "password": PASSWORD})
    assert login.status_code == 200, login.text
    assert client.get(URL).status_code == 403
