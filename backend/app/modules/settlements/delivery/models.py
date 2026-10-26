"""Delivery settlements and what the driver hands in (R2-1, DATA_MODEL.md §13.2).

A settlement is OPEN while it is being loaded. Every load table hangs from it: the cash count by
denomination, the additions (cash that appears after the count), the transfers with their verification
and the expenses. Closing it (R2-2) publishes one SETTLEMENT_CLOSE in the ledger and freezes it; the
difference and who absorbs it are materialized per version, and every close is kept in
`settlement_closings`. `status` is the editing state; `current_close_operation_id` is the close that
is in effect in the ledger, which a reopened settlement keeps until it is closed again.

Nothing is deleted. The count is replaced while OPEN and audited; additions and expenses are voided
with a reason; transfers keep every status decision in `transfer_status_events`.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
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

SETTLEMENT_TYPES = ("DELIVERY", "OUTLET")  # OUTLET: rendición de boca de expendio (R7-2, ADR 009)
EXPECTED_SOURCES = ("MANUAL", "STORE_SYS")  # STORE_SYS: the canonical contract of R9-1 (ADR 011)
SETTLEMENT_STATUSES = ("OPEN", "CLOSED")
TRANSFER_STATUSES = ("DECLARED", "VERIFIED", "REJECTED")
DIFFERENCE_KINDS = ("SHORTAGE", "SURPLUS")
# FINANCIAL: the close published a SETTLEMENT_CLOSE. ADMINISTRATIVE_ZERO: an OUTLET settlement of $0 without
# any movement, closed without a ledger operation (Enzo's R7-2-Q1); the database checks that nothing moved.
CLOSING_KINDS = ("FINANCIAL", "ADMINISTRATIVE_ZERO")
ALLOCATION_TARGETS = ("EMPLOYEE", "COMPANY")

_ACCOUNT_FK = f"{SCHEMA}.accounts.id"
_SETTLEMENT_FK = f"{SCHEMA}.settlements.id"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# A void is all three columns or none, and its reason is not blank.
_VOID_CHECK = (
    "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) OR "
    "(voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(btrim(coalesce(void_reason, ''))) > 0)"
)


class Settlement(IdMixin, TimestampMixin, Base):
    """One settlement of a business day: of a driver (DELIVERY, R2) or of a boca de expendio (OUTLET, R7-2).

    A driver or a boca may have several the same day (R2-D7). The identity depends on the type: a
    DELIVERY settlement has its driver and no boca, an OUTLET one its boca and no employee.
    """

    __tablename__ = "settlements"
    __table_args__ = (
        CheckConstraint(f"type IN ({_in(SETTLEMENT_TYPES)})", name="type"),
        CheckConstraint(f"expected_source IN ({_in(EXPECTED_SOURCES)})", name="expected_source"),
        CheckConstraint(f"status IN ({_in(SETTLEMENT_STATUSES)})", name="status"),
        CheckConstraint(
            "(type = 'DELIVERY' AND employee_id IS NOT NULL AND outlet_id IS NULL) OR "
            "(type = 'OUTLET' AND outlet_id IS NOT NULL AND employee_id IS NULL)",
            name="identity_by_type",
        ),
        # DELIVERY: unknown (NULL) while it is loaded or positive (Enzo's R9-1 adjustment 1). OUTLET: unknown (NULL)
        # or 0 too (D2). Either way it is required to close.
        CheckConstraint(
            "(type = 'DELIVERY' AND (expected_amount IS NULL OR expected_amount > 0)) OR "
            "(type = 'OUTLET' AND (expected_amount IS NULL OR expected_amount >= 0))",
            name="expected_amount_by_type",
        ),
        CheckConstraint("status <> 'CLOSED' OR expected_amount IS NOT NULL", name="closed_has_expected"),
        CheckConstraint("sequence_number >= 1", name="sequence_number_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        # Rendición 1, 2... of the same driver (or boca) and day; the number is generated, never typed.
        Index(
            "uq_settlements_sequence",
            "employee_id",
            "business_date",
            "sequence_number",
            unique=True,
            postgresql_where=text("type = 'DELIVERY'"),
        ),
        Index(
            "uq_settlements_outlet_sequence",
            "outlet_id",
            "business_date",
            "sequence_number",
            unique=True,
            postgresql_where=text("type = 'OUTLET'"),
        ),
        Index("ix_settlements_business_date", "business_date"),
        # A closed settlement always has the close that is in effect in the ledger; only an OUTLET one may
        # instead have an administrative zero close in effect (checked against its load at commit, 0023).
        CheckConstraint(
            "status <> 'CLOSED' OR current_close_operation_id IS NOT NULL "
            "OR (type = 'OUTLET' AND current_closing_id IS NOT NULL)",
            name="closed_has_close",
        ),
        # A close in effect in the ledger is always a documented close; a DELIVERY one is always financial.
        CheckConstraint(
            "current_close_operation_id IS NULL OR current_closing_id IS NOT NULL", name="close_documented"
        ),
        CheckConstraint(
            "type <> 'DELIVERY' OR current_closing_id IS NULL OR current_close_operation_id IS NOT NULL",
            name="delivery_close_financial",
        ),
        CheckConstraint(
            "(reopened_at IS NULL AND reopened_by IS NULL AND reopen_reason IS NULL) OR "
            "(reopened_at IS NOT NULL AND reopened_by IS NOT NULL AND length(btrim(coalesce(reopen_reason, ''))) > 0)",
            name="reopen_complete",
        ),
        {"schema": SCHEMA},
    )

    type: Mapped[str] = mapped_column(String(20))
    business_date: Mapped[date] = mapped_column(Date)
    # DELIVERY: the driver. NULL for OUTLET.
    employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    # OUTLET: the boca de expendio (platform). NULL for DELIVERY.
    outlet_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("outlets.id", ondelete="RESTRICT"), index=True)
    sequence_number: Mapped[int] = mapped_column(Integer)
    # NULL while the Store Sys amount is not loaded yet (any type since R9-1); required to close it.
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    expected_source: Mapped[str] = mapped_column(String(20), server_default="MANUAL")
    # The event of `expected_amount_events` that set the amount in effect (R9-1). NULL for an amount loaded before
    # R9-1 («Manual · anterior a integración Store Sys»), never backfilled, and for one never loaded.
    expected_event_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "settlements.expected_amount_events.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_settlements_expected_event_id_expected_amount_events",
        ),
    )
    # A free text reference typed by the user (R2/R7); never the Store Sys key, which lives in the events.
    external_reference: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), server_default="OPEN")
    # Grows with every reopening (R2-2); 1 for a settlement never reopened.
    version: Mapped[int] = mapped_column(Integer, server_default="1")
    # Idempotent creation: a retry of the same request never becomes "Rendición 2".
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    # Financial state (R2-2): the SETTLEMENT_CLOSE in effect, kept while a reopened settlement is corrected.
    # NULL with a close in effect only after an administrative zero close (OUTLET, R7-2).
    current_close_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT")
    )
    # The documentary close in effect (R7-2): financial or administrative zero; kept while it is corrected.
    current_closing_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            "settlements.settlement_closings.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_settlements_current_closing_id_settlement_closings",
        ),
    )
    closed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The reopening in progress; cleared by the next close (the history stays in audit and closings).
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    reopen_reason: Mapped[str | None] = mapped_column(Text)


class CashCountLine(IdMixin, Base):
    """Quantity of one denomination in the count, and the cash box it goes to.

    The denomination is copied (code and value), not referenced: changing or deactivating the
    configuration later never changes a historical count.
    """

    __tablename__ = "cash_count_lines"
    __table_args__ = (
        CheckConstraint("denomination_value > 0", name="denomination_value_positive"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        UniqueConstraint("settlement_id", "denomination_code", name="uq_cash_count_lines_denomination"),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    denomination_code: Mapped[str] = mapped_column(String(40))
    denomination_value: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    quantity: Mapped[int] = mapped_column(Integer)
    # A cash box (service): Caja principal, Caja de cambio... chosen line by line.
    destination_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_ACCOUNT_FK, ondelete="RESTRICT"))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CashAddition(IdMixin, Base):
    """Cash added without redoing the count (R2-D5). Never edited nor deleted; voided with a reason.

    The database allows a single UPDATE: the one that voids it (migration 0007).
    """

    __tablename__ = "cash_additions"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(_VOID_CHECK, name="void_complete"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    destination_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_ACCOUNT_FK, ondelete="RESTRICT"))
    description: Mapped[str | None] = mapped_column(Text)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    voided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)


class SettlementTransfer(IdMixin, TimestampMixin, Base):
    """A transfer the driver says a customer made. Only VERIFIED ones count as money received."""

    __tablename__ = "settlement_transfers"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(f"status IN ({_in(TRANSFER_STATUSES)})", name="status"),
        CheckConstraint(
            "status <> 'REJECTED' OR length(btrim(coalesce(status_reason, ''))) > 0", name="rejection_reason"
        ),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # The bank account where it should arrive (service: kind BANK).
    bank_account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_ACCOUNT_FK, ondelete="RESTRICT"))
    reference: Mapped[str | None] = mapped_column(String(100))
    # Customer as text until `public.customers` exists (ADR 007, C3).
    payer_name: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), server_default="DECLARED")
    # Current decision; every decision, earlier ones included, is in transfer_status_events.
    status_reason: Mapped[str | None] = mapped_column(Text)
    status_changed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    status_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))


class TransferStatusEvent(IdMixin, Base):
    """Append-only history of a transfer's status: declared, verified, rejected and every correction."""

    __tablename__ = "transfer_status_events"
    __table_args__ = (
        CheckConstraint(f"to_status IN ({_in(TRANSFER_STATUSES)})", name="to_status"),
        CheckConstraint(f"from_status IS NULL OR from_status IN ({_in(TRANSFER_STATUSES)})", name="from_status"),
        {"schema": SCHEMA},
    )

    transfer_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.settlement_transfers.id", ondelete="RESTRICT"), index=True
    )
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(Text)
    # True when an admin changed a decision already taken (VERIFIED or REJECTED).
    is_correction: Mapped[bool] = mapped_column(Boolean, server_default="false")
    changed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


