"""Consumptions, advances and adjustments of an employee's internal account through the API (R8-2, ADR 010).

Signs: + the employee owes the company, - the company owes the employee. The database repeats each rule
(`test_employee_movements_db_invariants.py`); races are in `test_employee_movements_concurrency.py`.
"""

from collections.abc import Callable
from decimal import Decimal
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.parties.models import Employee
from tests.modules.settlements.days import days_ago, today, yesterday
from tests.modules.settlements.employee_finance_support import BASE, EF, error
from tests.modules.settlements.employee_movement_support import (
    area_admin,
    area_operator,
    area_viewer,
    balance,
    body,
    internal_account,
    legs,
    outsider,
    register,
    registered,
    reverse,
)


@pytest.fixture
def funded(post, accounts) -> dict[str, int]:  # type: ignore[no-untyped-def]
    """Caja principal with $500.000 and the bank with $300.000, both since ten days ago."""
    post(
        "OPENING_BALANCE",
        (accounts["main_cash"], "500000"),
        (accounts["opening_balance"], "-500000"),
        business_date=days_ago(10),
    )
    post(
        "OPENING_BALANCE",
        (accounts["bank"], "300000"),
        (accounts["opening_balance"], "-300000"),
        business_date=days_ago(10),
    )
    return accounts  # type: ignore[no-any-return]


@pytest.fixture
def ana(employee: Callable[[str], int]) -> int:
    return employee("Ana Pérez")


def _audit(db: Session, event: str) -> list[Any]:
    db.rollback()
    return list(
        db.execute(
            text("SELECT payload->'after' FROM audit_events WHERE event_type = :e ORDER BY id"), {"e": event}
        ).scalars()
    )


# --- consumption ----------------------------------------------------------------------------------


def test_a_consumption_charges_the_employee_against_the_technical_account(
    user_as: Callable[..., TestClient], ana: int, accounts: dict[str, int], db: Session
) -> None:
    client = area_operator(user_as)
    created = registered(
        client, "CONSUMPTION", ana, "20000", description="  Mercadería   para consumo ", reference="R-1"
    )
    assert (created["kind"], created["direction"], created["status"]) == ("CONSUMPTION", None, "IN_EFFECT")
    assert (created["amount"], created["employee_amount"]) == ("20000.00", "20000.00")
    assert (created["description"], created["reference"]) == ("Mercadería para consumo", "R-1")
    assert created["created_by_name"] == "Usuario Test"
    assert legs(created) == [("EMPLOYEE", "20000.00"), ("EMPLOYEE_CONSUMPTIONS", "-20000.00")]
    assert {leg["account_name"] for leg in created["legs"]} == {"Cuenta interna · Ana Pérez", "Consumos de empleados"}
    assert balance(db, internal_account(db, ana)) == Decimal("20000")
    assert balance(db, accounts["employee_consumptions"]) == Decimal("-20000")
    row = client.get(f"{EF}/employees/{ana}").json()
    assert (row["balance"], row["balance_reading"], row["has_account"]) == ("20000.00", "OWES_COMPANY", True)
    audited = _audit(db, "settlements.employee_finance.employee_movement.created")
    assert len(audited) == 1
    assert (audited[0]["kind"], audited[0]["amount"], audited[0]["operation_id"]) == (
        "CONSUMPTION",
        "20000.00",
        created["operation_id"],
    )


def test_registering_twice_is_one_movement_and_a_reused_id_is_a_conflict(
    user_as: Callable[..., TestClient], ana: int, db: Session
) -> None:
    client = area_operator(user_as)
    request = uuid4()
    first = register(client, "CONSUMPTION", ana, "1500", request=request)
    again = register(client, "CONSUMPTION", ana, "1500", request=request)
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["id"] == first.json()["id"]
    assert error(register(client, "CONSUMPTION", ana, "1600", request=request)) == (409, "idempotency_conflict")
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM settlements.employee_movements")).scalar_one() == 1
    assert balance(db, internal_account(db, ana)) == Decimal("1500")


def test_who_registers_what(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]) -> None:
    advance = {"funding_account_id": funded["main_cash"]}
    # Without the area nothing exists: not for a Rendiciones admin, not for the platform admin.
    for client in (outsider(user_as), user_as("plataforma", platform="admin")):
        for kind, extra in (("CONSUMPTION", {}), ("ADVANCE", advance), ("ADJUSTMENT", {})):
            assert error(register(client, kind, ana, **extra)) == (404, "not_found"), kind
    viewer = area_viewer(user_as)
    for kind, extra in (("CONSUMPTION", {}), ("ADVANCE", advance), ("ADJUSTMENT", {})):
        assert register(viewer, kind, ana, **extra).status_code == 403, kind
    operator = area_operator(user_as)
    assert register(operator, "CONSUMPTION", ana).status_code == 201
    assert register(operator, "ADVANCE", ana, **advance).status_code == 403
    assert register(operator, "ADJUSTMENT", ana).status_code == 403
    admin = area_admin(user_as)
    for kind, extra in (("CONSUMPTION", {}), ("ADVANCE", advance), ("ADJUSTMENT", {})):
        assert register(admin, kind, ana, **extra).status_code == 201, kind


