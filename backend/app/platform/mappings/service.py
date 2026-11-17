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

from sqlalchemy import Select, case, func, or_, select, tuple_
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.service import record_change, record_event
from app.platform.auth.models import User
from app.platform.mappings import resolvers
from app.platform.mappings.models import AUTOMATIC_METHODS, STATUSES, ExternalEntityMapping
from app.platform.mappings.registry import ENTITY_TYPES
from app.platform.mappings.resolvers import ReResolution
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


@dataclass(frozen=True)
class Decision:
    mapping: ExternalEntityMapping
    resolution: ReResolution


def kind_key(db: Session, mapping: ExternalEntityMapping) -> resolvers.KindKey:
    code = db.scalar(select(SourceSystem.code).where(SourceSystem.id == mapping.source_system_id))
    assert code is not None
    return (mapping.entity_type, code, mapping.namespace)


def _expect(mapping: ExternalEntityMapping, expected_status: str | None) -> None:
    """The caller saw the mapping in `expected_status`; if someone decided it since, nothing is overwritten."""
    if expected_status is not None and mapping.status != expected_status:
        raise AppError(
            409,
            "mapping_changed",
            "Alguien decidió este vínculo mientras lo revisabas. Revisá el estado actual.",
            {"mapping_id": mapping.id, "status": mapping.status},
        )


def _decide(
    db: Session,
    actor: User,
    mapping: ExternalEntityMapping,
    action: str,
    *,
    status: str,
    entity_id: int | None,
    reason: str | None,
) -> Decision:
    """Apply a decision to a locked mapping and re-resolve its facts, in the caller's transaction (no commit: the
    endpoint commits, so a batch is applied whole or not at all)."""
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
    db.flush()
    key = kind_key(db, mapping)
    resolution = resolvers.apply(db, key, mapping, actor.id)
    if resolvers.registry.specs(key):
        # Ids and counts only: what changed because of the decision, never a code, label or name.
        record_event(
            db,
            "platform.mapping.facts_resolved",
            user_id=actor.id,
            entity_type="external_entity_mapping",
            entity_id=str(mapping.id),
            payload={
                "mapping_id": mapping.id,
                "action": action,
                "entity_type": mapping.entity_type,
                "namespace": mapping.namespace,
                "source_system": key[1],
                "entity_id": mapping.entity_id,
                "facts_updated": resolution.updated,
                "issues_closed": resolution.issues_closed,
            },
        )
    db.flush()
    db.refresh(mapping)
    return Decision(mapping, resolution)


def _check_entity(db: Session, mapping: ExternalEntityMapping, entity_id: int) -> None:
    if not ENTITY_TYPES[mapping.entity_type].linkable(db, entity_id):
        raise AppError(
            422, "invalid_mapping_entity", "Esa entidad no existe o está inactiva.", {"entity_id": entity_id}
        )


def confirm(
    db: Session,
    actor: User,
    mapping_id: int,
    entity_id: int,
    *,
    reason: str | None,
    is_admin: bool,
    expected_status: str | None = None,
) -> Decision:
    """Link an unresolved mapping. An excluded one is linked only by an admin, with a reason; a confirmed one is
    changed with `reassign`."""
    mapping = _locked(db, mapping_id)
    _expect(mapping, expected_status)
    if mapping.status == "confirmed":
        raise AppError(409, "mapping_already_confirmed", "Ese vínculo ya está confirmado: reasignalo.")
    excluded = mapping.status == "excluded"
    if excluded and not is_admin:
        raise AppError(403, "forbidden", "Solo un admin vincula un código excluido.")
    clean = _reason(reason, required=excluded)
    _check_entity(db, mapping, entity_id)
    return _decide(db, actor, mapping, "confirmed", status="confirmed", entity_id=entity_id, reason=clean)


def reassign(
    db: Session,
    actor: User,
    mapping_id: int,
    entity_id: int,
    *,
    reason: str | None,
    expected_status: str | None = None,
) -> Decision:
    """Point a confirmed mapping to another entity: admin only (the caller checks), always with a reason."""
    mapping = _locked(db, mapping_id)
    _expect(mapping, expected_status)
    if mapping.status != "confirmed":
        raise AppError(409, "mapping_not_confirmed", "Solo se reasigna un vínculo confirmado.")
    if mapping.entity_id == entity_id:
        raise AppError(409, "mapping_same_entity", "El vínculo ya apunta a esa entidad.")
    clean = _reason(reason, required=True)
    _check_entity(db, mapping, entity_id)
    return _decide(db, actor, mapping, "reassigned", status="confirmed", entity_id=entity_id, reason=clean)


def reject(
    db: Session, actor: User, mapping_id: int, *, reason: str | None, expected_status: str | None = None
) -> Decision:
    """Turn down the proposal of a pending or ambiguous mapping; it stays unlinked and can be confirmed later."""
    mapping = _locked(db, mapping_id)
    _expect(mapping, expected_status)
    if mapping.status not in ("pending", "ambiguous"):
        raise AppError(409, "mapping_not_open", "Solo se rechaza un vínculo pendiente o ambiguo.")
    return _decide(db, actor, mapping, "rejected", status="rejected", entity_id=None, reason=_reason(reason, False))


def exclude(
    db: Session,
    actor: User,
    mapping_id: int,
    *,
    reason: str | None,
    is_admin: bool,
    expected_status: str | None = None,
) -> Decision:
    """Mark a code as never linked (a pseudo-seller), with a reason. Unlinking a confirmed one is for an admin."""
    mapping = _locked(db, mapping_id)
    _expect(mapping, expected_status)
    if mapping.status == "excluded":
        raise AppError(409, "mapping_already_excluded", "Ese vínculo ya está excluido.")
    if mapping.status == "confirmed" and not is_admin:
        raise AppError(403, "forbidden", "Solo un admin excluye un vínculo confirmado.")
    clean = _reason(reason, required=True)
    return _decide(db, actor, mapping, "excluded", status="excluded", entity_id=None, reason=clean)


