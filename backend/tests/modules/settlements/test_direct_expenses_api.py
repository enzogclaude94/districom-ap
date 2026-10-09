"""Direct expenses from a cash box or the bank (R5-3) over HTTP: recording (ordinary and fuel), the paying
account, the business date and its funds, «Banco en descubierto», the exact legs, atomicity, reversal,
the descriptive correction, idempotency, permissions and the list. Database rules are in
`test_direct_expenses_db_invariants.py`, races in `test_direct_expenses_concurrency.py`."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.direct_support import (
    BASE,
    T,
    account_id,
    balance,
    counts,
    error,
    fund,
    legs,
    reverse,
    spend,
    spent,
)
from tests.modules.settlements.fuel_support import fuel_category, vehicle
from tests.modules.settlements.test_cheque_overdraft import _at_minus
from tests.modules.settlements.test_settlement_close_api import create


@pytest.fixture
def admin(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("admin")  # type: ignore[no-any-return]


@pytest.fixture
def cash(admin: TestClient) -> int:
    fund(admin, "main_cash", "100000", T - timedelta(days=10))
    return account_id(admin, "main_cash")


@pytest.fixture
def bank(admin: TestClient) -> int:
    fund(admin, "bank", "200000", T - timedelta(days=10))
    return account_id(admin, "bank")


@pytest.fixture
def tolls(admin: TestClient) -> int:
    return admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]  # type: ignore[no-any-return]


@pytest.fixture
def fuel_id(db: Session) -> int:
    return fuel_category(db)


@pytest.fixture
def truck(admin: TestClient, db: Session) -> int:
    return vehicle(db, "AB123CD")


@pytest.fixture
def juan(driver) -> int:  # type: ignore[no-untyped-def]
    return driver("Juan Pérez", function="settlements.cash_viewer")  # type: ignore[no-any-return]


def load(truck: int, employee: int | None, liters: str = "45.678", price: str = "1234.5678", **extra: Any) -> dict:
    return {"vehicle_id": truck, "employee_id": employee, "liters": liters, "price_per_liter": price, **extra}


def _audit(db: Session, event: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = :e ORDER BY id"),
        {"e": f"settlements.expense.{event}"},
    ).scalars()
    return list(rows)


# --- recording ----------------------------------------------------------------------------------------


def test_an_ordinary_expense_from_the_cash_box(as_role, cash, tolls, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    expense = spent(operator, cash, tolls, "5000", description="Peaje autopista")
    assert (expense["origin"], expense["state"]) == ("DIRECT", "IN_EFFECT")
    assert (expense["amount"], expense["business_date"], expense["settlement_id"]) == ("5000.00", T.isoformat(), None)
    assert (expense["paid_from_account_name"], expense["paid_from_account_kind"]) == ("Caja principal", "CASH_BOX")
    operation = expense["operation"]
    assert (operation["type"], operation["source_type"], operation["source_id"]) == (
        "DIRECT_EXPENSE",
        "expense",
        expense["id"],
    )
    assert operation["business_date"] == T.isoformat()
    assert legs(db, operation["id"]) == [("main_cash", Decimal("-5000.00")), ("expense", Decimal("5000.00"))]
    assert balance(operator, "main_cash") == Decimal("95000.00")
    assert balance(operator, "expense") == Decimal("5000.00")
    created = _audit(db, "created")
    assert created[-1]["after"]["operation_id"] == operation["id"]
    assert created[-1]["after"]["paid_from_account_id"] == cash


def test_an_ordinary_expense_from_the_bank(as_role, bank, tolls, db: Session) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), bank, tolls, "12345.67")
    assert expense["paid_from_account_kind"] == "BANK"
    assert legs(db, expense["operation_id"]) == [("bank", Decimal("-12345.67")), ("expense", Decimal("12345.67"))]
    assert balance(as_role("viewer"), "bank") == Decimal("187654.33")


def test_fuel_from_the_cash_box_needs_an_explicit_employee(as_role, cash, fuel_id, truck, juan, db) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert error(spend(operator, cash, fuel_id, fuel=load(truck, None))) == (422, "fuel_employee_required")
    expense = spent(operator, cash, fuel_id, fuel=load(truck, juan, odometer_km=120500))
    assert expense["amount"] == "56392.59"  # 45,678 l x $1.234,5678, half up
    assert expense["category_code"] == "FUEL"
    fuel = expense["fuel"]
    assert (fuel["vehicle_plate"], fuel["employee_name"], fuel["liters"], fuel["price_per_liter"]) == (
        "AB 123 CD",
        "Juan Pérez",
        "45.678",
        "1234.5678",
    )
    assert fuel["odometer_km"] == 120500
    assert legs(db, expense["operation_id"]) == [("main_cash", Decimal("-56392.59")), ("expense", Decimal("56392.59"))]


def test_fuel_from_the_bank(as_role, bank, fuel_id, truck, juan, db) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), bank, fuel_id, fuel=load(truck, juan, "33.333", "1499.99"))
    assert expense["amount"] == "49999.17"
    assert legs(db, expense["operation_id"]) == [("bank", Decimal("-49999.17")), ("expense", Decimal("49999.17"))]


def test_fuel_and_ordinary_requests_are_validated(as_role, cash, fuel_id, tolls, truck, juan, db) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    before = counts(db)
    assert error(spend(operator, cash, fuel_id, "100", fuel=load(truck, juan))) == (422, "fuel_amount_is_computed")
    assert error(spend(operator, cash, fuel_id)) == (422, "fuel_detail_required")
    assert error(spend(operator, cash, tolls, "100", fuel=load(truck, juan))) == (422, "fuel_detail_not_allowed")
    assert error(spend(operator, cash, tolls)) == (422, "invalid_amount")
    assert spend(operator, cash, tolls, "0").status_code == 422
    assert spend(operator, cash, tolls, "1.001").status_code == 422
    stopped = vehicle(db, "ZZ999ZZ", active=False)
    assert error(spend(operator, cash, fuel_id, fuel=load(stopped, juan))) == (422, "invalid_vehicle")
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": juan})
    db.commit()
    assert error(spend(operator, cash, fuel_id, fuel=load(truck, juan))) == (422, "invalid_employee")
    assert counts(db) == before


def test_only_an_active_cash_box_or_bank_pays(as_role, admin, cash, tolls, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    partner = admin.post(f"{BASE}/accounts", json={"name": "Socio A"}).json()["id"]
    employee_account = db.execute(
        text(
            "INSERT INTO settlements.accounts (name, name_key, kind, allow_negative, employee_id) "
            "VALUES ('Cuenta interna', 'cuenta interna', 'EMPLOYEE', true, :e) RETURNING id"
        ),
        {"e": juan},
    ).scalar_one()
    db.commit()
    operator = as_role("operator")
    technical = [
        account_id(operator, code)
        for code in (
            "expense",
            "pending_collections",
            "opening_balance",
            "adjustment",
            "delivery_collections",
            "settlement_difference",
            "uncollectible",
        )
    ]
    for account in [*technical, partner, employee_account, 999999]:
        assert error(spend(operator, account, tolls, "10")) == (422, "invalid_payment_account"), account
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE code = 'change_cash'"))
    db.commit()
    assert error(spend(operator, account_id(operator, "change_cash"), tolls, "10")) == (422, "invalid_payment_account")


def test_the_cheque_accounts_never_pay(as_role, cash, tolls, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    for code in ("cheque_portfolio", "cheques_in_transit"):
        assert error(spend(operator, account_id(operator, code), tolls, "10")) == (422, "invalid_payment_account")


def test_an_inactive_category_takes_no_new_expense(as_role, admin, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    admin.patch(f"{BASE}/expense-categories/{tolls}", json={"active": False})
    assert error(spend(as_role("operator"), cash, tolls, "10")) == (422, "invalid_expense_category")


def test_a_deactivated_fuel_category_takes_no_direct_fuel(as_role, admin, cash, fuel_id, truck, juan) -> None:  # type: ignore[no-untyped-def]
    admin.patch(f"{BASE}/expense-categories/{fuel_id}", json={"active": False})
    assert error(spend(as_role("operator"), cash, fuel_id, fuel=load(truck, juan))) == (
        422,
        "invalid_expense_category",
    )


# --- business date and funds --------------------------------------------------------------------------


def test_never_a_future_date(as_role, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    response = spend(as_role("operator"), cash, tolls, "10", business_date=T + timedelta(days=1))
    assert error(response) == (422, "future_business_date")


def test_a_past_date_with_funds_then(as_role, cash, tolls, db: Session) -> None:  # type: ignore[no-untyped-def]
    day = T - timedelta(days=3)
    expense = spent(as_role("operator"), cash, tolls, "40000", business_date=day)
    assert expense["business_date"] == day.isoformat()
    assert expense["operation"]["business_date"] == day.isoformat()
    assert balance(as_role("viewer"), "main_cash", day) == Decimal("60000.00")


def test_a_past_date_without_funds_then_is_refused_even_if_today_has_them(as_role, admin, tolls, db) -> None:  # type: ignore[no-untyped-def]
    """Enzo's example: today the box has the money, three days ago it did not."""
    fund(admin, "main_cash", "100000", T)
    cash = account_id(admin, "main_cash")
    before = counts(db)
    response = spend(as_role("operator"), cash, tolls, "50000", business_date=T - timedelta(days=3))
    assert error(response) == (409, "insufficient_funds")
    assert response.json()["details"]["business_date"] == (T - timedelta(days=3)).isoformat()
    assert counts(db) == before
    assert spend(as_role("operator"), cash, tolls, "50000").status_code == 201


