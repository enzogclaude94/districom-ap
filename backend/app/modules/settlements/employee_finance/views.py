"""Read side of Rendiciones > Empleados: internal accounts, salary terms and movements.

The internal account (`EMPLOYEE`, one per employee, created on first use) is net: positive means the employee owes
the company, negative that the company owes the employee. Its movements are read from the ledger and their source
documents, nothing is copied: what R2-R7 write (differences of settlements, pending write-offs) and the employee
movements of R8-2 (consumptions, advances, adjustments), each with its reversals. Each movement says where it comes
from; the balance is never split up by arithmetic (Enzo's precision D). There is no payroll yet, so nothing reads
as deducted or applied.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import exists, func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.employee_finance import compensation
from app.modules.settlements.employee_finance import movements as movement_service
from app.modules.settlements.employee_finance import schemas as s
from app.modules.settlements.employee_finance.models import EmployeeCompensationTerm, EmployeeMovement
from app.modules.settlements.ledger import service as ledger
from app.platform.auth.models import User
from app.platform.parties.models import Employee

SETTLEMENT_SOURCE = "settlement"
PENDING_SOURCE = "pending_collection"
MOVEMENT_SOURCE = movement_service.SOURCE


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
    settlement_ids = {e.source_id for e in entries if e.source_type == SETTLEMENT_SOURCE and e.source_id is not None}
    settlements = (
        {row.id: row for row in db.scalars(select(Settlement).where(Settlement.id.in_(settlement_ids)))}
        if settlement_ids
        else {}
    )
    movement_ids = {e.source_id for e in entries if e.source_type == MOVEMENT_SOURCE and e.source_id is not None}
    documents = (
        {row.id: row for row in db.scalars(select(EmployeeMovement).where(EmployeeMovement.id.in_(movement_ids)))}
        if movement_ids
        else {}
    )
    items = []
    for entry in entries:
        origin = _origin(entry.operation_type, entry.reversed_type, entry.amount)
        description: str | None = None
        reversed_: bool | None = None
        source_type, source_id = entry.source_type, entry.source_id
        kind: s.DocumentKind | None = None
        number: int | None = None
        document_date: date | None = None
        if source_type == SETTLEMENT_SOURCE and source_id in settlements:
            settlement = settlements[source_id]
            kind = "OUTLET_SETTLEMENT" if settlement.type == "OUTLET" else "DELIVERY_SETTLEMENT"
            number, document_date = settlement.sequence_number, settlement.business_date
        elif source_type == PENDING_SOURCE:
            kind = "PENDING_COLLECTION"
        elif source_type == MOVEMENT_SOURCE and source_id in documents:
            movement = documents[source_id]
            kind, document_date = "EMPLOYEE_MOVEMENT", movement.business_date
            description, reversed_ = movement.description, movement.reversal_operation_id is not None
        items.append(
            s.AccountMovementOut(
                entry_id=entry.entry_id,
                operation_id=entry.operation_id,
                business_date=entry.business_date,
                amount=entry.amount,
                reading="CHARGE" if entry.amount > 0 else "CREDIT",
                origin=origin,
                document_kind=kind,
                document_id=source_id if kind is not None else None,
                document_date=document_date,
                document_number=number,
                description=description,
                document_reversed=reversed_,
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
    return "OTHER"


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
