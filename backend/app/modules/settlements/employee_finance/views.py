"""Read side of Rendiciones > Empleados: internal accounts, salary terms and movements.

The internal account (`EMPLOYEE`, one per employee, created on first use) is net: positive means the employee owes
the company, negative that the company owes the employee. Its movements are read from the ledger and their source
documents, nothing is copied: what R2-R7 write (differences of settlements, pending write-offs) and the employee
movements of R8-2 (consumptions, advances, adjustments) and the loans of R8-3 (one movement per loan, never one per
installment), each with its reversals, and the credits of the internal payrolls (R8-4). Each movement says where it
comes from and how much of it a close in effect applied; the balance is never split up by arithmetic (Enzo's
precision D).
"""

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import Select, exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.employee_finance import compensation
from app.modules.settlements.employee_finance import loans as loan_service
from app.modules.settlements.employee_finance import movements as movement_service
from app.modules.settlements.employee_finance import payouts as payout_service
from app.modules.settlements.employee_finance import payrolls as payroll_service
from app.modules.settlements.employee_finance import schemas as s
from app.modules.settlements.employee_finance.models import (
    EmployeeCompensationTerm,
    EmployeeLoan,
    EmployeeLoanInstallment,
    EmployeeMovement,
    EmployeePayout,
    EmployeePayoutItem,
    EmployeePayroll,
    EmployeePayrollApplication,
    EmployeePayrollClosing,
    EmployeePayrollClosingLine,
    EmployeePayrollReopening,
)
from app.modules.settlements.ledger import service as ledger
from app.platform.auth.models import User
from app.platform.parties.models import Employee

SETTLEMENT_SOURCE = "settlement"
PENDING_SOURCE = "pending_collection"
MOVEMENT_SOURCE = movement_service.SOURCE
LOAN_SOURCE = loan_service.SOURCE
PAYROLL_SOURCE = payroll_service.SOURCE
PAYOUT_SOURCE = payout_service.SOURCE


def reading(balance: Decimal) -> s.BalanceReading:
    if balance > 0:
        return "OWES_COMPANY"
    if balance < 0:
        return "COMPANY_OWES"
    return "NONE"


def employees(db: Session, q: str | None, limit: int, offset: int) -> s.EmployeeFinancePage:
    """Active employees, and inactive ones with an internal account or salary terms (their history stays)."""
    has_terms = exists().where(EmployeeCompensationTerm.employee_id == Employee.id)
    query = select(Employee).where(
        or_(Employee.active.is_(True), Employee.id.in_(ledger.employee_account_owners()), has_terms)
    )
    if q and q.strip():
        query = query.where(Employee.full_name.ilike(f"%{_escape(q.strip())}%", escape="\\"))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(Employee.full_name, Employee.id).limit(limit).offset(offset)))
    return s.EmployeeFinancePage(items=_rows(db, rows), total=total)


def detail(db: Session, employee_id: int) -> s.EmployeeFinanceDetail:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    row = _rows(db, [employee])[0]
    has_account = ledger.employee_account_id(db, employee_id) is not None
    return s.EmployeeFinanceDetail(**row.model_dump(), hire_date=employee.hire_date, has_account=has_account)


def terms(db: Session, employee_id: int) -> list[s.CompensationTermOut]:
    if db.get(Employee, employee_id) is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    rows = compensation.history(db, employee_id)
    return [term_out(t, _user_names(db, rows), business_today()) for t in rows]


def term(db: Session, row: EmployeeCompensationTerm) -> s.CompensationTermOut:
    return term_out(row, _user_names(db, [row]), business_today())


def term_out(row: EmployeeCompensationTerm, users: dict[int, str], today: date) -> s.CompensationTermOut:
    return s.CompensationTermOut(
        id=row.id,
        employee_id=row.employee_id,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        base_salary=row.base_salary,
        notes=row.notes,
        status=compensation.status(row, today),
        created_at=row.created_at,
        created_by_name=users.get(row.created_by, ""),
        ended_at=row.ended_at,
        ended_by_name=users.get(row.ended_by) if row.ended_by is not None else None,
        end_reason=row.end_reason,
    )


def movements(db: Session, employee_id: int, limit: int, offset: int) -> s.AccountMovementPage:
    """Entries of the employee's internal account, newest first, each with its source document."""
    if db.get(Employee, employee_id) is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    entries, total = ledger.employee_account_entries(db, employee_id, limit=limit, offset=offset)
    documents = _documents(db, [(e.source_type, e.source_id) for e in entries])
    account = ledger.employee_account_id(db, employee_id)
    applied = payroll_service.applied_by_entry(db, account) if account is not None else {}
    items = []
    for entry in entries:
        doc = documents.get((entry.source_type, entry.source_id), _NO_DOCUMENT)
        items.append(
            s.AccountMovementOut(
                entry_id=entry.entry_id,
                operation_id=entry.operation_id,
                business_date=entry.business_date,
                amount=entry.amount,
                reading="CHARGE" if entry.amount > 0 else "CREDIT",
                origin=_origin(entry.operation_type, entry.reversed_type, entry.amount),
                document_kind=doc.kind,
                document_id=entry.source_id if doc.kind is not None else None,
                document_date=doc.on,
                document_number=doc.number,
                description=doc.description,
                document_reversed=doc.reversed,
                applied=applied.get(entry.entry_id, Decimal("0")),
                created_at=entry.created_at,
            )
        )
    return s.AccountMovementPage(items=items, total=total)


