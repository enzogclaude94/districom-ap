"""Consumptions, advances and adjustments of an employee's internal account (R8-2, ADR 010, Enzo's R8-2 decisions).

Each movement is the document of its own ledger operation. In ONE transaction the service writes the movement, its
operation (source `employee_movement:<id>`, on the movement's business date) and the audit; the database checks at
commit that both are exactly each other (migration 0026).

| Kind | Who (area) | Legs | Reads |
|---|---|---|---|
| CONSUMPTION | operator, admin | internal account +X, «Consumos de empleados» -X | the employee owes it |
| ADVANCE | admin | internal account +X, the cash box or bank -X | the employee owes it |
| ADJUSTMENT CHARGE | admin | internal account +X, «Ajustes a empleados» -X | the employee owes more |
| ADJUSTMENT CREDIT | admin | internal account -X, «Ajustes a empleados» +X | in the employee's favour |

- A consumption is an economic amount only: no product, stock nor quantities; the description says what it was.
- An adjustment is exceptional and corrective, never a stand-in for a consumption, an advance, a loan, a salary or a
  bonus; it needs a reason (its description).
- An advance is an ordinary debit of its cash box (never negative, on its date nor any later day) or of the bank
  (never into an overdraft, nor deeper into one), like a payment or a direct expense. No cheques.
- The employee exists. An active employee takes any movement the role allows, on any business date up to today. An
  inactive employee (`employees.active` is all there is: no end date is invented) takes only what documents the past:
  a consumption or an advance dated before today, or an adjustment, and only from an admin of the area.
- `business_date` is the financial date and is never in the future; it may be earlier than today (a late record or
  an authorized correction). `created_at` says when it was recorded; it is never used as the business date.
- Money is immutable once published: a mistake is a reversal plus a new movement. Reversal (admin of the area, with a
  reason): the REVERSAL of the whole operation on the ORIGINAL business date. Reversing an advance puts the money back
  into its cash box or bank, so it never fails for lack of funds there. Cajas never reverses a movement. R8-2 has no
  payroll yet: a movement can always be reversed (R8-4 will block it once a payroll applied it).

Lock order, always: the employee row (FOR SHARE), the employee's internal account, the cash box or bank of an advance
(FOR NO KEY UPDATE, before the insert takes it FOR SHARE), then (inside the ledger) the accounts that lose value, in id
order (that same one). No other flow locks an internal account, and payments, expenses and transfers lock only the
accounts they debit, so this never waits in a cycle with R2-R7. Idempotent by `client_request_id`: the ledger request
ids derive from the client's.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError, database_timeout
from app.core.logging import db_error_fields
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery.service import money
from app.modules.settlements.employee_finance import payrolls
from app.modules.settlements.employee_finance.access import AREA
from app.modules.settlements.employee_finance.models import EmployeeMovement
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import employee_account_id, lock_account
from app.platform.audit.service import record_change
from app.platform.auth.access import has_area_access, has_module_access
from app.platform.auth.models import User
from app.platform.parties.models import Employee

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
SOURCE = "employee_movement"
ENTITY = "employee_movement"
EVENT = "settlements.employee_finance.employee_movement"
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("6f2a9d41-8c3e-4b57-a1d0-7e94c2b5f318")
FUNDING_KINDS = ("CASH_BOX", "BANK")

Kind = Literal["CONSUMPTION", "ADVANCE", "ADJUSTMENT"]
Direction = Literal["CHARGE", "CREDIT"]
# Minimum role in the area to register each kind.
MIN_ROLE: dict[str, str] = {"CONSUMPTION": "operator", "ADVANCE": "admin", "ADJUSTMENT": "admin"}
COUNTERPART_CODE = {"CONSUMPTION": "employee_consumptions", "ADJUSTMENT": "employee_adjustments"}
KIND_LABEL = {"CONSUMPTION": "Consumo", "ADVANCE": "Adelanto", "ADJUSTMENT": "Ajuste"}


@dataclass(frozen=True)
class Command:
    """What the client asked, normalized."""

    kind: Kind
    employee_id: int
    business_date: date
    amount: Decimal
    description: str
    reference: str | None
    direction: Direction | None = None
    funding_account_id: int | None = None

    @property
    def employee_amount(self) -> Decimal:
        """The signed leg on the internal account: + the employee owes it, - in the employee's favour."""
        return -self.amount if self.direction == "CREDIT" else self.amount


