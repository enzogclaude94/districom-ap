"""Identity of a cheque: when two rows are the same real instrument (Enzo's R4 decisions D-H and "identidad").

The whole strategy lives here, so changing which data identifies an instrument never touches the domain.

Physical cheque: a cheque number is unique within the chequebook of one account, so the instrument is
bank + account + number (+ branch, when the account is an account number of that branch; a 22-digit CBU
already carries bank and branch). Amount and dates are data of the instrument, never its identity.

ECHEQ, two levels:
- its electronic identifier (`echeq_external_id`), unique, when the bank shows it;
- else a key from its bank data: bank + account (CBU) + number + the drawer's CUIT. Never bank + number
  alone. An ECHEQ may be declared without either, but it cannot be verified (nor justify, nor enter the
  portfolio) until it has one of them: a weak identity is never accepted to move forward.

With a complete identity a second row of the same instrument is refused (`cheque_duplicate`, unique
indexes of migration 0012). With an incomplete one, a live cheque of the same kind with the same bank and
number is a *possible* duplicate: the caller must confirm it is another instrument and say why, and that
confirmation stays in the cheque's history.
"""

import re
from dataclasses import dataclass

from app.core.errors import AppError

CBU_LENGTH = 22


@dataclass(frozen=True)
class Identity:
    number: str  # digits, without leading zeros
    branch: str | None
    account: str | None
    external_id: str | None
    instrument_key: str | None

    @property
    def strategy(self) -> str | None:
        """Which data identifies the instrument, or None while it is not identifiable."""
        if self.external_id is not None:
            return "ECHEQ_EXTERNAL_ID"
        if self.instrument_key is None:
            return None
        return "ECHEQ_BANK_DATA" if self.instrument_key.startswith("E:") else "PHYSICAL_BANK_DATA"


def digits(value: str | None, field: str, *, max_length: int) -> str | None:
    """Digits of a code typed with spaces, dots or dashes; leading zeros are kept as typed."""
    if value is None:
        return None
    cleaned = re.sub(r"[\s.\-/]", "", value)
    if not cleaned:
        return None
    if not cleaned.isdigit() or len(cleaned) > max_length:
        raise AppError(422, "invalid_cheque_data", "Ese dato del cheque solo admite números.", {"field": field})
    return cleaned


def account(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[\s.\-/]", "", value).upper()
    if not cleaned:
        return None
    if re.fullmatch(r"[0-9A-Z]{1,34}", cleaned) is None:
        raise AppError(
            422, "invalid_cheque_data", "La cuenta girada admite letras y números.", {"field": "account_ref"}
        )
    return cleaned


def external_id(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"\s", "", value).upper()
    if not cleaned:
        return None
    if len(cleaned) > 64:
        raise AppError(422, "invalid_cheque_data", "El identificador del ECHEQ es demasiado largo.")
    return cleaned


def tax_id(value: str | None) -> str | None:
    cleaned = digits(value, "drawer_tax_id", max_length=11)
    if cleaned is not None and len(cleaned) != 11:
        raise AppError(422, "invalid_cheque_data", "El CUIT tiene 11 dígitos.", {"field": "drawer_tax_id"})
    return cleaned


def _significant(code: str) -> str:
    return code.lstrip("0") or "0"


def compute(
    *,
    kind: str,
    bank_id: int,
    number: str,
    branch: str | None,
    account_ref: str | None,
    echeq_id: str | None,
    drawer_tax_id: str | None,
) -> Identity:
    """Identity of an instrument from its (already cleaned) data."""
    num = _significant(number)
    key = None
    if account_ref is not None:
        acct = _significant(account_ref)
        is_cbu = len(account_ref) == CBU_LENGTH and account_ref.isdigit()
        if kind == "PHYSICAL" and (is_cbu or branch is not None):
            key = f"P:{bank_id}:{'' if is_cbu else _significant(branch or '')}:{acct}:{num}"
        elif kind == "ECHEQ" and drawer_tax_id is not None:
            key = f"E:{bank_id}:{acct}:{num}:{drawer_tax_id}"
    return Identity(num, branch, account_ref, echeq_id if kind == "ECHEQ" else None, key)


def is_complete(kind: str, identity: Identity) -> bool:
    """Physical: identifiable by its bank data. ECHEQ: verifiable (external id or bank data)."""
    if kind == "PHYSICAL":
        return identity.instrument_key is not None
    return identity.strategy is not None


def may_be_same(kind: str, mine: Identity, other_key: str | None, other_external: str | None) -> bool:
    """Whether another live cheque of the same kind, bank and number may be this same instrument.

    Two cheques whose identities are both complete by the same data and differ are certainly different
    instruments (two chequebooks may repeat a number); in any other case we cannot tell.
    """
    if kind == "ECHEQ" and mine.external_id is not None and other_external is not None:
        return mine.external_id == other_external
    if mine.instrument_key is not None and other_key is not None:
        return mine.instrument_key == other_key
    return True