def _origin(op_type: str, reversed_type: str | None, amount: Decimal) -> s.Origin:
    if op_type == "SETTLEMENT_CLOSE":
        return "SETTLEMENT_SHORTAGE" if amount > 0 else "SETTLEMENT_SURPLUS"
    if op_type == "PENDING_WRITE_OFF":
        return "PENDING_WRITE_OFF"
    if op_type == "REVERSAL" and reversed_type == "SETTLEMENT_CLOSE":
        return "SETTLEMENT_CORRECTION"
    if op_type == "REVERSAL" and reversed_type == "PENDING_WRITE_OFF":
        return "PENDING_WRITE_OFF_REVERSAL"
    if op_type == "EMPLOYEE_CONSUMPTION":
        return "CONSUMPTION"
    if op_type == "EMPLOYEE_ADVANCE":
        return "ADVANCE"
    if op_type == "EMPLOYEE_ADJUSTMENT":
        return "ADJUSTMENT_CHARGE" if amount > 0 else "ADJUSTMENT_CREDIT"
    if op_type == "REVERSAL" and reversed_type == "EMPLOYEE_CONSUMPTION":
        return "CONSUMPTION_REVERSAL"
    if op_type == "REVERSAL" and reversed_type == "EMPLOYEE_ADVANCE":
        return "ADVANCE_REVERSAL"
    if op_type == "REVERSAL" and reversed_type == "EMPLOYEE_ADJUSTMENT":
        return "ADJUSTMENT_REVERSAL"
    if op_type == "EMPLOYEE_LOAN":
        return "LOAN"
    if op_type == "REVERSAL" and reversed_type == "EMPLOYEE_LOAN":
        return "LOAN_REVERSAL"
    if op_type == "PAYROLL_CLOSE":
        return "PAYROLL"
    if op_type == "REVERSAL" and reversed_type == "PAYROLL_CLOSE":
        return "PAYROLL_CORRECTION"
    if op_type == "EMPLOYEE_PAYOUT":
        return "PAYOUT"
    if op_type == "REVERSAL" and reversed_type == "EMPLOYEE_PAYOUT":
        return "PAYOUT_REVERSAL"
    return "OTHER"


@dataclass(frozen=True)
class _Document:
    kind: s.DocumentKind | None = None
    on: date | None = None
    number: int | None = None
    description: str | None = None
    reversed: bool | None = None


_NO_DOCUMENT = _Document()


def _documents(
    db: Session, refs: list[tuple[str | None, int | None]]
) -> dict[tuple[str | None, int | None], _Document]:
    """The source document of each leg of an internal account (a reversal carries its original's)."""

    def ids(source: str) -> set[int]:
        return {i for t, i in refs if t == source and i is not None}

    result: dict[tuple[str | None, int | None], _Document] = {}
    if wanted := ids(SETTLEMENT_SOURCE):
        for row in db.scalars(select(Settlement).where(Settlement.id.in_(wanted))):
            kind: s.DocumentKind = "OUTLET_SETTLEMENT" if row.type == "OUTLET" else "DELIVERY_SETTLEMENT"
            result[(SETTLEMENT_SOURCE, row.id)] = _Document(kind, row.business_date, row.sequence_number)
    for pending in ids(PENDING_SOURCE):
        result[(PENDING_SOURCE, pending)] = _Document("PENDING_COLLECTION")
    if wanted := ids(MOVEMENT_SOURCE):
        for m in db.scalars(select(EmployeeMovement).where(EmployeeMovement.id.in_(wanted))):
            result[(MOVEMENT_SOURCE, m.id)] = _Document(
                "EMPLOYEE_MOVEMENT", m.business_date, None, m.description, m.reversal_operation_id is not None
            )
    if wanted := ids(LOAN_SOURCE):
        for loan in db.scalars(select(EmployeeLoan).where(EmployeeLoan.id.in_(wanted))):
            result[(LOAN_SOURCE, loan.id)] = _Document(
                "EMPLOYEE_LOAN", loan.business_date, None, loan.description, loan.reversal_operation_id is not None
            )
    if wanted := ids(PAYROLL_SOURCE):
        for p in db.scalars(select(EmployeePayroll).where(EmployeePayroll.id.in_(wanted))):
            result[(PAYROLL_SOURCE, p.id)] = _Document(
                "EMPLOYEE_PAYROLL", p.period_end, None, f"Liquidación {_period_label(p)}", None
            )
    if wanted := ids(PAYOUT_SOURCE):
        rows = db.execute(
            select(EmployeePayout, EmployeePayroll)
            .join(EmployeePayroll, EmployeePayroll.id == EmployeePayout.payroll_id)
            .where(EmployeePayout.id.in_(wanted))
        ).all()
        for payout, p in rows:
            result[(PAYOUT_SOURCE, payout.id)] = _Document(
                "EMPLOYEE_PAYOUT",
                payout.business_date,
                None,
                f"Pago de la liquidación {_period_label(p)}",
                payout.reversal_operation_id is not None,
            )
    return result