FUEL = "FUEL"


class ExpenseCategory(IdMixin, TimestampMixin, Base):
    """Expense category, edited by an admin. Deactivated, never deleted.

    `code` marks the few categories with behavior (R5-2: FUEL asks for a fuel load). The system recognizes
    them by code, never by name, so an admin may rename them. Only a migration assigns a code; it never
    changes (trigger) and the API neither takes nor edits it. Ordinary categories have none.
    """

    __tablename__ = "expense_categories"
    __table_args__ = (
        CheckConstraint("code ~ '^[A-Z][A-Z0-9_]{0,39}$'", name="code_format"),
        {"schema": SCHEMA},
    )

    name: Mapped[str] = mapped_column(String(100))
    # Lower case, no accents, single spaces: "Peajes" and "peajes " are the same category.
    name_key: Mapped[str] = mapped_column(String(100), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    code: Mapped[str | None] = mapped_column(String(40), unique=True)


class Expense(IdMixin, TimestampMixin, Base):
    """An expense. SETTLEMENT: the driver paid it out of the collected money; edited while the settlement is
    OPEN, voided with a reason. DIRECT (R5-3): paid from a cash box or the bank by its own DIRECT_EXPENSE
    operation; its money never changes, it is reversed (`expenses/direct.py`). Each origin has exactly its
    own columns (check `origin_fields`)."""

    __tablename__ = "expenses"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(_VOID_CHECK, name="void_complete"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint("origin IN ('SETTLEMENT', 'DIRECT')", name="origin"),
        CheckConstraint(
            "(origin = 'SETTLEMENT' AND settlement_id IS NOT NULL AND paid_from_account_id IS NULL "
            "AND business_date IS NULL AND operation_id IS NULL AND reversal_operation_id IS NULL) OR "
            "(origin = 'DIRECT' AND settlement_id IS NULL AND paid_from_account_id IS NOT NULL "
            "AND business_date IS NOT NULL AND NOT legacy_fuel_without_detail)",
            name="origin_fields",
        ),
        CheckConstraint(
            "origin <> 'DIRECT' OR ((reversal_operation_id IS NULL) = (voided_at IS NULL))", name="direct_reversal"
        ),
        Index("ix_expenses_category_id", "category_id"),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True
    )
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.expense_categories.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    description: Mapped[str | None] = mapped_column(Text)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)
    # Where the expense was paid from: SETTLEMENT (out of a driver's collections) or DIRECT (R5-3).
    origin: Mapped[str] = mapped_column(String(20), server_default="SETTLEMENT")
    # DIRECT only: the one account it was paid from (a cash box or the bank), its business date, its
    # DIRECT_EXPENSE operation and, once reversed, the reversal of that operation.
    paid_from_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"), index=True
    )
    business_date: Mapped[date | None] = mapped_column(Date, index=True)
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    reversal_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    # True only for a FUEL expense that existed before migration 0015 and so has no fuel load. Only that
    # migration sets it; the database refuses it on a new row and refuses false -> true (Enzo, R5-2). It
    # goes back to false when the expense gets its load.
    legacy_fuel_without_detail: Mapped[bool] = mapped_column(Boolean, server_default="false")