@dataclass(frozen=True)
class Leg:
    account_id: int | None  # None: the internal account, created on first use
    account_name: str
    account_kind: str
    amount: Decimal


@dataclass(frozen=True)
class Plan:
    """A movement validated against the employee, the roles and the funds: what it would publish."""

    command: Command
    employee: Employee
    counterpart_id: int
    legs: list[Leg]


@dataclass(frozen=True)
class Created:
    id: int
    # False when the request repeated an earlier one (same client_request_id and content).
    created: bool


def command(
    kind: Kind,
    employee_id: int,
    business_date: date,
    amount: Decimal,
    description: str,
    reference: str | None,
    direction: Direction | None = None,
    funding_account_id: int | None = None,
) -> Command:
    text = " ".join(description.split())
    if not text:
        raise AppError(
            422,
            "description_required",
            "Indicá el motivo del ajuste." if kind == "ADJUSTMENT" else "Indicá la descripción.",
        )
    return Command(
        kind=kind,
        employee_id=employee_id,
        business_date=business_date,
        amount=amount.quantize(Decimal("0.01")),
        description=text,
        reference=" ".join((reference or "").split()) or None,
        direction=direction if kind == "ADJUSTMENT" else None,
        funding_account_id=funding_account_id if kind == "ADVANCE" else None,
    )


# --- writing --------------------------------------------------------------------------------------


def plan(db: Session, actor: User, cmd: Command, *, lock: bool = False) -> Plan:
    """Validate a movement (the employee row locked FOR SHARE when `lock`) and say what it would publish. Funds are
    checked here only as a preview; the ledger checks them again, under its locks, when publishing."""
    _require_role(actor, cmd.kind)
    query = select(Employee).where(Employee.id == cmd.employee_id)
    employee = db.scalars(query.with_for_update(read=True) if lock else query).first()
    if employee is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    today = business_today()
    if cmd.business_date > today:
        raise AppError(422, "future_business_date", "La fecha del movimiento no puede ser futura.")
    if not employee.active:
        _check_inactive(actor, cmd, today)
    if cmd.kind == "ADJUSTMENT" and cmd.direction is None:
        raise AppError(422, "direction_required", "Indicá si el ajuste es en contra o a favor del empleado.")

    internal = ledger.employee_account_id(db, employee.id)
    internal_leg = Leg(internal, f"Cuenta interna · {employee.full_name}", "EMPLOYEE", cmd.employee_amount)
    if cmd.kind == "ADVANCE":
        assert cmd.funding_account_id is not None
        funding = _funding_account(db, cmd.funding_account_id)
        lowest, on = ledger.lowest_balance_since(db, funding.account_id, cmd.business_date)
        if lowest < cmd.amount:
            raise _insufficient(funding, cmd, lowest, on)
        counterpart = Leg(funding.account_id, funding.name, funding.kind, -cmd.amount)
    else:
        counterpart_id = ledger.system_account_id(db, COUNTERPART_CODE[cmd.kind])
        balance = ledger.account_balance(db, counterpart_id)
        counterpart = Leg(counterpart_id, balance.name, balance.kind, -cmd.employee_amount)
    assert counterpart.account_id is not None
    legs = [counterpart, internal_leg] if cmd.kind == "ADVANCE" else [internal_leg, counterpart]
    return Plan(command=cmd, employee=employee, counterpart_id=counterpart.account_id, legs=legs)


