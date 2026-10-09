"""Plates: the one rule that normalizes them and the one that prints them."""

import re

from app.core.errors import AppError

_SEPARATORS = re.compile(r"[\s.\-]+")
_VALID = re.compile(r"^[A-Z0-9]{5,10}$")
_MERCOSUR = re.compile(r"^([A-Z]{2})([0-9]{3})([A-Z]{2})$")  # AB123CD, since 2016
_PREVIOUS = re.compile(r"^([A-Z]{3})([0-9]{3})$")  # ABC123, 1995-2016


def normalize(raw: str) -> str:
    """Upper case without spaces, dots or hyphens: «ab 123-cd» and «AB.123.CD» are AB123CD. Anything else
    that is not a letter A-Z or a digit, or a length outside 5-10, is not a plate (422 `invalid_plate`)."""
    plate = _SEPARATORS.sub("", raw).upper()
    if not _VALID.match(plate):
        raise AppError(
            422,
            "invalid_plate",
            "La patente tiene que tener entre 5 y 10 letras (A-Z) o números; espacios, puntos y guiones se ignoran.",
            {"plate": raw},
        )
    return plate


def plate_key(plate: str) -> str:
    """Comparison key of a normalized plate: today the plate itself; kept apart so a future normalization can
    change without losing uniqueness."""
    return plate


def display(plate: str) -> str:
    """«AB 123 CD» (Mercosur), «ABC 123» (previous format); any other plate as stored."""
    if match := _MERCOSUR.match(plate):
        return " ".join(match.groups())
    if match := _PREVIOUS.match(plate):
        return " ".join(match.groups())
    return plate
