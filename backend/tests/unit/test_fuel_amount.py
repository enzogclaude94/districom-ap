"""Amount of a fuel expense (R5-2): round(liters x price per liter, 2), half up. Checked by hand."""

from decimal import Decimal

import pytest

from app.core.errors import AppError
from app.modules.settlements.delivery.fuel import amount


@pytest.mark.parametrize(
    ("liters", "price", "expected"),
    [
        ("0.125", "1.0000", "0.13"),  # 0,1250000: half up (half even would give 0,12)
        ("45.678", "1234.5678", "56392.59"),  # 56.392,5879684
        ("33.333", "1499.9900", "49999.17"),  # 49.999,16667
        ("10.005", "1000.0000", "10005.00"),  # exact, nothing to round
        ("40", "1250", "50000.00"),  # exact, whole numbers
        ("0.001", "5.0000", "0.01"),  # 0,005: the smallest half rounds up to a cent
    ],
)
def test_amount_is_liters_times_price_half_up(liters: str, price: str, expected: str) -> None:
    assert amount(Decimal(liters), Decimal(price)) == Decimal(expected)


def test_an_amount_that_rounds_to_zero_is_refused() -> None:
    with pytest.raises(AppError) as caught:
        amount(Decimal("0.001"), Decimal("4.9999"))  # 0,0049999
    assert (caught.value.status_code, caught.value.code) == (422, "fuel_amount_zero")


def test_an_amount_beyond_the_money_column_is_refused() -> None:
    with pytest.raises(AppError) as caught:
        amount(Decimal("9999999.999"), Decimal("9999999999.9999"))
    assert (caught.value.status_code, caught.value.code) == (422, "amount_too_large")
    assert amount(Decimal("100"), Decimal("9999999999.9999")) == Decimal("999999999999.99")  # the largest that fits