def create(db: Session, actor: User, cmd: Command, client_request_id: UUID) -> Created:
    fingerprint = idempotency.fingerprint(actor.id, SOURCE, _request(cmd))
    # A retry is answered before anything else is validated: it must get the same result as the first time.
    existing = _by_request_id(db, client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "employee_movement_id", existing.id)
        return Created(existing.id, created=False)

    planned = plan(db, actor, cmd, lock=True)
    employee = planned.employee
    internal = employee_account_id(db, employee_id=employee.id, employee_name=employee.full_name, user_id=actor.id)
    lock_account(db, internal)
    if cmd.funding_account_id is not None:
        # Taken now, before the insert's FOR SHARE on it (0026), so two advances never both hold a share and wait to
        # upgrade it; the ledger takes the same lock again when it checks the funds.
        lock_account(db, cmd.funding_account_id)

    movement = EmployeeMovement(
        kind=cmd.kind,
        direction=cmd.direction,
        employee_id=employee.id,
        employee_account_id=internal,
        business_date=cmd.business_date,
        amount=cmd.amount,
        employee_amount=cmd.employee_amount,
        funding_account_id=cmd.funding_account_id,
        description=cmd.description,
        reference=cmd.reference,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(movement)
            db.flush()
    except IntegrityError as exc:
        winner = _by_request_id(db, client_request_id)
        db.rollback()  # release the locks taken for the insert that lost
        if winner is None:
            # A rule the database saw at insert time (e.g. the cash box was deactivated meanwhile).
            raise _rejected(exc) from exc
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "employee_movement_id", winner.id)
        return Created(winner.id, created=False)

    operation = ledger.post_operation(
        db,
        actor=actor,
        type=f"EMPLOYEE_{cmd.kind}",
        legs=[
            ledger.Leg(internal, cmd.employee_amount),
            ledger.Leg(planned.counterpart_id, -cmd.employee_amount),
        ],
        client_request_id=uuid5(NAMESPACE, f"employee-movement:{client_request_id}"),
        business_date=cmd.business_date,
        # No name nor concept: whoever reads the operation without the area sees it redacted anyway.
        description=f"{KIND_LABEL[cmd.kind]} de empleado · movimiento n.º {movement.id}",
        source=ledger.SourceRef(SOURCE, movement.id),
    ).operation
    movement.operation_id = operation.id
    db.flush()
    record_change(
        db,
        f"{EVENT}.created",
        action="create",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=movement.id,
        before=None,
        after=snapshot(movement) | {"operation_id": operation.id},
    )
    commit(db)
    return Created(movement.id, created=True)