def _period_label(p: EmployeePayroll) -> str:
    return f"{p.period_start:%d/%m/%Y} al {p.period_end:%d/%m/%Y}"


def preview(db: Session, actor: User, employee_id: int, body: s.MovementPreviewIn) -> s.MovementPreviewOut:
    """What registering a movement would publish, validated (roles, employee, funds), without writing anything."""
    cmd = movement_service.command(
        body.kind, employee_id, body.business_date, body.amount, "-", None, body.direction, body.funding_account_id
    )
    planned = movement_service.plan(db, actor, cmd)
    before = ledger.employee_balances(db, [employee_id]).get(employee_id, Decimal("0"))
    return s.MovementPreviewOut(
        kind=cmd.kind,
        direction=cmd.direction,
        amount=cmd.amount,
        employee_amount=cmd.employee_amount,
        balance_before=before,
        balance_after=before + cmd.employee_amount,
        legs=[s.MovementLegOut(**leg.__dict__) for leg in planned.legs],
    )


def movement(db: Session, movement_id: int) -> s.EmployeeMovementOut:
    row = db.get(EmployeeMovement, movement_id)
    if row is None:
        raise AppError(404, "employee_movement_not_found", "El movimiento no existe.")
    assert row.operation_id is not None  # published with its operation (0026)
    employee = db.get(Employee, row.employee_id)
    assert employee is not None
    users = dict(
        db.execute(
            select(User.id, User.full_name).where(
                User.id.in_([row.created_by] + ([row.reversed_by] if row.reversed_by is not None else []))
            )
        ).all()
    )
    detail = ledger.operation_detail(db, row.operation_id)
    funding = next((e.account_name for e in detail.entries if e.account_id == row.funding_account_id), None)
    legs = [
        s.MovementLegOut(
            account_id=e.account_id,
            account_name=f"Cuenta interna · {employee.full_name}" if e.account_kind == "EMPLOYEE" else e.account_name,
            account_kind=e.account_kind,
            amount=e.amount,
        )
        for e in detail.entries
    ]
    return s.EmployeeMovementOut(
        id=row.id,
        kind=row.kind,
        direction=row.direction,
        employee_id=row.employee_id,
        employee_name=employee.full_name,
        business_date=row.business_date,
        amount=row.amount,
        employee_amount=row.employee_amount,
        funding_account_id=row.funding_account_id,
        funding_account_name=funding,
        description=row.description,
        reference=row.reference,
        status="REVERSED" if row.reversal_operation_id is not None else "IN_EFFECT",
        operation_id=row.operation_id,
        reversal_operation_id=row.reversal_operation_id,
        reversed_at=row.reversed_at,
        reversed_by_name=users.get(row.reversed_by) if row.reversed_by is not None else None,
        reversal_reason=row.reversal_reason,
        created_at=row.created_at,
        created_by_name=users.get(row.created_by, ""),
        legs=legs,
    )


# --- loans (R8-3) ---------------------------------------------------------------------------------


def loan_command(employee_id: int, body: s.LoanPreviewIn | s.LoanIn) -> loan_service.Command:
    return loan_service.command(
        employee_id,
        body.business_date,
        body.principal,
        body.funding_account_id,
        body.installment_count,
        body.first_due_on,
        None
        if body.installments is None
        else [loan_service.Installment(i.due_on, i.amount) for i in body.installments],
        body.description if isinstance(body, s.LoanIn) else None,
        body.reference if isinstance(body, s.LoanIn) else None,
    )


def preview_loan(db: Session, actor: User, employee_id: int, body: s.LoanPreviewIn) -> s.LoanPreviewOut:
    """What giving a loan would publish (legs and schedule), validated (roles, employee, schedule, funds)."""
    planned = loan_service.plan(db, actor, loan_command(employee_id, body))
    cmd = planned.command
    before = ledger.employee_balances(db, [employee_id]).get(employee_id, Decimal("0"))
    return s.LoanPreviewOut(
        principal=cmd.principal,
        business_date=cmd.business_date,
        funding_account_id=planned.funding.account_id,
        funding_account_name=planned.funding.name,
        balance_before=before,
        balance_after=before + cmd.principal,
        legs=[s.MovementLegOut(**leg.__dict__) for leg in planned.legs],
        installments=[
            s.ScheduledInstallmentOut(number=n, due_on=i.due_on, amount=i.amount)
            for n, i in enumerate(cmd.installments, start=1)
        ],
        total=sum((i.amount for i in cmd.installments), Decimal("0")),
    )


