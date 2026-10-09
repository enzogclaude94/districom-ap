"""Pure rules of the ledger: command fingerprint and leg shapes per operation type."""

from datetime import date
from decimal import Decimal

import pytest

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
        ("PAYMENT", ["CHEQUE_PORTFOLIO", "MERCHANDISE_PAYMENTS"], [-1, 1], False),
        ("PAYMENT", ["CASH_BOX", "UNCOLLECTIBLE"], [-1, 1], False),
    ],
)
def test_leg_shapes(type: str, kinds: list[str], signs: list[int], ok: bool) -> None:
    assert (leg_shape_error(RULES[type], kinds, signs) is None) is ok


def test_adjustments_and_partner_movements_need_admin() -> None:
    assert {t for t, rule in RULES.items() if rule.min_role == "admin"} == {
        "OPENING_BALANCE",
        "OWNER_CONTRIBUTION",
        "OWNER_WITHDRAWAL",
        "ADJUSTMENT",
        "PENDING_WRITE_OFF",
        "CHEQUE_OPENING_BALANCE",
        "CHEQUE_OWNER_CONTRIBUTION",
        "CHEQUE_RETURN",
    }
    assert {t for t, rule in RULES.items() if rule.min_role == "operator"} == {
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
    }