def test_a_past_date_that_would_leave_a_later_day_negative_is_refused(as_role, admin, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    """$100.000 ten days ago; $80.000 left the box yesterday: a $30.000 expense dated three days ago would
    leave yesterday at -$10.000, although three days ago there was enough."""
    out = admin.post(
        f"{BASE}/operations/cash-transfer",
        json={
            "client_request_id": str(uuid4()),
            "business_date": (T - timedelta(days=1)).isoformat(),
            "from_account_id": cash,
            "to_account_id": account_id(admin, "change_cash"),
            "amount": "80000",
        },
    )
    assert out.status_code == 201, out.text
    response = spend(as_role("operator"), cash, tolls, "30000", business_date=T - timedelta(days=3))
    assert error(response) == (409, "insufficient_funds")
    assert response.json()["details"]["business_date"] == (T - timedelta(days=1)).isoformat()
    assert spend(as_role("operator"), cash, tolls, "20000", business_date=T - timedelta(days=3)).status_code == 201


def test_the_cash_box_is_never_negative(as_role, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert error(spend(operator, cash, tolls, "100000.01")) == (409, "insufficient_funds")
    assert spend(operator, cash, tolls, "100000").status_code == 201
    assert balance(operator, "main_cash") == Decimal("0.00")


def test_a_bank_overdrawn_by_a_rejection_is_not_deepened(admin, tolls, driver, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Bank at -$100.000 by a cheque rejection; a direct expense of $20.000 is refused (Enzo's example)."""
    _at_minus(admin, driver("Ana Cobranzas"), "0")
    assert balance(admin, "bank") == Decimal("-100000.00")
    before = counts(db)
    response = spend(admin, account_id(admin, "bank"), tolls, "20000")
    assert error(response) == (409, "insufficient_funds")
    assert balance(admin, "bank") == Decimal("-100000.00")
    assert counts(db) == before


def test_a_bank_at_zero_does_not_go_into_an_overdraft(as_role, tolls) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert error(spend(operator, account_id(operator, "bank"), tolls, "0.01")) == (409, "insufficient_funds")


# --- reversal -----------------------------------------------------------------------------------------


def test_a_reversal_undoes_the_operation_on_the_original_date(as_role, cash, tolls, db: Session) -> None:  # type: ignore[no-untyped-def]
    day = T - timedelta(days=4)
    expense = spent(as_role("operator"), cash, tolls, "50000", business_date=day)
    assert balance(as_role("viewer"), "main_cash") == Decimal("50000.00")
    admin = as_role("admin")
    response = reverse(admin, expense["id"])
    assert response.status_code == 201, response.text
    reversed_ = response.json()
    assert reversed_["state"] == "REVERSED"
    assert (reversed_["end_reason"], reversed_["ended_by_name"]) == (
        "Se cargó desde la caja equivocada",
        "Usuario Test",
    )
    reversal = reversed_["reversal"]
    assert (reversal["type"], reversal["business_date"], reversal["reverses_operation_id"]) == (
        "REVERSAL",
        day.isoformat(),
        expense["operation_id"],
    )
    assert reversal["created_at"][:10] == T.isoformat() or reversal["created_at"] > expense["created_at"]
    assert legs(db, reversal["id"]) == [("expense", Decimal("-50000.00")), ("main_cash", Decimal("50000.00"))]
    assert balance(admin, "main_cash") == Decimal("100000.00")
    assert balance(admin, "main_cash", day) == Decimal("100000.00")
    assert balance(admin, "expense") == Decimal("0.00")
    assert reversed_["operation"]["reversed_by_operation_id"] == reversal["id"]
    assert _audit(db, "reversed")[-1]["after"]["reversal_operation_id"] == reversal["id"]


def test_only_an_admin_reverses_and_with_a_reason(as_role, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, tolls, "100")
    assert error(reverse(as_role("operator"), expense["id"])) == (403, "forbidden")
    assert reverse(as_role("admin"), expense["id"], reason="").status_code == 422
    assert error(reverse(as_role("admin"), expense["id"], reason="   ")) == (422, "reason_required")


def test_a_second_reversal_is_refused_and_a_retry_is_the_same(as_role, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, tolls, "100")
    admin = as_role("admin")
    request = str(uuid4())
    first = reverse(admin, expense["id"], client_request_id=request)
    assert first.status_code == 201
    again = reverse(admin, expense["id"], client_request_id=request)
    assert again.status_code == 200
    assert again.json()["reversal_operation_id"] == first.json()["reversal_operation_id"]
    assert error(reverse(admin, expense["id"], reason="Otro motivo", client_request_id=request)) == (
        409,
        "idempotency_conflict",
    )
    assert error(reverse(admin, expense["id"])) == (409, "expense_already_reversed")


def test_a_new_expense_after_the_reversal(as_role, cash, bank, tolls) -> None:  # type: ignore[no-untyped-def]
    """Paid from the bank, loaded as cash: reversal and a new expense, never a changed account."""
    wrong = spent(as_role("operator"), cash, tolls, "7000")
    assert reverse(as_role("admin"), wrong["id"]).status_code == 201
    right = spent(as_role("operator"), bank, tolls, "7000")
    viewer = as_role("viewer")
    assert balance(viewer, "main_cash") == Decimal("100000.00")
    assert balance(viewer, "bank") == Decimal("193000.00")
    assert balance(viewer, "expense") == Decimal("7000.00")
    assert right["id"] != wrong["id"]


def test_cajas_never_reverses_a_direct_expense(as_role, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, tolls, "100")
    response = as_role("admin").post(
        f"{BASE}/operations/{expense['operation_id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Desde Cajas"},
    )
    assert error(response) == (409, "operation_owned_by_document")
    assert response.json()["details"] == {"source_type": "expense", "source_id": expense["id"]}


def test_a_reversed_fuel_expense_keeps_its_load(as_role, cash, fuel_id, truck, juan) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, fuel_id, fuel=load(truck, juan))
    reversed_ = reverse(as_role("admin"), expense["id"]).json()
    assert reversed_["fuel"]["liters"] == "45.678"
    assert reversed_["state"] == "REVERSED"


# --- descriptive correction ---------------------------------------------------------------------------


def test_an_admin_corrects_description_and_odometer_with_a_reason(as_role, cash, fuel_id, truck, juan, db) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, fuel_id, fuel=load(truck, juan, odometer_km=1000), description="YPF")
    operations = db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one()
    admin = as_role("admin")
    response = admin.post(
        f"{BASE}/expenses/{expense['id']}/descriptive",
        json={"reason": "Ticket mal leído", "description": "YPF Ruta 3", "odometer_km": 10000},
    )
    assert response.status_code == 200, response.text
    corrected = response.json()
    assert (corrected["description"], corrected["fuel"]["odometer_km"], corrected["amount"]) == (
        "YPF Ruta 3",
        10000,
        "56392.59",
    )
    assert db.execute(text("SELECT count(*) FROM settlements.financial_operations")).scalar_one() == operations
    event = _audit(db, "corrected")[-1]
    assert event["before"] == {"description": "YPF", "odometer_km": 1000}
    assert event["after"] == {"description": "YPF Ruta 3", "odometer_km": 10000, "reason": "Ticket mal leído"}
    assert corrected["history"][-1]["event_type"] == "corrected"
    # Clearing the odometer is a correction too; repeating it changes nothing and records nothing.
    for _ in range(2):
        cleared = admin.post(
            f"{BASE}/expenses/{expense['id']}/descriptive", json={"reason": "Sin dato", "odometer_km": None}
        )
        assert cleared.json()["fuel"]["odometer_km"] is None
    assert len(_audit(db, "corrected")) == 2


def test_the_correction_never_touches_money(as_role, cash, bank, tolls) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, tolls, "100")
    admin = as_role("admin")
    url = f"{BASE}/expenses/{expense['id']}/descriptive"
    for field, value in (
        ("amount", "50"),
        ("paid_from_account_id", bank),
        ("category_id", expense["category_id"]),
        ("business_date", T.isoformat()),
        ("liters", "1"),
        ("vehicle_id", 1),
    ):
        response = admin.post(url, json={"reason": "Corregir", field: value})
        assert response.status_code == 422, field
    assert error(admin.post(url, json={"reason": "Corregir", "odometer_km": 5})) == (422, "fuel_detail_not_allowed")
    assert error(admin.post(url, json={"reason": "Corregir"})) == (422, "nothing_to_correct")
    assert admin.post(url, json={"description": "x"}).status_code == 422  # reason is required
    assert error(as_role("operator").post(url, json={"reason": "r", "description": "x"})) == (403, "forbidden")
    assert admin.get(f"{BASE}/expenses/{expense['id']}").json()["amount"] == "100.00"
    assert balance(admin, "main_cash") == Decimal("99900.00")