def test_validations(user_as: Callable[..., TestClient], ana: int) -> None:
    client = area_admin(user_as)
    assert error(register(client, "CONSUMPTION", 999999)) == (404, "employee_not_found")
    assert error(register(client, "CONSUMPTION", ana, on=str(today().replace(year=today().year + 1)))) == (
        422,
        "future_business_date",
    )
    for amount in ("0", "-1", "1.234", "1234567890123"):
        assert error(register(client, "CONSUMPTION", ana, amount)) == (422, "validation_error"), amount
    assert error(register(client, "CONSUMPTION", ana, description="   ")) == (422, "description_required")
    assert error(register(client, "CONSUMPTION", ana, description="x" * 501)) == (422, "validation_error")
    assert error(register(client, "CONSUMPTION", ana, reference="x" * 101)) == (422, "validation_error")
    # Fields of another kind are refused, never ignored.
    assert error(register(client, "CONSUMPTION", ana, direction="CHARGE")) == (422, "validation_error")
    no_direction = body("ADJUSTMENT")
    del no_direction["direction"]
    assert error(client.post(f"{EF}/employees/{ana}/adjustments", json=no_direction)) == (422, "validation_error")
    assert error(register(client, "ADJUSTMENT", ana, direction="BONUS")) == (422, "validation_error")
    # A past business date is a late record: allowed, with its own date.
    late = registered(client, "CONSUMPTION", ana, on=str(days_ago(20)))
    assert late["business_date"] == days_ago(20).isoformat()


# --- advance ----------------------------------------------------------------------------------------


def test_an_advance_from_the_cash_box_and_from_the_bank(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    cash = registered(client, "ADVANCE", ana, "100000", funding_account_id=funded["main_cash"])
    assert legs(cash) == [("CASH_BOX", "-100000.00"), ("EMPLOYEE", "100000.00")]
    assert (cash["funding_account_id"], cash["funding_account_name"]) == (funded["main_cash"], "Caja principal")
    bank = registered(client, "ADVANCE", ana, "50000", funding_account_id=funded["bank"])
    assert legs(bank) == [("BANK", "-50000.00"), ("EMPLOYEE", "50000.00")]
    assert balance(db, funded["main_cash"]) == Decimal("400000")
    assert balance(db, funded["bank"]) == Decimal("250000")
    assert balance(db, internal_account(db, ana)) == Decimal("150000")


def test_an_advance_needs_funds_on_its_date_and_every_later_day(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session, post
) -> None:  # type: ignore[no-untyped-def]
    client = area_admin(user_as)
    # The cash box never goes negative.
    refused = register(client, "ADVANCE", ana, "500000.01", funding_account_id=funded["main_cash"])
    assert error(refused) == (409, "insufficient_funds")
    # The bank never goes into an overdraft for an advance: an ordinary debit.
    assert error(register(client, "ADVANCE", ana, "300000.01", funding_account_id=funded["bank"])) == (
        409,
        "insufficient_funds",
    )
    # Retroactive: the box had $500.000 eight days ago, but $450.000 left it yesterday.
    post(
        "CASH_TRANSFER",
        (funded["main_cash"], "-450000"),
        (funded["change_cash"], "450000"),
        business_date=yesterday(),
    )
    late = register(client, "ADVANCE", ana, "60000", on=str(days_ago(8)), funding_account_id=funded["main_cash"])
    assert error(late) == (409, "insufficient_funds")
    assert late.json()["details"]["business_date"] == yesterday().isoformat()
    assert (
        register(
            client, "ADVANCE", ana, "50000", on=str(days_ago(8)), funding_account_id=funded["main_cash"]
        ).status_code
        == 201
    )
    assert balance(db, funded["main_cash"]) == Decimal("0")


def test_an_advance_is_paid_by_an_active_cash_box_or_bank_only(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], employee: Callable[[str], int], db: Session
) -> None:
    client = area_admin(user_as)
    pedro = employee("Pedro")
    registered(client, "CONSUMPTION", pedro, "10")
    for account in (
        funded["opening_balance"],
        funded["employee_consumptions"],
        funded["employee_adjustments"],
        funded["cheque_portfolio"],
        internal_account(db, pedro),
        999999,
    ):
        response = register(client, "ADVANCE", ana, "10", funding_account_id=account)
        assert error(response) == (422, "invalid_funding_account"), account
    db.execute(text("UPDATE settlements.accounts SET active = false WHERE id = :a"), {"a": funded["change_cash"]})
    db.commit()
    assert error(register(client, "ADVANCE", ana, "10", funding_account_id=funded["change_cash"])) == (
        422,
        "invalid_funding_account",
    )
    no_funding = body("ADVANCE")
    assert error(client.post(f"{EF}/employees/{ana}/advances", json=no_funding)) == (422, "validation_error")


