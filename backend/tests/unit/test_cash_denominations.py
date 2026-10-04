"""Initial cash denominations of DistriCom (Enzo's R2-P4): notes of $100 or more, no coins."""

from decimal import Decimal

from app.modules.settlements.delivery.denominations import CODE_PATTERN, DEFAULT_DENOMINATIONS
from app.modules.settlements.settings import SETTINGS


def test_initial_list_is_exactly_the_notes_in_use() -> None:
    """[9] [10] Only $20.000, $10.000, $2.000, $1.000, $500, $200 and $100."""
    values = [Decimal(d["value"]) for d in DEFAULT_DENOMINATIONS]
    assert values == [Decimal(v) for v in (20000, 10000, 2000, 1000, 500, 200, 100)]
    assert min(values) >= 100
    assert all(d["active"] for d in DEFAULT_DENOMINATIONS)


def test_each_denomination_has_a_stable_code() -> None:
    """[6] The code identifies the denomination, independent of its position."""
    codes = [d["code"] for d in DEFAULT_DENOMINATIONS]
    assert codes == ["ARS_20000", "ARS_10000", "ARS_2000", "ARS_1000", "ARS_500", "ARS_200", "ARS_100"]
    assert len(set(codes)) == len(codes)
    assert all(CODE_PATTERN.match(code) for code in codes)


def test_suggested_destination_is_a_cash_box_code_not_a_rule() -> None:
    assert {d["suggested_destination"] for d in DEFAULT_DENOMINATIONS} == {"main_cash", "change_cash"}


def test_the_list_is_a_setting_with_this_default() -> None:
    spec = next(spec for spec in SETTINGS.specs if spec.key == "cash_denominations")
    assert spec.default is DEFAULT_DENOMINATIONS
