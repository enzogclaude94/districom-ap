"""Employees and their functions (DATA_MODEL.md §4.1).

Rules: one row per person; deactivation is logical; a function row is never edited in place, only
closed (`valid_to`), and periods of the same function never overlap (exclusion constraint).
"""

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.platform.audit.service import record_change
from app.platform.parties.functions import registry
from app.platform.parties.models import Employee, EmployeeFunction
from app.platform.parties.schemas import EmployeeIn

_OVERLAP_CONSTRAINT = "ex_employee_functions_no_overlap"


def _snapshot(employee: Employee) -> dict[str, Any]:
    """Audit view of an employee, without PII (the phone is reported only as present or not)."""
    return {
        "full_name": employee.full_name,
        "active": employee.active,
        "hire_date": employee.hire_date.isoformat() if employee.hire_date else None,
        "has_phone": employee.phone is not None,
    }


def _function_snapshot(row: EmployeeFunction) -> dict[str, Any]:
    return {
        "employee_id": row.employee_id,
        "function_code": row.function_code,
        "valid_from": row.valid_from.isoformat(),
        "valid_to": row.valid_to.isoformat() if row.valid_to else None,
    }


def get_employee(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    return employee


def list_employees(
    db: Session,
    *,
    active: bool | None = None,
    search: str | None = None,
    function_code: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[Sequence[Employee], int]:
    query = select(Employee)
    if active is not None:
        query = query.where(Employee.active.is_(active))
    if search:
        pattern = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(Employee.full_name.ilike(f"%{pattern}%", escape="\\"))
    if function_code:
        today = business_today()
        current = select(EmployeeFunction.employee_id).where(
            EmployeeFunction.function_code == function_code,
            EmployeeFunction.valid_from <= today,
            or_(EmployeeFunction.valid_to.is_(None), EmployeeFunction.valid_to >= today),
        )
        query = query.where(Employee.id.in_(current))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Employee.full_name, Employee.id).limit(limit).offset(offset)).all()
    return rows, total


def functions_of(db: Session, employee_ids: Sequence[int]) -> dict[int, list[EmployeeFunction]]:
    result: dict[int, list[EmployeeFunction]] = {employee_id: [] for employee_id in employee_ids}
    if not employee_ids:
        return result
    rows = db.scalars(
        select(EmployeeFunction)
        .where(EmployeeFunction.employee_id.in_(employee_ids))
        .order_by(EmployeeFunction.valid_from.desc(), EmployeeFunction.id.desc())
    )
    for row in rows:
        result[row.employee_id].append(row)
    return result


def has_function(db: Session, employee_id: int, function_code: str, on: date) -> bool:
    """Whether the employee holds the function on that date (for modules validating a role)."""
    found = db.scalar(
        select(EmployeeFunction.id).where(
            EmployeeFunction.employee_id == employee_id,
            EmployeeFunction.function_code == function_code,
            EmployeeFunction.valid_from <= on,
            or_(EmployeeFunction.valid_to.is_(None), EmployeeFunction.valid_to >= on),
        )
    )
    return found is not None


def create_employee(db: Session, data: EmployeeIn, *, user_id: int) -> Employee:
    employee = Employee(full_name=data.full_name, hire_date=data.hire_date, phone=data.phone)
    db.add(employee)
    db.flush()
    record_change(
        db,
        "platform.employee.created",
        action="create",
        user_id=user_id,
        entity_type="employee",
        entity_id=employee.id,
        before=None,
        after=_snapshot(employee),
    )
    db.commit()
    return employee


def update_employee(db: Session, employee_id: int, data: EmployeeIn, *, user_id: int) -> Employee:
    employee = get_employee(db, employee_id)
    before = _snapshot(employee)
    phone_changed = employee.phone != data.phone
    employee.full_name = data.full_name
    employee.hire_date = data.hire_date
    employee.phone = data.phone
    after = _snapshot(employee)
    if before != after or phone_changed:
        after["phone_changed"] = phone_changed
        record_change(
            db,
            "platform.employee.updated",
            action="update",
            user_id=user_id,
            entity_type="employee",
            entity_id=employee.id,
            before=before,
            after=after,
        )
    db.commit()
    return employee


