"""Pending collections: money a customer still owes when the settlement is handed in (R3, DATA_MODEL.md §13).

A pending collection is born in a settlement and justifies part of it. Its identity is stable across
the settlement's versions: a reopening never creates a new pending, it changes the same row.

Two different things are kept apart (Enzo's R3 decision 3):

- the **load** (`pending_collections`): what the next close will publish. It is edited while the
  settlement is OPEN, including during a correction, and voided with a reason;
- the **financial amount in effect**: the row of `settlement_closing_pendings` of the settlement's
  close in effect (`settlements.current_close_operation_id`). Every close freezes what it published;
  nothing else decides how much a pending is worth in Cajas.

So during a correction a pending keeps its previous amount in effect until the new close; a pending
added during it is not in effect yet; a pending marked for removal is still in effect until then.

`pending_collection_events` is the domain history of each pending (created, every changed field,
voided, published by a close, withdrawn by a correction). It is append-only and does not depend on
`audit_events`, which stays the cross-cutting audit.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

SCHEMA = "settlements"

EVENT_TYPES = (
    "CREATED",
    "AMOUNT_CHANGED",
    "CUSTOMER_CHANGED",
    "RESPONSIBLE_CHANGED",
    "REASON_CHANGED",
    "EXPECTED_DATE_CHANGED",
    "VOIDED",
    # A close of the settlement published it (with its amount), or a correction close left it out.
    "PUBLISHED",
    "WITHDRAWN",
    # R3-2: a receipt or a resolution, and their reversals. They point at the row; the money is there.
    "RECEIPT_RECORDED",
    "RECEIPT_REVERSED",
    "RESOLVED",
    "RESOLUTION_REVERSED",
)
CLOSE_EVENT_TYPES = ("PUBLISHED", "WITHDRAWN")
RECEIPT_EVENT_TYPES = ("RECEIPT_RECORDED", "RECEIPT_REVERSED")
RESOLUTION_EVENT_TYPES = ("RESOLVED", "RESOLUTION_REVERSED")
RECEIPT_METHODS = ("CASH", "TRANSFER")
RESOLUTION_TARGETS = ("COMPANY", "EMPLOYEE")

_SETTLEMENT_FK = f"{SCHEMA}.settlements.id"
_PENDING_FK = f"{SCHEMA}.pending_collections.id"
_RECEIPT_FK = f"{SCHEMA}.pending_collection_receipts.id"
_RESOLUTION_FK = f"{SCHEMA}.pending_collection_resolutions.id"
# Reversal of a receipt or a resolution: all of it or none, once (migration 0010 freezes it after that).
_REVERSAL_COMPLETE = (
    "(reversal_operation_id IS NULL AND reversed_by IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL) OR "
    "(reversal_operation_id IS NOT NULL AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL AND "
    "length(btrim(coalesce(reversal_reason, ''))) > 0)"
)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class PendingCollection(IdMixin, TimestampMixin, Base):
    """The load of a pending collection: what the next close of its settlement publishes.

    While the settlement is OPEN it is corrected or voided (with a reason), never deleted; the database
    refuses any change once the settlement is CLOSED (migration 0009).
    """

    __tablename__ = "pending_collections"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("length(btrim(customer_name)) > 0", name="customer_not_blank"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) OR "
            "(voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(btrim(coalesce(void_reason, ''))) > 0)",
            name="void_complete",
        ),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    # Customer as text until `public.customers` exists (Enzo's R3 decision 1): then customer_id with a real FK.
    customer_name: Mapped[str] = mapped_column(String(200))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str] = mapped_column(Text)
    # Who answers for collecting it: the driver by default, any employee if the operator says so (R3-D3).
    responsible_employee_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    expected_date: Mapped[date | None] = mapped_column(Date)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)


class SettlementClosingPending(IdMixin, Base):
    """What one close of a settlement published for one pending. Append-only.

    The amount in effect of a pending is its row for `settlements.current_close_operation_id`. The rows
    of a close add up exactly to its "Pendientes de cobro" leg (deferred trigger, migration 0009).
    """

    __tablename__ = "settlement_closing_pendings"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("length(btrim(customer_name)) > 0", name="customer_not_blank"),
        UniqueConstraint("close_operation_id", "pending_id", name="uq_settlement_closing_pendings_close_pending"),
        {"schema": SCHEMA},
    )

    close_operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT")
    )
    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    settlement_version: Mapped[int] = mapped_column(Integer)
    pending_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_PENDING_FK, ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # Who owes it, as published: a change of customer during a correction is not in effect until the close.
    customer_name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PendingCollectionEvent(IdMixin, Base):
    """Domain history of a pending collection. Append-only (no UPDATE, no DELETE).

    `before` / `after` hold only the fields the event is about. PUBLISHED and WITHDRAWN carry the close.
    """

    __tablename__ = "pending_collection_events"
    __table_args__ = (
        CheckConstraint(f"event_type IN ({_in(EVENT_TYPES)})", name="event_type"),
        CheckConstraint(
            f"(event_type IN ({_in(CLOSE_EVENT_TYPES)})) = (close_operation_id IS NOT NULL)", name="close_events"
        ),
        CheckConstraint(
            f"(event_type IN ({_in(RECEIPT_EVENT_TYPES)})) = (receipt_id IS NOT NULL)", name="receipt_events"
        ),
        CheckConstraint(
            f"(event_type IN ({_in(RESOLUTION_EVENT_TYPES)})) = (resolution_id IS NOT NULL)", name="resolution_events"
        ),
        CheckConstraint("event_type <> 'VOIDED' OR length(btrim(coalesce(reason, ''))) > 0", name="void_reason"),
        CheckConstraint("settlement_version >= 1", name="settlement_version_positive"),
        {"schema": SCHEMA},
    )

    pending_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_PENDING_FK, ondelete="RESTRICT"), index=True)
    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"))
    # Version of the settlement the change belongs to: v2 means "during the first correction".
    settlement_version: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(30))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    close_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT")
    )
    receipt_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(_RECEIPT_FK, ondelete="RESTRICT"))
    resolution_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(_RESOLUTION_FK, ondelete="RESTRICT"))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PendingCollectionReceipt(IdMixin, Base):
    """A customer paid (part of) a pending: its PENDING_COLLECTION operation moved it to a cash box or a bank.

    Append-only: never deleted, and the only change ever allowed is its reversal (the four reversal
    fields at once, once), in the same transaction as the REVERSAL in the ledger (migration 0010).
    """

    __tablename__ = "pending_collection_receipts"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(f"method IN ({_in(RECEIPT_METHODS)})", name="method"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint(_REVERSAL_COMPLETE, name="reversal_complete"),
        {"schema": SCHEMA},
    )

    pending_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_PENDING_FK, ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # When the customer paid: not before the settlement, not in the future (Enzo's R3-2 D1).
    business_date: Mapped[date] = mapped_column(Date)
    method: Mapped[str] = mapped_column(String(20))
    destination_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT")
    )
    operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reversal_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    reversed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[str | None] = mapped_column(Text)


class PendingCollectionResolution(IdMixin, Base):
    """An admin resolved (part of) a pending without collecting it: its PENDING_WRITE_OFF operation moved it
    to "Incobrables" (the company's part) and to employees' internal accounts (their parts).

    Append-only like a receipt; its parts are `pending_resolution_allocations` and add up exactly to it.
    """

    __tablename__ = "pending_collection_resolutions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint(_REVERSAL_COMPLETE, name="reversal_complete"),
        {"schema": SCHEMA},
    )

    pending_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_PENDING_FK, ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    business_date: Mapped[date] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(Text)
    operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    reversal_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    reversed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[str | None] = mapped_column(Text)


class PendingResolutionAllocation(IdMixin, Base):
    """Who absorbs part of a resolution: the company (at most one part) or an employee (one part each).
    Append-only: a resolution is corrected by reversing it whole."""

    __tablename__ = "pending_resolution_allocations"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(f"target IN ({_in(RESOLUTION_TARGETS)})", name="target"),
        CheckConstraint("(target = 'EMPLOYEE') = (employee_id IS NOT NULL)", name="employee_target"),
        Index(
            "uq_pending_resolution_allocations_employee",
            "resolution_id",
            "employee_id",
            unique=True,
            postgresql_where=text("employee_id IS NOT NULL"),
        ),
        Index(
            "uq_pending_resolution_allocations_company",
            "resolution_id",
            unique=True,
            postgresql_where=text("target = 'COMPANY'"),
        ),
        {"schema": SCHEMA},
    )

    resolution_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_RESOLUTION_FK, ondelete="RESTRICT"), index=True)
    target: Mapped[str] = mapped_column(String(20))
    employee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
