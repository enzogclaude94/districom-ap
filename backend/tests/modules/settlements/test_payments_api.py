"""Payments to third parties (R6-1) over HTTP: concepts and their counter-accounts, means of payment (cash,
bank, several), the exact legs, funds and «Banco en descubierto», business dates, third parties, idempotency,
reversal, the descriptive correction, permissions and the list. Database rules are in
`test_payments_db_invariants.py`, races in `test_payments_concurrency.py`."""

from datetime import timedelta
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.settlements.ledger.rules import DOCUMENT_TYPES, EXTERNAL_BANK_DEBITS, REVERSIBLE_DOCUMENT_TYPES
from tests.modules.settlements.direct_support import BASE, T, account_id, balance, error, fund, legs
from tests.modules.settlements.fuel_support import fuel_category
from tests.modules.settlements.payment_support import (
    THIRD_PARTIES,
    cash,
    counts,
    paid,
    pay,
    reverse_payment,
    third_party,
    transfer,
)
from tests.modules.settlements.test_cheque_overdraft import _at_minus


@pytest.fixture
def admin(as_role) -> TestClient:  # type: ignore[no-untyped-def]
    return as_role("admin")  # type: ignore[no-any-return]


@pytest.fixture
def box(admin: TestClient) -> int:
    fund(admin, "main_cash", "500000", T - timedelta(days=10))
    return account_id(admin, "main_cash")


@pytest.fixture
def change(admin: TestClient) -> int:
    fund(admin, "change_cash", "50000", T - timedelta(days=10))
    return account_id(admin, "change_cash")


@pytest.fixture
def bank(admin: TestClient) -> int:
    fund(admin, "bank", "1000000", T - timedelta(days=10))
    return account_id(admin, "bank")


@pytest.fixture
def perez(admin: TestClient) -> int:
    return third_party(admin)


@pytest.fixture
def tolls(admin: TestClient) -> int:
    return admin.post(f"{BASE}/expense-categories", json={"name": "Peajes"}).json()["id"]  # type: ignore[no-any-return]


def _audit(db: Session, event: str) -> list[dict[str, Any]]:
    rows = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = :e ORDER BY id"),
        {"e": f"settlements.payment.{event}"},
    ).scalars()
    return list(rows)


# --- concepts and counter-accounts --------------------------------------------------------------------