def test_a_reversed_expense_is_not_corrected(as_role, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, tolls, "100")
    admin = as_role("admin")
    reverse(admin, expense["id"])
    response = admin.post(f"{BASE}/expenses/{expense['id']}/descriptive", json={"reason": "r", "description": "x"})
    assert error(response) == (409, "expense_reversed")


def test_a_settlement_expense_is_not_handled_here(as_role, driver, tolls) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    sid = create(operator, driver("Pedro Gómez"), "100000")
    detail = operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "500"},
    ).json()
    eid = detail["expenses"][-1]["id"]
    admin = as_role("admin")
    assert error(reverse(admin, eid)) == (422, "not_a_direct_expense")
    correction = admin.post(f"{BASE}/expenses/{eid}/descriptive", json={"reason": "r", "description": "x"})
    assert error(correction) == (422, "not_a_direct_expense")
    assert error(reverse(admin, 999999)) == (404, "expense_not_found")
    assert error(admin.get(f"{BASE}/expenses/999999")) == (404, "expense_not_found")


# --- idempotency --------------------------------------------------------------------------------------


def test_a_retry_returns_the_same_expense_and_one_operation(as_role, cash, fuel_id, truck, juan, db) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    request = str(uuid4())
    first = spend(operator, cash, fuel_id, client_request_id=request, fuel=load(truck, juan))
    before = counts(db)
    again = spend(operator, cash, fuel_id, client_request_id=request, fuel=load(truck, juan))
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert counts(db) == before
    other = spend(operator, cash, fuel_id, client_request_id=request, fuel=load(truck, juan, "10"))
    assert error(other) == (409, "idempotency_conflict")
    assert balance(operator, "main_cash") == Decimal("43607.41")


