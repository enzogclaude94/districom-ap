"""Internal payrolls and what they apply of the internal account (R8-4, ADR 010 §4-§8, Enzo's R8-4 decisions).

«Liquidación interna», never a legal payslip. A payroll is for one employee and an inclusive period; REGULAR (the
normal cycle: two REGULAR of an employee never overlap) or EXTRAORDINARY (may overlap; never applies loan installments).

Draft (while OPEN): credits (haberes: SALARY, BONUS, INCENTIVE, OTHER_CREDIT) and the preparer's choices. A REGULAR
payroll proposes, included in full, every leg of the internal account still applicable up to the period's end
(shortages and surpluses of settlements and their corrections, pending write-offs, consumptions, advances, adjustments
and the reversals of all of them) and every installment SCHEDULED, of a loan in effect, due up to the period's end and
not applied. An EXTRAORDINARY payroll lists the legs without including them and never lists installments. A choice
excludes an item or applies part of a leg; an installment is always whole (Enzo's Q2: proposing is not applying, the
close confirms). Nothing of the draft is an application.

Close (admin of the area, with the preview token it confirms): one transaction freezes the version (its credit lines,
its applications pointing at each leg with the leg's sign, the snapshot) and publishes

    PAYROLL_CLOSE   «Sueldos y bonos internos» +credits   internal account -credits   (on the period's end)

when the credits are > 0 (FINANCIAL), or nothing (NO_ACCRUAL: no zero legs, maybe applications and a net > 0).
Applications move no money: those legs already exist. Net = credits - deductions + credits applied, never negative and
never cut automatically: the preparer excludes, applies part or defers an installment. After a reopening the new close
supersedes the previous one in the same transaction: it reverses the previous PAYROLL_CLOSE and publishes its own
(`replace_operation`), withdraws it (FINANCIAL -> NO_ACCRUAL) or publishes the first one (NO_ACCRUAL -> FINANCIAL); a
NO_ACCRUAL close never invents a reversal. The previous applications stay in effect until then.

Reopen (admin, with a reason): the close stays in effect (OPEN, version + 1) until the new one. A payroll with payouts
in effect on its close will not reopen (R8-5): reverse them first. Cancel (admin, with a reason): only a payroll never
closed.

Lock order, always: the employee's internal account, the payroll, the loans of the installments it may apply (FOR
SHARE, by id), then (inside the ledger) the accounts that lose value. Every flow that undoes something a payroll may
apply takes the internal account first too (a movement's or a loan's reversal, a deferral), so none waits in a cycle.
Idempotent: creating by `client_request_id`; closing by (payroll, version) and its fingerprint.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError, database_timeout
from app.core.logging import db_error_fields
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery.service import money
from app.modules.settlements.employee_finance import compensation
from app.modules.settlements.employee_finance.access import AREA
from app.modules.settlements.employee_finance.models import (
    EmployeeCompensationTerm,
    EmployeeLoan,
    EmployeeLoanInstallment,
    EmployeePayout,
    EmployeePayroll,
    EmployeePayrollApplication,
    EmployeePayrollChoice,
    EmployeePayrollClosing,
    EmployeePayrollClosingLine,
    EmployeePayrollLine,
    EmployeePayrollReopening,
)
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import employee_account_id, lock_account
from app.platform.audit.service import record_change
from app.platform.auth.access import has_area_access
from app.platform.auth.models import User
from app.platform.parties.models import Employee

logger = logging.getLogger(__name__)

SOURCE = "employee_payroll"
ENTITY = "employee_payroll"
EVENT = "settlements.employee_finance.payroll"
CLOSE_TYPE = "PAYROLL_CLOSE"
# Namespace of the deterministic ledger request ids of payroll closes; never change it.
NAMESPACE = UUID("8d3b7f20-5e1a-4c6b-9f42-0a7c1e5d9b63")
ZERO = Decimal("0.00")
CENT = Decimal("0.01")

Kind = Literal["REGULAR", "EXTRAORDINARY"]
LineKind = Literal["SALARY", "BONUS", "INCENTIVE", "OTHER_CREDIT"]
# What a payroll applies (each leg on the internal account), and the reversals of those.
APPLICABLE_TYPES = frozenset(
    {"SETTLEMENT_CLOSE", "PENDING_WRITE_OFF", "EMPLOYEE_CONSUMPTION", "EMPLOYEE_ADVANCE", "EMPLOYEE_ADJUSTMENT"}
)
LINE_LABEL = {"SALARY": "Sueldo", "BONUS": "Bono", "INCENTIVE": "Incentivo", "OTHER_CREDIT": "Otro haber"}


@dataclass(frozen=True)
class Created:
    id: int
    created: bool


# --- the draft: what a payroll proposes and what it would apply ---------------------------------------------------


@dataclass(frozen=True)
class LegItem:
    """A leg of the internal account the payroll may apply, with what is still applicable of it elsewhere."""

    leg: ledger.EmployeeLeg
    # What the closes in effect of OTHER payrolls already applied of it (absolute): this payroll's own close in effect
    # is replaced by its next close, so a correction sees the leg as the previous version did.
    applied_elsewhere: Decimal
    proposed: bool
    included: bool
    # Absolute amount to apply when included: the choice's, or everything still applicable.
    amount: Decimal
    chosen_amount: Decimal | None

    @property
    def available(self) -> Decimal:
        return abs(self.leg.amount) - self.applied_elsewhere

    @property
    def sign(self) -> int:
        return 1 if self.leg.amount > 0 else -1

    @property
    def signed_amount(self) -> Decimal:
        return self.amount * self.sign


@dataclass(frozen=True)
class InstallmentItem:
    installment: EmployeeLoanInstallment
    loan: EmployeeLoan
    loan_entry_id: int
    included: bool


@dataclass
class Draft:
    """A payroll's draft as it stands: credits, the items it may apply, the totals and what stops a close."""

    payroll: EmployeePayroll
    terms: list[EmployeeCompensationTerm]
    lines: list[EmployeePayrollLine]
    legs: list[LegItem]
    installments: list[InstallmentItem]
    blockers: list[tuple[str, str]] = field(default_factory=list)

    @property
    def credits(self) -> Decimal:
        return sum((line.amount for line in self.lines), ZERO)

    @property
    def deductions(self) -> Decimal:
        legs = sum((i.amount for i in self.legs if i.included and i.sign > 0), ZERO)
        return legs + sum((i.installment.amount for i in self.installments if i.included), ZERO)

    @property
    def credits_applied(self) -> Decimal:
        return sum((i.amount for i in self.legs if i.included and i.sign < 0), ZERO)

    @property
    def net(self) -> Decimal:
        return self.credits - self.deductions + self.credits_applied

    @property
    def deduction_pct(self) -> Decimal | None:
        """Deductions over credits, in % (Enzo's D6: shown, never capped); None without credits."""
        if self.credits <= 0:
            return None
        return (self.deductions * 100 / self.credits).quantize(CENT)


