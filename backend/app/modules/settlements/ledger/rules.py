"""What each operation type may do: minimum role and the shape of its legs (ADR 007, Enzo's R1 decisions).

Checked by `ledger.service` before anything is written, so no caller can post, say, an adjustment
without being admin or a "deposit" that moves money between two cash boxes.
"""

from dataclasses import dataclass

CASH_AND_BANK = frozenset({"CASH_BOX", "BANK"})


@dataclass(frozen=True)
class OperationRule:
    min_role: str
    # Kinds every leg must belong to.
    kinds: frozenset[str]
    # Kind of which there must be exactly one leg (the counterpart), if any.
    counterpart: str | None = None
    # Required sign per kind: +1 value enters, -1 value leaves.
    signs: tuple[tuple[str, int], ...] = ()


RULES: dict[str, OperationRule] = {
    # Real money found when Rendiciones starts; never an invented income (D4).
    "OPENING_BALANCE": OperationRule(
        "admin", CASH_AND_BANK | {"OPENING_BALANCE"}, "OPENING_BALANCE", (("CASH_BOX", 1), ("BANK", 1))
    ),
    "CASH_TRANSFER": OperationRule("operator", frozenset({"CASH_BOX"})),
    "BANK_DEPOSIT": OperationRule("operator", CASH_AND_BANK, "BANK", (("CASH_BOX", -1), ("BANK", 1))),
    "BANK_WITHDRAWAL": OperationRule("operator", CASH_AND_BANK, "BANK", (("CASH_BOX", 1), ("BANK", -1))),
    "OWNER_CONTRIBUTION": OperationRule(
        "admin", CASH_AND_BANK | {"OWNER_EQUITY"}, "OWNER_EQUITY", (("CASH_BOX", 1), ("BANK", 1))
    ),
    "OWNER_WITHDRAWAL": OperationRule(
        "admin", CASH_AND_BANK | {"OWNER_EQUITY"}, "OWNER_EQUITY", (("CASH_BOX", -1), ("BANK", -1))
    ),
    # Admin only, with a mandatory reason, audited and reversible (D4).
    "ADJUSTMENT": OperationRule("admin", CASH_AND_BANK | {"ADJUSTMENT"}, "ADJUSTMENT"),
}
# REVERSAL has no rule here: it is only built by `reverse_operation` from the original's legs.
REVERSAL_MIN_ROLE = "admin"


def leg_shape_error(rule: OperationRule, kinds: list[str], signs: list[int]) -> str | None:
    """Why legs with these account kinds and signs do not fit the rule, or None if they do."""
    if any(kind not in rule.kinds for kind in kinds):
        return "Una de las cuentas no corresponde a este tipo de operación."
    if rule.counterpart is not None and kinds.count(rule.counterpart) != 1:
        return "La operación debe tener exactamente un movimiento en la cuenta de contrapartida."
    required = dict(rule.signs)
    for kind, sign in zip(kinds, signs, strict=True):
        if kind in required and required[kind] != sign:
            return "El sentido de uno de los movimientos no corresponde a este tipo de operación."
    return None
