"""Bocas de expendio of the platform. Every signed-in user reads them, since Rendiciones names a boca in its
records; only a platform admin creates, corrects or (de)activates one. The code never changes and there is
no DELETE."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.audit.models import AuditEvent
from app.platform.auth.deps import CurrentUser, DbSession, require_role
from app.platform.auth.models import User
from app.platform.outlets import service
from app.platform.outlets.models import Outlet
from app.platform.outlets.schemas import OutletDetail, OutletEventOut, OutletIn, OutletOut, OutletPatch

router = APIRouter(prefix="/outlets", tags=["outlets"])

PlatformAdmin = Annotated[User, Depends(require_role("admin"))]


def _names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {row.id: row.full_name for row in rows}


def _out(row: Outlet, names: dict[int, str]) -> OutletOut:
    return OutletOut(
        id=row.id,
        code=row.code,
        name=row.name,
        description=row.description,
        active=row.active,
        created_at=row.created_at,
        created_by_name=names.get(row.created_by, "—"),
        updated_at=row.updated_at,
        updated_by_name=names.get(row.updated_by, "—"),
    )


def _one(db: Session, row: Outlet) -> OutletOut:
    db.refresh(row)
    return _out(row, _names(db, {row.created_by, row.updated_by}))


def _event(row: AuditEvent, names: dict[int, str]) -> OutletEventOut:
    payload = row.payload or {}
    return OutletEventOut(
        id=row.id,
        event_type=row.event_type.removeprefix("platform.outlet."),
        occurred_at=row.occurred_at,
        user_name=names.get(row.user_id) if row.user_id is not None else None,
        before=payload.get("before"),
        after=payload.get("after"),
    )


@router.get("", response_model=list[OutletOut], summary="Bocas de expendio")
def list_outlets(db: DbSession, _: CurrentUser, include_inactive: bool = False) -> list[OutletOut]:
    rows = service.list_all(db, include_inactive=include_inactive)
    names = _names(db, {r.created_by for r in rows} | {r.updated_by for r in rows})
    return [_out(row, names) for row in rows]


@router.get("/{outlet_id}", response_model=OutletDetail, summary="Boca de expendio e historia de cambios")
def get_outlet(outlet_id: int, db: DbSession, _: CurrentUser) -> OutletDetail:
    row = service.get(db, outlet_id)
    events = service.history(db, outlet_id)
    names = _names(db, {row.created_by, row.updated_by} | {e.user_id for e in events if e.user_id is not None})
    return OutletDetail(**_out(row, names).model_dump(), history=[_event(e, names) for e in events])


@router.post("", response_model=OutletOut, status_code=201, summary="Registrar una boca de expendio")
def create_outlet(body: OutletIn, db: DbSession, user: PlatformAdmin) -> OutletOut:
    return _one(db, service.create(db, user, body))


@router.patch("/{outlet_id}", response_model=OutletOut, summary="Corregir, desactivar o reactivar")
def update_outlet(outlet_id: int, body: OutletPatch, db: DbSession, user: PlatformAdmin) -> OutletOut:
    return _one(db, service.update(db, user, outlet_id, body))