@dataclass(frozen=True)
class SalaryHint:
    """What the draft says about the salary (Enzo's D5): a proposal from the only term in effect over a full
    calendar month, or why it must be typed by hand."""

    status: Literal["PROPOSED", "MANUAL", "NONE"]
    message: str
    term_id: int | None = None
    amount: Decimal | None = None


def salary_hint(payroll: EmployeePayroll, terms: list[EmployeeCompensationTerm]) -> SalaryHint:
    if payroll.kind != "REGULAR":
        return SalaryHint("NONE", "Una liquidación extraordinaria no propone sueldo.")
    start, end = payroll.period_start, payroll.period_end
    full_month = start.day == 1 and _month_end(start) == end
    if not terms:
        return SalaryHint("MANUAL", "El empleado no tiene condición salarial vigente en el período: cargá el sueldo.")
    covering = [t for t in terms if t.valid_from <= start and (t.valid_to is None or t.valid_to >= end)]
    if full_month and len(terms) == 1 and covering:
        term = covering[0]
        return SalaryHint("PROPOSED", "Sueldo base interno de la condición vigente.", term.id, term.base_salary)
    if not full_month:
        return SalaryHint(
            "MANUAL",
            "El período no es un mes calendario completo: no hay prorrateo automático. Cargá el sueldo con una nota.",
        )
    return SalaryHint(
        "MANUAL",
        "Hay un cambio de condición salarial dentro del período: no hay prorrateo automático. Cargá el sueldo con una "
        "nota.",
    )


def draft(db: Session, payroll: EmployeePayroll) -> Draft:
    """Read the draft of a payroll (no lock): what it proposes, what was chosen and what a close would freeze."""
    terms = compensation.overlapping(db, payroll.employee_id, payroll.period_start, payroll.period_end)
    lines = list(
        db.scalars(
            select(EmployeePayrollLine)
            .where(EmployeePayrollLine.payroll_id == payroll.id)
            .order_by(EmployeePayrollLine.id)
        )
    )
    choices = list(db.scalars(select(EmployeePayrollChoice).where(EmployeePayrollChoice.payroll_id == payroll.id)))
    by_entry = {c.ledger_entry_id: c for c in choices if c.ledger_entry_id is not None}
    by_installment = {c.installment_id: c for c in choices if c.installment_id is not None}
    own_closing = payroll.current_closing_id
    applied = _applied_elsewhere(db, payroll.employee_account_id, own_closing)

    legs: list[LegItem] = []
    blockers: list[tuple[str, str]] = []
    for leg in ledger.employee_legs(db, payroll.employee_account_id, until=payroll.period_end):
        if not _applicable(leg):
            continue
        taken = applied.get(leg.entry_id, ZERO)
        available = abs(leg.amount) - taken
        if available <= 0:
            continue
        proposed = payroll.kind == "REGULAR"
        choice = by_entry.get(leg.entry_id)
        included = choice.included if choice is not None else proposed
        chosen = choice.amount if choice is not None else None
        amount = chosen if chosen is not None else available
        if included and chosen is not None and chosen > available:
            blockers.append(
                (
                    "choice_exceeds_available",
                    f"Un importe elegido (${money(chosen)}) supera lo que queda por aplicar del movimiento "
                    f"(${money(available)}).",
                )
            )
        legs.append(LegItem(leg, taken, proposed, included, amount, chosen))

    installments: list[InstallmentItem] = []
    if payroll.kind == "REGULAR":
        for installment, loan in _due_installments(db, payroll):
            choice = by_installment.get(installment.id)
            assert loan.operation_id is not None  # a published loan always has it (0027)
            entry = ledger.leg_of_operation(db, loan.operation_id, payroll.employee_account_id)
            assert entry is not None
            installments.append(
                InstallmentItem(installment, loan, entry, choice.included if choice is not None else True)
            )

    result = Draft(payroll, terms, lines, legs, installments, blockers)
    if payroll.status != "OPEN":
        result.blockers.insert(0, ("payroll_not_open", "La liquidación no está abierta."))
    if payroll.period_end > business_today():
        result.blockers.append(
            (
                "period_not_ended",
                f"El período termina el {payroll.period_end:%d/%m/%Y}: se cierra desde ese día (el devengado lleva "
                "esa fecha).",
            )
        )
    if result.net < 0:
        result.blockers.append(
            (
                "net_negative",
                f"El neto daría ${money(result.net)}: excluí movimientos, aplicá una parte o postergá una cuota. "
                "Nada se recorta solo.",
            )
        )
    return result


