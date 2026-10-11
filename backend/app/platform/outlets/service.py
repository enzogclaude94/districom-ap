"""Bocas de expendio: reading for every signed-in user, administration for platform admins (R7-1).

Every change is audited with who, before and after (`platform.outlet.*`): created, name_changed,
description_changed, deactivated, reactivated. The boca's history is those events. The code is never in a
change: it is fixed at creation. Nothing is deleted: the database refuses it (migration 0021).
"""

import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.models import AuditEvent
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.outlets.models import CODE_PATTERN, Outlet
from app.platform.outlets.schemas import OutletIn, OutletPatch
from app.platform.third_parties.models import name_key

ENTITY = "outlet"
_CODE = re.compile(CODE_PATTERN)


def normalize_code(raw: str) -> str:
    """Upper case, without the spaces around it. Anything else that breaks the format is refused, never fixed."""
    code = raw.strip().upper()
    if not _CODE.fullmatch(code):
        raise AppError(
            422,
            "invalid_outlet_code",
            "El código tiene que tener entre 2 y 20 caracteres: letras, números o guiones.",
            {"code": raw},
        )
    return code


def list_all(db: Session, *, include_inactive: bool = False) -> list[Outlet]:
    query = select(Outlet).order_by(Outlet.code, Outlet.id)
    if not include_inactive:
        query = query.where(Outlet.active.is_(True))
    return list(db.scalars(query))


def get(db: Session, outlet_id: int) -> Outlet:
    row = db.get(Outlet, outlet_id)
    if row is None:
        raise AppError(404, "outlet_not_found", "La boca de expendio no existe.")
    return row


def active(db: Session, outlet_id: int) -> Outlet:
    """A boca a new record may name; 422 when unknown or deactivated. Records already open keep theirs."""
    row = db.get(Outlet, outlet_id)
    if row is None or not row.active:
        raise AppError(
            422, "inactive_outlet", "La boca de expendio tiene que ser una activa.", {"outlet_id": outlet_id}
        )
    return row


def history(db: Session, outlet_id: int) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == ENTITY, AuditEvent.entity_id == str(outlet_id))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )


def create(db: Session, actor: User, body: OutletIn) -> Outlet:
    code = normalize_code(body.code)
    name = _name(body.name)
    description = _description(body.description)
    _require_free(db, code, name, None)
    row = Outlet(
        code=code,
        name=name,
        name_key=name_key(name),
        description=description,
        created_by=actor.id,
        updated_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:  # another admin registered the same code or name at the same time
        raise _conflict(db, code, name, None) from exc
    _audit(db, actor, row, "created", None, {"code": code, "name": name, "description": description, "active": True})
    db.commit()
    return row


def update(db: Session, actor: User, outlet_id: int, body: OutletPatch) -> Outlet:
    row = db.scalars(select(Outlet).where(Outlet.id == outlet_id).with_for_update()).first()
    if row is None:
        raise AppError(404, "outlet_not_found", "La boca de expendio no existe.")
    changes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    if body.name is not None:
        name = _name(body.name)
        if name != row.name:
            if name_key(name) != row.name_key:
                _require_free(db, None, name, row.id)
            changes.append(("name_changed", {"name": row.name}, {"name": name}))
            row.name, row.name_key = name, name_key(name)
    if body.description is not None:
        description = _description(body.description)
        if description != row.description:
            changes.append(("description_changed", {"description": row.description}, {"description": description}))
            row.description = description
    if body.active is not None and body.active != row.active:
        changes.append(
            ("reactivated" if body.active else "deactivated", {"active": row.active}, {"active": body.active})
        )
        row.active = body.active
    if changes:
        row.updated_by = actor.id
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError as exc:  # the new name was taken meanwhile
            raise _conflict(db, None, row.name, row.id) from exc
        for kind, before, after in changes:
            _audit(db, actor, row, kind, before, after)
    db.commit()
    return row


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name_key(name):
        raise AppError(422, "outlet_name_required", "Indicá el nombre de la boca de expendio.")
    return name


def _description(raw: str | None) -> str | None:
    if raw is None:
        return None
    return " ".join(raw.split()) or None


def _require_free(db: Session, code: str | None, name: str, own_id: int | None) -> None:
    if code is not None:
        other = db.scalars(select(Outlet).where(Outlet.code == code)).first()
        if other is not None:
            raise _code_taken(other)
    other = db.scalars(select(Outlet).where(Outlet.name_key == name_key(name))).first()
    if other is not None and other.id != own_id:
        raise _name_taken(other)


def _conflict(db: Session, code: str | None, name: str, own_id: int | None) -> AppError:
    db.expire_all()
    try:
        _require_free(db, code, name, own_id)
    except AppError as taken:
        return taken
    return AppError(409, "outlet_conflict", "No se pudo guardar la boca de expendio. Volvé a intentar.")


def _code_taken(other: Outlet) -> AppError:
    return AppError(
        409,
        "outlet_code_taken",
        f"El código {other.code} ya es de otra boca ({other.name}).",
        {"outlet_id": other.id, "active": other.active},
    )


def _name_taken(other: Outlet) -> AppError:
    return AppError(
        409,
        "outlet_name_taken",
        f"Ya hay una boca con ese nombre: {other.code} — {other.name}.",
        {"outlet_id": other.id, "active": other.active},
    )


def _audit(
    db: Session, actor: User, row: Outlet, kind: str, before: dict[str, Any] | None, after: dict[str, Any]
) -> None:
    record_change(
        db,
        f"platform.outlet.{kind}",
        action="create" if kind == "created" else "update",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=row.id,
        before=before,
        after=after,
    )
