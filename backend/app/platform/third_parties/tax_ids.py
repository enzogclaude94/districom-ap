"""CUIT: the one rule that normalizes and validates it, and the one that prints it (Enzo's R6 decision D8).

Stored as its 11 digits; the hyphens of «30-71234567-1» are only how screens print it. The check digit is
the AFIP modulo 11 over the first ten digits with weights 5, 4, 3, 2, 7, 6, 5, 4, 3, 2: 11 - (sum mod 11),
where 11 becomes 0 and 10 has no valid CUIT. Never logged (PII).
"""

import re

from app.core.errors import AppError

_SEPARATORS = re.compile(r"[\s.\-/]+")
_WEIGHTS = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)


def check_digit(first_ten: str) -> int | None:
    """The check digit those ten digits require, or None when no CUIT can start with them."""
    rest = sum(int(d) * w for d, w in zip(first_ten, _WEIGHTS, strict=True)) % 11
    digit = 11 - rest
    if digit == 11:
        return 0
    return None if digit == 10 else digit


def normalize(raw: str | None) -> str | None:
    """The 11 digits of a CUIT typed with or without spaces, dots, hyphens or slashes; None when blank.

    422 `invalid_tax_id` when it is not 11 digits or its check digit is wrong."""
    if raw is None:
        return None
    cleaned = _SEPARATORS.sub("", raw)
    if not cleaned:
        return None
    if not re.fullmatch(r"[0-9]{11}", cleaned):
        raise AppError(422, "invalid_tax_id", "El CUIT tiene 11 dígitos.")
    if check_digit(cleaned[:10]) != int(cleaned[10]):
        raise AppError(422, "invalid_tax_id", "El CUIT no es válido: revisá el dígito verificador.")
    return cleaned


def display(tax_id: str) -> str:
    """«30-71234567-1»."""
    return f"{tax_id[:2]}-{tax_id[2:10]}-{tax_id[10]}"