def loans(db: Session, employee_id: int) -> list[s.EmployeeLoanRow]:
    """The employee's loans, newest first, in effect and reversed."""
    if db.get(Employee, employee_id) is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    rows = list(
        db.scalars(
            select(EmployeeLoan)
            .where(EmployeeLoan.employee_id == employee_id)
            .order_by(EmployeeLoan.business_date.desc(), EmployeeLoan.id.desc())
        )
    )
    installments = _installments(db, [r.id for r in rows])
    applied = _applied(db, [i.id for schedule in installments.values() for i in schedule])
    return [_loan_row(r, installments.get(r.id, []), applied) for r in rows]


def loan(db: Session, loan_id: int) -> s.EmployeeLoanOut:
    row = db.get(EmployeeLoan, loan_id)
    if row is None:
        raise AppError(404, "employee_loan_not_found", "El préstamo no existe.")
    assert row.operation_id is not None  # published with its operation (0027)
    employee = db.get(Employee, row.employee_id)
    assert employee is not None
    schedule = _installments(db, [row.id]).get(row.id, [])
    applied = _applied(db, [i.id for i in schedule])
    user_ids = {row.created_by} | {u for u in [row.reversed_by, *(i.deferred_by for i in schedule)] if u is not None}
    users = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids))).all())
    detail = ledger.operation_detail(db, row.operation_id)
    funding = next(e.account_name for e in detail.entries if e.account_id == row.funding_account_id)
    legs = [
        s.MovementLegOut(
            account_id=e.account_id,
            account_name=f"Cuenta interna · {employee.full_name}" if e.account_kind == "EMPLOYEE" else e.account_name,
            account_kind=e.account_kind,
            amount=e.amount,
        )
        for e in detail.entries
    ]
    numbers = {i.id: i.number for i in schedule}
    children: dict[int, list[EmployeeLoanInstallment]] = {}
    for i in schedule:
        if i.deferred_from_id is not None:
            children.setdefault(i.deferred_from_id, []).append(i)
    return s.EmployeeLoanOut(
        **_loan_row(row, schedule, applied).model_dump(),
        employee_id=row.employee_id,
        employee_name=employee.full_name,
        funding_account_id=row.funding_account_id,
        funding_account_name=funding,
        first_due_on=row.first_due_on,
        reference=row.reference,
        operation_id=row.operation_id,
        reversal_operation_id=row.reversal_operation_id,
        reversed_at=row.reversed_at,
        reversed_by_name=users.get(row.reversed_by) if row.reversed_by is not None else None,
        reversal_reason=row.reversal_reason,
        created_at=row.created_at,
        created_by_name=users.get(row.created_by, ""),
        legs=legs,
        installments=[
            s.LoanInstallmentOut(
                id=i.id,
                number=i.number,
                due_on=i.due_on,
                amount=i.amount,
                status=i.status,
                applied=i.id in applied,
                applied_payroll_id=applied.get(i.id),
                deferred_from_id=i.deferred_from_id,
                deferred_from_number=numbers.get(i.deferred_from_id) if i.deferred_from_id is not None else None,
                deferred_reason=i.deferred_reason,
                deferred_at=i.deferred_at,
                deferred_by_name=users.get(i.deferred_by) if i.deferred_by is not None else None,
                replaced_by=[
                    s.ScheduledInstallmentOut(number=c.number, due_on=c.due_on, amount=c.amount)
                    for c in children.get(i.id, [])
                ],
                created_at=i.created_at,
            )
            for i in schedule
        ],
    )


def _installments(db: Session, loan_ids: list[int]) -> dict[int, list[EmployeeLoanInstallment]]:
    if not loan_ids:
        return {}
    result: dict[int, list[EmployeeLoanInstallment]] = {}
    for i in db.scalars(
        select(EmployeeLoanInstallment)
        .where(EmployeeLoanInstallment.loan_id.in_(loan_ids))
        .order_by(EmployeeLoanInstallment.loan_id, EmployeeLoanInstallment.number)
    ):
        result.setdefault(i.loan_id, []).append(i)
    return result


def _applied(db: Session, installment_ids: list[int]) -> dict[int, int]:
    """Installment -> the payroll whose close in effect applied it."""
    if not installment_ids:
        return {}
    rows = db.execute(
        select(EmployeePayrollApplication.installment_id, EmployeePayrollApplication.payroll_id).where(
            EmployeePayrollApplication.installment_id.in_(installment_ids),
            EmployeePayrollApplication.superseded_at.is_(None),
        )
    ).all()
    return {i: p for i, p in rows if i is not None}