class FuelLoad(IdMixin, TimestampMixin, Base):
    """Operational detail of a FUEL expense, 1:1 (R5-2). The money is the expense's: its amount is
    round(liters x price per liter, 2), half up, computed by the backend and checked by the database; the
    load keeps no total of its own and no ledger legs. It is never deleted and has no state of its own: it
    is voided when its expense is."""

    __tablename__ = "fuel_loads"
    __table_args__ = (
        CheckConstraint("liters > 0", name="liters_positive"),
        CheckConstraint("price_per_liter > 0", name="price_positive"),
        CheckConstraint("odometer_km IS NULL OR odometer_km >= 0", name="odometer_not_negative"),
        {"schema": SCHEMA},
    )

    expense_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.expenses.id", ondelete="RESTRICT"), unique=True
    )
    vehicle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("vehicles.id", ondelete="RESTRICT"), index=True)
    # Who loaded the fuel: by default the settlement's driver. Not who typed it (`created_by`).
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    liters: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    price_per_liter: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    odometer_km: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))


class SettlementClosing(IdMixin, Base):
    """One close of one version of a settlement: its ledger operation and, after a reopening, the reversal
    of the previous close published with it. Append-only; rebuilds the history v1 -> close -> reopen ->
    v2 -> reversal + new close."""

    __tablename__ = "settlement_closings"
    __table_args__ = (
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint(f"kind IN ({_in(CLOSING_KINDS)})", name="kind"),
        CheckConstraint("(kind = 'FINANCIAL') = (operation_id IS NOT NULL)", name="operation_by_kind"),
        CheckConstraint("kind = 'FINANCIAL' OR snapshot IS NOT NULL", name="administrative_snapshot"),
        CheckConstraint(f"expected_source IN ({_in(EXPECTED_SOURCES)})", name="expected_source"),
        UniqueConstraint("settlement_id", "settlement_version", name="uq_settlement_closings_version"),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    settlement_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30), server_default="FINANCIAL")
    # The SETTLEMENT_CLOSE it published; NULL for an administrative zero close, which publishes nothing.
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    reversal_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    # Everything the close meant (totals, difference, allocations, legs): same version + other content
    # is an idempotency conflict, never the old close returned silently.
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    # What an administrative zero close certified (all its totals at $0); NULL for a financial close, whose
    # document is its ledger operation.
    snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # The expected amount this close was made against (R9-1): its amount, origin, event and, from Store Sys, its key.
    # NULL on closes made before R9-1, never backfilled; the database checks they are the settlement's at insert.
    expected_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    expected_source: Mapped[str | None] = mapped_column(String(20))
    expected_event_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.expected_amount_events.id", ondelete="RESTRICT")
    )
    expected_reference: Mapped[str | None] = mapped_column(String(100))
    expected_revision: Mapped[int | None] = mapped_column(Integer)
    closed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettlementReopening(IdMixin, Base):
    """One reopening of a closed settlement: from which version to which, the close that stayed in effect,
    why, who and when. Append-only; with `settlement_closings` it rebuilds the history without the audit log."""

    __tablename__ = "settlement_reopenings"
    __table_args__ = (
        CheckConstraint("to_version = from_version + 1", name="next_version"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        UniqueConstraint("settlement_id", "from_version", name="uq_settlement_reopenings_version"),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    from_version: Mapped[int] = mapped_column(Integer)
    to_version: Mapped[int] = mapped_column(Integer)
    # The close that stayed in effect while the settlement was corrected, and its SETTLEMENT_CLOSE (NULL when
    # that close was an administrative zero close, R7-2).
    closing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.settlement_closings.id", ondelete="RESTRICT")
    )
    close_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(Text)
    reopened_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    reopened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettlementDifference(IdMixin, Base):
    """Shortage or surplus of one version of a settlement, materialized when it closes with a difference.

    A new close after a reopening supersedes it; it is never deleted nor changed otherwise.
    """

    __tablename__ = "settlement_differences"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(DIFFERENCE_KINDS)})", name="kind"),
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint("settlement_id", "settlement_version", name="uq_settlement_differences_version"),
        Index(
            "uq_settlement_differences_current",
            "settlement_id",
            unique=True,
            postgresql_where=text("superseded_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    settlement_version: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(20))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    close_operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT")
    )
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))


