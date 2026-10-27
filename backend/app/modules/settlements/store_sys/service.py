"""Employees' Store Sys codes (R9-1, Enzo's R9-1 adjustment 3): `employees.external_store_id` is the code a Store Sys
record names a driver by. Reading it takes the area; loading, correcting or removing it takes its admin, with the
previous and new value, who and when in the audit trail (never a silent change)."""

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.modules.settlements.store_sys import schemas as s
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.parties import service as parties
from app.platform.parties.models import Employee

CHANGED_EVENT = "settlements.store_sys.employee_code_changed"


def _out(employee: Employee) -> s.EmployeeCodeOut:
    return s.EmployeeCodeOut(
        employee_id=employee.id,
        full_name=employee.full_name,
        active=employee.active,
        store_code=employee.external_store_id,
    )


def list_codes(db: Session, search: str | None, limit: int, offset: int) -> s.EmployeeCodePage:
    query = select(Employee)
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(Employee.full_name.ilike(pattern), Employee.external_store_id.ilike(pattern)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(Employee.active.desc(), Employee.full_name, Employee.id).limit(limit).offset(offset)
    )
    return s.EmployeeCodePage(items=[_out(e) for e in rows], total=total)


def get_code(db: Session, employee_id: int) -> s.EmployeeCodeOut:
    return _out(parties.get_employee(db, employee_id))


def set_code(db: Session, actor: User, employee_id: int, body: s.EmployeeCodeIn) -> s.EmployeeCodeOut:
    employee = parties.set_external_store_id(
        db, employee_id, body.store_code, body.reason, user_id=actor.id, event_type=CHANGED_EVENT
    )
    return _out(employee)


def history(db: Session, employee_id: int) -> list[s.EmployeeCodeChangeOut]:
    parties.get_employee(db, employee_id)
    rows = db.execute(
        select(AuditEvent, User.full_name)
        .outerjoin(User, User.id == AuditEvent.user_id)
        .where(
            AuditEvent.event_type == CHANGED_EVENT,
            AuditEvent.entity_type == "employee",
            AuditEvent.entity_id == str(employee_id),
        )
        .order_by(AuditEvent.occurred_at, AuditEvent.id)
    )
    return [
        s.EmployeeCodeChangeOut(
            before=(event.payload.get("before") or {}).get("external_store_id"),
            after=(event.payload.get("after") or {}).get("external_store_id"),
            reason=(event.payload.get("after") or {}).get("reason"),
            changed_by_name=name,
            changed_at=event.occurred_at,
        )
        for event, name in rows
    ]
