"""Catalog of financial institutions (BCRA codes). Every signed-in user reads it, since forms of any
module name a bank; only a platform admin adds a missing entity or (de)activates one."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.platform.auth.deps import CurrentUser, DbSession, require_role
from app.platform.auth.models import User
from app.platform.banks import service
from app.platform.banks.models import FinancialInstitution
from app.platform.banks.schemas import ActiveIn, FinancialInstitutionIn, FinancialInstitutionOut

router = APIRouter(prefix="/financial-institutions", tags=["financial institutions"])

PlatformAdmin = Annotated[User, Depends(require_role("admin"))]


def _out(row: FinancialInstitution) -> FinancialInstitutionOut:
    return FinancialInstitutionOut(
        id=row.id,
        bcra_code=row.bcra_code,
        name=row.name,
        source=row.source,
        active=row.active,
    )


@router.get("", response_model=list[FinancialInstitutionOut], summary="Entidades bancarias (códigos BCRA)")
def list_institutions(db: DbSession, _: CurrentUser, include_inactive: bool = False) -> list[FinancialInstitutionOut]:
    return [_out(row) for row in service.list_all(db, include_inactive=include_inactive)]


@router.post("", response_model=FinancialInstitutionOut, status_code=201, summary="Agregar una entidad faltante")
def add_institution(body: FinancialInstitutionIn, db: DbSession, user: PlatformAdmin) -> FinancialInstitutionOut:
    return _out(service.add_manual(db, user, body.bcra_code, body.name, body.reason))


@router.patch("/{institution_id}", response_model=FinancialInstitutionOut, summary="Activar o desactivar")
def set_institution_active(
    institution_id: int, body: ActiveIn, db: DbSession, user: PlatformAdmin
) -> FinancialInstitutionOut:
    return _out(service.set_active(db, user, institution_id, body.active))
