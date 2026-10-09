"""Third parties of the platform (R6-1). Every signed-in user reads them, since forms of any module name a
third party; only a platform admin creates, corrects or (de)activates one here. A module may offer its
own quick creation (Rendiciones, during a payment). There is no DELETE."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.audit.models import AuditEvent
from app.platform.auth.deps import CurrentUser, DbSession, require_role
from app.platform.auth.models import User
from app.platform.third_parties import service, tax_ids
from app.platform.third_parties.models import ThirdParty
from app.platform.third_parties.schemas import (
    ThirdPartyDetail,
    ThirdPartyEventOut,
    ThirdPartyIn,
    ThirdPartyOut,
    ThirdPartyPatch,
)

router = APIRouter(prefix="/third-parties", tags=["third parties"])

PlatformAdmin = Annotated[User, Depends(require_role("admin"))]


def names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return {row.id: row.full_name for row in rows}


def out(row: ThirdParty, users: dict[int, str]) -> ThirdPartyOut:
    return ThirdPartyOut(
        id=row.id,
        name=row.name,
        tax_id=row.tax_id,
        tax_id_display=tax_ids.display(row.tax_id) if row.tax_id else None,
        notes=row.notes,
        active=row.active,
        created_at=row.created_at,
        created_by_name=users.get(row.created_by, "—"),
        updated_at=row.updated_at,
        updated_by_name=users.get(row.updated_by, "—"),
    )


def one(db: Session, row: ThirdParty) -> ThirdPartyOut:
    db.refresh(row)
    return out(row, names(db, {row.created_by, row.updated_by}))


def _event(row: AuditEvent, users: dict[int, str]) -> ThirdPartyEventOut:
    payload = row.payload or {}
    return ThirdPartyEventOut(
        id=row.id,
        event_type=row.event_type.removeprefix("platform.third_party."),
        occurred_at=row.occurred_at,
        user_name=users.get(row.user_id) if row.user_id is not None else None,
        before=payload.get("before"),
        after=payload.get("after"),
    )


@router.get("", response_model=list[ThirdPartyOut], summary="Terceros")
def list_third_parties(
    db: DbSession,
    _: CurrentUser,
    include_inactive: bool = False,
    q: Annotated[str | None, Query(max_length=200, description="Nombre o parte del CUIT")] = None,
) -> list[ThirdPartyOut]:
    rows = service.list_all(db, include_inactive=include_inactive, q=q)
    users = names(db, {r.created_by for r in rows} | {r.updated_by for r in rows})
    return [out(row, users) for row in rows]


@router.get("/{third_party_id}", response_model=ThirdPartyDetail, summary="Tercero e historia de cambios")
def get_third_party(third_party_id: int, db: DbSession, _: CurrentUser) -> ThirdPartyDetail:
    row = service.get(db, third_party_id)
    events = service.history(db, third_party_id)
    users = names(db, {row.created_by, row.updated_by} | {e.user_id for e in events if e.user_id is not None})
    return ThirdPartyDetail(**out(row, users).model_dump(), history=[_event(e, users) for e in events])


@router.post("", response_model=ThirdPartyOut, status_code=201, summary="Registrar un tercero")
def create_third_party(body: ThirdPartyIn, db: DbSession, user: PlatformAdmin) -> ThirdPartyOut:
    return one(db, service.create(db, user, body))


@router.patch("/{third_party_id}", response_model=ThirdPartyOut, summary="Corregir, activar o desactivar")
def update_third_party(third_party_id: int, body: ThirdPartyPatch, db: DbSession, user: PlatformAdmin) -> ThirdPartyOut:
    return one(db, service.update(db, user, third_party_id, body))
