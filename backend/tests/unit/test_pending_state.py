"""Derived state of a pending collection (Enzo's R3 decision 3): every combination, checked by hand."""

from decimal import Decimal

import pytest

from app.modules.settlements.pending.state import financial, load_change

D = Decimal


@pytest.mark.parametrize(
    ("in_effect", "voided", "collected", "resolved", "state", "balance"),
    [
        (None, False, D(0), D(0), "DRAFT", None),
        (None, True, D(0), D(0), "VOIDED", None),
        # Marked for removal during a correction: still in effect, so its financial state is not VOIDED.
        (D(100000), True, D(0), D(0), "OPEN", D(100000)),
        (D(100000), False, D(0), D(0), "OPEN", D(100000)),
        (D(100000), False, D(60000), D(0), "PARTIAL", D(40000)),
        (D(100000), False, D(0), D(30000), "PARTIAL", D(70000)),
        (D(100000), False, D(60000), D(10000), "PARTIAL", D(30000)),
        (D(100000), False, D(100000), D(0), "COLLECTED", D(0)),
        (D(100000), False, D(0), D(100000), "RESOLVED", D(0)),
        (D(100000), False, D(70000), D(30000), "RESOLVED", D(0)),
    ],
)
def test_financial_state(
    in_effect: Decimal | None, voided: bool, collected: Decimal, resolved: Decimal, state: str, balance: Decimal | None
) -> None:
    result = financial(amount_in_effect=in_effect, voided=voided, collected=collected, resolved=resolved)
    assert (result.state, result.balance) == (state, balance)
    assert result.applied == collected + resolved


@pytest.mark.parametrize(
    ("being_corrected", "in_effect", "amount", "customer", "voided", "change"),
    [
        (False, None, D(100), "A", False, None),
        (False, (D(100), "A"), D(70), "A", False, None),
        (True, None, D(100), "A", False, "ADDED"),
        (True, None, D(100), "A", True, None),
        (True, (D(100), "A"), D(100), "A", False, None),
        (True, (D(100), "A"), D(70), "A", False, "MODIFIED"),
        (True, (D(100), "A"), D(100), "B", False, "MODIFIED"),
        (True, (D(100), "A"), D(100), "A", True, "MARKED_FOR_REMOVAL"),
    ],
)
def test_load_change(
    being_corrected: bool,
    in_effect: tuple[Decimal, str] | None,
    amount: Decimal,
    customer: str,
    voided: bool,
    change: str | None,
) -> None:
    assert (
        load_change(
            being_corrected=being_corrected, in_effect=in_effect, amount=amount, customer_name=customer, voided=voided
        )
        == change
    )
