"""Customers and mappings under /api/v1/commercial (ADR 014 §2.6, docs/IMPORTS.md §6).

`viewer` reads. `operator` confirms, rejects or excludes an open mapping, alone or several confirmations at once
(all or nothing). `admin` links an excluded code, unlinks (excludes) a confirmed one, reassigns, and sets a customer's
flags. There is no endpoint that creates a customer, an employee or a mapping: only imports create mappings (F1.3).
Every decision re-resolves the facts that took their identity from the mapping, in the same transaction, committed
here at the boundary (F1.5, docs/IMPORTS.md §6.4).
"""

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.commercial.identities import targets
from app.modules.commercial.identities.schemas import (
    BatchConfirmIn,
    BatchConfirmOut,
    BatchItemOut,
    CustomerContactOut,
    CustomerFlagsIn,
    CustomerOut,
    CustomerPage,
    FactCountOut,
    LinkTargetOut,
    LinkTargetPage,
    MappingImpactFactOut,
    MappingImpactOut,
    MappingLinkIn,
    MappingOut,
    MappingPage,
    MappingReasonIn,
    MappingSummaryOut,
    ResolutionOut,
)
from app.platform.auth.access import has_module_access, require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User
from app.platform.customers import service as customers
from app.platform.customers.models import Customer
from app.platform.imports.models import ImportJob
from app.platform.mappings import resolvers
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


def _targets_out(found: dict[int, targets.Target], ids: list[int]) -> list[LinkTargetOut]:
    return [LinkTargetOut.model_validate(found[i].__dict__) for i in ids if i in found]


def _mappings(
    db: Session, rows: Sequence[ExternalEntityMapping], user: User, *, detail: bool = False
) -> list[MappingOut]:
    """The queue's projection: business label, first and last import, facts, linked entity and candidates."""
    codes = mappings.source_codes(db)
    keys = {r.id: (r.entity_type, codes[r.source_system_id], r.namespace) for r in rows}
    by_key: dict[resolvers.KindKey, list[int]] = {}
    for mapping_id, key in keys.items():
        by_key.setdefault(key, []).append(mapping_id)
    facts = resolvers.totals(db, by_key)
    job_ids = {j for r in rows for j in (r.first_seen_job_id, r.last_seen_job_id) if j is not None}
    jobs = (
        dict(db.execute(select(ImportJob.id, ImportJob.created_at).where(ImportJob.id.in_(job_ids))).all())
        if job_ids
        else {}
    )
    decider_ids = {r.decided_by for r in rows if r.decided_by is not None}
    deciders = (
        dict(db.execute(select(User.id, User.full_name).where(User.id.in_(decider_ids))).all()) if decider_ids else {}
    )
    full = has_module_access(user, "commercial", "operator")
    entities: dict[str, dict[int, targets.Target]] = {}
    for entity_type in {r.entity_type for r in rows}:
        ids = [
            i
            for r in rows
            if r.entity_type == entity_type
            for i in ([r.entity_id] if r.entity_id else []) + (list(r.candidates) if detail else [])
        ]
        entities[entity_type] = targets.describe(db, entity_type, ids, full=full)
    result = []
    for r in rows:
        found = entities.get(r.entity_type, {})
        result.append(
            MappingOut(
                id=r.id,
                entity_type=r.entity_type,
                source_system=codes[r.source_system_id],
                namespace=r.namespace,
                external_id=r.external_id,
                external_label=r.external_label,
                entity_id=r.entity_id,
                status=r.status,
                method=r.method,
                candidates=list(r.candidates),
                decided_by=r.decided_by,
                decided_at=r.decided_at,
                decision_reason=r.decision_reason,
                created_at=r.created_at,
                updated_at=r.updated_at,
                kind_label=resolvers.registry.label(keys[r.id]),
                first_seen_job_id=r.first_seen_job_id,
                last_seen_job_id=r.last_seen_job_id,
                first_seen_at=jobs.get(r.first_seen_job_id) if r.first_seen_job_id else None,
                last_seen_at=jobs.get(r.last_seen_job_id) if r.last_seen_job_id else None,
                decided_by_name=deciders.get(r.decided_by) if r.decided_by else None,
                entity=next(iter(_targets_out(found, [r.entity_id])), None) if r.entity_id else None,
                candidate_entities=_targets_out(found, list(r.candidates)) if detail else [],
                facts=[
                    FactCountOut(kind=f.kind, label=f.label, total=f.total, linked=f.linked)
                    for f in facts.get(r.id, [])
                ],
            )
        )
    return result


def _decided(db: Session, decision: mappings.Decision, user: User) -> MappingOut:
    """Commit at the endpoint boundary (the service never commits) and answer with what changed."""
    db.commit()
    out = _mappings(db, [decision.mapping], user, detail=True)[0]
    out.resolution = ResolutionOut(
        facts_updated=decision.resolution.updated, issues_closed=decision.resolution.issues_closed
    )
    return out


Filters = tuple[str | None, str | None, str | None, str | None]


@router.get("/mappings", response_model=MappingPage, summary="Vínculos con códigos externos")
def list_mappings(
    db: DbSession,
    user: Viewer,
    entity_type: Annotated[str | None, Query(max_length=40)] = None,
    source_system: Annotated[str | None, Query(max_length=40)] = None,
    namespace: Annotated[str | None, Query(max_length=40)] = None,
    status: Annotated[list[str] | None, Query()] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
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
        q=q,
    )
    return MappingPage(items=_mappings(db, rows, user), total=total)