def test_a_request_id_of_a_settlement_expense_is_not_reused(as_role, driver, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    sid = create(operator, driver("Pedro Gómez"), "100000")
    request = str(uuid4())
    operator.post(
        f"{BASE}/deliveries/{sid}/expenses", json={"client_request_id": request, "category_id": tolls, "amount": "5"}
    )
    assert error(spend(operator, cash, tolls, "5", client_request_id=request)) == (409, "idempotency_conflict")


# --- permissions --------------------------------------------------------------------------------------


def test_permissions(as_role, client: TestClient, make_user, cash, tolls) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, tolls, "100")
    viewer = as_role("viewer")
    assert viewer.get(f"{BASE}/expenses").status_code == 200
    assert viewer.get(f"{BASE}/expenses/{expense['id']}").status_code == 200
    assert error(spend(viewer, cash, tolls, "1")) == (403, "forbidden")
    assert error(reverse(viewer, expense["id"])) == (403, "forbidden")
    make_user(email="nobody@districom.test", role="viewer")
    from tests.conftest import PASSWORD

    client.post("/api/v1/auth/login", json={"email": "nobody@districom.test", "password": PASSWORD})
    assert client.get(f"{BASE}/expenses").status_code == 403


# --- list and detail ----------------------------------------------------------------------------------


