"""Responsables of a boca de expendio (R7-1): who answers for it, day by day.

Ranges are inclusive at both ends: a responsibility is in effect on `day` when
`valid_from <= day <= valid_to`, with no upper bound while `valid_to` is NULL. Every creation and every end
is audited (`settlements.outlet_responsibility.*`); the database repeats each rule (migration 0022).
"""

from datetime import date

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.outlets.models import OutletResponsibility
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.outlets.models import Outlet
from app.platform.parties.models import Employee

ENTITY = "outlet_responsibility"


def in_effect_on(day: date) -> ColumnElement[bool]:
    """The inclusive rule, written once."""
    return (OutletResponsibility.valid_from <= day) & or_(
        OutletResponsibility.valid_to.is_(None), OutletResponsibility.valid_to >= day
    )


def listing(
    db: Session,
    *,
    outlet_id: int | None = None,
    employee_id: int | None = None,
    on: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[OutletResponsibility], int]:
    query = select(OutletResponsibility)
    if outlet_id is not None:
        query = query.where(OutletResponsibility.outlet_id == outlet_id)
    if employee_id is not None:
        query = query.where(OutletResponsibility.employee_id == employee_id)
    if on is not None:
        query = query.where(in_effect_on(on))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(OutletResponsibility.valid_from.desc(), OutletResponsibility.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


def selectable_employees(db: Session) -> list[Employee]:
    """Any active employee may answer for a boca (no employee function: Enzo's decision D3)."""
    return list(db.scalars(select(Employee).where(Employee.active.is_(True)).order_by(Employee.full_name, Employee.id)))


def create(db: Session, actor: User, outlet_id: int, employee_id: int, valid_from: date) -> OutletResponsibility:
    outlet = db.scalars(select(Outlet).where(Outlet.id == outlet_id).with_for_update(read=True)).first()
    if outlet is None:
        raise AppError(404, "outlet_not_found", "La boca de expendio no existe.")
    if not outlet.active:
        raise AppError(
            422,
            "inactive_outlet",
            "La boca está desactivada: no suma responsables nuevos.",
            {"outlet_id": outlet_id},
        )
    employee = db.scalars(select(Employee).where(Employee.id == employee_id).with_for_update(read=True)).first()
    if employee is None or not employee.active:
        raise AppError(
            422,
            "inactive_employee",
            "El responsable tiene que ser un empleado activo.",
            {"employee_id": employee_id},
        )
    _require_no_overlap(db, outlet_id, employee_id, valid_from)
    row = OutletResponsibility(outlet_id=outlet_id, employee_id=employee_id, valid_from=valid_from, created_by=actor.id)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:  # the same assignment registered at the same time
        db.expire_all()
        _require_no_overlap(db, outlet_id, employee_id, valid_from)
        raise AppError(409, "outlet_responsibility_conflict", "No se pudo guardar. Volvé a intentar.") from exc
    record_change(
        db,
        f"settlements.{ENTITY}.created",
        action="create",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=row.id,
        before=None,
        after={"outlet_id": outlet_id, "employee_id": employee_id, "valid_from": valid_from.isoformat()},
    )
    db.commit()
    return row


def end(db: Session, actor: User, responsibility_id: int, valid_to: date, reason: str) -> OutletResponsibility:
    """Once, with a reason; also when the boca or the employee was deactivated meanwhile."""
    row = db.scalars(
        select(OutletResponsibility).where(OutletResponsibility.id == responsibility_id).with_for_update()
    ).first()
    if row is None:
        raise AppError(404, "outlet_responsibility_not_found", "La responsabilidad no existe.")
    if row.valid_to is not None:
        raise AppError(
            409,
            "outlet_responsibility_already_ended",
            f"Esta responsabilidad ya terminó el {row.valid_to:%d/%m/%Y}. La historia no se reescribe.",
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
        raise AppError(422, "end_reason_required", "Indicá el motivo del fin de la responsabilidad.")
    row.valid_to, row.end_reason, row.ended_by, row.ended_at = valid_to, text, actor.id, func.now()
    db.flush()
    record_change(
        db,
        f"settlements.{ENTITY}.ended",
        action="update",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=row.id,
        before={"valid_to": None},
        after={"valid_to": valid_to.isoformat(), "reason": text},
    )
    db.commit()
    return row


def _require_no_overlap(db: Session, outlet_id: int, employee_id: int, valid_from: date) -> None:
    """A new one is open-ended, so it overlaps any of the same pair that is open or ends on or after its start."""
    other = db.scalars(
        select(OutletResponsibility)
        .where(
            OutletResponsibility.outlet_id == outlet_id,
            OutletResponsibility.employee_id == employee_id,
            or_(OutletResponsibility.valid_to.is_(None), OutletResponsibility.valid_to >= valid_from),
        )
        .order_by(OutletResponsibility.valid_from)
    ).first()
    if other is not None:
        until = "sin fecha de fin" if other.valid_to is None else f"hasta el {other.valid_to:%d/%m/%Y}"
        raise AppError(
            409,
            "outlet_responsibility_overlap",
            f"Ese empleado ya es responsable de esta boca desde el {other.valid_from:%d/%m/%Y} {until}.",
            {"responsibility_id": other.id},
        )