BATCH_LIMIT = 200
BATCHABLE = ("pending", "ambiguous", "rejected")


@dataclass(frozen=True)
class BatchItem:
    mapping_id: int
    entity_id: int


def confirm_many(db: Session, actor: User, items: Sequence[BatchItem]) -> list[Decision]:
    """Confirm several open mappings, each to the entity a person chose for it, all or nothing.

    Rows are locked in id order (two batches over the same mappings never deadlock). The first item that cannot be
    confirmed raises with its mapping id and the caller's transaction is rolled back: nothing is applied. An excluded
    mapping is never part of a batch (relinking it needs an admin's reason, one at a time)."""
    if not items:
        raise AppError(422, "batch_empty", "Elegí al menos un vínculo.")
    if len(items) > BATCH_LIMIT:
        raise AppError(422, "batch_too_large", f"Se confirman como máximo {BATCH_LIMIT} vínculos por vez.")
    ids = [item.mapping_id for item in items]
    if len(set(ids)) != len(ids):
        raise AppError(422, "batch_duplicate_mapping", "Un vínculo aparece dos veces en la selección.")
    decisions = []
    for item in sorted(items, key=lambda i: i.mapping_id):
        try:
            mapping = _locked(db, item.mapping_id)
            if mapping.status not in BATCHABLE:
                raise AppError(
                    409,
                    "mapping_not_open",
                    "Ese vínculo ya no está abierto: alguien lo decidió.",
                    {"status": mapping.status},
                )
            _check_entity(db, mapping, item.entity_id)
            decisions.append(
                _decide(db, actor, mapping, "confirmed", status="confirmed", entity_id=item.entity_id, reason=None)
            )
        except AppError as error:
            raise AppError(
                error.status_code,
                error.code,
                f"No se confirmó ningún vínculo: {error.message}",
                {**(error.details or {}), "mapping_id": item.mapping_id},
            ) from error
    resolution = ReResolution()
    for decision in decisions:
        for kind, written in decision.resolution.updated.items():
            resolution.updated[kind] = resolution.updated.get(kind, 0) + written
        resolution.issues_closed += decision.resolution.issues_closed
    record_event(
        db,
        "platform.mapping.batch_confirmed",
        user_id=actor.id,
        entity_type="external_entity_mapping",
        entity_id=None,
        payload={
            "mapping_ids": sorted(ids),
            "confirmed": len(decisions),
            "facts_updated": resolution.updated,
            "issues_closed": resolution.issues_closed,
        },
    )
    db.flush()
    return decisions


def _filtered(
    db: Session,
    *,
    entity_type: str | None,
    source_system: str | None,
    namespace: str | None,
    q: str | None,
) -> Select[ExternalEntityMapping]:
    query = select(ExternalEntityMapping)
    if entity_type:
        query = query.where(ExternalEntityMapping.entity_type == entity_type)
    if source_system:
        query = query.where(ExternalEntityMapping.source_system_id == source_system_id(db, source_system))
    if namespace:
        query = query.where(ExternalEntityMapping.namespace == namespace)
    text = " ".join((q or "").split())
    if text:
        # The code as keyed (exact or its start) or a part of the label, accents and case aside.
        pattern = label_key(text).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.where(
            or_(
                ExternalEntityMapping.external_id.startswith(code_key(text), autoescape=True),
                ExternalEntityMapping.external_id.contains(label_key(text), autoescape=True),
                func.upper(func.translate(ExternalEntityMapping.external_label, ACCENTED, PLAIN)).like(
                    f"%{pattern}%", escape="\\"
                ),
            )
        )
    return query


# The accents `label_key` removes, for the label search in SQL (no unaccent extension needed).
ACCENTED = "ÁÉÍÓÚÜÑáéíóúüñ"
PLAIN = "AEIOUUNaeiouun"
OPEN = ("pending", "ambiguous")


def search(
    db: Session,
    *,
    entity_type: str | None,
    source_system: str | None,
    namespace: str | None,
    statuses: Sequence[str],
    limit: int,
    offset: int,
    q: str | None = None,
) -> tuple[Sequence[ExternalEntityMapping], int]:
    """The mappings to review: open ones first, the oldest first (what waits longest goes first)."""
    query = _filtered(db, entity_type=entity_type, source_system=source_system, namespace=namespace, q=q)
    if statuses:
        query = query.where(ExternalEntityMapping.status.in_(statuses))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    first = case((ExternalEntityMapping.status.in_(OPEN), 0), else_=1)
    rows = db.scalars(query.order_by(first, ExternalEntityMapping.id).offset(offset).limit(limit)).all()
    return rows, total


def summary(
    db: Session, *, entity_type: str | None, source_system: str | None, namespace: str | None, q: str | None
) -> dict[str, int]:
    """How many mappings are in each status, with the same filters as the queue (the status aside)."""
    query = _filtered(db, entity_type=entity_type, source_system=source_system, namespace=namespace, q=q).subquery()
    counted: dict[str, int] = dict(db.execute(select(query.c.status, func.count()).group_by(query.c.status)).all())
    return {status: int(counted.get(status, 0)) for status in STATUSES}


def source_codes(db: Session) -> dict[int, str]:
    return {row.id: row.code for row in db.execute(select(SourceSystem.id, SourceSystem.code))}