def _loan_row(row: EmployeeLoan, schedule: list[EmployeeLoanInstallment], applied: dict[int, int]) -> s.EmployeeLoanRow:
    reversed_ = row.reversal_operation_id is not None
    applied_total = sum((i.amount for i in schedule if i.id in applied), Decimal("0"))
    scheduled = [i for i in schedule if i.status == "SCHEDULED" and i.id not in applied]
    return s.EmployeeLoanRow(
        id=row.id,
        business_date=row.business_date,
        principal=row.principal,
        installment_count=row.installment_count,
        status="REVERSED" if reversed_ else "ACTIVE",
        # From the loan and the installments applied, never from the internal account (Enzo's R8-3 §9, R8-4 §6).
        balance=Decimal("0") if reversed_ else row.principal - applied_total,
        applied_total=applied_total,
        scheduled_count=len(scheduled),
        next_due_on=min((i.due_on for i in scheduled), default=None),
        description=row.description,
    )


def _rows(db: Session, employees: list[Employee]) -> list[s.EmployeeFinanceRow]:
    ids = [e.id for e in employees]
    balances = ledger.employee_balances(db, ids)
    today = business_today()
    current, upcoming = compensation.current_and_next(db, ids, today)
    users = _user_names(db, [*current.values(), *upcoming.values()])
    result = []
    for e in employees:
        balance = balances.get(e.id, Decimal("0"))
        result.append(
            s.EmployeeFinanceRow(
                employee_id=e.id,
                full_name=e.full_name,
                active=e.active,
                balance=balance,
                balance_reading=reading(balance),
                current_term=term_out(current[e.id], users, today) if e.id in current else None,
                next_term=term_out(upcoming[e.id], users, today) if e.id in upcoming else None,
            )
        )
    return result


def _user_names(db: Session, rows: list[EmployeeCompensationTerm]) -> dict[int, str]:
    ids = {r.created_by for r in rows} | {r.ended_by for r in rows if r.ended_by is not None}
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all())


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- internal payrolls (R8-4) --------------------------------------------------------------------------------------

_NO_TOTALS = s.PayrollTotals(
    credits=Decimal("0"), deductions=Decimal("0"), credits_applied=Decimal("0"), net=Decimal("0"), deduction_pct=None
)


def payrolls(
    db: Session,
    employee_id: int | None,
    status: s.PayrollStatus | None,
    kind: s.PayrollKind | None,
    limit: int,
    offset: int,
) -> s.PayrollPage:
    """Payrolls, newest period first. An OPEN one shows its draft's totals."""
    query = select(EmployeePayroll)
    if employee_id is not None:
        query = query.where(EmployeePayroll.employee_id == employee_id)
    if status is not None:
        query = query.where(EmployeePayroll.status == status)
    if kind is not None:
        query = query.where(EmployeePayroll.kind == kind)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(
        db.scalars(
            query.order_by(EmployeePayroll.period_end.desc(), EmployeePayroll.id.desc()).limit(limit).offset(offset)
        )
    )
    names = _employee_names(db, {r.employee_id for r in rows})
    closings = _closings_by_id(db, [r.current_closing_id for r in rows if r.current_closing_id is not None])
    paid = payout_service.paid_by_closing(db, list(closings))
    return s.PayrollPage(
        items=[_payroll_row(db, r, names, closings.get(r.current_closing_id or 0), paid) for r in rows], total=total
    )


