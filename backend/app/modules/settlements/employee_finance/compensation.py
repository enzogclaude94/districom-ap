"""Salary terms (R8-1, ADR 010): history by validity, never rewritten.

A term is in effect on `day` when `valid_from <= day <= valid_to` (no upper bound while `valid_to` is NULL).
Creating and ending are audited as `settlements.employee_finance.compensation_term.*`; the audit holds the
salary, so it is read only through this area. The database repeats every rule (migration 0025).
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.employee_finance.models import EmployeeCompensationTerm
from app.modules.settlements.employee_finance.schemas import TermStatus
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.parties.models import Employee

ENTITY = "compensation_term"
EVENT = "settlements.employee_finance.compensation_term"


def in_effect_on(day: date) -> ColumnElement[bool]:
    """The inclusive rule, written once."""
    return (EmployeeCompensationTerm.valid_from <= day) & or_(
        EmployeeCompensationTerm.valid_to.is_(None), EmployeeCompensationTerm.valid_to >= day
    )


def status(term: EmployeeCompensationTerm, today: date) -> TermStatus:
    if term.valid_from > today:
        return "FUTURE"
    if term.valid_to is not None and term.valid_to < today:
        return "PAST"
    return "CURRENT"


def history(db: Session, employee_id: int) -> list[EmployeeCompensationTerm]:
    """Every term of the employee, newest start first."""
    return list(
        db.scalars(
            select(EmployeeCompensationTerm)
            .where(EmployeeCompensationTerm.employee_id == employee_id)
            .order_by(EmployeeCompensationTerm.valid_from.desc(), EmployeeCompensationTerm.id.desc())
        )
    )


def current_and_next(
    db: Session, employee_ids: list[int], today: date
) -> tuple[dict[int, EmployeeCompensationTerm], dict[int, EmployeeCompensationTerm]]:
    """The term in effect today and the first one starting later, per employee."""
    current: dict[int, EmployeeCompensationTerm] = {}
    upcoming: dict[int, EmployeeCompensationTerm] = {}
    if not employee_ids:
        return current, upcoming
    rows = db.scalars(
        select(EmployeeCompensationTerm)
        .where(
            EmployeeCompensationTerm.employee_id.in_(employee_ids),
            or_(EmployeeCompensationTerm.valid_to.is_(None), EmployeeCompensationTerm.valid_to >= today),
        )
        .order_by(EmployeeCompensationTerm.valid_from)
    )
    for term in rows:
        if term.valid_from <= today:
            current[term.employee_id] = term
        else:
            upcoming.setdefault(term.employee_id, term)
    return current, upcoming


def create(
    db: Session, actor: User, employee_id: int, valid_from: date, base_salary: Decimal, notes: str | None
) -> EmployeeCompensationTerm:
    """A new open term: the employee exists and is active (as for every new assignment); it may start later."""
    employee = db.scalars(select(Employee).where(Employee.id == employee_id).with_for_update(read=True)).first()
    if employee is None:
        raise AppError(404, "employee_not_found", "El empleado no existe.")
    if not employee.active:
        raise AppError(
            422,
            "inactive_employee",
            "El empleado está inactivo: no suma condiciones salariales nuevas.",
            {"employee_id": employee_id},
        )
    _require_no_overlap(db, employee_id, valid_from)
    text = " ".join((notes or "").split()) or None
    row = EmployeeCompensationTerm(
        employee_id=employee_id, valid_from=valid_from, base_salary=base_salary, notes=text, created_by=actor.id
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:  # another term of the same employee registered at the same time
        db.expire_all()
        _require_no_overlap(db, employee_id, valid_from)
        raise AppError(409, "compensation_term_conflict", "No se pudo guardar. Volvé a intentar.") from exc
    record_change(
        db,
        f"{EVENT}.created",
        action="create",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=row.id,
        before=None,
        after={
            "employee_id": employee_id,
            "valid_from": valid_from.isoformat(),
            "base_salary": f"{base_salary:.2f}",
            "notes": text,
        },
    )
    db.commit()
    return row


def end(db: Session, actor: User, term_id: int, valid_to: date, reason: str) -> EmployeeCompensationTerm:
    """Once, with a reason; also when the employee was deactivated meanwhile."""
    row = db.scalars(
        select(EmployeeCompensationTerm).where(EmployeeCompensationTerm.id == term_id).with_for_update()
    ).first()
    if row is None:
        raise AppError(404, "compensation_term_not_found", "La condición salarial no existe.")
    if row.valid_to is not None:
        raise AppError(
            409,
            "compensation_term_already_ended",
            f"Esta condición ya terminó el {row.valid_to:%d/%m/%Y}. La historia no se reescribe.",
            {"valid_to": row.valid_to.isoformat()},
        )
    if valid_to < row.valid_from:
        raise AppError(
            422,
            "invalid_valid_to",
            f"La fecha de fin no puede ser anterior al inicio ({row.valid_from:%d/%m/%Y}).",
            {"valid_from": row.valid_from.isoformat()},
        )
    text = " ".join(reason.split())
    if not text:
        raise AppError(422, "end_reason_required", "Indicá el motivo del fin de la condición.")
    row.valid_to, row.end_reason, row.ended_by, row.ended_at = valid_to, text, actor.id, func.now()
    db.flush()
    record_change(
        db,
        f"{EVENT}.ended",
        action="update",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=row.id,
        before={"valid_to": None},
        after={"valid_to": valid_to.isoformat(), "reason": text},
    )
    db.commit()
    db.refresh(row)
    return row


def _require_no_overlap(db: Session, employee_id: int, valid_from: date) -> None:
    """A new term is open-ended, so it overlaps any other that is open or ends on or after its start."""
    other = db.scalars(
        select(EmployeeCompensationTerm)
        .where(
            EmployeeCompensationTerm.employee_id == employee_id,
            or_(EmployeeCompensationTerm.valid_to.is_(None), EmployeeCompensationTerm.valid_to >= valid_from),
        )
        .order_by(EmployeeCompensationTerm.valid_from)
    ).first()
    if other is not None:
        until = "sin fecha de fin" if other.valid_to is None else f"hasta el {other.valid_to:%d/%m/%Y}"
        raise AppError(
            409,
            "compensation_term_overlap",
            f"Ya hay una condición salarial desde el {other.valid_from:%d/%m/%Y} {until}. "
            "Terminala antes de cargar la nueva.",
            {"compensation_term_id": other.id},
        )


def overlapping(db: Session, employee_id: int, start: date, end: date) -> list[EmployeeCompensationTerm]:
    """The employee's terms in effect on any day of `start`..`end` (inclusive), oldest first."""
    return list(
        db.scalars(
            select(EmployeeCompensationTerm)
            .where(
                EmployeeCompensationTerm.employee_id == employee_id,
                EmployeeCompensationTerm.valid_from <= end,
                or_(EmployeeCompensationTerm.valid_to.is_(None), EmployeeCompensationTerm.valid_to >= start),
            )
            .order_by(EmployeeCompensationTerm.valid_from, EmployeeCompensationTerm.id)
        )
    )
