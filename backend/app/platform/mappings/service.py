"""Resolution and decisions over `external_entity_mappings` (ADR 014 §2.6, docs/IMPORTS.md §6).

Resolution order, the same for every adapter:
1. an existing mapping decides: `confirmed` → its entity, `excluded` → deliberately none, any other status → still
   unresolved (the fact is stored with its entity NULL and completed when someone decides);
2. an exact external id the caller vouches for (`created_from_master`, `exact_external_id`) or an explicit rule an
   admin enabled (`rule`) → created `confirmed`;
3. otherwise the caller's exact candidates: none or one → `pending` (one is a proposal, never confirmed alone),
   two or more → `ambiguous`;
4. a person decides (`confirm`, `reject`, `exclude`, `reassign`), always audited.

`resolve` only reads, for analysis; `observe` writes, at confirmation, and never changes the status or entity of a
mapping that exists. There is no similarity matching anywhere.
"""

import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.mappings.models import AUTOMATIC_METHODS, ExternalEntityMapping
from app.platform.mappings.registry import ENTITY_TYPES
from app.platform.sources.models import SourceSystem

REASON_LENGTH = 500


def label_key(label: str) -> str:
    """How a source that only brings a name is keyed (`customer_name`, `seller`): upper case, no accents, single
    spaces. «Almacén  Doña Rosa» and «ALMACEN DOÑA ROSA» are the same key; nothing more is assumed."""
    decomposed = unicodedata.normalize("NFKD", label.upper())
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(plain.split())


def code_key(code: str) -> str:
    """How a source code is keyed: trimmed, inner spaces collapsed, nothing else changed."""
    return " ".join(code.split())


@dataclass(frozen=True)
class MappingKey:
    entity_type: str
    source_system: str  # source_systems.code
    namespace: str
    external_id: str  # already keyed with label_key or code_key


@dataclass(frozen=True)
class Resolution:
    status: str  # "new" (no mapping yet) or the mapping's status
    entity_id: int | None = None
    mapping_id: int | None = None
    candidates: tuple[int, ...] = field(default_factory=tuple)

    @property
    def resolved(self) -> bool:
        return self.status == "confirmed"


# --- reading ----------------------------------------------------------------------------------------------------


def source_system_id(db: Session, code: str) -> int:
    found = db.scalar(select(SourceSystem.id).where(SourceSystem.code == code))
    if found is None:
        raise ValueError(f"Unknown source system: {code}")
    return found


def _check_key(key: MappingKey) -> None:
    if key.entity_type not in ENTITY_TYPES:
        raise ValueError(f"Unknown mapping entity type: {key.entity_type}")
    if not key.external_id.strip() or not key.namespace.strip():
        raise ValueError("A mapping needs a namespace and an external id")


def resolve_many(db: Session, keys: Iterable[MappingKey]) -> dict[MappingKey, Resolution]:
    """What each key resolves to today, without writing (analysis runs read-only)."""
    wanted = set(keys)
    for key in wanted:
        _check_key(key)
    if not wanted:
        return {}
    systems = {code: source_system_id(db, code) for code in {k.source_system for k in wanted}}
    by_id = {v: k for k, v in systems.items()}
    rows = db.scalars(
        select(ExternalEntityMapping).where(
            tuple_(
                ExternalEntityMapping.entity_type,
                ExternalEntityMapping.source_system_id,
                ExternalEntityMapping.namespace,
                ExternalEntityMapping.external_id,
            ).in_([(k.entity_type, systems[k.source_system], k.namespace, k.external_id) for k in wanted])
        )
    ).all()
    found = {
        MappingKey(r.entity_type, by_id[r.source_system_id], r.namespace, r.external_id): Resolution(
            r.status, r.entity_id, r.id, tuple(r.candidates)
        )
        for r in rows
    }
    return {key: found.get(key, Resolution("new")) for key in wanted}


def resolve(db: Session, key: MappingKey) -> Resolution:
    return resolve_many(db, [key])[key]


def get(db: Session, mapping_id: int) -> ExternalEntityMapping:
    mapping = db.get(ExternalEntityMapping, mapping_id)
    if mapping is None:
        raise AppError(404, "mapping_not_found", "Ese vínculo no existe.")
    return mapping