def reverse(db: Session, actor: User, movement_id: int, reason: str, client_request_id: UUID) -> Created:
    """Undo a whole movement (admin of the area, with a reason) on its original business date."""
    if not has_area_access(actor, AREA, "admin"):
        raise AppError(403, "forbidden", "Solo un administrador de Empleados revierte un movimiento.")
    reason = " ".join(reason.split())
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo del reverso.")
    # The internal account first (the lock order of every R8 flow, R8-4): a payroll closing over it waits or wins.
    account = db.scalar(select(EmployeeMovement.employee_account_id).where(EmployeeMovement.id == movement_id))
    if account is None:
        raise AppError(404, "employee_movement_not_found", "El movimiento no existe.")
    lock_account(db, account)
    movement = db.scalars(
        select(EmployeeMovement)
        .where(EmployeeMovement.id == movement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    if movement.kind == "ADVANCE":
        _require_cash_role(actor)
    request_id = uuid5(NAMESPACE, f"employee-movement-reversal:{client_request_id}")
    if movement.reversal_operation_id is not None:
        reversal = ledger.get_operation(db, movement.reversal_operation_id)
        if reversal.client_request_id == request_id:
            if (movement.reversed_by, movement.reversal_reason) != (actor.id, reason):
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Ese identificador de solicitud ya se usó para un reverso distinto.",
                    {"operation_id": reversal.id},
                )
            db.commit()  # a retry of the same reversal: release the locks
            return Created(movement.id, created=False)
        raise AppError(
            409,
            "employee_movement_already_reversed",
            "El movimiento ya fue revertido.",
            {"reversal_operation_id": reversal.id},
        )
    assert movement.operation_id is not None  # a published movement always has it (0026)
    if payrolls.operation_applied(db, movement.operation_id, movement.employee_account_id):
        raise AppError(
            409,
            "movement_applied",
            "Una liquidación cerrada ya aplicó este movimiento: no se revierte directamente. Reabrí la liquidación y "
            "excluilo, o corregí con un ajuste.",
        )
    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=movement.operation_id,
        source=ledger.SourceRef(SOURCE, movement.id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    movement.reversal_operation_id = reversal.id
    movement.reversed_by = actor.id
    movement.reversed_at = datetime.now(UTC)
    movement.reversal_reason = reason
    db.flush()
    record_change(
        db,
        f"{EVENT}.reversed",
        action="reverse",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=movement.id,
        before={"operation_id": movement.operation_id},
        after={
            "reversal_operation_id": reversal.id,
            "business_date": reversal.business_date.isoformat(),
            "reason": reason,
        },
    )
    commit(db)
    return Created(movement.id, created=True)


# --- shared ---------------------------------------------------------------------------------------


def snapshot(movement: EmployeeMovement) -> dict[str, Any]:
    return {
        "kind": movement.kind,
        "direction": movement.direction,
        "employee_id": movement.employee_id,
        "employee_account_id": movement.employee_account_id,
        "business_date": movement.business_date.isoformat(),
        "amount": money(movement.amount),
        "employee_amount": money(movement.employee_amount),
        "funding_account_id": movement.funding_account_id,
        "description": movement.description,
        "reference": movement.reference,
    }


def commit(db: Session) -> None:
    """Commit; a rule only the database saw is 409, never a raw error."""
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        raise _rejected(exc) from exc


def _rejected(exc: DBAPIError) -> AppError:
    logger.warning("employee_movement_rejected", extra=db_error_fields(exc))
    return AppError(
        409,
        "invalid_employee_movement",
        "El movimiento no cumple las reglas de la cuenta interna y no se registró. Revisá los datos.",
    )


def _request(cmd: Command) -> dict[str, Any]:
    return {
        "employee_movement_kind": cmd.kind,
        "employee_id": cmd.employee_id,
        "business_date": cmd.business_date.isoformat(),
        "amount": money(cmd.amount),
        "direction": cmd.direction,
        "funding_account_id": cmd.funding_account_id,
        "description": cmd.description,
        "reference": cmd.reference,
    }


def _require_role(actor: User, kind: str) -> None:
    if not has_area_access(actor, AREA, MIN_ROLE[kind]):
        message = {
            "CONSUMPTION": "No tenés permiso para registrar consumos.",
            "ADVANCE": "Solo un administrador de Empleados entrega adelantos.",
            "ADJUSTMENT": "Solo un administrador de Empleados registra ajustes.",
        }[kind]
        raise AppError(403, "forbidden", message)
    if kind == "ADVANCE":
        _require_cash_role(actor)


def _require_cash_role(actor: User) -> None:
    """What moves a cash box or the bank also needs operator in Rendiciones: the discipline of Cajas does not change
    for employees (ADR 010 D9)."""
    if not has_module_access(actor, MODULE_KEY, "operator"):
        raise AppError(
            403, "forbidden", "Un adelanto mueve una caja o el banco: necesitás además el rol operador en Rendiciones."
        )


def _check_inactive(actor: User, cmd: Command, today: date) -> None:
    """An inactive employee takes only what documents the past, and only from an admin of the area."""
    historical = cmd.kind == "ADJUSTMENT" or cmd.business_date < today
    if not historical or not has_area_access(actor, AREA, "admin"):
        raise AppError(
            422,
            "inactive_employee",
            "El empleado está inactivo: solo un administrador registra un ajuste, o un consumo o adelanto con fecha "
            "anterior a hoy, para documentar lo que ya pasó.",
            {"employee_id": cmd.employee_id},
        )


def _funding_account(db: Session, account_id: int) -> ledger.AccountBalance:
    try:
        account = ledger.account_balance(db, account_id)
    except AppError:
        account = None
    if account is None or not account.active or account.kind not in FUNDING_KINDS:
        raise AppError(
            422,
            "invalid_funding_account",
            "Un adelanto se entrega desde una caja o un banco activos.",
            {"funding_account_id": account_id},
        )
    return account


def _insufficient(funding: ledger.AccountBalance, cmd: Command, lowest: Decimal, on: date) -> AppError:
    return AppError(
        409,
        "insufficient_funds",
        f"{funding.name} quedaría con saldo negativo el {on:%d/%m/%Y}: no alcanza para entregar ${money(cmd.amount)}.",
        {
            "account_id": funding.account_id,
            "business_date": on.isoformat(),
            "balance": money(lowest),
            "amount": money(-cmd.amount),
        },
    )


def _by_request_id(db: Session, client_request_id: UUID) -> EmployeeMovement | None:
    return db.scalars(
        select(EmployeeMovement).where(EmployeeMovement.client_request_id == client_request_id)
    ).one_or_none()
