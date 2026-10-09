"""CUIT and name rules of the third parties (R6-1, Enzo's decisions D2 and D8)."""

import pytest

from app.core.errors import AppError
from app.platform.third_parties import tax_ids
from app.platform.third_parties.models import name_key


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("30712345671", "30712345671"),
        ("30-71234567-1", "30712345671"),
        (" 20.12345678.6 ", "20123456786"),
        ("27 30111222 5", "27301112225"),
        ("30/50000000/3", "30500000003"),  # remainder 0: check digit 0 after 11 - 0 = 11
    ],
)
def test_a_valid_cuit_is_stored_as_its_eleven_digits(raw: str, stored: str) -> None:
    assert tax_ids.normalize(raw) == stored


@pytest.mark.parametrize("raw", [None, "", "  ", "--"])
def test_a_blank_cuit_is_none(raw: str | None) -> None:
    assert tax_ids.normalize(raw) is None


@pytest.mark.parametrize(
    "raw",
    [
        "30712345672",  # wrong check digit
        "3071234567",  # 10 digits
        "307123456711",  # 12 digits
        "30-7123456A-1",
        "20999999990",  # no check digit is valid for these ten digits (11 - remainder = 10)
        "20999999991",
    ],
)
def test_an_invalid_cuit_is_refused(raw: str) -> None:
    with pytest.raises(AppError) as caught:
        tax_ids.normalize(raw)
    assert (caught.value.status_code, caught.value.code) == (422, "invalid_tax_id")


def test_check_digit_rule() -> None:
    assert tax_ids.check_digit("3071234567") == 1
    assert tax_ids.check_digit("3050000000") == 3
    assert tax_ids.check_digit("2099999999") is None


def test_display() -> None:
    assert tax_ids.display("30712345671") == "30-71234567-1"


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Distribuidora Pérez S.A.", "distribuidora  perez sa"),
        ("La Serenísima", "LA SERENISIMA"),
        ("Coca-Cola FEMSA", "coca cola femsa"),
        ("D'Angelo Hnos.", "dangelo hnos"),
    ],
)
def test_the_name_key_ignores_case_accents_punctuation_and_spacing(a: str, b: str) -> None:
    assert name_key(a) == name_key(b)


def test_the_name_key_keeps_different_names_apart() -> None:
    assert name_key("Distribuidora Norte") != name_key("Distribuidora Sur")