def mappings_of(db: Session, entity_type: str, entity_id: int) -> Sequence[ExternalEntityMapping]:
    return db.scalars(
        select(ExternalEntityMapping)
        .where(ExternalEntityMapping.entity_type == entity_type, ExternalEntityMapping.entity_id == entity_id)
        .order_by(ExternalEntityMapping.id)
    ).all()


# --- what an import does ----------------------------------------------------------------------------------------


def observe(
    db: Session,
    key: MappingKey,
    *,
    label: str | None,
    job_id: int | None,
    entity_id: int | None = None,
    method: str | None = None,
    candidates: Sequence[int] = (),
) -> ExternalEntityMapping:
    """Record that an import saw `key`, inside the confirmation's transaction.

    A new key is created `confirmed` only with an automatic method and its entity; otherwise `pending` (with at
    most one proposed candidate) or `ambiguous` (two or more). An existing mapping keeps its status, entity and
    candidates: only its label and last job are refreshed."""
    _check_key(key)
    if (entity_id is None) != (method is None):
        raise ValueError("An automatic confirmation needs both the entity and its method")
    if method is not None and method not in AUTOMATIC_METHODS:
        raise ValueError(f"An import cannot confirm a mapping by {method}")
    proposed = sorted(set(candidates))
    status = "confirmed" if entity_id is not None else ("ambiguous" if len(proposed) >= 2 else "pending")
    system_id = source_system_id(db, key.source_system)
    clean_label = " ".join(label.split())[:200] if label else None
    db.execute(
        insert(ExternalEntityMapping)
        .values(
            entity_type=key.entity_type,
            source_system_id=system_id,
            namespace=key.namespace,
            external_id=key.external_id,
            external_label=clean_label,
            entity_id=entity_id,
            status=status,
            method=method,
            candidates=[] if entity_id is not None else proposed,
            first_seen_job_id=job_id,
            last_seen_job_id=job_id,
        )
        .on_conflict_do_update(
            index_elements=["entity_type", "source_system_id", "namespace", "external_id"],
            set_={
                "external_label": func.coalesce(clean_label, ExternalEntityMapping.external_label),
                "last_seen_job_id": func.coalesce(job_id, ExternalEntityMapping.last_seen_job_id),
                "updated_at": func.now(),
            },
        )
    )
    mapping = db.scalars(
        select(ExternalEntityMapping).where(
            ExternalEntityMapping.entity_type == key.entity_type,
            ExternalEntityMapping.source_system_id == system_id,
            ExternalEntityMapping.namespace == key.namespace,
            ExternalEntityMapping.external_id == key.external_id,
        )
    ).one()
    db.refresh(mapping)
    return mapping


# --- what a person decides ---------------------------------------------------------------------------------------


def _locked(db: Session, mapping_id: int) -> ExternalEntityMapping:
    mapping = db.scalars(
        select(ExternalEntityMapping).where(ExternalEntityMapping.id == mapping_id).with_for_update()
    ).first()
    if mapping is None:
        raise AppError(404, "mapping_not_found", "Ese vínculo no existe.")
    return mapping


def _reason(reason: str | None, required: bool) -> str | None:
    clean = " ".join(reason.split()) if reason else None
    if required and not clean:
        raise AppError(422, "reason_required", "Indicá el motivo de la decisión.")
    if clean and len(clean) > REASON_LENGTH:
        raise AppError(422, "reason_too_long", f"El motivo tiene como máximo {REASON_LENGTH} caracteres.")
    return clean


def _snapshot(mapping: ExternalEntityMapping) -> dict[str, object]:
    return {"status": mapping.status, "entity_id": mapping.entity_id, "method": mapping.method}


def _decide(
    db: Session,
    actor: User,
    mapping: ExternalEntityMapping,
    action: str,
    *,
    status: str,
    entity_id: int | None,
    reason: str | None,
) -> ExternalEntityMapping:
    before = _snapshot(mapping)
    mapping.status = status
    mapping.entity_id = entity_id
    mapping.method = "manual" if entity_id is not None else None
    mapping.decided_by = actor.id
    mapping.decided_at = func.now()
    mapping.decision_reason = reason
    # Never the external id or label in the audit: a seller or customer name may be one.
    record_change(
        db,
        f"platform.mapping.{action}",
        action=action,
        user_id=actor.id,
        entity_type="external_entity_mapping",
        entity_id=mapping.id,
        before=before,
        after={
            **_snapshot(mapping),
            "entity_type": mapping.entity_type,
            "namespace": mapping.namespace,
            "reason": reason,
        },
    )
    db.commit()
    db.refresh(mapping)
    return mapping


