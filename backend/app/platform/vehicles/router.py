"""Vehicles of the platform. Every signed-in user reads them, since forms of any module name a vehicle;
only a platform admin creates, corrects or (de)activates one. There is no DELETE."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.audit.models import AuditEvent
from app.platform.auth.deps import CurrentUser, DbSession, require_role
from app.platform.auth.models import User
from app.platform.vehicles import plates, service
from app.platform.vehicles.models import Vehicle
from app.platform.vehicles.schemas import VehicleDetail, VehicleEventOut, VehicleIn, VehicleOut, VehiclePatch

router = APIRouter(prefix="/vehicles", tags=["vehicles"])

PlatformAdmin = Annotated[User, Depends(require_role("admin"))]


def _names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {row.id: row.full_name for row in rows}


def _out(row: Vehicle, names: dict[int, str]) -> VehicleOut:
    return VehicleOut(
        id=row.id,
        plate=row.plate,
        plate_display=plates.display(row.plate),
        description=row.description,
        active=row.active,
        created_at=row.created_at,
        created_by_name=names.get(row.created_by, "—"),
        updated_at=row.updated_at,
        updated_by_name=names.get(row.updated_by, "—"),
    )


def _one(db: Session, row: Vehicle) -> VehicleOut:
    db.refresh(row)
    return _out(row, _names(db, {row.created_by, row.updated_by}))


def _event(row: AuditEvent, names: dict[int, str]) -> VehicleEventOut:
    payload = row.payload or {}
    return VehicleEventOut(
        id=row.id,
        event_type=row.event_type.removeprefix("platform.vehicle."),
        occurred_at=row.occurred_at,
        user_name=names.get(row.user_id) if row.user_id is not None else None,
        before=payload.get("before"),
        after=payload.get("after"),
    )


@router.get("", response_model=list[VehicleOut], summary="Vehículos")
def list_vehicles(db: DbSession, _: CurrentUser, include_inactive: bool = False) -> list[VehicleOut]:
    rows = service.list_all(db, include_inactive=include_inactive)
    names = _names(db, {r.created_by for r in rows} | {r.updated_by for r in rows})
    return [_out(row, names) for row in rows]


@router.get("/{vehicle_id}", response_model=VehicleDetail, summary="Vehículo e historia de cambios")
def get_vehicle(vehicle_id: int, db: DbSession, _: CurrentUser) -> VehicleDetail:
    row = service.get(db, vehicle_id)
    events = service.history(db, vehicle_id)
    names = _names(db, {row.created_by, row.updated_by} | {e.user_id for e in events if e.user_id is not None})
    return VehicleDetail(**_out(row, names).model_dump(), history=[_event(e, names) for e in events])


@router.post("", response_model=VehicleOut, status_code=201, summary="Registrar un vehículo")
def create_vehicle(body: VehicleIn, db: DbSession, user: PlatformAdmin) -> VehicleOut:
    return _one(db, service.create(db, user, body))


@router.patch("/{vehicle_id}", response_model=VehicleOut, summary="Corregir, activar o desactivar")
def update_vehicle(vehicle_id: int, body: VehiclePatch, db: DbSession, user: PlatformAdmin) -> VehicleOut:
    return _one(db, service.update(db, user, vehicle_id, body))
