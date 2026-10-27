"""History of the expected amount of a settlement (R9-1, ADR 011, DATA_MODEL.md §13.13).

Every change of the expected amount, manual or from Store Sys, is one row of `expected_amount_events`, and
`settlements.expected_event_id` points at the one that set the amount in effect. A Store Sys record is keyed by
(source, external_reference, revision): the same key with the same fingerprint is a retry and returns the same
event; with another fingerprint it is a conflict and nothing is written.

An event never changes, except a PENDING_CONFIRMATION one, which is decided once: CONFIRMED (its amount becomes
the expected), KEPT (the amount in effect stays, with a reason) or SUPERSEDED (a later Store Sys event replaced it
before anyone decided, and names it). Nothing is deleted.
"""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, func
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import Uuid

from app.db.base import Base, IdMixin

SCHEMA = "settlements"

MANUAL = "MANUAL"
STORE_SYS = "STORE_SYS"
SOURCES = (MANUAL, STORE_SYS)

APPLIED = "APPLIED"
MATCHED = "MATCHED"
PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
CONFIRMED = "CONFIRMED"
KEPT = "KEPT"
REJECTED_CLOSED = "REJECTED_CLOSED"
SUPERSEDED = "SUPERSEDED"
# Enzo's R9-1 adjustment 2: exactly these outcomes; a retry returns the event it already created.
OUTCOMES = (APPLIED, MATCHED, PENDING_CONFIRMATION, CONFIRMED, KEPT, REJECTED_CLOSED, SUPERSEDED, MANUAL)
# Outcomes whose amount is the expected in effect when the settlement points at the event.
SETTING_OUTCOMES = (MANUAL, APPLIED, MATCHED, CONFIRMED)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class ExpectedAmountEvent(IdMixin, Base):
    __tablename__ = "expected_amount_events"
    __table_args__ = (
        CheckConstraint(f"source IN ({_in(SOURCES)})", name="source"),
        CheckConstraint(f"outcome IN ({_in(OUTCOMES)})", name="outcome"),
        # MANUAL is the only outcome of a manual event, and never the outcome of a Store Sys one.
        CheckConstraint("(source = 'MANUAL') = (outcome = 'MANUAL')", name="outcome_by_source"),
        # A Store Sys event carries its key and fingerprint and an amount; a manual one none of them (it may clear the
        # expected amount of a boca, D2).
        CheckConstraint(
            "(source = 'STORE_SYS' AND external_reference IS NOT NULL AND revision IS NOT NULL "
            "AND request_fingerprint IS NOT NULL AND expected_amount IS NOT NULL) OR "
            "(source = 'MANUAL' AND external_reference IS NULL AND revision IS NULL AND request_fingerprint IS NULL "
            "AND reported_at IS NULL AND client_request_id IS NULL)",
            name="fields_by_source",
        ),
        CheckConstraint("length(btrim(external_reference)) > 0", name="external_reference_not_blank"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("expected_amount >= 0", name="expected_amount_not_negative"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint("reason IS NULL OR length(btrim(reason)) > 0", name="reason_not_blank"),
        # A decision is who, when and (to keep) why; only CONFIRMED, KEPT and a pending SUPERSEDED by a later event
        # (which names it in `supersedes_event_id`) have one. A late revision SUPERSEDED on arrival has none.
        CheckConstraint(
            "(decided_by IS NULL) = (decided_at IS NULL) "
            "AND (outcome NOT IN ('CONFIRMED', 'KEPT') OR decided_by IS NOT NULL) "
            "AND (outcome <> 'KEPT' OR length(btrim(coalesce(decision_reason, ''))) > 0) "
            "AND (decision_reason IS NULL OR outcome = 'KEPT') "
            "AND (decided_by IS NULL OR outcome IN ('CONFIRMED', 'KEPT', 'SUPERSEDED'))",
            name="decision_fields",
        ),
        Index(
            "uq_expected_amount_events_store_key",
            "source",
            "external_reference",
            "revision",
            unique=True,
            postgresql_where=sql_text("source = 'STORE_SYS'"),
        ),
        # At most one undecided pending per settlement: a later Store Sys event decides it SUPERSEDED first.
        Index(
            "uq_expected_amount_events_one_pending",
            "settlement_id",
            unique=True,
            postgresql_where=sql_text("outcome = 'PENDING_CONFIRMATION'"),
        ),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.settlements.id", ondelete="RESTRICT"), index=True
    )
    # The version of the settlement when the event arrived (a reopening grows it, R2-2).
    settlement_version: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str] = mapped_column(String(30))
    # STORE_SYS only: the canonical key built by the adapter (ADR 011), never interpreted here.
    external_reference: Mapped[str | None] = mapped_column(String(100))
    revision: Mapped[int | None] = mapped_column(Integer)
    request_fingerprint: Mapped[str | None] = mapped_column(String(64))
    # Informative instant the sender reported; DistriCom's own instant is `received_at`.
    reported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The batch it came in (informative; idempotency is per record).
    client_request_id: Mapped[UUID | None] = mapped_column(Uuid)
    # The amount informed (Store Sys) or set (manual); NULL only for a manual clear of a boca's expected.
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    # What was in effect when it arrived.
    previous_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    previous_source: Mapped[str | None] = mapped_column(String(20))
    previous_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.expected_amount_events.id", ondelete="RESTRICT")
    )
    # Manual: why (mandatory to correct an amount that came from Store Sys).
    reason: Mapped[str | None] = mapped_column(Text)
    received_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    # The undecided pending this event replaced (it was decided SUPERSEDED in the same transaction).
    supersedes_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.expected_amount_events.id", ondelete="RESTRICT"), unique=True
    )