def test_reversing_an_advance_returns_the_money_even_if_the_box_is_empty_now(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session, post
) -> None:  # type: ignore[no-untyped-def]
    """Enzo's R8-2 point 3: the reversal credits the cash box or the bank, so it never fails for lack of funds."""
    client = area_admin(user_as)
    advance = registered(client, "ADVANCE", ana, "100000", on=str(days_ago(3)), funding_account_id=funded["main_cash"])
    # Everything left in the box is moved out afterwards: it is empty now.
    post("CASH_TRANSFER", (funded["main_cash"], "-400000"), (funded["change_cash"], "400000"))
    assert balance(db, funded["main_cash"]) == Decimal("0")
    request = uuid4()
    response = reverse(client, advance["id"], "Se cargó a otro empleado", request)
    assert response.status_code == 200, response.text
    reversed_ = response.json()
    assert (reversed_["status"], reversed_["reversal_reason"], reversed_["reversed_by_name"]) == (
        "REVERSED",
        "Se cargó a otro empleado",
        "Usuario Test",
    )
    assert balance(db, funded["main_cash"]) == Decimal("100000")
    assert balance(db, internal_account(db, ana)) == Decimal("0")
    db.rollback()
    reversal = db.execute(
        text("SELECT type, business_date, reverses_operation_id FROM settlements.financial_operations WHERE id = :o"),
        {"o": reversed_["reversal_operation_id"]},
    ).one()
    assert (reversal.type, reversal.business_date, reversal.reverses_operation_id) == (
        "REVERSAL",
        days_ago(3),
        advance["operation_id"],
    )
    # A retry is the same reversal; another reversal is refused; nothing doubles.
    assert reverse(client, advance["id"], "Se cargó a otro empleado", request).status_code == 200
    assert error(reverse(client, advance["id"], "Se cargó a otro empleado", request=uuid4())) == (
        409,
        "employee_movement_already_reversed",
    )
    assert error(reverse(client, advance["id"], "Otro motivo", request)) == (409, "idempotency_conflict")
    assert balance(db, funded["main_cash"]) == Decimal("100000")
    assert _audit(db, "settlements.employee_finance.employee_movement.reversed")[0]["reason"] == (
        "Se cargó a otro empleado"
    )


# --- adjustment -------------------------------------------------------------------------------------


