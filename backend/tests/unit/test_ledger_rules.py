"""Pure rules of the ledger: command fingerprint and leg shapes per operation type."""

from datetime import date
from decimal import Decimal

import pytest

from app.modules.settlements.employee_finance.access import AREA
from app.modules.settlements.ledger.rules import RULES, leg_shape_error
from app.modules.settlements.ledger.service import Leg, SourceRef, command_fingerprint

BASE = {
    "actor_id": 1,
    "type": "CASH_TRANSFER",
    "business_date": date(2026, 10, 4),
    "description": None,
    "legs": [Leg(1, Decimal("-500000")), Leg(2, Decimal("500000"))],
    "source": None,
    "reverses_operation_id": None,
}


def test_fingerprint_is_a_stable_sha256() -> None:
    fingerprint = command_fingerprint(**BASE)  # type: ignore[arg-type]
    assert len(fingerprint) == 64
    assert fingerprint == command_fingerprint(**BASE)  # type: ignore[arg-type]


def test_fingerprint_ignores_leg_order_and_amount_format() -> None:
    reordered = {**BASE, "legs": [Leg(2, Decimal("500000.00")), Leg(1, Decimal("-500000.0"))]}
    assert command_fingerprint(**reordered) == command_fingerprint(**BASE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "change",
    [
        {"actor_id": 2},
        {"type": "BANK_DEPOSIT"},
        {"business_date": date(2026, 10, 3)},
        {"description": "motivo"},
        {"legs": [Leg(1, Decimal("-700000")), Leg(2, Decimal("700000"))]},
        {"legs": [Leg(1, Decimal("-500000")), Leg(3, Decimal("500000"))]},
        {"source": SourceRef("settlement", 7)},
        {"reverses_operation_id": 9},
    ],
)
def test_fingerprint_changes_with_the_command(change: dict[str, object]) -> None:
    assert command_fingerprint(**{**BASE, **change}) != command_fingerprint(**BASE)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("type", "kinds", "signs", "ok"),
    [
        ("CASH_TRANSFER", ["CASH_BOX", "CASH_BOX"], [-1, 1], True),
        ("CASH_TRANSFER", ["CASH_BOX", "BANK"], [-1, 1], False),
        ("BANK_DEPOSIT", ["CASH_BOX", "CASH_BOX", "BANK"], [-1, -1, 1], True),
        ("BANK_DEPOSIT", ["CASH_BOX", "BANK"], [1, -1], False),
        ("BANK_WITHDRAWAL", ["BANK", "CASH_BOX"], [-1, 1], True),
        ("OPENING_BALANCE", ["CASH_BOX", "BANK", "OPENING_BALANCE"], [1, 1, -1], True),
        ("OPENING_BALANCE", ["CASH_BOX", "OPENING_BALANCE", "OPENING_BALANCE"], [1, -1, 1], False),
        ("OPENING_BALANCE", ["CASH_BOX", "BANK"], [1, -1], False),
        ("OWNER_CONTRIBUTION", ["BANK", "OWNER_EQUITY"], [1, -1], True),
        ("OWNER_CONTRIBUTION", ["BANK", "OWNER_EQUITY"], [-1, 1], False),
        ("OWNER_WITHDRAWAL", ["CASH_BOX", "OWNER_EQUITY"], [-1, 1], True),
        ("ADJUSTMENT", ["CASH_BOX", "ADJUSTMENT"], [1, -1], True),
        ("ADJUSTMENT", ["CASH_BOX", "ADJUSTMENT"], [-1, 1], True),
        ("ADJUSTMENT", ["CASH_BOX", "CASH_BOX"], [-1, 1], False),
        ("PENDING_COLLECTION", ["PENDING_COLLECTIONS", "CASH_BOX"], [-1, 1], True),
        ("PENDING_COLLECTION", ["PENDING_COLLECTIONS", "BANK"], [-1, 1], True),
        ("PENDING_COLLECTION", ["PENDING_COLLECTIONS", "CASH_BOX"], [1, -1], False),
        ("PENDING_COLLECTION", ["PENDING_COLLECTIONS", "EXPENSE"], [-1, 1], False),
        ("PENDING_WRITE_OFF", ["PENDING_COLLECTIONS", "UNCOLLECTIBLE", "EMPLOYEE"], [-1, 1, 1], True),
        ("PENDING_WRITE_OFF", ["PENDING_COLLECTIONS", "EMPLOYEE"], [-1, 1], True),
        ("PENDING_WRITE_OFF", ["PENDING_COLLECTIONS", "UNCOLLECTIBLE"], [1, -1], False),
        ("PENDING_WRITE_OFF", ["PENDING_COLLECTIONS", "CASH_BOX"], [-1, 1], False),
        # R6-1: items leave cash boxes and banks, exactly one counter-leg of a payment concept enters.
        ("PAYMENT", ["CASH_BOX", "MERCHANDISE_PAYMENTS"], [-1, 1], True),
        ("PAYMENT", ["CASH_BOX", "BANK", "EXPENSE"], [-1, -1, 1], True),
        ("PAYMENT", ["CASH_BOX", "CASH_BOX", "THIRD_PARTY_REFUNDS"], [-1, -1, 1], True),
        ("PAYMENT", ["BANK", "OTHER_PAYMENTS"], [-1, 1], True),
        ("PAYMENT", ["CASH_BOX", "MERCHANDISE_PAYMENTS", "EXPENSE"], [-1, 1, 1], False),
        ("PAYMENT", ["CASH_BOX", "BANK"], [-1, 1], False),
        ("PAYMENT", ["CASH_BOX", "MERCHANDISE_PAYMENTS"], [1, -1], False),
        # R6-2: cheques of the portfolio leave it, one leg each, never enter it through a payment.
        ("PAYMENT", ["CHEQUE_PORTFOLIO", "MERCHANDISE_PAYMENTS"], [-1, 1], True),
        ("PAYMENT", ["CASH_BOX", "BANK", "CHEQUE_PORTFOLIO", "CHEQUE_PORTFOLIO", "EXPENSE"], [-1, -1, -1, -1, 1], True),
        ("PAYMENT", ["CHEQUE_PORTFOLIO", "MERCHANDISE_PAYMENTS"], [1, -1], False),
        ("PAYMENT", ["CHEQUES_IN_TRANSIT", "MERCHANDISE_PAYMENTS"], [-1, 1], False),
        ("PAYMENT", ["CASH_BOX", "UNCOLLECTIBLE"], [-1, 1], False),
        # R8-2: movements of an employee's internal account, one internal account and one counterpart each.
        ("EMPLOYEE_CONSUMPTION", ["EMPLOYEE", "EMPLOYEE_CONSUMPTIONS"], [1, -1], True),
        ("EMPLOYEE_CONSUMPTION", ["EMPLOYEE", "EMPLOYEE_CONSUMPTIONS"], [-1, 1], False),
        ("EMPLOYEE_CONSUMPTION", ["EMPLOYEE", "EMPLOYEE", "EMPLOYEE_CONSUMPTIONS"], [1, 1, -1], False),
        ("EMPLOYEE_CONSUMPTION", ["EMPLOYEE", "EXPENSE"], [1, -1], False),
        ("EMPLOYEE_ADVANCE", ["CASH_BOX", "EMPLOYEE"], [-1, 1], True),
        ("EMPLOYEE_ADVANCE", ["BANK", "EMPLOYEE"], [-1, 1], True),
        ("EMPLOYEE_ADVANCE", ["CASH_BOX", "EMPLOYEE"], [1, -1], False),
        ("EMPLOYEE_ADVANCE", ["CASH_BOX", "BANK", "EMPLOYEE"], [-1, -1, 1], False),
        ("EMPLOYEE_ADVANCE", ["CHEQUE_PORTFOLIO", "EMPLOYEE"], [-1, 1], False),
        ("EMPLOYEE_ADVANCE", ["EMPLOYEE_CONSUMPTIONS", "EMPLOYEE"], [-1, 1], False),
        ("EMPLOYEE_ADJUSTMENT", ["EMPLOYEE", "EMPLOYEE_ADJUSTMENTS"], [1, -1], True),
        ("EMPLOYEE_ADJUSTMENT", ["EMPLOYEE", "EMPLOYEE_ADJUSTMENTS"], [-1, 1], True),
        ("EMPLOYEE_ADJUSTMENT", ["EMPLOYEE", "ADJUSTMENT"], [1, -1], False),
        ("EMPLOYEE_ADJUSTMENT", ["CASH_BOX", "EMPLOYEE_ADJUSTMENTS"], [1, -1], False),
        # R8-3: a loan, from one cash box or bank to the internal account; installments have no legs.
        ("EMPLOYEE_LOAN", ["CASH_BOX", "EMPLOYEE"], [-1, 1], True),
        ("EMPLOYEE_LOAN", ["BANK", "EMPLOYEE"], [-1, 1], True),
        ("EMPLOYEE_LOAN", ["CASH_BOX", "EMPLOYEE"], [1, -1], False),
        ("EMPLOYEE_LOAN", ["CASH_BOX", "BANK", "EMPLOYEE"], [-1, -1, 1], False),
        ("EMPLOYEE_LOAN", ["CASH_BOX", "EMPLOYEE", "EMPLOYEE"], [-1, 1, 1], False),
        ("EMPLOYEE_LOAN", ["CHEQUE_PORTFOLIO", "EMPLOYEE"], [-1, 1], False),
        ("EMPLOYEE_LOAN", ["OUTLET_ADVANCES", "EMPLOYEE"], [-1, 1], False),
        ("EMPLOYEE_LOAN", ["EMPLOYEE_ADJUSTMENTS", "EMPLOYEE"], [-1, 1], False),
    ],
)
def test_leg_shapes(type: str, kinds: list[str], signs: list[int], ok: bool) -> None:
    assert (leg_shape_error(RULES[type], kinds, signs) is None) is ok


