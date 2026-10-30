"""Loans to employees, their installments and deferrals (R8-3, ADR 010, Enzo's R8-3 decisions).

A loan is interest-free money handed to an employee from a cash box or the bank, paid back in installments. It is
the document of its own operation (`EMPLOYEE_LOAN`, source `employee_loan:<id>`): the cash box or bank -P, the
employee's internal account +P, once. The installments are a schedule, never money: none has a ledger leg, and
deferring one moves nothing. The database checks every rule at commit (migration 0027).

| Action | Who | Ledger |
|---|---|---|
| give a loan | admin of the area + operator in Rendiciones (it moves a cash box or the bank) | EMPLOYEE_LOAN |
| defer an installment | admin of the area | none |
| reverse the loan | admin of the area + operator in Rendiciones | REVERSAL, on the loan's date |

- The schedule: N installments, equal by default with the last one absorbing the rounding, monthly from the first
  due date (the same day of each month, or its last day). The admin may change the dates and amounts before
  confirming; they must add up exactly to the principal, be in order and fall on or after the loan's date (the first
  one may fall in the loan's own month). Once confirmed the schedule never changes: only a deferral replaces an
  installment.
- A deferral marks a SCHEDULED installment DEFERRED (who, when, why: history, never rewritten) and creates one or
  more new SCHEDULED installments, with new numbers, adding up to exactly its amount, each due strictly after the
  deferred one and in order (deferring moves debt later, never earlier: bringing installments forward would be
  another operation). A DEFERRED installment is deferred again through its replacement, against its own date.
- The balance of a loan is its principal while it is in effect (R8-4 will subtract the installments a payroll
  applies), never derived from the internal account. Its state is derived: ACTIVE or REVERSED.
- Reversal: the whole loan, with a reason; the REVERSAL of its operation on the original date puts the money back
  into its cash box or bank (no funds check), and every installment still SCHEDULED becomes CANCELLED in the same
  transaction; a DEFERRED one stays as it was. R8-4 will refuse it once a payroll applied an installment
  (`applied_installments`).
- The employee rules are those of an advance: an inactive employee takes only a loan dated before today.

Lock order: creating takes the employee row (FOR SHARE), the internal account, the cash box or bank (before the
insert takes it FOR SHARE), then the ledger's locks, as an advance does (R8-2). Deferring and reversing take the loan
row first (FOR UPDATE), so both serialize on it; reversing then takes the internal account and the ledger's locks.
Idempotent by `client_request_id`; the ledger request ids derive from the client's.
"""

import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, Decimal
from itertools import pairwise
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError, database_timeout
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery.service import money
from app.modules.settlements.employee_finance import payrolls
from app.modules.settlements.employee_finance.access import AREA
from app.modules.settlements.employee_finance.models import EmployeeLoan, EmployeeLoanInstallment
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import employee_account_id, lock_account
from app.platform.audit.service import record_change
from app.platform.auth.access import has_area_access, has_module_access
from app.platform.auth.models import User
from app.platform.parties.models import Employee

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
SOURCE = "employee_loan"
DEFERRAL_SOURCE = "employee_loan_deferral"
ENTITY = "employee_loan"
EVENT = "settlements.employee_finance.employee_loan"
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("2c7e5b90-4f1d-4a63-9e8b-b13d0a6f7c52")
FUNDING_KINDS = ("CASH_BOX", "BANK")
MAX_INSTALLMENTS = 120  # the database allows 1..120 (0027)
MAX_REPLACEMENTS = 24
CENT = Decimal("0.01")


@dataclass(frozen=True)
class Installment:
    due_on: date
    amount: Decimal


@dataclass(frozen=True)
class Command:
    """What the client asked, normalized; `installments` is the confirmed schedule."""

    employee_id: int
    business_date: date
    principal: Decimal
    funding_account_id: int
    installments: tuple[Installment, ...]
    description: str | None
    reference: str | None

    @property
    def installment_count(self) -> int:
        return len(self.installments)

    @property
    def first_due_on(self) -> date:
        return self.installments[0].due_on


@dataclass(frozen=True)
class Leg:
    account_id: int | None  # None: the internal account, created on first use
    account_name: str
    account_kind: str
    amount: Decimal