def test_merchandise_paid_in_cash(as_role, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    payment = paid(operator, perez, [cash(box, "100000", "Recibo 0001-123")], amount="100000", reference="FC A 12")
    assert (payment["state"], payment["concept"], payment["amount"]) == ("IN_EFFECT", "MERCHANDISE", "100000.00")
    assert (payment["third_party_name"], payment["third_party_tax_id_display"]) == (
        "Distribuidora Pérez S.A.",
        "30-71234567-1",
    )
    assert payment["counter_account_name"] == "Pagos por mercadería"
    assert (payment["reference"], payment["items"][0]["reference"]) == ("FC A 12", "Recibo 0001-123")
    [item] = payment["items"]
    assert (item["method"], item["account_name"], item["account_kind"], item["amount"]) == (
        "CASH",
        "Caja principal",
        "CASH_BOX",
        "100000.00",
    )
    operation = payment["operation"]
    assert (operation["type"], operation["source_type"], operation["source_id"], operation["business_date"]) == (
        "PAYMENT",
        "payment",
        payment["id"],
        T.isoformat(),
    )
    assert legs(db, operation["id"]) == [
        ("main_cash", Decimal("-100000.00")),
        ("merchandise_payments", Decimal("100000.00")),
    ]
    assert balance(operator, "main_cash") == Decimal("400000.00")
    assert balance(operator, "merchandise_payments") == Decimal("100000.00")
    [created] = _audit(db, "created")
    assert created["after"]["operation_id"] == operation["id"]
    assert created["after"]["items"] == [
        {"method": "CASH", "account_id": box, "cheque_id": None, "amount": "100000.00", "reference": "Recibo 0001-123"}
    ]


def test_an_expense_paid_by_transfer_goes_to_operating_expenses(as_role, bank, perez, tolls, db) -> None:  # type: ignore[no-untyped-def]
    payment = paid(
        as_role("operator"), perez, [transfer(bank, "7500.50")], concept="EXPENSE", expense_category_id=tolls
    )
    assert (payment["expense_category_name"], payment["counter_account_name"]) == ("Peajes", "Gastos operativos")
    assert legs(db, payment["operation_id"]) == [("bank", Decimal("-7500.50")), ("expense", Decimal("7500.50"))]
    # A payment is not a direct expense: no expense row is created (PAYMENT is not DIRECT_EXPENSE).
    assert db.execute(text("SELECT count(*) FROM settlements.expenses")).scalar_one() == 0


def test_an_expense_needs_an_active_category_that_is_not_fuel(as_role, admin, box, perez, tolls, db) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    before = counts(db)
    assert error(pay(operator, perez, [cash(box, "100")], concept="EXPENSE")) == (422, "expense_category_required")
    fuel = pay(operator, perez, [cash(box, "100")], concept="EXPENSE", expense_category_id=fuel_category(db))
    assert error(fuel) == (422, "fuel_not_allowed_in_payment")
    as_role("admin").patch(f"{BASE}/expense-categories/{tolls}", json={"active": False})
    inactive = pay(as_role("operator"), perez, [cash(box, "100")], concept="EXPENSE", expense_category_id=tolls)
    assert error(inactive) == (422, "invalid_expense_category")
    assert counts(db) == before


@pytest.mark.parametrize("concept", ["MERCHANDISE", "REFUND"])
def test_merchandise_and_refund_take_no_category(as_role, box, perez, tolls, concept: str) -> None:  # type: ignore[no-untyped-def]
    response = pay(as_role("operator"), perez, [cash(box, "100")], concept=concept, expense_category_id=tolls)
    assert error(response) == (422, "expense_category_not_allowed")


def test_a_refund_to_a_third_party(as_role, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    payment = paid(as_role("operator"), perez, [cash(box, "2500")], concept="REFUND", description="Envases de más")
    assert payment["counter_account_name"] == "Devoluciones a terceros"
    assert legs(db, payment["operation_id"]) == [
        ("main_cash", Decimal("-2500.00")),
        ("third_party_refunds", Decimal("2500.00")),
    ]


def test_other_is_recorded_by_an_admin_with_a_description(admin, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    payment = paid(admin, perez, [cash(box, "999")], concept="OTHER", description="Reintegro de flete compartido")
    assert payment["counter_account_name"] == "Otros pagos"
    assert legs(db, payment["operation_id"]) == [
        ("main_cash", Decimal("-999.00")),
        ("other_payments", Decimal("999.00")),
    ]
    for blank in (None, "", "   "):
        response = pay(admin, perez, [cash(box, "1")], concept="OTHER", description=blank)
        assert error(response) == (422, "description_required"), blank


def test_other_by_an_operator_is_refused(as_role, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    before = counts(db)
    response = pay(as_role("operator"), perez, [cash(box, "999")], concept="OTHER", description="Algo")
    assert error(response) == (403, "forbidden")
    assert counts(db) == before


# --- means of payment ----------------------------------------------------------------------------------


def test_cash_and_bank_in_one_payment(as_role, box, bank, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    payment = paid(operator, perez, [cash(box, "100000"), transfer(bank, "200000", "Transf. 778")], amount="300000")
    assert payment["amount"] == "300000.00"
    assert [(i["method"], i["account_name"], i["amount"]) for i in payment["items"]] == [
        ("CASH", "Caja principal", "100000.00"),
        ("TRANSFER", "Banco", "200000.00"),
    ]
    assert legs(db, payment["operation_id"]) == [
        ("bank", Decimal("-200000.00")),
        ("main_cash", Decimal("-100000.00")),
        ("merchandise_payments", Decimal("300000.00")),
    ]
    assert balance(operator, "main_cash") == Decimal("400000.00")
    assert balance(operator, "bank") == Decimal("800000.00")


def test_two_cash_boxes_in_one_payment(as_role, box, change, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    payment = paid(as_role("operator"), perez, [cash(box, "30000"), cash(change, "20000")])
    assert payment["amount"] == "50000.00"  # without a total sent, the sum of the items
    assert legs(db, payment["operation_id"]) == [
        ("main_cash", Decimal("-30000.00")),
        ("change_cash", Decimal("-20000.00")),
        ("merchandise_payments", Decimal("50000.00")),
    ]


def test_the_same_account_twice_is_refused(as_role, box, bank, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    before = counts(db)
    assert error(pay(operator, perez, [cash(box, "100"), cash(box, "200")])) == (422, "duplicate_payment_account")
    twice = pay(operator, perez, [transfer(bank, "100"), transfer(bank, "200")])
    assert error(twice) == (422, "duplicate_payment_account")
    assert counts(db) == before


def test_the_total_is_exactly_the_sum_of_the_items(as_role, box, bank, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    before = counts(db)
    for total in ("300000.01", "299999.99", "100000"):
        response = pay(operator, perez, [cash(box, "100000"), transfer(bank, "200000")], amount=total)
        assert error(response) == (422, "payment_amount_mismatch"), total
        assert response.json()["details"] == {
            "amount": str(Decimal(total).quantize(Decimal("0.01"))),
            "items_total": "300000.00",
        }
    assert counts(db) == before


def test_means_need_the_right_kind_of_active_account(admin, as_role, box, bank, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    before = counts(db)
    assert error(pay(operator, perez, [cash(bank, "100")])) == (422, "invalid_payment_account")
    assert error(pay(operator, perez, [transfer(box, "100")])) == (422, "invalid_payment_account")
    assert error(pay(operator, perez, [cash(account_id(admin, "merchandise_payments"), "100")])) == (
        422,
        "invalid_payment_account",
    )
    assert error(pay(operator, perez, [cash(999999, "100")])) == (422, "invalid_payment_account")
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE code = 'change_cash'"))
    db.commit()
    assert error(pay(operator, perez, [cash(account_id(admin, "change_cash"), "100")])) == (
        422,
        "invalid_payment_account",
    )
    assert counts(db) == before


def test_a_payment_needs_at_least_one_item_with_a_positive_amount(as_role, box, perez) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    assert error(pay(operator, perez, [])) == (422, "payment_items_required")
    assert error(pay(operator, perez, [cash(box, "0")])) == (422, "validation_error")
    assert error(pay(operator, perez, [cash(box, "-5")])) == (422, "validation_error")
    assert error(pay(operator, perez, [cash(box, "1.001")])) == (422, "validation_error")


# --- funds -----------------------------------------------------------------------------------------------


def test_a_cash_box_never_goes_negative(as_role, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    before = counts(db)
    response = pay(operator, perez, [cash(box, "500000.01")])
    assert error(response) == (409, "insufficient_funds")
    assert counts(db) == before  # nothing half written: no payment, no item, no operation
    assert balance(operator, "main_cash") == Decimal("500000.00")
    assert paid(operator, perez, [cash(box, "500000")])["amount"] == "500000.00"  # exactly to zero is fine
    assert balance(operator, "main_cash") == Decimal("0.00")


def test_when_one_mean_lacks_funds_nothing_is_paid(as_role, box, change, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    before = counts(db)
    response = pay(operator, perez, [cash(box, "1000"), cash(change, "50000.01")])
    assert error(response) == (409, "insufficient_funds")
    assert response.json()["details"]["account_id"] == change
    assert counts(db) == before
    assert balance(operator, "main_cash") == Decimal("500000.00")


def test_a_payment_never_creates_nor_deepens_an_overdraft(admin, perez, db: Session, driver) -> None:  # type: ignore[no-untyped-def]
    ana = driver("Ana Cobranzas")
    _at_minus(admin, ana, "50000")
    assert balance(admin, "bank") == Decimal("-50000.00")
    bank = account_id(admin, "bank")
    before = counts(db)
    assert error(pay(admin, perez, [transfer(bank, "1")])) == (409, "insufficient_funds")
    assert counts(db) == before
    assert balance(admin, "bank") == Decimal("-50000.00")


def test_a_past_date_checks_the_funds_of_that_day_and_every_later_one(as_role, admin, perez, db) -> None:  # type: ignore[no-untyped-def]
    fund(admin, "main_cash", "100000", T - timedelta(days=10))
    box = account_id(admin, "main_cash")
    # Before the money existed.
    operator = as_role("operator")
    assert error(pay(operator, perez, [cash(box, "1")], on=T - timedelta(days=11))) == (409, "insufficient_funds")
    # $80.000 left the box on T-2: a payment of $50.000 on T-5 would leave T-2 at -$30.000.
    change = account_id(admin, "change_cash")
    admin = as_role("admin")
    moved = admin.post(
        f"{BASE}/operations/cash-transfer",
        json={
            "client_request_id": str(uuid4()),
            "business_date": (T - timedelta(days=2)).isoformat(),
            "from_account_id": box,
            "to_account_id": change,
            "amount": "80000",
        },
    )
    assert moved.status_code == 201, moved.text
    operator = as_role("operator")
    response = pay(operator, perez, [cash(box, "50000")], on=T - timedelta(days=5))
    assert error(response) == (409, "insufficient_funds")
    assert response.json()["details"]["business_date"] == (T - timedelta(days=2)).isoformat()
    # $20.000 fits on every day from T-5 on.
    payment = paid(operator, perez, [cash(box, "20000")], on=T - timedelta(days=5))
    assert (payment["business_date"], payment["operation"]["business_date"]) == (
        (T - timedelta(days=5)).isoformat(),
    ) * 2
    assert balance(operator, "main_cash", T - timedelta(days=5)) == Decimal("80000.00")


def test_a_future_date_is_refused(as_role, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    before = counts(db)
    response = pay(as_role("operator"), perez, [cash(box, "100")], on=T + timedelta(days=1))
    assert error(response) == (422, "future_business_date")
    assert counts(db) == before


def test_payment_is_an_ordinary_document_operation() -> None:
    """Cajas never reverses it, its own flow does, and the bank is never overdrawn for it."""
    assert "PAYMENT" in DOCUMENT_TYPES
    assert "PAYMENT" in REVERSIBLE_DOCUMENT_TYPES
    assert "PAYMENT" not in EXTERNAL_BANK_DEBITS


# --- third parties -------------------------------------------------------------------------------------


def test_the_third_party_must_exist_and_be_active(admin, as_role, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    old = paid(operator, perez, [cash(box, "100")])
    assert error(pay(operator, 999999, [cash(box, "100")])) == (422, "invalid_third_party")
    as_role("admin").patch(f"{THIRD_PARTIES}/{perez}", json={"active": False})
    operator = as_role("operator")
    before = counts(db)
    assert error(pay(operator, perez, [cash(box, "100")])) == (422, "third_party_inactive")
    assert counts(db) == before
    # The old payment keeps its third party, visible as deactivated.
    shown = operator.get(f"{BASE}/payments/{old['id']}").json()
    assert (shown["third_party_name"], shown["third_party_active"]) == ("Distribuidora Pérez S.A.", False)


def test_an_operator_creates_the_third_party_and_pays_it(as_role, box, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    created = operator.post(f"{BASE}/third-parties", json={"name": "Gomería El Tano"})
    assert created.status_code == 201, created.text
    payment = paid(operator, created.json()["id"], [cash(box, "15000")], concept="REFUND")
    assert payment["third_party_name"] == "Gomería El Tano"


# --- idempotency ----------------------------------------------------------------------------------------


def test_the_same_request_is_one_payment(as_role, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    body = {
        "client_request_id": str(uuid4()),
        "business_date": T.isoformat(),
        "third_party_id": perez,
        "concept": "MERCHANDISE",
        "amount": "1000",
        "items": [cash(box, "1000")],
    }
    first = operator.post(f"{BASE}/payments", json=body)
    assert first.status_code == 201
    after_first = counts(db)
    again = operator.post(f"{BASE}/payments", json=body)
    assert again.status_code == 200
    assert again.json()["id"] == first.json()["id"]
    assert counts(db) == after_first
    assert balance(operator, "main_cash") == Decimal("499000.00")
    other = operator.post(f"{BASE}/payments", json={**body, "items": [cash(box, "999")], "amount": "999"})
    assert error(other) == (409, "idempotency_conflict")
    assert counts(db) == after_first


# --- reversal ----------------------------------------------------------------------------------------


def test_an_admin_reverses_the_whole_payment_on_its_original_date(admin, as_role, box, bank, perez, db) -> None:  # type: ignore[no-untyped-def]
    operator = as_role("operator")
    on = T - timedelta(days=3)
    payment = paid(operator, perez, [cash(box, "100000"), transfer(bank, "200000")], on=on)
    assert error(reverse_payment(operator, payment["id"])) == (403, "forbidden")
    admin = as_role("admin")
    reversed_ = reverse_payment(admin, payment["id"])
    assert reversed_.status_code == 201, reversed_.text
    body = reversed_.json()
    assert (body["state"], body["reversal_reason"], body["reversed_by_name"]) == (
        "REVERSED",
        "Se pagó al tercero equivocado",
        "Usuario Test",
    )
    reversal = body["reversal"]
    assert (reversal["type"], reversal["business_date"], reversal["reverses_operation_id"]) == (
        "REVERSAL",
        on.isoformat(),
        payment["operation_id"],
    )
    assert reversal["created_at"] > payment["operation"]["created_at"]
    assert legs(db, reversal["id"]) == [
        ("merchandise_payments", Decimal("-300000.00")),
        ("main_cash", Decimal("100000.00")),
        ("bank", Decimal("200000.00")),
    ]
    assert balance(admin, "main_cash") == Decimal("500000.00")
    assert balance(admin, "bank") == Decimal("1000000.00")
    assert balance(admin, "merchandise_payments") == Decimal("0.00")
    assert error(reverse_payment(admin, payment["id"])) == (409, "payment_already_reversed")
    [event] = _audit(db, "reversed")
    assert event["after"]["reason"] == "Se pagó al tercero equivocado"


def test_a_reversal_is_idempotent(admin, box, perez) -> None:  # type: ignore[no-untyped-def]
    payment = paid(admin, perez, [cash(box, "100")])
    request = str(uuid4())
    first = reverse_payment(admin, payment["id"], client_request_id=request)
    again = reverse_payment(admin, payment["id"], client_request_id=request)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["reversal_operation_id"] == first.json()["reversal_operation_id"]
    other = reverse_payment(admin, payment["id"], client_request_id=request, reason="Otro motivo")
    assert error(other) == (409, "idempotency_conflict")


def test_a_reversal_needs_a_reason(admin, box, perez) -> None:  # type: ignore[no-untyped-def]
    payment = paid(admin, perez, [cash(box, "100")])
    assert error(reverse_payment(admin, payment["id"], reason="   ")) == (422, "reason_required")


def test_cajas_never_reverses_a_payment(admin, box, perez) -> None:  # type: ignore[no-untyped-def]
    payment = paid(admin, perez, [cash(box, "100")])
    response = admin.post(
        f"{BASE}/operations/{payment['operation_id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Desde Cajas"},
    )
    assert error(response) == (409, "operation_owned_by_document")


def test_a_financial_correction_is_a_reversal_and_a_new_payment(admin, as_role, box, bank, perez) -> None:  # type: ignore[no-untyped-def]
    wrong = paid(as_role("operator"), perez, [cash(box, "1000")])
    reverse_payment(as_role("admin"), wrong["id"], reason="Era por transferencia")
    right = paid(as_role("operator"), perez, [transfer(bank, "1000")])
    assert right["id"] != wrong["id"]
    viewer = as_role("viewer")
    assert balance(viewer, "main_cash") == Decimal("500000.00")
    assert balance(viewer, "bank") == Decimal("999000.00")
    assert balance(viewer, "merchandise_payments") == Decimal("1000.00")


# --- descriptive correction --------------------------------------------------------------------------------


def test_an_admin_corrects_reference_description_and_item_references(admin, as_role, box, bank, perez, db) -> None:  # type: ignore[no-untyped-def]
    payment = paid(as_role("operator"), perez, [cash(box, "100"), transfer(bank, "200", "T-1")], reference="FC 1")
    cash_item, transfer_item = payment["items"]
    body = {
        "reason": "Número de factura mal tipeado",
        "reference": "FC 0001-00000012",
        "description": "Pedido semanal",
        "items": [{"id": transfer_item["id"], "reference": "Transf. 00778"}],
    }
    assert error(as_role("operator").post(f"{BASE}/payments/{payment['id']}/descriptive", json=body)) == (
        403,
        "forbidden",
    )
    corrected = as_role("admin").post(f"{BASE}/payments/{payment['id']}/descriptive", json=body)
    assert corrected.status_code == 200, corrected.text
    out = corrected.json()
    assert (out["reference"], out["description"]) == ("FC 0001-00000012", "Pedido semanal")
    assert [i["reference"] for i in out["items"]] == [None, "Transf. 00778"]
    assert (out["amount"], out["operation_id"]) == (payment["amount"], payment["operation_id"])
    [event] = _audit(db, "corrected")
    assert event["before"] == {
        "reference": "FC 1",
        "description": None,
        "items": [{"id": transfer_item["id"], "reference": "T-1"}],
    }
    assert event["after"]["reason"] == "Número de factura mal tipeado"
    assert [e["event_type"] for e in out["history"]] == ["created", "corrected"]
    assert cash_item["reference"] is None


def test_a_descriptive_correction_never_touches_money(admin, box, perez, db: Session) -> None:  # type: ignore[no-untyped-def]
    payment = paid(admin, perez, [cash(box, "100")])
    url = f"{BASE}/payments/{payment['id']}/descriptive"
    for field, value in (
        ("amount", "200"),
        ("third_party_id", perez),
        ("business_date", T.isoformat()),
        ("concept", "REFUND"),
    ):
        assert error(admin.post(url, json={"reason": "x", field: value})) == (422, "validation_error"), field
    assert error(admin.post(url, json={"reason": "x"})) == (422, "nothing_to_correct")
    other_item = admin.post(url, json={"reason": "x", "items": [{"id": 999999, "reference": "x"}]})
    assert error(other_item) == (422, "invalid_payment_item")
    other = paid(admin, perez, [cash(box, "1")], concept="OTHER", description="Algo")
    blank = admin.post(f"{BASE}/payments/{other['id']}/descriptive", json={"reason": "x", "description": " "})
    assert error(blank) == (422, "description_required")
    reverse_payment(admin, payment["id"])
    assert error(admin.post(url, json={"reason": "x", "reference": "R"})) == (409, "payment_reversed")


# --- permissions and the list ---------------------------------------------------------------------------


def test_a_viewer_reads_and_does_not_pay(as_role, box, perez) -> None:  # type: ignore[no-untyped-def]
    payment = paid(as_role("operator"), perez, [cash(box, "100")])
    viewer = as_role("viewer")
    assert viewer.get(f"{BASE}/payments").json()["total"] == 1
    assert viewer.get(f"{BASE}/payments/{payment['id']}").status_code == 200
    assert error(pay(viewer, perez, [cash(box, "100")]))[0] == 403
    assert error(viewer.post(f"{BASE}/third-parties", json={"name": "X"}))[0] == 403


def test_without_rendiciones_there_are_no_payments(client, login, box, perez) -> None:  # type: ignore[no-untyped-def]
    login("viewer")
    assert client.get(f"{BASE}/payments").status_code == 403


def test_the_list_filters_and_sums_only_what_is_in_effect(admin, as_role, box, bank, perez, tolls) -> None:  # type: ignore[no-untyped-def]
    other = third_party(admin, "Fletes Rodríguez", None)
    a = paid(admin, perez, [cash(box, "100")], on=T - timedelta(days=2))
    b = paid(admin, perez, [transfer(bank, "200")], concept="EXPENSE", expense_category_id=tolls)
    c = paid(admin, other, [cash(box, "300"), transfer(bank, "400")])
    reverse_payment(admin, a["id"])
    viewer = as_role("viewer")

    def ids(**params: Any) -> list[int]:
        page = viewer.get(f"{BASE}/payments", params=params).json()
        return [p["id"] for p in page["items"]]

    page = viewer.get(f"{BASE}/payments").json()
    assert ([p["id"] for p in page["items"]], page["total"], page["amount_in_effect"]) == (
        [c["id"], b["id"], a["id"]],
        3,
        "900.00",
    )
    assert ids(third_party_id=perez) == [b["id"], a["id"]]
    assert ids(concept="EXPENSE") == [b["id"]]
    assert ids(state="REVERSED") == [a["id"]]
    assert ids(state="IN_EFFECT") == [c["id"], b["id"]]
    assert ids(account_id=box) == [c["id"], a["id"]]
    assert ids(date_from=T.isoformat()) == [c["id"], b["id"]]
    assert ids(date_to=(T - timedelta(days=1)).isoformat()) == [a["id"]]
    assert ids(limit=1, offset=1) == [b["id"]]


def test_the_operation_in_cajas_points_at_its_payment(admin, box, perez) -> None:  # type: ignore[no-untyped-def]
    payment = paid(admin, perez, [cash(box, "100")])
    movements = admin.get(f"{BASE}/accounts/{box}/movements").json()["items"]
    mine = next(m for m in movements if m["operation_id"] == payment["operation_id"])
    assert (mine["operation_type"], mine["source_type"], mine["source_id"]) == ("PAYMENT", "payment", payment["id"])
    assert mine["description"] == f"Pago n.º {payment['id']} · Pago por mercadería · Distribuidora Pérez S.A."