def payroll(db: Session, payroll_id: int) -> s.PayrollOut:
    row = payroll_service.get(db, payroll_id)
    names = _employee_names(db, {row.employee_id})
    closings = list(
        db.scalars(
            select(EmployeePayrollClosing)
            .where(EmployeePayrollClosing.payroll_id == row.id)
            .order_by(EmployeePayrollClosing.payroll_version.desc())
        )
    )
    current = next((c for c in closings if c.id == row.current_closing_id), None)
    reopenings = list(
        db.scalars(
            select(EmployeePayrollReopening)
            .where(EmployeePayrollReopening.payroll_id == row.id)
            .order_by(EmployeePayrollReopening.from_version.desc())
        )
    )
    user_ids = {row.created_by, *(c.closed_by for c in closings), *(r.reopened_by for r in reopenings)}
    if row.cancelled_by is not None:
        user_ids.add(row.cancelled_by)
    users = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids))).all())
    terms = compensation.overlapping(db, row.employee_id, row.period_start, row.period_end)
    hint = payroll_service.salary_hint(row, terms)
    lines: list[s.PayrollLineOut] = []
    legs: list[s.PayrollLegOut] = []
    installments: list[s.PayrollInstallmentOut] = []
    preview: s.PayrollPreviewOut | None = None
    if row.status == "OPEN":
        d = payroll_service.draft(db, row)
        lines = [
            s.PayrollLineOut(
                id=line.id,
                kind=line.kind,
                amount=line.amount,
                description=line.description,
                source=line.source,
                compensation_term_id=line.compensation_term_id,
            )
            for line in d.lines
        ]
        legs = _draft_legs(db, d)
        installments = [
            s.PayrollInstallmentOut(
                installment_id=i.installment.id,
                loan_id=i.loan.id,
                loan_description=i.loan.description,
                number=i.installment.number,
                due_on=i.installment.due_on,
                amount=i.installment.amount,
                overdue=i.installment.due_on < row.period_start,
                included=i.included,
            )
            for i in d.installments
        ]
        preview = s.PayrollPreviewOut(
            **_totals(d.credits, d.deductions, d.credits_applied, d.net).model_dump(),
            preview_token=payroll_service.preview_token(db, d),
            blockers=[s.PayrollBlocker(code=c, message=m) for c, m in d.blockers],
            accrual="FINANCIAL" if d.credits > 0 else "NO_ACCRUAL",
        )
    closing_outs = _closing_outs(db, row.employee_account_id, closings, users)
    return s.PayrollOut(
        **_payroll_row(db, row, names, current).model_dump(),
        notes=row.notes,
        created_at=row.created_at,
        created_by_name=users.get(row.created_by, ""),
        cancelled_at=row.cancelled_at,
        cancelled_by_name=users.get(row.cancelled_by) if row.cancelled_by is not None else None,
        cancel_reason=row.cancel_reason,
        salary_hint=s.SalaryHintOut(
            status=hint.status, message=hint.message, amount=hint.amount, compensation_term_id=hint.term_id
        ),
        lines=lines,
        legs=legs,
        installments=installments,
        preview=preview,
        closing=next((c for c in closing_outs if c.id == row.current_closing_id), None),
        closings=closing_outs,
        reopenings=[
            s.PayrollReopeningOut(
                from_version=r.from_version,
                to_version=r.to_version,
                reason=r.reason,
                reopened_at=r.reopened_at,
                reopened_by_name=users.get(r.reopened_by, ""),
            )
            for r in reopenings
        ],
        payment=_payment(db, row),
        payouts=_payout_outs(db, list(db.scalars(_payouts_query().where(EmployeePayout.payroll_id == row.id)))),
    )


def _totals(credits: Decimal, deductions: Decimal, credits_applied: Decimal, net: Decimal) -> s.PayrollTotals:
    pct = (deductions * 100 / credits).quantize(Decimal("0.01")) if credits > 0 else None
    return s.PayrollTotals(
        credits=credits, deductions=deductions, credits_applied=credits_applied, net=net, deduction_pct=pct
    )


def _payroll_row(
    db: Session,
    row: EmployeePayroll,
    names: dict[int, str],
    closing: EmployeePayrollClosing | None,
    paid: dict[int, Decimal] | None = None,
) -> s.PayrollRow:
    from_draft = row.status == "OPEN"
    if from_draft:
        d = payroll_service.draft(db, row)
        totals = _totals(d.credits, d.deductions, d.credits_applied, d.net)
    elif closing is not None:
        totals = _totals(closing.credits, closing.deductions, closing.credits_applied, closing.net)
    else:
        totals = _NO_TOTALS
    return s.PayrollRow(
        id=row.id,
        employee_id=row.employee_id,
        employee_name=names.get(row.employee_id, ""),
        kind=row.kind,
        period_start=row.period_start,
        period_end=row.period_end,
        payment_date=row.payment_date,
        status=row.status,
        version=row.version,
        totals=totals,
        totals_from_draft=from_draft,
        in_correction=row.status == "OPEN" and row.current_closing_id is not None,
        payment_status=(
            payout_service.state(
                closing.net, paid[closing.id] if paid is not None else payout_service.paid(db, closing.id)
            ).status
            if row.status == "CLOSED" and closing is not None
            else None
        ),
    )


def _draft_legs(db: Session, d: payroll_service.Draft) -> list[s.PayrollLegOut]:
    documents = _documents(db, [(i.leg.source_type, i.leg.source_id) for i in d.legs])
    result = []
    for i in d.legs:
        leg = i.leg
        doc = documents.get((leg.source_type, leg.source_id), _NO_DOCUMENT)
        result.append(
            s.PayrollLegOut(
                entry_id=leg.entry_id,
                operation_id=leg.operation_id,
                origin=_origin(leg.operation_type, leg.reversed_type, leg.amount),
                business_date=leg.business_date,
                document_kind=doc.kind,
                document_id=leg.source_id if doc.kind is not None else None,
                description=doc.description,
                group_operation_id=leg.reversed_operation_id or leg.operation_id,
                reversed=ledger.reversal_of(db, leg.operation_id) is not None,
                original_amount=leg.amount,
                applied_elsewhere=i.applied_elsewhere * i.sign,
                available=i.available * i.sign,
                proposed=i.proposed,
                included=i.included,
                amount=i.signed_amount,
                partial=i.chosen_amount is not None and i.chosen_amount < i.available,
            )
        )
    return result


