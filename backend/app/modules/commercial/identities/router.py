"""Customers and mappings under /api/v1/commercial (ADR 014 §2.6, docs/IMPORTS.md §6).

`viewer` reads. `operator` confirms, rejects or excludes an open mapping. `admin` links an excluded code, unlinks
(excludes) a confirmed one, reassigns, and sets a customer's flags. There is no endpoint that creates a customer or a
mapping: only imports do (F1.3).
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.commercial.identities.schemas import (
    CustomerContactOut,
    CustomerFlagsIn,
    CustomerOut,
    CustomerPage,
    MappingLinkIn,
    MappingOut,
    MappingPage,
    MappingReasonIn,
)
from app.platform.auth.access import has_module_access, require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User
from app.platform.customers import service as customers
from app.platform.customers.models import Customer
from app.platform.mappings import service as mappings
from app.platform.mappings.models import STATUSES, ExternalEntityMapping

router = APIRouter()

Viewer = Annotated[User, Depends(require_module_access("commercial", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("commercial", "operator"))]
Admin = Annotated[User, Depends(require_module_access("commercial", "admin"))]


def _customer(customer: Customer, user: User) -> CustomerContactOut | CustomerOut:
    """The one projection of a customer: CUIT, address and phone only for an operator or admin of the module. A
    viewer's response does not carry those keys at all (not masked, not null)."""
    if has_module_access(user, "commercial", "operator"):
        return CustomerContactOut.model_validate(customer)
    return CustomerOut.model_validate(customer)


@router.get("/customers", response_model=CustomerPage, summary="Clientes")
def list_customers(
    db: DbSession,
    user: Viewer,
    q: Annotated[str | None, Query(max_length=100)] = None,
    include_inactive: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CustomerPage:
    rows, total = customers.search(db, q=q, include_inactive=include_inactive, limit=limit, offset=offset)
    return CustomerPage(items=[_customer(r, user) for r in rows], total=total)


@router.get(
    "/customers/{customer_id}",
    response_model=CustomerContactOut | CustomerOut,
    summary="Cliente (CUIT, dirección y teléfono solo para operator o admin)",
)
def get_customer(db: DbSession, user: Viewer, customer_id: int) -> CustomerContactOut | CustomerOut:
    return _customer(customers.get(db, customer_id), user)


@router.patch(
    "/customers/{customer_id}", response_model=CustomerContactOut, summary="Marcar cliente genérico o inactivo"
)
def update_customer(db: DbSession, user: Admin, customer_id: int, body: CustomerFlagsIn) -> CustomerContactOut:
    customer = customers.set_flags(
        db, user, customer_id, is_generic=body.is_generic, active=body.active, reason=body.reason
    )
    return CustomerContactOut.model_validate(customer)


def _mapping(row: ExternalEntityMapping, codes: dict[int, str]) -> MappingOut:
    return MappingOut(
        id=row.id,
        entity_type=row.entity_type,
        source_system=codes[row.source_system_id],
        namespace=row.namespace,
        external_id=row.external_id,
        external_label=row.external_label,
        entity_id=row.entity_id,
        status=row.status,
        method=row.method,
        candidates=list(row.candidates),
        decided_by=row.decided_by,
        decided_at=row.decided_at,
        decision_reason=row.decision_reason,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/mappings", response_model=MappingPage, summary="Vínculos con códigos externos")
def list_mappings(
    db: DbSession,
    _: Viewer,
    entity_type: Annotated[str | None, Query(max_length=40)] = None,
    source_system: Annotated[str | None, Query(max_length=40)] = None,
    namespace: Annotated[str | None, Query(max_length=40)] = None,
    status: Annotated[list[str] | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> MappingPage:
    statuses = [s for s in (status or []) if s in STATUSES]
    codes = mappings.source_codes(db)
    if source_system and source_system not in codes.values():
        return MappingPage(items=[], total=0)
    rows, total = mappings.search(
        db,
        entity_type=entity_type,
        source_system=source_system,
        namespace=namespace,
        statuses=statuses,
        limit=limit,
        offset=offset,
    )
    return MappingPage(items=[_mapping(r, codes) for r in rows], total=total)


@router.get("/mappings/{mapping_id}", response_model=MappingOut, summary="Vínculo")
def get_mapping(db: DbSession, _: Viewer, mapping_id: int) -> MappingOut:
    return _mapping(mappings.get(db, mapping_id), mappings.source_codes(db))


@router.post("/mappings/{mapping_id}/confirm", response_model=MappingOut, summary="Confirmar vínculo")
def confirm_mapping(db: DbSession, user: Operator, mapping_id: int, body: MappingLinkIn) -> MappingOut:
    is_admin = has_module_access(user, "commercial", "admin")
    row = mappings.confirm(db, user, mapping_id, body.entity_id, reason=body.reason, is_admin=is_admin)
    return _mapping(row, mappings.source_codes(db))


@router.post("/mappings/{mapping_id}/reassign", response_model=MappingOut, summary="Reasignar vínculo confirmado")
def reassign_mapping(db: DbSession, user: Admin, mapping_id: int, body: MappingLinkIn) -> MappingOut:
    return _mapping(
        mappings.reassign(db, user, mapping_id, body.entity_id, reason=body.reason), mappings.source_codes(db)
    )


@router.post("/mappings/{mapping_id}/reject", response_model=MappingOut, summary="Rechazar propuesta")
def reject_mapping(db: DbSession, user: Operator, mapping_id: int, body: MappingReasonIn) -> MappingOut:
    return _mapping(mappings.reject(db, user, mapping_id, reason=body.reason), mappings.source_codes(db))


@router.post("/mappings/{mapping_id}/exclude", response_model=MappingOut, summary="Excluir código")
def exclude_mapping(db: DbSession, user: Operator, mapping_id: int, body: MappingReasonIn) -> MappingOut:
    is_admin = has_module_access(user, "commercial", "admin")
    row = mappings.exclude(db, user, mapping_id, reason=body.reason, is_admin=is_admin)
    return _mapping(row, mappings.source_codes(db))