def preview_token(db: Session, d: Draft) -> str:
    """SHA-256 of everything a close would freeze (Enzo's R8-4 point 15). Any change that could alter it (a term, a
    line, a choice, a new or reversed leg, an installment deferred, what other payrolls applied, the version) changes
    it, and the close is refused as `payroll_preview_stale`."""
    payroll = d.payroll
    payload: dict[str, Any] = {
        "payroll": payroll.id,
        "employee": payroll.employee_id,
        "kind": payroll.kind,
        "period": [payroll.period_start.isoformat(), payroll.period_end.isoformat()],
        "version": payroll.version,
        "status": payroll.status,
        "closing_in_effect": payroll.current_closing_id,
        "terms": sorted(
            [t.id, t.valid_from.isoformat(), t.valid_to.isoformat() if t.valid_to else None, money(t.base_salary)]
            for t in d.terms
        ),
        "lines": sorted(
            [line.id, line.kind, money(line.amount), line.description, line.source, line.compensation_term_id]
            for line in d.lines
        ),
        "legs": sorted(
            [
                i.leg.entry_id,
                money(i.leg.amount),
                money(i.applied_elsewhere),
                i.included,
                money(i.amount),
                money(i.chosen_amount) if i.chosen_amount is not None else None,
                ledger.reversal_of(db, i.leg.operation_id),
            ]
            for i in d.legs
        ),
        "installments": sorted(
            [
                i.installment.id,
                i.installment.status,
                i.installment.due_on.isoformat(),
                money(i.installment.amount),
                i.included,
                i.loan.reversal_operation_id,
            ]
            for i in d.installments
        ),
        "credits": money(d.credits),
        "deductions": money(d.deductions),
        "credits_applied": money(d.credits_applied),
        "net": money(d.net),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- writing the draft --------------------------------------------------------------------------------------------


def create(
    db: Session,
    actor: User,
    employee_id: int,
    kind: Kind,
    period_start: date,
    period_end: date,
    payment_date: date | None,
    notes: str | None,
    client_request_id: UUID,
) -> Created:
    """Open a payroll (operator of the area). A REGULAR one over a full calendar month with exactly one salary term in
    effect gets its SALARY proposed; otherwise the draft says why the salary is typed by hand."""
    _require(actor, "operator", "No tenés permiso para preparar liquidaciones.")
    notes = " ".join((notes or "").split()) or None
    request = {
        "employee_id": employee_id,
        "kind": kind,
        "period": [period_start.isoformat(), period_end.isoformat()],
        "payment_date": payment_date.isoformat() if payment_date else None,
        "notes": notes,
    }
    fingerprint = idempotency.fingerprint(actor.id, SOURCE, request)
    existing = _by_request_id(db, client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "payroll_id", existing.id)
        return Created(existing.id, created=False)
    if period_start > period_end:
        raise AppError(422, "invalid_period", "El período empieza después de terminar.")
    employee = db.scalars(select(Employee).where(Employee.id == employee_id).with_for_update(read=True)).first()
    if employee is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    account = employee_account_id(db, employee_id=employee.id, employee_name=employee.full_name, user_id=actor.id)
    lock_account(db, account)
    payroll = EmployeePayroll(
        employee_id=employee.id,
        employee_account_id=account,
        kind=kind,
        period_start=period_start,
        period_end=period_end,
        payment_date=payment_date,
        notes=notes,
        status="OPEN",
        version=1,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(payroll)
            db.flush()
    except IntegrityError as exc:
        winner = _by_request_id(db, client_request_id)
        db.rollback()
        if winner is not None:
            idempotency.check_replay(winner.request_fingerprint, fingerprint, "payroll_id", winner.id)
            return Created(winner.id, created=False)
        raise _integrity(exc) from exc
    hint = salary_hint(payroll, compensation.overlapping(db, employee.id, period_start, period_end))
    if hint.status == "PROPOSED":
        assert hint.amount is not None
        db.add(
            EmployeePayrollLine(
                payroll_id=payroll.id,
                kind="SALARY",
                amount=hint.amount,
                source="TERM",
                compensation_term_id=hint.term_id,
                created_by=actor.id,
            )
        )
    record_change(
        db,
        f"{EVENT}.created",
        action="create",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payroll.id,
        before=None,
        after=request | {"salary_proposed": money(hint.amount) if hint.amount is not None else None},
    )
    commit(db)
    return Created(payroll.id, created=True)


def update_period(
    db: Session,
    actor: User,
    payroll_id: int,
    period_start: date,
    period_end: date,
    payment_date: date | None,
    notes: str | None,
) -> None:
    """Change the period, payment date or notes of a payroll never closed (operator of the area)."""
    _require(actor, "operator", "No tenés permiso para preparar liquidaciones.")
    if period_start > period_end:
        raise AppError(422, "invalid_period", "El período empieza después de terminar.")
    payroll = _lock_draft(db, payroll_id)
    if payroll.current_closing_id is not None:
        raise AppError(409, "payroll_was_closed", "El período de una liquidación ya cerrada no cambia.")
    before = _period(payroll)
    payroll.period_start, payroll.period_end = period_start, period_end
    payroll.payment_date = payment_date
    payroll.notes = " ".join((notes or "").split()) or None
    _flush(db)
    record_change(
        db,
        f"{EVENT}.updated",
        action="update",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payroll.id,
        before=before,
        after=_period(payroll),
    )
    commit(db)


def add_line(
    db: Session, actor: User, payroll_id: int, kind: LineKind, amount: Decimal, description: str | None
) -> int:
    """Add a credit to the draft (admin of the area: what the company will owe). A SALARY typed by hand, and
    OTHER_CREDIT, need a description; an INCENTIVE is MANUAL (nothing is read from Comercial)."""
    _require(actor, "admin", "Solo un administrador de Empleados carga haberes.")
    description = _line_description(kind, description)
    payroll = _lock_draft(db, payroll_id)
    line = EmployeePayrollLine(
        payroll_id=payroll.id,
        kind=kind,
        amount=amount.quantize(CENT),
        description=description,
        source="MANUAL",
        created_by=actor.id,
    )
    db.add(line)
    _flush(db)
    _audit_draft(db, actor, payroll, "line_added", {"line_id": line.id, "kind": kind, "amount": money(line.amount)})
    commit(db)
    return line.id


def update_line(db: Session, actor: User, line_id: int, amount: Decimal, description: str | None) -> int:
    _require(actor, "admin", "Solo un administrador de Empleados carga haberes.")
    line = db.get(EmployeePayrollLine, line_id)
    if line is None:
        raise AppError(404, "payroll_line_not_found", "El haber no existe.")
    payroll = _lock_draft(db, line.payroll_id)
    # A proposed salary changed by hand is no longer the term's: it becomes MANUAL and needs its note.
    description = _line_description(line.kind, description, manual_salary=True)
    before = {"amount": money(line.amount), "description": line.description, "source": line.source}
    line.amount = amount.quantize(CENT)
    line.description = description
    if line.source == "TERM":
        line.source, line.compensation_term_id = "MANUAL", None
    _flush(db)
    _audit_draft(db, actor, payroll, "line_changed", {"line_id": line.id, "before": before, "amount": money(amount)})
    commit(db)
    return payroll.id


def remove_line(db: Session, actor: User, line_id: int) -> int:
    _require(actor, "admin", "Solo un administrador de Empleados carga haberes.")
    line = db.get(EmployeePayrollLine, line_id)
    if line is None:
        raise AppError(404, "payroll_line_not_found", "El haber no existe.")
    payroll = _lock_draft(db, line.payroll_id)
    gone = {"line_id": line.id, "kind": line.kind, "amount": money(line.amount)}
    db.delete(line)
    _flush(db)
    _audit_draft(db, actor, payroll, "line_removed", gone)
    commit(db)
    return payroll.id


def choose(
    db: Session,
    actor: User,
    payroll_id: int,
    *,
    ledger_entry_id: int | None,
    installment_id: int | None,
    included: bool,
    amount: Decimal | None,
) -> None:
    """Include or exclude one item of the draft, or apply part of a leg (operator of the area). An installment is
    whole: it is included or not, never reduced (Enzo's Q2)."""
    _require(actor, "operator", "No tenés permiso para preparar liquidaciones.")
    if (ledger_entry_id is None) == (installment_id is None):
        raise AppError(422, "one_item_required", "Elegí un movimiento o una cuota.")
    if installment_id is not None and amount is not None:
        raise AppError(422, "installment_whole", "Una cuota se aplica completa: no se reduce su importe.")
    if not included and amount is not None:
        amount = None
    payroll = _lock_draft(db, payroll_id)
    if installment_id is not None and payroll.kind != "REGULAR":
        raise AppError(
            422, "extraordinary_without_installments", "Una liquidación extraordinaria no aplica cuotas de préstamos."
        )
    current = _draft_items(db, payroll)
    if ledger_entry_id is not None:
        item = current.legs.get(ledger_entry_id)
        if item is None:
            raise AppError(404, "payroll_item_not_found", "El movimiento no se puede aplicar en esta liquidación.")
        if amount is not None and not 0 < amount <= item.available:
            raise AppError(
                422,
                "invalid_application_amount",
                f"El importe a aplicar tiene que ser mayor a cero y hasta ${money(item.available)}.",
                {"available": money(item.available)},
            )
    elif installment_id not in current.installments:
        raise AppError(404, "payroll_item_not_found", "La cuota no se puede aplicar en esta liquidación.")
    query = select(EmployeePayrollChoice).where(EmployeePayrollChoice.payroll_id == payroll.id)
    query = query.where(
        EmployeePayrollChoice.ledger_entry_id == ledger_entry_id
        if ledger_entry_id is not None
        else EmployeePayrollChoice.installment_id == installment_id
    )
    choice = db.scalars(query).one_or_none()
    if choice is None:
        choice = EmployeePayrollChoice(
            payroll_id=payroll.id, ledger_entry_id=ledger_entry_id, installment_id=installment_id, updated_by=actor.id
        )
        db.add(choice)
    choice.included = included
    choice.amount = amount.quantize(CENT) if amount is not None else None
    choice.updated_by = actor.id
    choice.updated_at = datetime.now(UTC)
    _flush(db)
    _audit_draft(
        db,
        actor,
        payroll,
        "choice",
        {
            "ledger_entry_id": ledger_entry_id,
            "installment_id": installment_id,
            "included": included,
            "amount": money(amount) if amount is not None else None,
        },
    )
    commit(db)


def cancel(db: Session, actor: User, payroll_id: int, reason: str) -> None:
    """Cancel a payroll never closed (admin of the area, with a reason): it frees its period."""
    _require(actor, "admin", "Solo un administrador de Empleados anula una liquidación.")
    reason = " ".join(reason.split())
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la anulación.")
    payroll = _lock_draft(db, payroll_id)
    if payroll.current_closing_id is not None:
        raise AppError(
            409, "payroll_was_closed", "Una liquidación que ya se cerró no se anula: se reabre y se corrige."
        )
    payroll.status = "CANCELLED"
    payroll.cancelled_by = actor.id
    payroll.cancelled_at = datetime.now(UTC)
    payroll.cancel_reason = reason
    _flush(db)
    record_change(
        db,
        f"{EVENT}.cancelled",
        action="cancel",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payroll.id,
        before={"status": "OPEN"},
        after={"status": "CANCELLED", "reason": reason},
    )
    commit(db)


# --- close and reopen ---------------------------------------------------------------------------------------------


def close(db: Session, actor: User, payroll_id: int, version: int, token: str, confirm: bool) -> Created:
    _require(actor, "admin", "Solo un administrador de Empleados cierra una liquidación.")
    payroll = _lock(db, payroll_id)
    d = draft(db, payroll)
    _lock_loans(db, [i.loan.id for i in d.installments])
    d = draft(db, payroll)  # read again under the locks: what is frozen is what was checked
    fingerprint = _fingerprint(d)

    existing = db.scalars(
        select(EmployeePayrollClosing).where(
            EmployeePayrollClosing.payroll_id == payroll.id, EmployeePayrollClosing.payroll_version == version
        )
    ).one_or_none()
    if existing is not None:
        # This version was closed already: a retry of the same close returns it; anything else is a conflict.
        if existing.request_fingerprint == fingerprint:
            db.commit()  # release the locks
            return Created(payroll.id, created=False)
        if version != payroll.version:
            raise _concurrent(payroll)
        raise AppError(
            409,
            "idempotency_conflict",
            "Esta versión de la liquidación ya se cerró con otro contenido.",
            {"payroll_id": payroll.id, "version": version},
        )
    if payroll.status != "OPEN":
        raise AppError(
            409,
            "payroll_not_open",
            "La liquidación no está abierta.",
            {"payroll_id": payroll.id, "status": payroll.status},
        )
    if version != payroll.version:
        raise _concurrent(payroll)
    if token != preview_token(db, d):
        raise AppError(
            409,
            "payroll_preview_stale",
            "La liquidación cambió desde la última vista previa (haberes, movimientos, cuotas o lo aplicado en otra "
            "liquidación). Revisala nuevamente antes de cerrar.",
            {"payroll_id": payroll.id, "version": payroll.version},
        )
    if d.blockers:
        code, message = d.blockers[0]
        raise AppError(409 if code != "period_not_ended" else 422, code, message, {"payroll_id": payroll.id})
    if not confirm:
        raise AppError(422, "confirmation_required", "Confirmá el cierre de la liquidación.")

    previous = db.get(EmployeePayrollClosing, payroll.current_closing_id) if payroll.current_closing_id else None
    reason = f"Corrección de la liquidación interna n.º {payroll.id}"
    source = ledger.SourceRef(SOURCE, payroll.id)
    operation_id: int | None = None
    reversal_id: int | None = None
    credits = d.credits
    if credits > 0:
        legs = [
            ledger.Leg(ledger.system_account_id(db, "payroll_expense"), credits),
            ledger.Leg(payroll.employee_account_id, -credits),
        ]
        description = f"Liquidación interna n.º {payroll.id} (versión {payroll.version})"
        if previous is not None and previous.operation_id is not None:
            replaced = ledger.replace_operation(
                db,
                actor=actor,
                original_id=previous.operation_id,
                type=CLOSE_TYPE,
                legs=legs,
                client_request_id=_request_id(payroll, "close"),
                reversal_request_id=_request_id(payroll, "reversal"),
                business_date=payroll.period_end,
                description=description,
                reason=reason,
                source=source,
            )
            operation_id, reversal_id = replaced.replacement.id, replaced.reversal.id
        else:
            operation_id = ledger.post_operation(
                db,
                actor=actor,
                type=CLOSE_TYPE,
                legs=legs,
                client_request_id=_request_id(payroll, "close"),
                business_date=payroll.period_end,
                description=description,
                source=source,
            ).operation.id
    elif previous is not None and previous.operation_id is not None:
        # FINANCIAL -> NO_ACCRUAL: the previous accrual is withdrawn and nothing replaces it.
        reversal_id = ledger.withdraw_document_operation(
            db,
            actor=actor,
            original_id=previous.operation_id,
            type=CLOSE_TYPE,
            source=source,
            reason=reason,
            reversal_request_id=_request_id(payroll, "reversal"),
        ).operation.id

    now = datetime.now(UTC)
    if previous is not None:
        # Superseded first: the previous applications stop counting before the new ones take their place.
        previous.superseded_at, previous.superseded_by = now, actor.id
        db.execute(
            update(EmployeePayrollApplication)
            .where(EmployeePayrollApplication.closing_id == previous.id)
            .values(superseded_at=now)
        )
        _flush(db)
    snapshot = _snapshot(db, d)
    closing = EmployeePayrollClosing(
        payroll_id=payroll.id,
        payroll_version=payroll.version,
        kind="FINANCIAL" if credits > 0 else "NO_ACCRUAL",
        operation_id=operation_id,
        reversal_operation_id=reversal_id,
        credits=credits,
        deductions=d.deductions,
        credits_applied=d.credits_applied,
        net=d.net,
        snapshot=snapshot,
        request_fingerprint=fingerprint,
        closed_by=actor.id,
        closed_at=now,
    )
    db.add(closing)
    _flush(db)
    db.add_all(
        EmployeePayrollClosingLine(
            closing_id=closing.id,
            kind=line.kind,
            amount=line.amount,
            description=line.description,
            source=line.source,
            compensation_term_id=line.compensation_term_id,
        )
        for line in d.lines
    )
    db.add_all(
        EmployeePayrollApplication(
            closing_id=closing.id,
            payroll_id=payroll.id,
            employee_account_id=payroll.employee_account_id,
            ledger_entry_id=item.leg.entry_id,
            amount=item.signed_amount,
        )
        for item in d.legs
        if item.included
    )
    db.add_all(
        EmployeePayrollApplication(
            closing_id=closing.id,
            payroll_id=payroll.id,
            employee_account_id=payroll.employee_account_id,
            ledger_entry_id=item.loan_entry_id,
            installment_id=item.installment.id,
            amount=item.installment.amount,
        )
        for item in d.installments
        if item.included
    )
    _flush(db)
    payroll.status = "CLOSED"
    payroll.current_closing_id = closing.id
    _flush(db)
    record_change(
        db,
        f"{EVENT}.closed",
        action="close",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payroll.id,
        before={"status": "OPEN", "version": payroll.version, "closing_in_effect": previous.id if previous else None},
        after={
            "status": "CLOSED",
            "version": payroll.version,
            "closing_id": closing.id,
            "kind": closing.kind,
            "operation_id": operation_id,
            "reversal_operation_id": reversal_id,
            "credits": money(closing.credits),
            "deductions": money(closing.deductions),
            "credits_applied": money(closing.credits_applied),
            "net": money(closing.net),
        },
    )
    commit(db)
    return Created(payroll.id, created=True)


def payouts_in_effect(db: Session, payroll_id: int) -> int:
    """Payouts in effect of the payroll's close in effect (R8-5): a paid payroll does not reopen (D7). A reversed
    payout does not count."""
    return (
        db.scalar(
            select(func.count())
            .select_from(EmployeePayout)
            .join(EmployeePayroll, EmployeePayroll.current_closing_id == EmployeePayout.payroll_closing_id)
            .where(EmployeePayroll.id == payroll_id, EmployeePayout.reversal_operation_id.is_(None))
        )
        or 0
    )


def reopen(db: Session, actor: User, payroll_id: int, version: int, reason: str) -> Created:
    """Reopen a closed payroll for correction (admin of the area, with a reason). Its close stays in effect."""
    _require(actor, "admin", "Solo un administrador de Empleados reabre una liquidación.")
    reason = " ".join(reason.split())
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la reapertura.")
    payroll = _lock(db, payroll_id)
    if payroll.status != "CLOSED":
        last = db.scalars(
            select(EmployeePayrollReopening).where(
                EmployeePayrollReopening.payroll_id == payroll.id, EmployeePayrollReopening.from_version == version
            )
        ).one_or_none()
        if last is not None and (last.reopened_by, last.reason) == (actor.id, reason):
            db.commit()  # a retry of the same reopening
            return Created(payroll.id, created=False)
        raise AppError(409, "payroll_not_closed", "La liquidación no está cerrada.", {"payroll_id": payroll.id})
    if version != payroll.version:
        raise _concurrent(payroll)
    if payouts_in_effect(db, payroll.id):
        raise AppError(
            409,
            "payroll_has_payouts",
            "Esta liquidación tiene pagos vigentes. Revertí los pagos antes de reabrirla o registrá la corrección "
            "económica en una liquidación posterior.",
            {"payroll_id": payroll.id},
        )
    assert payroll.current_closing_id is not None
    now = datetime.now(UTC)
    db.add(
        EmployeePayrollReopening(
            payroll_id=payroll.id,
            from_version=payroll.version,
            to_version=payroll.version + 1,
            closing_id=payroll.current_closing_id,
            reason=reason,
            reopened_by=actor.id,
            reopened_at=now,
        )
    )
    payroll.status = "OPEN"
    payroll.version += 1
    _flush(db)
    record_change(
        db,
        f"{EVENT}.reopened",
        action="reopen",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payroll.id,
        before={"status": "CLOSED", "version": payroll.version - 1},
        after={
            "status": "OPEN",
            "version": payroll.version,
            "reason": reason,
            "closing_in_effect": payroll.current_closing_id,
        },
    )
    commit(db)
    return Created(payroll.id, created=True)


# --- what is applied (read by the loans and movements) ------------------------------------------------------------


def applied_installment_ids(db: Session, installment_ids: list[int]) -> set[int]:
    """Which installments a close in effect applied (Enzo's R8-4 point 6: «Aplicada» is derived, never stored)."""
    if not installment_ids:
        return set()
    return set(
        db.scalars(
            select(EmployeePayrollApplication.installment_id).where(
                EmployeePayrollApplication.installment_id.in_(installment_ids),
                EmployeePayrollApplication.superseded_at.is_(None),
            )
        )
    )


def operation_applied(db: Session, operation_id: int, account_id: int) -> bool:
    """Whether a close in effect applied (any part of) the leg of this operation on the internal account."""
    entry = ledger.leg_of_operation(db, operation_id, account_id)
    if entry is None:
        return False
    return (
        db.scalar(
            select(func.count())
            .select_from(EmployeePayrollApplication)
            .where(
                EmployeePayrollApplication.ledger_entry_id == entry,
                EmployeePayrollApplication.superseded_at.is_(None),
            )
        )
        or 0
    ) > 0


def applied_by_entry(db: Session, account_id: int) -> dict[int, Decimal]:
    """What the closes in effect applied of each leg of an internal account (absolute), installments included."""
    query = (
        select(EmployeePayrollApplication.ledger_entry_id, func.sum(func.abs(EmployeePayrollApplication.amount)))
        .where(
            EmployeePayrollApplication.employee_account_id == account_id,
            EmployeePayrollApplication.superseded_at.is_(None),
        )
        .group_by(EmployeePayrollApplication.ledger_entry_id)
    )
    return {entry: Decimal(total) for entry, total in db.execute(query).all()}


# --- shared -------------------------------------------------------------------------------------------------------


def commit(db: Session) -> None:
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        raise _integrity(exc) from exc


def get(db: Session, payroll_id: int) -> EmployeePayroll:
    payroll = db.get(EmployeePayroll, payroll_id)
    if payroll is None:
        raise AppError(404, "payroll_not_found", "La liquidación no existe.")
    return payroll


@dataclass(frozen=True)
class _Items:
    legs: dict[int, LegItem]
    installments: set[int]


def _draft_items(db: Session, payroll: EmployeePayroll) -> _Items:
    d = draft(db, payroll)
    return _Items({i.leg.entry_id: i for i in d.legs}, {i.installment.id for i in d.installments})


def _applicable(leg: ledger.EmployeeLeg) -> bool:
    if leg.operation_type == "REVERSAL":
        return leg.reversed_type in APPLICABLE_TYPES
    return leg.operation_type in APPLICABLE_TYPES


def _applied_elsewhere(db: Session, account_id: int, own_closing: int | None) -> dict[int, Decimal]:
    query = (
        select(EmployeePayrollApplication.ledger_entry_id, func.sum(func.abs(EmployeePayrollApplication.amount)))
        .where(
            EmployeePayrollApplication.employee_account_id == account_id,
            EmployeePayrollApplication.superseded_at.is_(None),
            EmployeePayrollApplication.installment_id.is_(None),
        )
        .group_by(EmployeePayrollApplication.ledger_entry_id)
    )
    if own_closing is not None:
        query = query.where(EmployeePayrollApplication.closing_id != own_closing)
    return {entry: Decimal(total) for entry, total in db.execute(query).all()}


def _due_installments(db: Session, payroll: EmployeePayroll) -> list[tuple[EmployeeLoanInstallment, EmployeeLoan]]:
    """SCHEDULED installments of the employee's loans in effect, due up to the period's end, that no other payroll's
    close in effect applied (this payroll's own is replaced by its next close)."""
    applied_elsewhere = select(EmployeePayrollApplication.installment_id).where(
        EmployeePayrollApplication.installment_id.is_not(None),
        EmployeePayrollApplication.superseded_at.is_(None),
    )
    if payroll.current_closing_id is not None:
        applied_elsewhere = applied_elsewhere.where(EmployeePayrollApplication.closing_id != payroll.current_closing_id)
    rows = db.execute(
        select(EmployeeLoanInstallment, EmployeeLoan)
        .join(EmployeeLoan, EmployeeLoan.id == EmployeeLoanInstallment.loan_id)
        .where(
            EmployeeLoan.employee_id == payroll.employee_id,
            EmployeeLoan.reversal_operation_id.is_(None),
            EmployeeLoan.operation_id.is_not(None),
            EmployeeLoanInstallment.status == "SCHEDULED",
            EmployeeLoanInstallment.due_on <= payroll.period_end,
            EmployeeLoanInstallment.id.not_in(applied_elsewhere),
        )
        .order_by(EmployeeLoanInstallment.due_on, EmployeeLoan.id, EmployeeLoanInstallment.number)
    ).all()
    return list(rows)


def _snapshot(db: Session, d: Draft) -> dict[str, Any]:
    """The readable copy of what a close froze (Enzo's R8-4 point 11). The rows carry the invariants."""
    payroll = d.payroll
    terms_used = {line.compensation_term_id for line in d.lines if line.compensation_term_id is not None}
    return {
        "version": payroll.version,
        "kind": payroll.kind,
        "employee_id": payroll.employee_id,
        "period_start": payroll.period_start.isoformat(),
        "period_end": payroll.period_end.isoformat(),
        "payment_date": payroll.payment_date.isoformat() if payroll.payment_date else None,
        "compensation_terms": [
            {
                "id": t.id,
                "valid_from": t.valid_from.isoformat(),
                "valid_to": t.valid_to.isoformat() if t.valid_to else None,
                "base_salary": money(t.base_salary),
                "used": t.id in terms_used,
            }
            for t in d.terms
        ],
        "lines": [
            {
                "kind": line.kind,
                "amount": money(line.amount),
                "description": line.description,
                "source": line.source,
                "compensation_term_id": line.compensation_term_id,
            }
            for line in d.lines
        ],
        "applications": [
            {
                "ledger_entry_id": i.leg.entry_id,
                "operation_id": i.leg.operation_id,
                "operation_type": i.leg.operation_type,
                "reversed_type": i.leg.reversed_type,
                "source_type": i.leg.source_type,
                "source_id": i.leg.source_id,
                "business_date": i.leg.business_date.isoformat(),
                "original_amount": money(i.leg.amount),
                "applied_before": money(i.applied_elsewhere * i.sign),
                "applied": money(i.signed_amount),
                "pending_after": money((i.available - i.amount) * i.sign),
            }
            for i in d.legs
            if i.included
        ],
        "installments": [
            {
                "installment_id": i.installment.id,
                "loan_id": i.loan.id,
                "number": i.installment.number,
                "due_on": i.installment.due_on.isoformat(),
                "amount": money(i.installment.amount),
                "ledger_entry_id": i.loan_entry_id,
            }
            for i in d.installments
            if i.included
        ],
        "credits": money(d.credits),
        "deductions": money(d.deductions),
        "credits_applied": money(d.credits_applied),
        "net": money(d.net),
        "deduction_pct": str(d.deduction_pct) if d.deduction_pct is not None else None,
    }


def _fingerprint(d: Draft) -> str:
    payload = {
        "lines": sorted([line.kind, money(line.amount), line.description or ""] for line in d.lines),
        "legs": sorted([i.leg.entry_id, money(i.signed_amount)] for i in d.legs if i.included),
        "installments": sorted(i.installment.id for i in d.installments if i.included),
        "version": d.payroll.version,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _request_id(payroll: EmployeePayroll, what: str) -> UUID:
    return uuid5(NAMESPACE, f"payroll:{payroll.id}:v{payroll.version}:{what}")


def _require(actor: User, role: str, message: str) -> None:
    if not has_area_access(actor, AREA, role):
        raise AppError(403, "forbidden", message)


def _line_description(kind: str, description: str | None, *, manual_salary: bool = True) -> str | None:
    text = " ".join((description or "").split()) or None
    if text is None and kind == "OTHER_CREDIT":
        raise AppError(422, "description_required", "Indicá la descripción del haber.")
    if text is None and kind == "SALARY" and manual_salary:
        raise AppError(
            422,
            "salary_note_required",
            "Un sueldo cargado a mano necesita una nota (período parcial, cambio de condición...).",
        )
    return text


def lock(db: Session, payroll_id: int) -> EmployeePayroll:
    """The payroll locked in R8's order (a payout or its reversal takes it too)."""
    return _lock(db, payroll_id)


def _lock(db: Session, payroll_id: int) -> EmployeePayroll:
    """The internal account first, then the payroll (the lock order of every R8 flow)."""
    account = db.scalar(select(EmployeePayroll.employee_account_id).where(EmployeePayroll.id == payroll_id))
    if account is None:
        raise AppError(404, "payroll_not_found", "La liquidación no existe.")
    lock_account(db, account)
    return db.scalars(
        select(EmployeePayroll)
        .where(EmployeePayroll.id == payroll_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _lock_draft(db: Session, payroll_id: int) -> EmployeePayroll:
    payroll = _lock(db, payroll_id)
    if payroll.status != "OPEN":
        raise AppError(
            409,
            "payroll_not_open",
            "La liquidación no está abierta: su borrador no cambia.",
            {"payroll_id": payroll.id, "status": payroll.status},
        )
    return payroll


def _lock_loans(db: Session, loan_ids: list[int]) -> None:
    for loan_id in sorted(set(loan_ids)):
        db.execute(select(EmployeeLoan.id).where(EmployeeLoan.id == loan_id).with_for_update(read=True))


def _concurrent(payroll: EmployeePayroll) -> AppError:
    return AppError(
        409,
        "payroll_version_changed",
        "La liquidación cambió de versión mientras la revisabas. Actualizá y revisala nuevamente.",
        {"payroll_id": payroll.id, "version": payroll.version},
    )


def _period(payroll: EmployeePayroll) -> dict[str, Any]:
    return {
        "period_start": payroll.period_start.isoformat(),
        "period_end": payroll.period_end.isoformat(),
        "payment_date": payroll.payment_date.isoformat() if payroll.payment_date else None,
        "notes": payroll.notes,
    }


def _audit_draft(db: Session, actor: User, payroll: EmployeePayroll, what: str, after: dict[str, Any]) -> None:
    record_change(
        db,
        f"{EVENT}.draft",
        action="update",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=payroll.id,
        before=None,
        after={"change": what, "version": payroll.version} | after,
    )


def _flush(db: Session) -> None:
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        db.rollback()
        raise _integrity(exc) from exc


def _integrity(exc: DBAPIError) -> AppError:
    constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
    if constraint == "ex_employee_payrolls_regular_no_overlap":
        return AppError(
            409,
            "regular_payroll_overlap",
            "El empleado ya tiene una liquidación regular en ese período. Una extraordinaria sí puede superponerse.",
        )
    logger.warning("employee_payroll_rejected", extra=db_error_fields(exc))
    return AppError(
        409,
        "invalid_employee_payroll",
        "La liquidación no cumple las reglas de la cuenta interna y no se registró. Revisala nuevamente.",
    )


def _by_request_id(db: Session, client_request_id: UUID) -> EmployeePayroll | None:
    return db.scalars(
        select(EmployeePayroll).where(EmployeePayroll.client_request_id == client_request_id)
    ).one_or_none()


def _month_end(day: date) -> date:
    import calendar

    return day.replace(day=calendar.monthrange(day.year, day.month)[1])