def _closings_by_id(db: Session, ids: list[int]) -> dict[int, EmployeePayrollClosing]:
    if not ids:
        return {}
    return {c.id: c for c in db.scalars(select(EmployeePayrollClosing).where(EmployeePayrollClosing.id.in_(ids)))}


def _closing_outs(
    db: Session, account_id: int, closings: list[EmployeePayrollClosing], users: dict[int, str]
) -> list[s.PayrollClosingOut]:
    if not closings:
        return []
    ids = [c.id for c in closings]
    lines: dict[int, list[EmployeePayrollClosingLine]] = {}
    for line in db.scalars(
        select(EmployeePayrollClosingLine)
        .where(EmployeePayrollClosingLine.closing_id.in_(ids))
        .order_by(EmployeePayrollClosingLine.id)
    ):
        lines.setdefault(line.closing_id, []).append(line)
    applications: dict[int, list[EmployeePayrollApplication]] = {}
    for a in db.scalars(
        select(EmployeePayrollApplication)
        .where(EmployeePayrollApplication.closing_id.in_(ids))
        .order_by(EmployeePayrollApplication.id)
    ):
        applications.setdefault(a.closing_id, []).append(a)
    all_apps = [a for apps in applications.values() for a in apps]
    legs = {
        leg.entry_id: leg
        for leg in (
            ledger.employee_legs(db, account_id, entry_ids=[a.ledger_entry_id for a in all_apps]) if all_apps else []
        )
    }
    installment_ids = [a.installment_id for a in all_apps if a.installment_id is not None]
    installments = (
        {
            i.id: i
            for i in db.scalars(select(EmployeeLoanInstallment).where(EmployeeLoanInstallment.id.in_(installment_ids)))
        }
        if installment_ids
        else {}
    )
    result = []
    for c in closings:
        apps = []
        for a in applications.get(c.id, []):
            leg = legs[a.ledger_entry_id]
            inst = installments.get(a.installment_id) if a.installment_id is not None else None
            apps.append(
                s.PayrollApplicationOut(
                    entry_id=a.ledger_entry_id,
                    origin=_origin(leg.operation_type, leg.reversed_type, leg.amount),
                    business_date=leg.business_date,
                    amount=a.amount,
                    installment_id=a.installment_id,
                    installment_number=inst.number if inst is not None else None,
                    loan_id=inst.loan_id if inst is not None else None,
                )
            )
        result.append(
            s.PayrollClosingOut(
                **_totals(c.credits, c.deductions, c.credits_applied, c.net).model_dump(),
                id=c.id,
                version=c.payroll_version,
                kind=c.kind,
                operation_id=c.operation_id,
                reversal_operation_id=c.reversal_operation_id,
                closed_at=c.closed_at,
                closed_by_name=users.get(c.closed_by, ""),
                superseded_at=c.superseded_at,
                lines=[
                    s.PayrollLineOut(
                        id=line.id,
                        kind=line.kind,
                        amount=line.amount,
                        description=line.description,
                        source=line.source,
                        compensation_term_id=line.compensation_term_id,
                    )
                    for line in lines.get(c.id, [])
                ],
                applications=apps,
            )
        )
    return result


def _employee_names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(Employee.id, Employee.full_name).where(Employee.id.in_(ids))).all())


# --- payouts (R8-5) -----------------------------------------------------------------------------------


def payout_command(payroll_id: int, body: s.PayoutPreviewIn | s.PayoutIn) -> payout_service.Command:
    return payout_service.command(
        payroll_id,
        body.payroll_closing_id,
        body.business_date,
        [payout_service.Item(i.method, i.account_id, i.amount) for i in body.items],
        body.reference,
    )


def preview_payout(db: Session, actor: User, payroll_id: int, body: s.PayoutPreviewIn) -> s.PayoutPreviewOut:
    """What paying would publish, validated (roles, payroll, date, pending, funds), without writing anything."""
    planned = payout_service.plan(db, actor, payout_command(payroll_id, body))
    employee_id = planned.payroll.employee_id
    before = ledger.employee_balances(db, [employee_id]).get(employee_id, Decimal("0"))
    amount = planned.command.amount
    return s.PayoutPreviewOut(
        net=planned.payment.net,
        paid=planned.payment.paid,
        amount=amount,
        pending_after=planned.pending_after,
        balance_before=before,
        balance_after=before + amount,
        legs=[s.MovementLegOut(**leg.__dict__) for leg in planned.legs],
    )


def payout(db: Session, payout_id: int) -> s.PayoutOut:
    rows = list(db.scalars(_payouts_query().where(EmployeePayout.id == payout_id)))
    if not rows:
        raise AppError(404, "employee_payout_not_found", "El pago no existe.")
    return _payout_outs(db, rows)[0]


