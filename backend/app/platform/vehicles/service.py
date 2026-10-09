"""Vehicles: reading for every module, administration for platform admins (R5-1).

Every change is audited with who, before and after (`platform.vehicle.*`); the vehicle's history is those
events. Nothing is deleted: the database refuses it (migration 0014).
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.models import AuditEvent
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.vehicles import plates
from app.platform.vehicles.models import Vehicle
from app.platform.vehicles.schemas import VehicleIn, VehiclePatch

ENTITY = "vehicle"


def list_all(db: Session, *, include_inactive: bool = False) -> list[Vehicle]:
    query = select(Vehicle).order_by(Vehicle.plate, Vehicle.id)
    if not include_inactive:
        query = query.where(Vehicle.active.is_(True))
    return list(db.scalars(query))


def get(db: Session, vehicle_id: int) -> Vehicle:
    row = db.get(Vehicle, vehicle_id)
    if row is None:
        raise AppError(404, "vehicle_not_found", "El vehículo no existe.")
    return row


def active(db: Session, vehicle_id: int) -> Vehicle:
    """A vehicle a new record may name; 422 when unknown or deactivated. Old records keep theirs."""
    row = db.get(Vehicle, vehicle_id)
    if row is None or not row.active:
        raise AppError(422, "invalid_vehicle", "El vehículo tiene que ser uno activo.", {"vehicle_id": vehicle_id})
    return row


def history(db: Session, vehicle_id: int) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == ENTITY, AuditEvent.entity_id == str(vehicle_id))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )


def create(db: Session, actor: User, body: VehicleIn) -> Vehicle:
    plate = plates.normalize(body.plate)
    description = _description(body.description)
    _require_free_plate(db, plate, None)
    row = Vehicle(
        plate=plate,
        plate_key=plates.plate_key(plate),
        description=description,
        created_by=actor.id,
        updated_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:  # another admin registered the same plate at the same time
        raise _plate_taken(db, plate) from exc
    _audit(db, actor, row, "created", None, {"plate": plate, "description": description, "active": True})
    db.commit()
    return row


def update(db: Session, actor: User, vehicle_id: int, body: VehiclePatch) -> Vehicle:
    row = db.scalars(select(Vehicle).where(Vehicle.id == vehicle_id).with_for_update()).first()
    if row is None:
        raise AppError(404, "vehicle_not_found", "El vehículo no existe.")
    changes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    if body.plate is not None:
        plate = plates.normalize(body.plate)
        if plate != row.plate:
            _require_free_plate(db, plate, row.id)
            changes.append(("plate_changed", {"plate": row.plate}, {"plate": plate}))
            row.plate, row.plate_key = plate, plates.plate_key(plate)
    if body.description is not None:
        description = _description(body.description)
        if description != row.description:
            changes.append(("description_changed", {"description": row.description}, {"description": description}))
            row.description = description
    if body.active is not None and body.active != row.active:
        changes.append(("activated" if body.active else "deactivated", {"active": row.active}, {"active": body.active}))
        row.active = body.active
    if changes:
        row.updated_by = actor.id
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError as exc:  # the new plate was taken meanwhile
            raise _plate_taken(db, row.plate) from exc
        for kind, before, after in changes:
            _audit(db, actor, row, kind, before, after)
    db.commit()
    return row


def _description(raw: str) -> str:
    description = " ".join(raw.split())
    if not description:
        raise AppError(422, "description_required", "Indicá una descripción del vehículo.")
    return description


def _require_free_plate(db: Session, plate: str, own_id: int | None) -> None:
    other = db.scalars(select(Vehicle).where(Vehicle.plate_key == plates.plate_key(plate))).first()
    if other is not None and other.id != own_id:
        raise _taken(other)


def _plate_taken(db: Session, plate: str) -> AppError:
    db.expire_all()
    winner = db.scalars(select(Vehicle).where(Vehicle.plate_key == plates.plate_key(plate))).first()
    if winner is None:  # not a plate conflict after all
        return AppError(409, "vehicle_conflict", "No se pudo guardar el vehículo. Volvé a intentar.")
    return _taken(winner)


def _taken(other: Vehicle) -> AppError:
    return AppError(
        409,
        "vehicle_plate_taken",
        f"La patente {plates.display(other.plate)} ya es de otro vehículo ({other.description}).",
        {"vehicle_id": other.id, "active": other.active},
    )


def _audit(
    db: Session, actor: User, row: Vehicle, kind: str, before: dict[str, Any] | None, after: dict[str, Any]
) -> None:
    record_change(
        db,
        f"platform.vehicle.{kind}",
        action="create" if kind == "created" else "update",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=row.id,
        before=before,
        after=after,
    )