@router.get("/mappings/summary", response_model=MappingSummaryOut, summary="Vínculos por estado")
def mappings_summary(
    db: DbSession,
    _: Viewer,
    entity_type: Annotated[str | None, Query(max_length=40)] = None,
    source_system: Annotated[str | None, Query(max_length=40)] = None,
    namespace: Annotated[str | None, Query(max_length=40)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
) -> MappingSummaryOut:
    if source_system and source_system not in mappings.source_codes(db).values():
        return MappingSummaryOut(counts=dict.fromkeys(STATUSES, 0))
    counts = mappings.summary(db, entity_type=entity_type, source_system=source_system, namespace=namespace, q=q)
    return MappingSummaryOut(counts=counts)


@router.post("/mappings/batch-confirm", response_model=BatchConfirmOut, summary="Confirmar varios vínculos")
def batch_confirm(db: DbSession, user: Operator, body: BatchConfirmIn) -> BatchConfirmOut:
    """All or nothing: each item names the entity a person chose; one failure confirms none (the error names it)."""
    decisions = mappings.confirm_many(
        db, user, [mappings.BatchItem(item.mapping_id, item.entity_id) for item in body.items]
    )
    db.commit()
    totals: dict[str, int] = {}
    for decision in decisions:
        for kind, written in decision.resolution.updated.items():
            totals[kind] = totals.get(kind, 0) + written
    return BatchConfirmOut(
        confirmed=len(decisions),
        failed=0,
        items=[
            BatchItemOut(
                mapping_id=d.mapping.id, entity_id=d.mapping.entity_id or 0, facts_updated=d.resolution.updated
            )
            for d in decisions
        ],
        facts_updated=totals,
        issues_closed=sum(d.resolution.issues_closed for d in decisions),
    )


@router.get("/mappings/{mapping_id}", response_model=MappingOut, summary="Vínculo")
def get_mapping(db: DbSession, user: Viewer, mapping_id: int) -> MappingOut:
    return _mappings(db, [mappings.get(db, mapping_id)], user, detail=True)[0]


@router.get("/mappings/{mapping_id}/impact", response_model=MappingImpactOut, summary="Qué cambiaría una decisión")
def mapping_impact(
    db: DbSession, _: Viewer, mapping_id: int, entity_id: Annotated[int | None, Query(gt=0)] = None
) -> MappingImpactOut:
    mapping = mappings.get(db, mapping_id)
    counts = resolvers.count(db, mappings.kind_key(db, mapping), mapping.id, entity_id)
    facts = [
        MappingImpactFactOut(
            kind=c.kind,
            label=c.label,
            total=c.total,
            linked=c.linked,
            on_target=c.on_target,
            changes_on_link=c.changes_on_link(),
            changes_on_unlink=c.changes_on_unlink(),
        )
        for c in counts
    ]
    return MappingImpactOut(
        mapping_id=mapping.id,
        status=mapping.status,
        entity_id=entity_id,
        facts=facts,
        on_link=sum(f.changes_on_link for f in facts),
        on_unlink=sum(f.changes_on_unlink for f in facts),
    )


@router.get(
    "/mappings/{mapping_id}/targets",
    response_model=LinkTargetPage,
    summary="Clientes o empleados con los que se puede vincular",
)
def mapping_targets(
    db: DbSession,
    _: Operator,
    mapping_id: int,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> LinkTargetPage:
    """Only for whoever can decide. Never creates an entity; inactive ones are not offered."""
    mapping = mappings.get(db, mapping_id)
    found = targets.search(db, mapping.entity_type, q, limit=limit)
    return LinkTargetPage(items=[LinkTargetOut.model_validate(t.__dict__) for t in found])


@router.post("/mappings/{mapping_id}/confirm", response_model=MappingOut, summary="Confirmar vínculo")
def confirm_mapping(db: DbSession, user: Operator, mapping_id: int, body: MappingLinkIn) -> MappingOut:
    is_admin = has_module_access(user, "commercial", "admin")
    decision = mappings.confirm(
        db,
        user,
        mapping_id,
        body.entity_id,
        reason=body.reason,
        is_admin=is_admin,
        expected_status=body.expected_status,
    )
    return _decided(db, decision, user)


@router.post("/mappings/{mapping_id}/reassign", response_model=MappingOut, summary="Reasignar vínculo confirmado")
def reassign_mapping(db: DbSession, user: Admin, mapping_id: int, body: MappingLinkIn) -> MappingOut:
    decision = mappings.reassign(
        db, user, mapping_id, body.entity_id, reason=body.reason, expected_status=body.expected_status
    )
    return _decided(db, decision, user)


@router.post("/mappings/{mapping_id}/reject", response_model=MappingOut, summary="Rechazar propuesta")
def reject_mapping(db: DbSession, user: Operator, mapping_id: int, body: MappingReasonIn) -> MappingOut:
    decision = mappings.reject(db, user, mapping_id, reason=body.reason, expected_status=body.expected_status)
    return _decided(db, decision, user)


@router.post("/mappings/{mapping_id}/exclude", response_model=MappingOut, summary="Excluir código")
def exclude_mapping(db: DbSession, user: Operator, mapping_id: int, body: MappingReasonIn) -> MappingOut:
    is_admin = has_module_access(user, "commercial", "admin")
    decision = mappings.exclude(
        db, user, mapping_id, reason=body.reason, is_admin=is_admin, expected_status=body.expected_status
    )
    return _decided(db, decision, user)
