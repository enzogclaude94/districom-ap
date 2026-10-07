"""State of a cheque, derived from what happened to it; never stored (Enzo's R4 decisions).

Where the cheque is comes from the ledger (its legs per account kind); what happened before it had any
effect comes from its row (void, an ECHEQ's verification decision). R4-1 has these states:

- DECLARED ("Declarado"): an ECHEQ loaded but not verified. Justifies nothing.
- VERIFICATION_REJECTED ("Verificación rechazada"): an ECHEQ that could not be validated. Different from
  VOIDED, with its own history, though neither is in the portfolio nor justifies anything.
- RECEIVED ("Recibido, sin publicar"): a physical cheque confirmed in hand, or a verified ECHEQ, waiting
  for the close of its settlement.
- IN_PORTFOLIO ("En cartera"): its legs left it in "Cartera de cheques". A cheque marked for removal in a
  correction stays here until the new close (`load_change`).
- VOIDED ("Anulado"): a load voided because of an internal error, never in the portfolio (or taken out by
  a correction close).

R4-2 adds DEPOSITED, CREDITED, REJECTED and RETURNED from the same ledger position.
"""

from decimal import Decimal
from typing import Literal

State = Literal["DECLARED", "VERIFICATION_REJECTED", "RECEIVED", "IN_PORTFOLIO", "DEPOSITED", "VOIDED"]
LoadChange = Literal["ADDED", "MARKED_FOR_REMOVAL"]

LABELS: dict[str, str] = {
    "DECLARED": "Declarado",
    "VERIFICATION_REJECTED": "Verificación rechazada",
    "RECEIVED": "Recibido, sin publicar",
    "IN_PORTFOLIO": "En cartera",
    "DEPOSITED": "Depositado",
    "VOIDED": "Anulado",
}
LOAD_CHANGE_DETAIL: dict[str, str] = {
    "ADDED": "Agregado en esta corrección: entra a la cartera con el nuevo cierre.",
    "MARKED_FOR_REMOVAL": "Marcado para quitar en esta corrección: sigue en cartera hasta confirmar el nuevo cierre.",
}


def derive(*, kind: str, voided: bool, verification_status: str | None, position: dict[str, Decimal]) -> State:
    if position.get("CHEQUE_PORTFOLIO", Decimal(0)) > 0:
        return "IN_PORTFOLIO"
    if position.get("CHEQUES_IN_TRANSIT", Decimal(0)) > 0:
        return "DEPOSITED"
    if voided:
        return "VOIDED"
    if kind == "ECHEQ" and verification_status != "VERIFIED":
        return "VERIFICATION_REJECTED" if verification_status == "VERIFICATION_REJECTED" else "DECLARED"
    return "RECEIVED"


def justifies(*, kind: str, voided: bool, verification_status: str | None) -> bool:
    """Whether a cheque of a settlement's load counts as value received: never an unverified ECHEQ."""
    return not voided and (kind == "PHYSICAL" or verification_status == "VERIFIED")


def load_change(*, being_corrected: bool, in_effect: bool, voided: bool, justifies_now: bool) -> LoadChange | None:
    """What a correction in progress proposes for a cheque of the settlement, if anything."""
    if not being_corrected:
        return None
    if in_effect and voided:
        return "MARKED_FOR_REMOVAL"
    if not in_effect and justifies_now:
        return "ADDED"
    return None