@dataclass(frozen=True)
class Plan:
    """A loan validated against the employee, the roles, the schedule and the funds: what it would publish."""

    command: Command
    employee: Employee
    funding: ledger.AccountBalance
    legs: list[Leg]


@dataclass(frozen=True)
class Created:
    id: int  # the loan
    # False when the request repeated an earlier one (same client_request_id and content).
    created: bool


# --- the schedule ---------------------------------------------------------------------------------


def add_months(start: date, months: int) -> date:
    """The same day `months` later, or the last day of that month when it is shorter (31/01 + 1 = 28/02)."""
    month_index = start.month - 1 + months
    year, month = start.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(start.day, calendar.monthrange(year, month)[1]))


def default_first_due_on(business_date: date) -> date:
    """What the form proposes: a month after the loan (editable down to the loan's own date)."""
    return add_months(business_date, 1)


def default_schedule(principal: Decimal, count: int, first_due_on: date) -> list[Installment]:
    """`count` equal monthly installments from `first_due_on`; the last absorbs the rounding, so the sum is exact."""
    _check_count(count)
    base = (principal / count).quantize(CENT, rounding=ROUND_DOWN)
    if base <= 0:
        raise AppError(
            422,
            "principal_too_small",
            f"${money(principal)} no alcanza para {count} cuotas de al menos $0,01.",
            {"principal": money(principal), "installment_count": count},
        )
    last = principal - base * (count - 1)
    return [Installment(add_months(first_due_on, k), last if k == count - 1 else base) for k in range(count)]


def command(
    employee_id: int,
    business_date: date,
    principal: Decimal,
    funding_account_id: int,
    installment_count: int,
    first_due_on: date,
    installments: list[Installment] | None,
    description: str | None,
    reference: str | None,
) -> Command:
    """Normalize a loan; without `installments` the schedule is the default one."""
    principal = principal.quantize(CENT)
    if first_due_on < business_date:
        raise _due_before_loan(first_due_on, business_date)
    if installments is None:
        schedule = default_schedule(principal, installment_count, first_due_on)
    else:
        _check_count(installment_count)
        schedule = [Installment(i.due_on, i.amount.quantize(CENT)) for i in installments]
        _check_schedule(schedule, principal, installment_count, first_due_on, business_date)
    return Command(
        employee_id=employee_id,
        business_date=business_date,
        principal=principal,
        funding_account_id=funding_account_id,
        installments=tuple(schedule),
        description=" ".join((description or "").split()) or None,
        reference=" ".join((reference or "").split()) or None,
    )


# --- giving a loan --------------------------------------------------------------------------------


def plan(db: Session, actor: User, cmd: Command, *, lock: bool = False) -> Plan:
    """Validate a loan (the employee row locked FOR SHARE when `lock`) and say what it would publish. Funds are
    checked here only as a preview; the ledger checks them again, under its locks, when publishing."""
    _require_admin(actor, "Solo un administrador de Empleados da un préstamo.")
    _require_cash_role(actor)
    query = select(Employee).where(Employee.id == cmd.employee_id)
    employee = db.scalars(query.with_for_update(read=True) if lock else query).first()
    if employee is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    today = business_today()
    if cmd.business_date > today:
        raise AppError(422, "future_business_date", "La fecha del préstamo no puede ser futura.")
    if not employee.active and cmd.business_date >= today:
        raise AppError(
            422,
            "inactive_employee",
            "El empleado está inactivo: solo se registra un préstamo con fecha anterior a hoy, para documentar lo que "
            "ya pasó.",
            {"employee_id": cmd.employee_id},
        )
    funding = _funding_account(db, cmd.funding_account_id)
    lowest, on = ledger.lowest_balance_since(db, funding.account_id, cmd.business_date)
    if lowest < cmd.principal:
        raise AppError(
            409,
            "insufficient_funds",
            f"{funding.name} quedaría con saldo negativo el {on:%d/%m/%Y}: no alcanza para prestar "
            f"${money(cmd.principal)}.",
            {
                "account_id": funding.account_id,
                "business_date": on.isoformat(),
                "balance": money(lowest),
                "amount": money(-cmd.principal),
            },
        )
    internal = ledger.employee_account_id(db, employee.id)
    legs = [
        Leg(funding.account_id, funding.name, funding.kind, -cmd.principal),
        Leg(internal, f"Cuenta interna · {employee.full_name}", "EMPLOYEE", cmd.principal),
    ]
    return Plan(command=cmd, employee=employee, funding=funding, legs=legs)


