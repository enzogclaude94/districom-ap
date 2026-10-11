"""Responsables of the bocas de expendio (R7-1). Reading is for Rendiciones viewers; naming and ending a
responsable is for its admins. The bocas themselves are the platform's (`/api/v1/outlets`)."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.settlements.outlets import service
from app.modules.settlements.outlets.models import OutletResponsibility
from app.modules.settlements.outlets.schemas import (
    ResponsibilityEndIn,
    ResponsibilityIn,
    ResponsibilityOut,
    ResponsibilityPage,
    SelectableEmployeeOut,
)
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User
from app.platform.outlets import service as outlets
from app.platform.outlets.models import Outlet
from app.platform.parties.models import Employee

router = APIRouter()

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


def _page(db: Session, rows: list[OutletResponsibility], total: int) -> ResponsibilityPage:
    outlet_rows = {o.id: o for o in db.scalars(select(Outlet).where(Outlet.id.in_({r.outlet_id for r in rows})))}
    employees = {e.id: e for e in db.scalars(select(Employee).where(Employee.id.in_({r.employee_id for r in rows})))}
    user_ids = {r.created_by for r in rows} | {r.ended_by for r in rows if r.ended_by is not None}
    names = {u.id: u.full_name for u in db.execute(select(User.id, User.full_name).where(User.id.in_(user_ids)))}
    items = []
    for r in rows:
        outlet, employee = outlet_rows[r.outlet_id], employees[r.employee_id]
        items.append(
            ResponsibilityOut(
                id=r.id,
                outlet_id=outlet.id,
                outlet_code=outlet.code,
                outlet_name=outlet.name,
                outlet_active=outlet.active,
                employee_id=employee.id,
                employee_name=employee.full_name,
                employee_active=employee.active,
                valid_from=r.valid_from,
                valid_to=r.valid_to,
                created_at=r.created_at,
                created_by_name=names.get(r.created_by, "—"),
                ended_at=r.ended_at,
                ended_by_name=names.get(r.ended_by) if r.ended_by is not None else None,
                end_reason=r.end_reason,
            )
        )
    return ResponsibilityPage(items=items, total=total)


def _one(db: Session, row: OutletResponsibility) -> ResponsibilityOut:
    db.refresh(row)
    return _page(db, [row], 1).items[0]


@router.get(
    "/outlet-responsibilities",
    response_model=ResponsibilityPage,
    summary="Responsables de bocas (con `on`: los vigentes ese día, ambos extremos incluidos)",
)
def list_responsibilities(
    db: DbSession,
    _: Viewer,
    outlet_id: int | None = None,
    employee_id: int | None = None,
    on: date | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> ResponsibilityPage:
    rows, total = service.listing(db, outlet_id=outlet_id, employee_id=employee_id, on=on, limit=limit, offset=offset)
    return _page(db, rows, total)


@router.get(
    "/outlet-responsibilities/employees",
    response_model=list[SelectableEmployeeOut],
    summary="Empleados activos que pueden ser responsables",
)
def selectable_employees(db: DbSession, _: Admin) -> list[SelectableEmployeeOut]:
    return [SelectableEmployeeOut(id=e.id, full_name=e.full_name) for e in service.selectable_employees(db)]


@router.get(
    "/outlets/{outlet_id}/responsibilities",
    response_model=ResponsibilityPage,
    summary="Historia de responsables de una boca (con `on`: los vigentes ese día)",
)
def outlet_responsibilities(
    outlet_id: int, db: DbSession, _: Viewer, on: date | None = None, limit: Limit = 50, offset: Offset = 0
) -> ResponsibilityPage:
    outlets.get(db, outlet_id)
    rows, total = service.listing(db, outlet_id=outlet_id, on=on, limit=limit, offset=offset)
    return _page(db, rows, total)


@router.post(
    "/outlets/{outlet_id}/responsibilities",
    response_model=ResponsibilityOut,
    status_code=201,
    summary="Sumar un responsable a una boca",
)
def create_responsibility(outlet_id: int, body: ResponsibilityIn, db: DbSession, user: Admin) -> ResponsibilityOut:
    return _one(db, service.create(db, user, outlet_id, body.employee_id, body.valid_from))


@router.post(
    "/outlet-responsibilities/{responsibility_id}/end",
    response_model=ResponsibilityOut,
    summary="Terminar una responsabilidad (una sola vez, con motivo)",
)
def end_responsibility(
    responsibility_id: int, body: ResponsibilityEndIn, db: DbSession, user: Admin
) -> ResponsibilityOut:
    return _one(db, service.end(db, user, responsibility_id, body.valid_to, body.reason))
