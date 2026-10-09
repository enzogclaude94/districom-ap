"""Plates (R5-1): one normalization, one comparison key and a display that is computed, never stored."""

import pytest

from app.core.errors import AppError
from app.platform.vehicles import plates


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("ab123cd", "AB123CD"),
        ("AB 123 CD", "AB123CD"),
        ("AB-123-CD", "AB123CD"),
        ("ab.123.cd", "AB123CD"),
        ("  abc 123 ", "ABC123"),
        ("A 123 BCD", "A123BCD"),  # motorcycles and other formats: stored as written, normalized
    ],
)
def test_plates_are_upper_case_without_separators(raw: str, normalized: str) -> None:  # [2]
    assert plates.normalize(raw) == normalized


def test_spaced_and_hyphenated_plates_share_one_key() -> None:  # [3]
    assert plates.plate_key(plates.normalize("AB 123 CD")) == plates.plate_key(plates.normalize("AB-123-CD"))


def test_the_previous_format_is_accepted() -> None:  # [4]
    assert plates.normalize("abc-123") == "ABC123"
    assert plates.display("ABC123") == "ABC 123"


def test_display_is_computed_from_the_stored_plate() -> None:
    assert plates.display("AB123CD") == "AB 123 CD"
    assert plates.display("A123BCD") == "A123BCD"


@pytest.mark.parametrize("raw", ["", "  ", "AB/123", "ÑA123CD", "AB_123_CD", "AB1", "AB1234567890", "AB 12#"])
def test_anything_else_is_not_a_plate(raw: str) -> None:  # [8]
    with pytest.raises(AppError) as caught:
        plates.normalize(raw)
    assert (caught.value.status_code, caught.value.code) == (422, "invalid_plate")