def _check_entity(db: Session, mapping: ExternalEntityMapping, entity_id: int) -> None:
    if not ENTITY_TYPES[mapping.entity_type].linkable(db, entity_id):
        raise AppError(
            422, "invalid_mapping_entity", "Esa entidad no existe o está inactiva.", {"entity_id": entity_id}
        )


def confirm(
    db: Session, actor: User, mapping_id: int, entity_id: int, *, reason: str | None, is_admin: bool
) -> ExternalEntityMapping:
    """Link an unresolved mapping. An excluded one is linked only by an admin, with a reason; a confirmed one is
    changed with `reassign`."""
    mapping = _locked(db, mapping_id)
    if mapping.status == "confirmed":
        raise AppError(409, "mapping_already_confirmed", "Ese vínculo ya está confirmado: reasignalo.")
    excluded = mapping.status == "excluded"
    if excluded and not is_admin:
        raise AppError(403, "forbidden", "Solo un admin vincula un código excluido.")
    clean = _reason(reason, required=excluded)
    _check_entity(db, mapping, entity_id)
    return _decide(db, actor, mapping, "confirmed", status="confirmed", entity_id=entity_id, reason=clean)


def reassign(db: Session, actor: User, mapping_id: int, entity_id: int, *, reason: str | None) -> ExternalEntityMapping:
    """Point a confirmed mapping to another entity: admin only (the caller checks), always with a reason."""
    mapping = _locked(db, mapping_id)
    if mapping.status != "confirmed":
        raise AppError(409, "mapping_not_confirmed", "Solo se reasigna un vínculo confirmado.")
    if mapping.entity_id == entity_id:
        raise AppError(409, "mapping_same_entity", "El vínculo ya apunta a esa entidad.")
    clean = _reason(reason, required=True)
    _check_entity(db, mapping, entity_id)
    return _decide(db, actor, mapping, "reassigned", status="confirmed", entity_id=entity_id, reason=clean)


def reject(db: Session, actor: User, mapping_id: int, *, reason: str | None) -> ExternalEntityMapping:
    """Turn down the proposal of a pending or ambiguous mapping; it stays unlinked and can be confirmed later."""
    mapping = _locked(db, mapping_id)
    if mapping.status not in ("pending", "ambiguous"):
        raise AppError(409, "mapping_not_open", "Solo se rechaza un vínculo pendiente o ambiguo.")
    return _decide(db, actor, mapping, "rejected", status="rejected", entity_id=None, reason=_reason(reason, False))


def exclude(db: Session, actor: User, mapping_id: int, *, reason: str | None, is_admin: bool) -> ExternalEntityMapping:
    """Mark a code as never linked (a pseudo-seller), with a reason. Unlinking a confirmed one is for an admin."""
    mapping = _locked(db, mapping_id)
    if mapping.status == "excluded":
        raise AppError(409, "mapping_already_excluded", "Ese vínculo ya está excluido.")
    if mapping.status == "confirmed" and not is_admin:
        raise AppError(403, "forbidden", "Solo un admin excluye un vínculo confirmado.")
    clean = _reason(reason, required=True)
    return _decide(db, actor, mapping, "excluded", status="excluded", entity_id=None, reason=clean)


def search(
    db: Session,
    *,
    entity_type: str | None,
    source_system: str | None,
    namespace: str | None,
    statuses: Sequence[str],
    limit: int,
    offset: int,
) -> tuple[Sequence[ExternalEntityMapping], int]:
    """The mappings to review, oldest first (what waits longest goes first)."""
    query = select(ExternalEntityMapping)
    if entity_type:
        query = query.where(ExternalEntityMapping.entity_type == entity_type)
    if source_system:
        query = query.where(ExternalEntityMapping.source_system_id == source_system_id(db, source_system))
    if namespace:
        query = query.where(ExternalEntityMapping.namespace == namespace)
    if statuses:
        query = query.where(ExternalEntityMapping.status.in_(statuses))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(ExternalEntityMapping.id).offset(offset).limit(limit)).all()
    return rows, total


def source_codes(db: Session) -> dict[int, str]:
    return {row.id: row.code for row in db.execute(select(SourceSystem.id, SourceSystem.code))}
