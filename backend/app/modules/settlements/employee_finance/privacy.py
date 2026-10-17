"""What Cajas shows of employee finance to someone without the area (R8-1, ADR 010, Enzo's D2).

Without `settlements.employee_finance`:
- internal accounts (`EMPLOYEE`) and the technical accounts of R8 are not listed, and reading one or its
  movements answers 404 like a missing account;
- an operation of R8 seen from a real cash box or bank keeps what reconciling it needs (date, amount, the
  box, the direction) and loses the rest: its exact type, description, source document and every leg outside
  cash and the bank. It reads «Movimiento de empleado (reservado)». A reversal of one is redacted the same way.

Settlement closes (R2/R7) and pending write-offs (R3) are not R8 operations: the allocation of a difference
to an employee stays visible where it is decided (the settlement), which never shows the internal balance.
"""

from dataclasses import replace

from sqlalchemy.orm import Session

from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.service import AccountBalance, EntryDetail, Movement, OperationDetail

# Accounts only the area reads: internal accounts and the technical accounts of R8 (R8-2 on), ADR 010 §20.
PRIVATE_ACCOUNT_KINDS = frozenset({"EMPLOYEE", "EMPLOYEE_CONSUMPTIONS", "EMPLOYEE_ADJUSTMENTS"})
# Operation types of R8, decided in ADR 010 (each ships in its own phase). Listed now so that Cajas never
# shows one unredacted, whatever phase adds it.
PRIVATE_OPERATION_TYPES = frozenset(
    {
        "EMPLOYEE_CONSUMPTION",
        "EMPLOYEE_ADVANCE",
        "EMPLOYEE_ADJUSTMENT",
        "EMPLOYEE_LOAN",
        "PAYROLL_CLOSE",
        "EMPLOYEE_PAYOUT",
    }
)
RESERVED_TYPE = "EMPLOYEE_RESERVED"
RESERVED_LABEL = "Movimiento de empleado (reservado)"
# The only legs a redacted operation keeps: real money.
VISIBLE_KINDS_WHEN_RESERVED = frozenset({"CASH_BOX", "BANK"})


def visible_balances(balances: list[AccountBalance]) -> list[AccountBalance]:
    return [b for b in balances if b.kind not in PRIVATE_ACCOUNT_KINDS]


def is_private_account(balance: AccountBalance) -> bool:
    return balance.kind in PRIVATE_ACCOUNT_KINDS


def private_operation_ids(db: Session, operation_ids: set[int], types: dict[int, str]) -> set[int]:
    """Which of these operations are R8 or reverse one. `types` maps each id to its own type."""
    private = {op_id for op_id in operation_ids if types[op_id] in PRIVATE_OPERATION_TYPES}
    reversals = sorted(op_id for op_id in operation_ids if types[op_id] == "REVERSAL")
    if reversals:
        reversed_types = ledger.reversed_operation_types(db, reversals)
        private |= {op_id for op_id in reversals if reversed_types.get(op_id, "") in PRIVATE_OPERATION_TYPES}
    return private


def redact_operation(detail: OperationDetail) -> OperationDetail:
    entries: list[EntryDetail] = [e for e in detail.entries if e.account_kind in VISIBLE_KINDS_WHEN_RESERVED]
    return replace(
        detail, type=RESERVED_TYPE, description=RESERVED_LABEL, source_type=None, source_id=None, entries=entries
    )


def redact_movement(movement: Movement) -> Movement:
    return replace(movement, operation_type=RESERVED_TYPE, description=RESERVED_LABEL, source_type=None, source_id=None)


def operation_for(db: Session, detail: OperationDetail, can_read: bool) -> OperationDetail:
    if can_read or not private_operation_ids(db, {detail.id}, {detail.id: detail.type}):
        return detail
    return redact_operation(detail)


def movements_for(db: Session, movements: list[Movement], can_read: bool) -> list[Movement]:
    if can_read:
        return movements
    private = private_operation_ids(
        db, {m.operation_id for m in movements}, {m.operation_id: m.operation_type for m in movements}
    )
    return [redact_movement(m) if m.operation_id in private else m for m in movements]
