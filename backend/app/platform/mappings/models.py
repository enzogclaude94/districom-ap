"""`(entity_type, source_system, namespace, external_id)` identifies one external identifier; the row says what
DistriCom decided it is (DATA_MODEL §7.4, docs/IMPORTS.md §6).

- `pending`: no decision yet; at most one proposed candidate (an exact-name proposal is never confirmed alone).
- `ambiguous`: two or more candidates; a person decides.
- `confirmed`: linked to `entity_id`, by `method`.
- `rejected`: the proposal was turned down; still unlinked, can be confirmed later.
- `excluded`: deliberately never linked (pseudo-sellers such as «BOCA DE EXPENDIO»).

An import only creates mappings and refreshes `external_label` / `last_seen_job_id`; it never changes a status or
an entity (`app.platform.mappings.service`). Every decision is audited. Nothing is deleted (migration 0034).
"""

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

STATUSES = ("pending", "ambiguous", "confirmed", "rejected", "excluded")
METHODS = ("created_from_master", "exact_external_id", "rule", "manual")
# A mapping an import may create already confirmed: the entity comes from the same system and code, or from an
# explicit rule an admin enabled. A proposal by name never is one of these.
AUTOMATIC_METHODS = ("created_from_master", "exact_external_id", "rule")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ExternalEntityMapping(IdMixin, TimestampMixin, Base):
    __tablename__ = "external_entity_mappings"
    __table_args__ = (
        UniqueConstraint(
            "entity_type", "source_system_id", "namespace", "external_id", name="uq_external_entity_mappings_key"
        ),
        CheckConstraint(_in("status", STATUSES), name="status"),
        CheckConstraint(f"method IS NULL OR {_in('method', METHODS)}", name="method"),
        CheckConstraint("length(btrim(external_id)) > 0", name="external_id_not_blank"),
        CheckConstraint("length(btrim(namespace)) > 0", name="namespace_not_blank"),
        # Linked exactly when confirmed, and then always with how.
        CheckConstraint(
            "(status = 'confirmed') = (entity_id IS NOT NULL) AND (status <> 'confirmed' OR method IS NOT NULL)",
            name="confirmed_has_entity",
        ),
        CheckConstraint(
            "status <> 'ambiguous' OR jsonb_array_length(candidates) >= 2", name="ambiguous_has_candidates"
        ),
        CheckConstraint("status <> 'pending' OR jsonb_array_length(candidates) <= 1", name="pending_one_candidate"),
        Index("ix_external_entity_mappings_status", "status"),
        Index("ix_external_entity_mappings_entity", "entity_type", "entity_id"),
    )

    entity_type: Mapped[str] = mapped_column(String(40))
    source_system_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_systems.id", ondelete="RESTRICT"))
    namespace: Mapped[str] = mapped_column(String(40))
    external_id: Mapped[str] = mapped_column(String(200))
    external_label: Mapped[str | None] = mapped_column(String(200))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20))
    method: Mapped[str | None] = mapped_column(String(30))
    candidates: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    first_seen_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    last_seen_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