class DifferenceAllocation(IdMixin, Base):
    """Who absorbs part of a difference: an employee (operator decides) or the company (admin, with a reason).

    The allocations of a difference add up exactly to it (deferred constraint trigger). Append-only.
    """

    __tablename__ = "difference_allocations"
    __table_args__ = (
        CheckConstraint(f"target IN ({_in(ALLOCATION_TARGETS)})", name="target"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("(target = 'EMPLOYEE') = (employee_id IS NOT NULL)", name="employee_target"),
        CheckConstraint(
            "target <> 'COMPANY' OR length(btrim(coalesce(reason, ''))) > 0", name="company_reason_required"
        ),
        Index(
            "uq_difference_allocations_employee",
            "difference_id",
            "employee_id",
            unique=True,
            postgresql_where=text("employee_id IS NOT NULL"),
        ),
        Index(
            "uq_difference_allocations_company",
            "difference_id",
            unique=True,
            postgresql_where=text("target = 'COMPANY'"),
        ),
        {"schema": SCHEMA},
    )

    difference_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.settlement_differences.id", ondelete="RESTRICT"), index=True
    )
    target: Mapped[str] = mapped_column(String(20))
    employee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reason: Mapped[str | None] = mapped_column(Text)
    decided_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettlementClosingFuelLoad(IdMixin, Base):
    """What one close of a settlement published for one fuel load (R5-4). Append-only.

    As with pendings (R3 decision 3) and cheques, the load is what the next close will publish and is
    edited in place during a correction; the fuel **in effect** is the row of the settlement's close in
    effect (`settlements.current_close_operation_id`). During a correction a load keeps its published
    liters, price, amount, vehicle and employee until the new close; a load added during it is not in
    effect yet; a load voided during it stays in effect until then. The rows of a close are exactly the
    loads of its settlement's expenses in effect when it was published (deferred trigger, migration 0017).
    """

    __tablename__ = "settlement_closing_fuel_loads"
    __table_args__ = (
        CheckConstraint("liters > 0", name="liters_positive"),
        CheckConstraint("price_per_liter > 0", name="price_positive"),
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint("close_operation_id", "fuel_load_id", name="uq_settlement_closing_fuel_loads_close_load"),
        {"schema": SCHEMA},
    )

    close_operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), index=True
    )
    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    settlement_version: Mapped[int] = mapped_column(Integer)
    fuel_load_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.fuel_loads.id", ondelete="RESTRICT"), index=True
    )
    expense_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.expenses.id", ondelete="RESTRICT"))
    vehicle_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("vehicles.id", ondelete="RESTRICT"), index=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    liters: Mapped[Decimal] = mapped_column(Numeric(10, 3))
    price_per_liter: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