def deactivate_employee(db: Session, employee_id: int, end_date: date | None, *, user_id: int) -> Employee:
    """Logical deletion: the employee becomes inactive and their open functions end on `end_date`."""
    employee = get_employee(db, employee_id)
    if not employee.active:
        raise AppError(409, "employee_inactive", "El empleado ya está dado de baja.")
    end = end_date or business_today()
    open_functions = db.scalars(
        select(EmployeeFunction)
        .where(EmployeeFunction.employee_id == employee.id, EmployeeFunction.valid_to.is_(None))
        .with_for_update()
    ).all()
    if any(row.valid_from > end for row in open_functions):
        raise AppError(
            409,
            "function_starts_after_end",
            "Hay una función que empieza después de la fecha de baja.",
        )
    before = _snapshot(employee)
    employee.active = False
    for row in open_functions:
        row_before = _function_snapshot(row)
        row.valid_to = end
        record_change(
            db,
            "platform.employee_function.ended",
            action="end",
            user_id=user_id,
            entity_type="employee_function",
            entity_id=row.id,
            before=row_before,
            after=_function_snapshot(row),
        )
    record_change(
        db,
        "platform.employee.deactivated",
        action="deactivate",
        user_id=user_id,
        entity_type="employee",
        entity_id=employee.id,
        before=before,
        after={**_snapshot(employee), "end_date": end.isoformat()},
    )
    db.commit()
    return employee


def reactivate_employee(db: Session, employee_id: int, *, user_id: int) -> Employee:
    """Functions are not reopened: a new period is assigned explicitly."""
    employee = get_employee(db, employee_id)
    if employee.active:
        raise AppError(409, "employee_active", "El empleado ya está activo.")
    before = _snapshot(employee)
    employee.active = True
    record_change(
        db,
        "platform.employee.reactivated",
        action="reactivate",
        user_id=user_id,
        entity_type="employee",
        entity_id=employee.id,
        before=before,
        after=_snapshot(employee),
    )
    db.commit()
    return employee


def assign_function(
    db: Session, employee_id: int, function_code: str, valid_from: date, *, user_id: int
) -> EmployeeFunction:
    employee = get_employee(db, employee_id)
    if not employee.active:
        raise AppError(409, "employee_inactive", "No se pueden asignar funciones a un empleado dado de baja.")
    if registry.get(function_code) is None:
        raise AppError(422, "unknown_function", "La función no existe.")
    row = EmployeeFunction(
        employee_id=employee.id, function_code=function_code, valid_from=valid_from, created_by=user_id
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        if _OVERLAP_CONSTRAINT in str(exc.orig):
            raise AppError(
                409,
                "function_overlap",
                "El empleado ya tiene esa función en un período que se superpone.",
            ) from exc
        raise
    record_change(
        db,
        "platform.employee_function.assigned",
        action="assign",
        user_id=user_id,
        entity_type="employee_function",
        entity_id=row.id,
        before=None,
        after=_function_snapshot(row),
    )
    db.commit()
    return row


def end_function(db: Session, employee_id: int, function_id: int, valid_to: date, *, user_id: int) -> EmployeeFunction:
    row = db.scalar(
        select(EmployeeFunction)
        .where(EmployeeFunction.id == function_id, EmployeeFunction.employee_id == employee_id)
        .with_for_update()
    )
    if row is None:
        raise AppError(404, "function_not_found", "La función no existe.")
    if row.valid_to is not None:
        raise AppError(409, "function_already_ended", "La función ya está cerrada.")
    if valid_to < row.valid_from:
        raise AppError(422, "invalid_end_date", "La fecha de fin no puede ser anterior al inicio.")
    before = _function_snapshot(row)
    row.valid_to = valid_to
    record_change(
        db,
        "platform.employee_function.ended",
        action="end",
        user_id=user_id,
        entity_type="employee_function",
        entity_id=row.id,
        before=before,
        after=_function_snapshot(row),
    )
    db.commit()
    return row
