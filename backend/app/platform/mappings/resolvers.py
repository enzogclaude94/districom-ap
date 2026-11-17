"""Re-resolution of facts after a mapping decision (F1.5, docs/IMPORTS.md §6.4).

The platform decides a mapping; the module that owns the facts declares, in `ModuleSpec.mapping_resolvers`, which
of its columns took their identity from that mapping (`FactLink`) and which of its quality issues the decision may
close. The platform never imports a module: it only receives SQLAlchemy columns and a callable.

The rule is the same for every fact: a row whose `mapping_column` is the decided mapping ends with its
`entity_column` equal to the mapping's entity when it is `confirmed`, NULL otherwise. Only rows that differ are
written, so a decision is idempotent and a row resolved by another mapping is never touched. Nothing is matched by
name or code here: the link is the stored mapping id.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, cast

from sqlalchemy import ColumnElement, CursorResult, and_, func, literal, select, update
from sqlalchemy.orm import InstrumentedAttribute, Session

from app.platform.mappings.models import ExternalEntityMapping

# (entity_type, source system code, namespace)
KindKey = tuple[str, str, str]


@dataclass(frozen=True)
class FactLink:
    """A fact column that holds the entity a mapping resolved, next to the column that holds the mapping."""

    kind: str  # stable id used in the API and the audit, e.g. `commercial.no_purchase_events`
    label: str  # plural business noun shown to users, e.g. «eventos de no compra»
    entity_column: InstrumentedAttribute[Any]
    mapping_column: InstrumentedAttribute[Any]
    # Which rows are current (not superseded nor voided). Every row is re-resolved, history included, so it never
    # contradicts its mapping; the current ones are what the screen counts first. None: every row is current.
    current: ColumnElement[bool] | None = None


# Closes the quality issues whose condition the decision removed; returns how many it closed. It runs after the
# facts were re-resolved, inside the same transaction.
IssueCloser = Callable[[Session, ExternalEntityMapping, int], int]


@dataclass(frozen=True)
class MappingResolverSpec:
    entity_type: str
    source_system: str  # source_systems.code
    namespace: str
    label: str  # business name of the kind of code: «Preventista AXUM»
    facts: tuple[FactLink, ...] = ()
    close_issues: IssueCloser | None = None

    @property
    def key(self) -> KindKey:
        return (self.entity_type, self.source_system, self.namespace)


@dataclass(frozen=True)
class FactCount:
    kind: str
    label: str
    total: int  # rows that took their identity from the mapping
    linked: int  # of them, with an entity today
    on_target: int  # of them, already pointing to the requested entity (0 without one)
    current: int  # of `total`, the current ones (the rest is superseded or voided history)
    current_linked: int
    current_on_target: int

    def changes_on_link(self) -> int:
        """Rows a confirm or reassign to the target would write."""
        return self.total - self.on_target

    def changes_on_unlink(self) -> int:
        """Rows an exclude would write (back to no entity)."""
        return self.linked

    def current_changes_on_link(self) -> int:
        return self.current - self.current_on_target

    def current_changes_on_unlink(self) -> int:
        return self.current_linked


@dataclass
class ReResolution:
    updated: dict[str, int] = field(default_factory=dict)  # kind → rows written
    issues_closed: int = 0

    @property
    def facts_updated(self) -> int:
        return sum(self.updated.values())


class MappingResolverRegistry:
    def __init__(self) -> None:
        self._specs: dict[KindKey, list[MappingResolverSpec]] = {}

    def register(self, spec: MappingResolverSpec) -> None:
        """Idempotent per (key, label), so re-creating the app (tests) does not duplicate a resolver. Several specs may
        share a key (two modules with facts of the same code); the first one's label names the kind."""
        specs = self._specs.setdefault(spec.key, [])
        specs[:] = [s for s in specs if s.label != spec.label] + [spec]

    def specs(self, key: KindKey) -> list[MappingResolverSpec]:
        return list(self._specs.get(key, ()))

    def keys(self) -> list[KindKey]:
        return list(self._specs)

    def label(self, key: KindKey) -> str | None:
        specs = self._specs.get(key)
        return specs[0].label if specs else None

    def all(self) -> list[MappingResolverSpec]:
        return [spec for specs in self._specs.values() for spec in specs]


registry = MappingResolverRegistry()


def _facts(key: KindKey) -> list[FactLink]:
    return [fact for spec in registry.specs(key) for fact in spec.facts]


def _measures(fact: FactLink, target_entity_id: int | None) -> list[Any]:
    current = fact.current if fact.current is not None else literal(True)
    on_target = fact.entity_column == target_entity_id if target_entity_id else literal(False)
    return [
        func.count(),
        func.count(fact.entity_column),
        func.count().filter(on_target),
        func.count().filter(current),
        func.count(fact.entity_column).filter(current),
        func.count().filter(and_(current, on_target)),
    ]


def count(db: Session, key: KindKey, mapping_id: int, target_entity_id: int | None = None) -> list[FactCount]:
    """How many facts each link holds for this mapping: what a decision would change, before taking it."""
    result = []
    for fact in _facts(key):
        row = db.execute(select(*_measures(fact, target_entity_id)).where(fact.mapping_column == mapping_id)).one()
        result.append(FactCount(fact.kind, fact.label, *(int(v or 0) for v in row)))
    return result


def totals(db: Session, mapping_ids_by_key: Mapping[KindKey, Sequence[int]]) -> dict[int, list[FactCount]]:
    """Facts per mapping for a page of the queue, one grouped query per link."""
    found: dict[int, list[FactCount]] = {}
    for key, ids in mapping_ids_by_key.items():
        if not ids:
            continue
        for fact in _facts(key):
            rows = db.execute(
                select(fact.mapping_column, *_measures(fact, None))
                .where(fact.mapping_column.in_(ids))
                .group_by(fact.mapping_column)
            ).all()
            counted = {int(r[0]): tuple(int(v or 0) for v in r[1:]) for r in rows}
            for mapping_id in ids:
                values = counted.get(mapping_id, (0, 0, 0, 0, 0, 0))
                found.setdefault(mapping_id, []).append(FactCount(fact.kind, fact.label, *values))
    return found


def apply(db: Session, key: KindKey, mapping: ExternalEntityMapping, user_id: int) -> ReResolution:
    """Make every fact of this mapping agree with its decision, in the caller's (locked) transaction."""
    target = mapping.entity_id if mapping.status == "confirmed" else None
    outcome = ReResolution()
    for fact in _facts(key):
        statement = (
            update(fact.entity_column.class_)
            .where(fact.mapping_column == mapping.id, fact.entity_column.is_distinct_from(target))
            .values({fact.entity_column.key: target})
            .execution_options(synchronize_session=False)
        )
        written = cast(CursorResult[Any], db.execute(statement)).rowcount
        outcome.updated[fact.kind] = outcome.updated.get(fact.kind, 0) + written
    for spec in registry.specs(key):
        if spec.close_issues is not None:
            outcome.issues_closed += spec.close_issues(db, mapping, user_id)
    return outcome