def test_an_adjustment_charges_or_credits_with_a_reason(
    user_as: Callable[..., TestClient], ana: int, accounts: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    charge = registered(client, "ADJUSTMENT", ana, "8000", direction="CHARGE", description="Rotura de envases")
    assert (charge["direction"], charge["employee_amount"]) == ("CHARGE", "8000.00")
    assert legs(charge) == [("EMPLOYEE", "8000.00"), ("EMPLOYEE_ADJUSTMENTS", "-8000.00")]
    credit = registered(client, "ADJUSTMENT", ana, "15000", direction="CREDIT", description="Reconocimiento")
    assert (credit["direction"], credit["employee_amount"]) == ("CREDIT", "-15000.00")
    assert legs(credit) == [("EMPLOYEE", "-15000.00"), ("EMPLOYEE_ADJUSTMENTS", "15000.00")]
    assert balance(db, internal_account(db, ana)) == Decimal("-7000")
    assert balance(db, accounts["employee_adjustments"]) == Decimal("7000")
    row = client.get(f"{EF}/employees/{ana}").json()
    assert (row["balance"], row["balance_reading"]) == ("-7000.00", "COMPANY_OWES")
    assert error(register(client, "ADJUSTMENT", ana, description=" ")) == (422, "description_required")


def test_reversal_needs_the_area_admin_and_a_reason(user_as: Callable[..., TestClient], ana: int, db: Session) -> None:
    consumption = registered(area_operator(user_as), "CONSUMPTION", ana, "3000")
    assert reverse(area_operator(user_as), consumption["id"]).status_code == 403
    assert reverse(area_viewer(user_as), consumption["id"]).status_code == 403
    assert error(reverse(outsider(user_as), consumption["id"])) == (404, "not_found")
    admin = area_admin(user_as)
    assert error(reverse(admin, consumption["id"], "   ")) == (422, "reason_required")
    assert error(reverse(admin, 999999)) == (404, "employee_movement_not_found")
    assert reverse(admin, consumption["id"]).json()["status"] == "REVERSED"
    assert balance(db, internal_account(db, ana)) == Decimal("0")


def test_cajas_never_reverses_a_movement(user_as: Callable[..., TestClient], ana: int, funded: dict[str, int]) -> None:
    advance = registered(area_admin(user_as), "ADVANCE", ana, "1000", funding_account_id=funded["main_cash"])
    boss = user_as("jefe", settlements="admin", area="admin")
    response = boss.post(
        f"{BASE}/operations/{advance['operation_id']}/reverse",
        json={"client_request_id": str(uuid4()), "reason": "Desde Cajas"},
    )
    assert error(response) == (409, "operation_owned_by_document")


# --- inactive employee ------------------------------------------------------------------------------


def test_an_inactive_employee_takes_only_what_documents_the_past(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    row = db.get(Employee, ana)
    assert row is not None
    row.active = False
    db.commit()
    operator = area_operator(user_as)
    assert error(register(operator, "CONSUMPTION", ana, on=str(yesterday()))) == (422, "inactive_employee")
    admin = area_admin(user_as)
    assert error(register(admin, "CONSUMPTION", ana)) == (422, "inactive_employee")
    assert error(register(admin, "ADVANCE", ana, funding_account_id=funded["main_cash"])) == (422, "inactive_employee")
    assert register(admin, "CONSUMPTION", ana, "100", on=str(yesterday())).status_code == 201
    assert (
        register(admin, "ADVANCE", ana, "100", on=str(yesterday()), funding_account_id=funded["main_cash"]).status_code
        == 201
    )
    assert register(admin, "ADJUSTMENT", ana, "50", direction="CREDIT").status_code == 201
    assert balance(db, internal_account(db, ana)) == Decimal("150")


# --- preview ----------------------------------------------------------------------------------------


def test_the_preview_shows_the_legs_and_the_balance_without_writing(
    user_as: Callable[..., TestClient], ana: int, funded: dict[str, int], db: Session
) -> None:
    client = area_admin(user_as)
    registered(client, "CONSUMPTION", ana, "20000")
    preview = client.post(
        f"{EF}/employees/{ana}/movements/preview",
        json={
            "kind": "ADVANCE",
            "business_date": str(today()),
            "amount": "100000",
            "funding_account_id": funded["main_cash"],
        },
    )
    assert preview.status_code == 200, preview.text
    out = preview.json()
    assert [(leg["account_name"], leg["amount"]) for leg in out["legs"]] == [
        ("Caja principal", "-100000.00"),
        ("Cuenta interna · Ana Pérez", "100000.00"),
    ]
    assert (out["balance_before"], out["balance_after"]) == ("20000.00", "120000.00")
    credit = client.post(
        f"{EF}/employees/{ana}/movements/preview",
        json={"kind": "ADJUSTMENT", "business_date": str(today()), "amount": "15000", "direction": "CREDIT"},
    ).json()
    assert (credit["employee_amount"], credit["balance_after"]) == ("-15000.00", "5000.00")
    assert [leg["account_name"] for leg in credit["legs"]] == ["Cuenta interna · Ana Pérez", "Ajustes a empleados"]
    short = client.post(
        f"{EF}/employees/{ana}/movements/preview",
        json={
            "kind": "ADVANCE",
            "business_date": str(today()),
            "amount": "900000",
            "funding_account_id": funded["bank"],
        },
    )
    assert error(short) == (409, "insufficient_funds")
    # Previewing writes nothing; an operator previews a consumption but not an advance.
    db.rollback()
    assert db.execute(text("SELECT count(*) FROM settlements.employee_movements")).scalar_one() == 1
    operator = area_operator(user_as)
    assert (
        operator.post(
            f"{EF}/employees/{ana}/movements/preview",
            json={"kind": "CONSUMPTION", "business_date": str(today()), "amount": "10"},
        ).status_code
        == 200
    )
    assert (
        operator.post(
            f"{EF}/employees/{ana}/movements/preview",
            json={
                "kind": "ADVANCE",
                "business_date": str(today()),
                "amount": "10",
                "funding_account_id": funded["bank"],
            },
        ).status_code
        == 403
    )