def create(db: Session, actor: User, cmd: Command, client_request_id: UUID) -> Created:
    fingerprint = idempotency.fingerprint(actor.id, SOURCE, _request(cmd))
    # A retry is answered before anything else is validated: it must get the same result as the first time.
    existing = _by_request_id(db, client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "employee_loan_id", existing.id)
        return Created(existing.id, created=False)

    planned = plan(db, actor, cmd, lock=True)
    employee = planned.employee
    internal = employee_account_id(db, employee_id=employee.id, employee_name=employee.full_name, user_id=actor.id)
    lock_account(db, internal)
    # Taken now, before the insert's FOR SHARE on it (0027), as for an advance (R8-2's deadlock lesson).
    lock_account(db, cmd.funding_account_id)

    loan = EmployeeLoan(
        employee_id=employee.id,
        employee_account_id=internal,
        business_date=cmd.business_date,
        principal=cmd.principal,
        funding_account_id=cmd.funding_account_id,
        installment_count=cmd.installment_count,
        first_due_on=cmd.first_due_on,
        description=cmd.description,
        reference=cmd.reference,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(loan)
            db.flush()
    except IntegrityError as exc:
        winner = _by_request_id(db, client_request_id)
        db.rollback()  # release the locks taken for the insert that lost
        if winner is None:
            raise _rejected(exc) from exc  # a rule the database saw at insert time (e.g. the box was deactivated)
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "employee_loan_id", winner.id)
        return Created(winner.id, created=False)

    for number, item in enumerate(cmd.installments, start=1):
        db.add(
            EmployeeLoanInstallment(
                loan_id=loan.id,
                number=number,
                due_on=item.due_on,
                amount=item.amount,
                status="SCHEDULED",
                created_by=actor.id,
            )
        )
        db.flush()  # one at a time: each takes the next number (0027)
    operation = ledger.post_operation(
        db,
        actor=actor,
        type="EMPLOYEE_LOAN",
        legs=[ledger.Leg(cmd.funding_account_id, -cmd.principal), ledger.Leg(internal, cmd.principal)],
        client_request_id=uuid5(NAMESPACE, f"employee-loan:{client_request_id}"),
        business_date=cmd.business_date,
        # No name nor concept: whoever reads the operation without the area sees it redacted anyway.
        description=f"Préstamo a empleado · préstamo n.º {loan.id}",
        source=ledger.SourceRef(SOURCE, loan.id),
    ).operation
    loan.operation_id = operation.id
    db.flush()
    record_change(
        db,
        f"{EVENT}.created",
        action="create",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=loan.id,
        before=None,
        after=snapshot(loan) | {"operation_id": operation.id, "installments": _schedule_snapshot(cmd.installments)},
    )
    commit(db)
    return Created(loan.id, created=True)


# --- deferring an installment ---------------------------------------------------------------------