def test_employee_movements_are_authorized_by_the_area() -> None:
    """R8-2: a role in Rendiciones never authorizes them; the protected area does (ADR 010)."""
    assert {t for t, rule in RULES.items() if rule.area is not None} == {
        "EMPLOYEE_CONSUMPTION",
        "EMPLOYEE_ADVANCE",
        "EMPLOYEE_ADJUSTMENT",
        "EMPLOYEE_LOAN",
    }
    assert {t: RULES[t].min_role for t in RULES if RULES[t].area is not None} == {
        "EMPLOYEE_CONSUMPTION": "operator",
        "EMPLOYEE_ADVANCE": "admin",
        "EMPLOYEE_ADJUSTMENT": "admin",
        "EMPLOYEE_LOAN": "admin",
    }
    assert {rule.area for rule in RULES.values() if rule.area is not None} == {AREA}


def test_adjustments_and_partner_movements_need_admin() -> None:
    assert {t for t, rule in RULES.items() if rule.min_role == "admin" and rule.area is None} == {
        "OPENING_BALANCE",
        "OWNER_CONTRIBUTION",
        "OWNER_WITHDRAWAL",
        "ADJUSTMENT",
        "PENDING_WRITE_OFF",
        "CHEQUE_OPENING_BALANCE",
        "CHEQUE_OWNER_CONTRIBUTION",
        "CHEQUE_RETURN",
    }
    assert {t for t, rule in RULES.items() if rule.min_role == "operator" and rule.area is None} == {
        "CASH_TRANSFER",
        "BANK_DEPOSIT",
        "BANK_WITHDRAWAL",
        "SETTLEMENT_CLOSE",
        "PENDING_COLLECTION",
        "CHEQUE_DEPOSIT",
        "CHEQUE_CREDIT",
        "CHEQUE_REJECTION",
        "DIRECT_EXPENSE",
        "PAYMENT",
        "OUTLET_REMITTANCE",
    }