def test_the_list_filters_and_pages(as_role, admin, cash, bank, tolls, fuel_id, truck, juan, driver) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    old = spent(operator, cash, tolls, "100", business_date=T - timedelta(days=5))
    fuel_bank = spent(operator, bank, fuel_id, fuel=load(truck, juan))
    gone = spent(operator, cash, tolls, "300", business_date=T - timedelta(days=1))
    reverse(as_role("admin"), gone["id"])
    sid = create(operator, driver("Pedro Gómez"), "100000")
    operator.post(
        f"{BASE}/deliveries/{sid}/expenses",
        json={"client_request_id": str(uuid4()), "category_id": tolls, "amount": "50"},
    )
    viewer = as_role("viewer")

    def ids(**params: Any) -> list[int]:
        response = viewer.get(f"{BASE}/expenses", params=params)
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["items"]]

    direct = ids(origin="DIRECT")
    assert direct == [fuel_bank["id"], gone["id"], old["id"]]  # newest business date first
    assert len(ids()) == 4
    assert ids(origin="SETTLEMENT") == [i for i in ids() if i not in direct]
    assert ids(origin="DIRECT", category_id=fuel_id) == [fuel_bank["id"]]
    assert ids(origin="DIRECT", account_id=cash) == [gone["id"], old["id"]]
    assert ids(origin="DIRECT", date_from=(T - timedelta(days=2)).isoformat()) == [fuel_bank["id"], gone["id"]]
    assert ids(origin="DIRECT", date_to=(T - timedelta(days=2)).isoformat()) == [old["id"]]
    assert ids(origin="DIRECT", state="REVERSED") == [gone["id"]]
    assert ids(origin="DIRECT", state="IN_EFFECT") == [fuel_bank["id"], old["id"]]
    page = viewer.get(f"{BASE}/expenses", params={"origin": "DIRECT", "limit": 1, "offset": 1}).json()
    assert (page["total"], [i["id"] for i in page["items"]]) == (3, [gone["id"]])
    item = viewer.get(f"{BASE}/expenses", params={"category_id": fuel_id}).json()["items"][0]
    assert (item["fuel"]["vehicle_plate"], item["paid_from_account_name"]) == ("AB 123 CD", "Banco")
    assert viewer.get(f"{BASE}/expenses", params={"limit": 500}).status_code == 422


def test_a_vehicle_deactivated_later_still_shows(as_role, cash, fuel_id, truck, juan, db: Session) -> None:  # type: ignore[no-untyped-def]
    expense = spent(as_role("operator"), cash, fuel_id, fuel=load(truck, juan))
    db.execute(text("UPDATE vehicles SET active = false WHERE id = :v"), {"v": truck})
    db.commit()
    shown = as_role("viewer").get(f"{BASE}/expenses/{expense['id']}").json()
    assert (shown["fuel"]["vehicle_plate"], shown["fuel"]["vehicle_active"]) == ("AB 123 CD", False)