def defer(
    db: Session,
    actor: User,
    installment_id: int,
    reason: str,
    replacements: list[Installment],
    client_request_id: UUID,
) -> Created:
    """Replace a SCHEDULED installment by one or more new ones adding up to exactly its amount. Moves no money."""
    _require_admin(actor, "Solo un administrador de Empleados posterga una cuota.")
    reason = " ".join(reason.split())
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la postergación.")
    if not 1 <= len(replacements) <= MAX_REPLACEMENTS:
        raise AppError(
            422, "replacements_required", f"Indicá entre 1 y {MAX_REPLACEMENTS} cuotas que reemplacen a la postergada."
        )
    items = [Installment(r.due_on, r.amount.quantize(CENT)) for r in replacements]
    fingerprint = idempotency.fingerprint(
        actor.id,
        DEFERRAL_SOURCE,
        {"installment_id": installment_id, "reason": reason, "replacements": _schedule_snapshot(items)},
    )
    replayed = _replayed_deferral(db, client_request_id, fingerprint)
    if replayed is not None:
        return replayed

    found = db.execute(
        select(EmployeeLoan.id, EmployeeLoan.employee_account_id)
        .join(EmployeeLoanInstallment, EmployeeLoanInstallment.loan_id == EmployeeLoan.id)
        .where(EmployeeLoanInstallment.id == installment_id)
    ).one_or_none()
    if found is None:
        raise AppError(404, "installment_not_found", "La cuota no existe.")
    lock_account(db, found.employee_account_id)  # the internal account first (R8-4's lock order)
    loan = _lock_loan(db, found.id)
    installment = db.scalars(
        select(EmployeeLoanInstallment)
        .where(EmployeeLoanInstallment.id == installment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    replayed = _replayed_deferral(db, client_request_id, fingerprint)  # a twin request that won the lock
    if replayed is not None:
        db.commit()  # release the locks
        return replayed
    if loan.reversal_operation_id is not None:
        raise AppError(409, "employee_loan_reversed", "El préstamo fue revertido: sus cuotas ya no se postergan.")
    if installment.status != "SCHEDULED":
        raise AppError(
            409,
            "installment_not_scheduled",
            f"La cuota {installment.number} ya fue postergada: para moverla otra vez, postergá la cuota que la "
            "reemplazó."
            if installment.status == "DEFERRED"
            else f"La cuota {installment.number} está cancelada.",
            {"installment_id": installment.id, "status": installment.status},
        )
    if payrolls.applied_installment_ids(db, [installment.id]):
        raise AppError(
            409,
            "installment_applied",
            f"Una liquidación cerrada ya aplicó la cuota {installment.number}: no se posterga. Reabrí la liquidación "
            "y excluila.",
        )
    for item in items:
        if item.amount <= 0:
            raise AppError(422, "invalid_installment_amount", "Cada cuota nueva tiene que ser mayor a cero.")
        if item.due_on <= installment.due_on:
            raise AppError(
                422,
                "replacement_due_not_after_original",
                f"Postergar es mover la cuota {installment.number} más adelante: cada cuota nueva tiene que vencer "
                f"después del {installment.due_on.strftime('%d/%m/%Y')}.",
                {"due_on": item.due_on.isoformat(), "deferred_due_on": installment.due_on.isoformat()},
            )
    if any(b.due_on < a.due_on for a, b in pairwise(items)):
        raise AppError(422, "schedule_out_of_order", "Las cuotas nuevas tienen que ir en orden de vencimiento.")
    total = sum((i.amount for i in items), Decimal("0"))
    if total != installment.amount:
        raise AppError(
            422,
            "replacement_mismatch",
            f"Las cuotas nuevas suman ${money(total)} y la cuota {installment.number} es de "
            f"${money(installment.amount)}: tienen que coincidir.",
            {"replaced_amount": money(installment.amount), "new_total": money(total)},
        )

    installment.status = "DEFERRED"
    installment.deferred_reason = reason
    installment.deferred_by = actor.id
    installment.deferred_at = datetime.now(UTC)
    installment.deferral_request_id = client_request_id
    installment.deferral_fingerprint = fingerprint
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:  # the request id was used to defer another installment
        db.rollback()
        replayed = _replayed_deferral(db, client_request_id, fingerprint)
        if replayed is not None:
            return replayed
        raise _rejected(exc) from exc
    next_number = db.scalar(
        select(func.max(EmployeeLoanInstallment.number)).where(EmployeeLoanInstallment.loan_id == loan.id)
    )
    assert next_number is not None
    created_numbers = []
    for item in items:
        next_number += 1
        db.add(
            EmployeeLoanInstallment(
                loan_id=loan.id,
                number=next_number,
                due_on=item.due_on,
                amount=item.amount,
                status="SCHEDULED",
                deferred_from_id=installment.id,
                created_by=actor.id,
            )
        )
        db.flush()
        created_numbers.append(next_number)
    record_change(
        db,
        f"{EVENT}.deferred",
        action="defer",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=loan.id,
        before={
            "installment": installment.number,
            "due_on": installment.due_on.isoformat(),
            "amount": money(installment.amount),
        },
        after={
            "reason": reason,
            "replaced_by": [
                {"number": n, **s} for n, s in zip(created_numbers, _schedule_snapshot(items), strict=True)
            ],
        },
    )
    commit(db)
    return Created(loan.id, created=True)


# --- reversing a loan -----------------------------------------------------------------------------


def applied_installments(db: Session, loan_id: int) -> int:
    """How many installments of the loan a close in effect applied: a loan with any can no longer be reversed."""
    ids = list(db.scalars(select(EmployeeLoanInstallment.id).where(EmployeeLoanInstallment.loan_id == loan_id)))
    return len(payrolls.applied_installment_ids(db, ids))


def reverse(db: Session, actor: User, loan_id: int, reason: str, client_request_id: UUID) -> Created:
    """Undo the whole loan (admin of the area + operator in Rendiciones, with a reason) on its original date."""
    _require_admin(actor, "Solo un administrador de Empleados revierte un préstamo.")
    _require_cash_role(actor)
    reason = " ".join(reason.split())
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo del reverso.")
    account = db.scalar(select(EmployeeLoan.employee_account_id).where(EmployeeLoan.id == loan_id))
    if account is None:
        raise AppError(404, "employee_loan_not_found", "El préstamo no existe.")
    lock_account(db, account)  # the internal account first (R8-4's lock order)
    loan = _lock_loan(db, loan_id)
    request_id = uuid5(NAMESPACE, f"employee-loan-reversal:{client_request_id}")
    if loan.reversal_operation_id is not None:
        reversal = ledger.get_operation(db, loan.reversal_operation_id)
        if reversal.client_request_id == request_id:
            if (loan.reversed_by, loan.reversal_reason) != (actor.id, reason):
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Ese identificador de solicitud ya se usó para un reverso distinto.",
                    {"operation_id": reversal.id},
                )
            db.commit()  # a retry of the same reversal: release the locks
            return Created(loan.id, created=False)
        raise AppError(
            409,
            "employee_loan_already_reversed",
            "El préstamo ya fue revertido.",
            {"reversal_operation_id": reversal.id},
        )
    if applied_installments(db, loan.id):
        raise AppError(
            409,
            "loan_applied",
            "Una liquidación cerrada ya aplicó cuotas de este préstamo: no se revierte. Reabrí la liquidación y "
            "excluilas.",
        )
    assert loan.operation_id is not None  # a published loan always has it (0027)
    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=loan.operation_id,
        source=ledger.SourceRef(SOURCE, loan.id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    loan.reversal_operation_id = reversal.id
    loan.reversed_by = actor.id
    loan.reversed_at = datetime.now(UTC)
    loan.reversal_reason = reason
    db.flush()
    cancelled = list(
        db.scalars(
            update(EmployeeLoanInstallment)
            .where(EmployeeLoanInstallment.loan_id == loan.id, EmployeeLoanInstallment.status == "SCHEDULED")
            .values(status="CANCELLED")
            .returning(EmployeeLoanInstallment.number)
            .execution_options(synchronize_session=False)
        )
    )
    record_change(
        db,
        f"{EVENT}.reversed",
        action="reverse",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=loan.id,
        before={"operation_id": loan.operation_id},
        after={
            "reversal_operation_id": reversal.id,
            "business_date": reversal.business_date.isoformat(),
            "reason": reason,
            "cancelled_installments": sorted(cancelled),
        },
    )
    commit(db)
    return Created(loan.id, created=True)


# --- shared ---------------------------------------------------------------------------------------


def snapshot(loan: EmployeeLoan) -> dict[str, Any]:
    return {
        "employee_id": loan.employee_id,
        "employee_account_id": loan.employee_account_id,
        "business_date": loan.business_date.isoformat(),
        "principal": money(loan.principal),
        "funding_account_id": loan.funding_account_id,
        "installment_count": loan.installment_count,
        "first_due_on": loan.first_due_on.isoformat(),
        "description": loan.description,
        "reference": loan.reference,
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
    logger.warning("employee_loan_rejected", extra={"sqlstate": getattr(exc.orig, "sqlstate", None)})
    return AppError(
        409,
        "invalid_employee_loan",
        "El préstamo no cumple las reglas de la cuenta interna y no se registró. Revisá los datos.",
    )


def _schedule_snapshot(items: tuple[Installment, ...] | list[Installment]) -> list[dict[str, str]]:
    return [{"due_on": i.due_on.isoformat(), "amount": money(i.amount)} for i in items]


def _request(cmd: Command) -> dict[str, Any]:
    return {
        "employee_id": cmd.employee_id,
        "business_date": cmd.business_date.isoformat(),
        "principal": money(cmd.principal),
        "funding_account_id": cmd.funding_account_id,
        "installments": _schedule_snapshot(cmd.installments),
        "description": cmd.description,
        "reference": cmd.reference,
    }


def _check_count(count: int) -> None:
    if not 1 <= count <= MAX_INSTALLMENTS:
        raise AppError(
            422, "invalid_installment_count", f"La cantidad de cuotas va de 1 a {MAX_INSTALLMENTS}.", {"count": count}
        )


def _check_schedule(
    schedule: list[Installment], principal: Decimal, count: int, first_due_on: date, business_date: date
) -> None:
    if len(schedule) != count:
        raise AppError(
            422,
            "schedule_mismatch",
            f"El cronograma tiene {len(schedule)} cuotas y se pidieron {count}.",
            {"installments": len(schedule), "installment_count": count},
        )
    if schedule[0].due_on != first_due_on:
        raise AppError(
            422, "schedule_mismatch", "La primera cuota del cronograma no vence en la primera fecha indicada."
        )
    for previous, item in zip([None, *schedule[:-1]], schedule, strict=True):
        if item.amount <= 0:
            raise AppError(422, "invalid_installment_amount", "Cada cuota tiene que ser mayor a cero.")
        if item.due_on < business_date:
            raise _due_before_loan(item.due_on, business_date)
        if previous is not None and item.due_on < previous.due_on:
            raise AppError(
                422,
                "schedule_out_of_order",
                "Las cuotas van en orden: cada una vence el mismo día o después que la anterior.",
            )
    total = sum((i.amount for i in schedule), Decimal("0"))
    if total != principal:
        raise AppError(
            422,
            "schedule_total_mismatch",
            f"Las cuotas suman ${money(total)} y el préstamo es de ${money(principal)}: tienen que coincidir.",
            {"principal": money(principal), "total": money(total)},
        )


def _due_before_loan(due_on: date, business_date: date) -> AppError:
    return AppError(
        422,
        "due_before_loan",
        f"Una cuota no puede vencer antes del préstamo ({business_date:%d/%m/%Y}).",
        {"due_on": due_on.isoformat(), "business_date": business_date.isoformat()},
    )


def _require_admin(actor: User, message: str) -> None:
    if not has_area_access(actor, AREA, "admin"):
        raise AppError(403, "forbidden", message)


def _require_cash_role(actor: User) -> None:
    """A loan moves a cash box or the bank, so it also needs operator in Rendiciones (ADR 010 D9, as an advance)."""
    if not has_module_access(actor, MODULE_KEY, "operator"):
        raise AppError(
            403, "forbidden", "Un préstamo mueve una caja o el banco: necesitás además el rol operador en Rendiciones."
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
            "Un préstamo se entrega desde una caja o un banco activos.",
            {"funding_account_id": account_id},
        )
    return account


def _lock_loan(db: Session, loan_id: int) -> EmployeeLoan:
    return db.scalars(
        select(EmployeeLoan)
        .where(EmployeeLoan.id == loan_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _replayed_deferral(db: Session, client_request_id: UUID, fingerprint: str) -> Created | None:
    done = db.scalars(
        select(EmployeeLoanInstallment)
        .where(EmployeeLoanInstallment.deferral_request_id == client_request_id)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if done is None:
        return None
    assert done.deferral_fingerprint is not None
    idempotency.check_replay(done.deferral_fingerprint, fingerprint, "installment_id", done.id)
    return Created(done.loan_id, created=False)


def _by_request_id(db: Session, client_request_id: UUID) -> EmployeeLoan | None:
    return db.scalars(select(EmployeeLoan).where(EmployeeLoan.client_request_id == client_request_id)).one_or_none()
