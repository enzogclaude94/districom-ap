"""Derived state of a pending collection (Enzo's R3 decision 3). Nothing here is stored or editable.

The financial state comes only from the amount in effect (the close in effect of the settlement) and
what was applied to it (collected and resolved, not reversed):

    DRAFT      not in the close in effect and not voided: still a load, not in Cajas
    VOIDED     voided and not in the close in effect (voided before closing, or removed by a correction)
    OPEN       in effect, balance > 0, nothing applied
    PARTIAL    in effect, balance > 0, something collected and/or resolved
    COLLECTED  in effect, balance 0, all of it collected
    RESOLVED   in effect, balance 0, with some administrative resolution

A pending in effect stays in its financial state while a correction edits or voids its load: the edit
is only the next proposed version, shown apart (`load_change`), and takes effect with the new close.
"""

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

State = Literal["DRAFT", "VOIDED", "OPEN", "PARTIAL", "COLLECTED", "RESOLVED"]
# What a correction in progress proposes for a pending in effect, not confirmed until the new close.
LoadChange = Literal["ADDED", "MODIFIED", "MARKED_FOR_REMOVAL"]

ZERO = Decimal("0.00")

LOAD_CHANGE_DETAIL: dict[LoadChange, str] = {
    "ADDED": "Agregado en la corrección: entra en vigencia con el nuevo cierre.",
    "MODIFIED": "Modificación pendiente de confirmar en la rendición.",
    "MARKED_FOR_REMOVAL": "Marcado para quitar en la corrección: sigue vigente hasta el nuevo cierre.",
}


@dataclass(frozen=True)
class Financial:
    state: State
    amount_in_effect: Decimal | None
    collected: Decimal
    resolved: Decimal

    @property
    def applied(self) -> Decimal:
        return self.collected + self.resolved

    @property
    def balance(self) -> Decimal | None:
        return None if self.amount_in_effect is None else self.amount_in_effect - self.applied


def financial(
    *, amount_in_effect: Decimal | None, voided: bool, collected: Decimal = ZERO, resolved: Decimal = ZERO
) -> Financial:
    if amount_in_effect is None:
        return Financial("VOIDED" if voided else "DRAFT", None, collected, resolved)
    balance = amount_in_effect - collected - resolved
    state: State
    if balance > 0:
        state = "PARTIAL" if collected + resolved > 0 else "OPEN"
    else:
        state = "RESOLVED" if resolved > 0 else "COLLECTED"
    return Financial(state, amount_in_effect, collected, resolved)


def load_change(
    *,
    being_corrected: bool,
    in_effect: tuple[Decimal, str] | None,
    amount: Decimal,
    customer_name: str,
    voided: bool,
) -> LoadChange | None:
    """What the load proposes against the close in effect, while a correction is open; None otherwise."""
    if not being_corrected:
        return None
    if in_effect is None:
        return None if voided else "ADDED"
    if voided:
        return "MARKED_FOR_REMOVAL"
    return "MODIFIED" if (amount, customer_name) != in_effect else None