def payouts(db: Session, employee_id: int | None, limit: int, offset: int) -> s.PayoutPage:
    """Payouts, newest first, reversed ones too."""
    query = _payouts_query()
    if employee_id is not None:
        query = query.where(EmployeePayout.employee_id == employee_id)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    return s.PayoutPage(items=_payout_outs(db, list(db.scalars(query.limit(limit).offset(offset)))), total=total)


def pay_summary(db: Session, employee_id: int) -> s.EmployeePaySummary:
    """The employee's payrolls and payouts, apart from the internal account's balance."""
    if db.get(Employee, employee_id) is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    last = db.scalars(
        select(EmployeePayroll)
        .where(EmployeePayroll.employee_id == employee_id, EmployeePayroll.status != "CANCELLED")
        .order_by(EmployeePayroll.period_end.desc(), EmployeePayroll.id.desc())
        .limit(1)
    ).first()
    closed = list(
        db.scalars(
            select(EmployeePayroll).where(
                EmployeePayroll.employee_id == employee_id, EmployeePayroll.status == "CLOSED"
            )
        )
    )
    # The payment state of every closed payroll in two queries (R9-4: it was two per payroll, a year adds twelve).
    closed_closings = _closings_by_id(db, [r.current_closing_id for r in closed if r.current_closing_id is not None])
    paid = payout_service.paid_by_closing(db, list(closed_closings))
    states = [payout_service.state(c.net, paid[c.id]) for c in closed_closings.values()]
    pending = [p for p in states if p.pending > 0]
    recent = list(db.scalars(_payouts_query().where(EmployeePayout.employee_id == employee_id).limit(5)))
    last_row = None
    if last is not None:
        closings = _closings_by_id(db, [last.current_closing_id] if last.current_closing_id is not None else [])
        last_row = _payroll_row(
            db, last, _employee_names(db, {employee_id}), closings.get(last.current_closing_id or 0)
        )
    return s.EmployeePaySummary(
        pending_payrolls=sum((p.pending for p in pending), Decimal("0")),
        pending_payroll_count=len(pending),
        last_payroll=last_row,
        recent_payouts=_payout_outs(db, recent),
    )


def _payment(db: Session, row: EmployeePayroll) -> s.PayrollPaymentOut | None:
    state = payout_service.payment(db, row)
    if state is None:
        return None
    return s.PayrollPaymentOut(net=state.net, paid=state.paid, pending=state.pending, status=state.status)


def _payouts_query() -> Select[EmployeePayout]:
    return select(EmployeePayout).order_by(EmployeePayout.business_date.desc(), EmployeePayout.id.desc())


def _payout_outs(db: Session, rows: list[EmployeePayout]) -> list[s.PayoutOut]:
    if not rows:
        return []
    ids = [r.id for r in rows]
    items: dict[int, list[EmployeePayoutItem]] = {}
    for item in db.scalars(
        select(EmployeePayoutItem).where(EmployeePayoutItem.payout_id.in_(ids)).order_by(EmployeePayoutItem.id)
    ):
        items.setdefault(item.payout_id, []).append(item)
    account_ids = {i.account_id for group in items.values() for i in group}
    accounts = {b.account_id: b.name for b in ledger.accounts(db, include_inactive=True) if b.account_id in account_ids}
    payrolls = {
        p.id: p for p in db.scalars(select(EmployeePayroll).where(EmployeePayroll.id.in_({r.payroll_id for r in rows})))
    }
    versions = dict(
        db.execute(
            select(EmployeePayrollClosing.id, EmployeePayrollClosing.payroll_version).where(
                EmployeePayrollClosing.id.in_({r.payroll_closing_id for r in rows})
            )
        ).all()
    )
    user_ids = {r.created_by for r in rows} | {r.reversed_by for r in rows if r.reversed_by is not None}
    users = dict(db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids))).all())
    names = _employee_names(db, {r.employee_id for r in rows})
    result = []
    for r in rows:
        assert r.operation_id is not None
        result.append(
            s.PayoutOut(
                id=r.id,
                payroll_id=r.payroll_id,
                payroll_closing_id=r.payroll_closing_id,
                payroll_version=versions[r.payroll_closing_id],
                payroll_period=_period_label(payrolls[r.payroll_id]),
                employee_id=r.employee_id,
                employee_name=names.get(r.employee_id, ""),
                business_date=r.business_date,
                amount=r.amount,
                reference=r.reference,
                items=[
                    s.PayoutItemOut(
                        method=i.method,
                        account_id=i.account_id,
                        account_name=accounts.get(i.account_id, ""),
                        amount=i.amount,
                    )
                    for i in items.get(r.id, [])
                ],
                operation_id=r.operation_id,
                in_effect=r.reversal_operation_id is None,
                reversal_operation_id=r.reversal_operation_id,
                reversed_at=r.reversed_at,
                reversed_by_name=users.get(r.reversed_by) if r.reversed_by is not None else None,
                reversal_reason=r.reversal_reason,
                created_at=r.created_at,
                created_by_name=users.get(r.created_by, ""),
            )
        )
    return result
