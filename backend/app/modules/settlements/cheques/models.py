"""Cheques: one row per real instrument the company receives (R4, DATA_MODEL.md §13.4).

Two dimensions that are never mixed (Enzo's R4 decisions):

- `kind`: PHYSICAL (paper) or ECHEQ (electronic);
- `payment_type`: COMMON (payable from its issue date) or DEFERRED (payable from `payment_date`).

The amount, the identity and the dates that make it payable belong to the instrument. While it had no
financial effect (a settlement still open, an ECHEQ not verified) every field is corrected with a
`CORRECTED` event; once any ledger leg names it, they never change (migration 0012): only descriptive
data (drawer, notes) is corrected, by an admin with a reason.

Who signed it (`drawer_*`, "Titular / librador") and who handed it to us (`delivered_by_name`, our
counterparty: who owes us if it bounces) are different people and stay apart.

Where a cheque is (portfolio, deposited...) is never stored here: the ledger says it, through the legs
that name the cheque (each leg's `cheque_id`). Its state is derived (`state.py`). What is stored is
what happened: the reception, an ECHEQ's verification decision (with history, like transfers), a void.

`cheque_events` is the cheque's domain history, append-only and independent from `audit_events`.
`settlement_closing_cheques` freezes, per close, which cheques that close put in the portfolio.
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

KINDS = ("PHYSICAL", "ECHEQ")
PAYMENT_TYPES = ("COMMON", "DEFERRED")
VERIFICATION_STATUSES = ("DECLARED", "VERIFIED", "VERIFICATION_REJECTED")
# R4-2 adds PENDING_RECEIPT (a pending collected with a cheque).
ORIGIN_TYPES = ("SETTLEMENT", "OPENING_BALANCE", "OWNER_CONTRIBUTION")
EVENT_TYPES = (
    "RECEIVED",
    "CORRECTED",
    "VERIFIED",
    "VERIFICATION_REJECTED",
    "VOIDED",
    # A close of its settlement put it in the portfolio, or a correction close took it out.
    "PUBLISHED",
    "WITHDRAWN",
    "DESCRIPTIVE_CORRECTED",
)
CLOSE_EVENT_TYPES = ("PUBLISHED", "WITHDRAWN")
REASON_EVENT_TYPES = ("VOIDED", "VERIFICATION_REJECTED", "DESCRIPTIVE_CORRECTED")

_SETTLEMENT_FK = f"{SCHEMA}.settlements.id"
_CHEQUE_FK = f"{SCHEMA}.cheques.id"
# An instrument blocks its identity unless it was voided or its verification was rejected.
_HOLDS_IDENTITY = "voided_at IS NULL AND coalesce(verification_status, '') <> 'VERIFICATION_REJECTED'"


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Cheque(IdMixin, TimestampMixin, Base):
    __tablename__ = "cheques"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(KINDS)})", name="kind"),
        CheckConstraint(f"payment_type IN ({_in(PAYMENT_TYPES)})", name="payment_type"),
        CheckConstraint(f"origin_type IN ({_in(ORIGIN_TYPES)})", name="origin_type"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("cheque_number ~ '^[0-9]{1,20}$'", name="number_digits"),
        CheckConstraint("branch_code IS NULL OR branch_code ~ '^[0-9]{1,10}$'", name="branch_digits"),
        CheckConstraint("account_ref IS NULL OR account_ref ~ '^[0-9A-Z]{1,34}$'", name="account_format"),
        CheckConstraint("drawer_tax_id IS NULL OR drawer_tax_id ~ '^[0-9]{11}$'", name="drawer_tax_id_format"),
        CheckConstraint("length(btrim(drawer_name)) > 0", name="drawer_not_blank"),
        CheckConstraint("length(btrim(delivered_by_name)) > 0", name="delivered_by_not_blank"),
        # A deferred cheque is payable from its payment date, later than its issue; a common one has none.
        CheckConstraint(
            "(payment_type = 'DEFERRED') = (payment_date IS NOT NULL) AND "
            "(payment_date IS NULL OR (payment_date > issue_date AND payment_date <= issue_date + 360))",
            name="payment_date_by_type",
        ),
        CheckConstraint("issue_date <= received_business_date", name="issued_before_received"),
        # A paper cheque is received only when someone confirms having it in hand (Enzo's R4-D3).
        CheckConstraint(
            "(kind = 'PHYSICAL') = "
            "(physical_receipt_confirmed_by IS NOT NULL AND physical_receipt_confirmed_at IS NOT NULL)",
            name="physical_receipt",
        ),
        CheckConstraint(
            f"(kind = 'ECHEQ') = (verification_status IS NOT NULL) AND "
            f"(verification_status IS NULL OR verification_status IN ({_in(VERIFICATION_STATUSES)}))",
            name="verification_by_kind",
        ),
        CheckConstraint("kind = 'ECHEQ' OR echeq_external_id IS NULL", name="external_id_only_echeq"),
        CheckConstraint("kind = 'PHYSICAL' OR drawer_tax_id IS NOT NULL", name="echeq_drawer_tax_id"),
        CheckConstraint("(origin_type = 'SETTLEMENT') = (settlement_id IS NOT NULL)", name="settlement_origin"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) OR "
            "(voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(btrim(coalesce(void_reason, ''))) > 0)",
            name="void_complete",
        ),
        Index(
            "uq_cheques_instrument_key",
            "instrument_key",
            unique=True,
            postgresql_where=text(f"instrument_key IS NOT NULL AND {_HOLDS_IDENTITY}"),
        ),
        Index(
            "uq_cheques_echeq_external_id",
            "echeq_external_id",
            unique=True,
            postgresql_where=text(f"echeq_external_id IS NOT NULL AND {_HOLDS_IDENTITY}"),
        ),
        Index("ix_cheques_bank_number", "issuer_bank_id", "cheque_number"),
        {"schema": SCHEMA},
    )

    kind: Mapped[str] = mapped_column(String(10))
    payment_type: Mapped[str] = mapped_column(String(10))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # --- identity of the instrument (computed and checked by `identity.py`) ---
    issuer_bank_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("financial_institutions.id", ondelete="RESTRICT")
    )
    branch_code: Mapped[str | None] = mapped_column(String(10))
    # Account the cheque is drawn on: an account number or a CBU, upper-case letters and digits only.
    account_ref: Mapped[str | None] = mapped_column(String(34))
    cheque_number: Mapped[str] = mapped_column(String(20))
    # The ECHEQ's own identifier, when the bank shows it (Enzo's R4 decision D-H).
    echeq_external_id: Mapped[str | None] = mapped_column(String(64))
    # Key of the instrument from its bank data, when complete enough to identify it.
    instrument_key: Mapped[str | None] = mapped_column(String(200))
    # --- dates of the instrument ---
    issue_date: Mapped[date] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    # --- people ---
    drawer_name: Mapped[str] = mapped_column(String(200))
    drawer_tax_id: Mapped[str | None] = mapped_column(String(11))
    # Our counterparty (customer as text until `public.customers` exists, like pending collections).
    delivered_by_name: Mapped[str] = mapped_column(String(200))
    # --- how it came in ---
    origin_type: Mapped[str] = mapped_column(String(30))
    settlement_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True
    )
    received_business_date: Mapped[date] = mapped_column(Date)
    received_by_employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT")
    )
    physical_receipt_confirmed_by: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("users.id", ondelete="RESTRICT")
    )
    physical_receipt_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # ECHEQ only: the current verification decision; every decision is a `cheque_events` row.
    verification_status: Mapped[str | None] = mapped_column(String(30))
    verification_changed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    verification_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_reason: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    void_reason: Mapped[str | None] = mapped_column(Text)


class ChequeEvent(IdMixin, Base):
    """One thing that happened to a cheque. Append-only (migration 0012)."""

    __tablename__ = "cheque_events"
    __table_args__ = (
        CheckConstraint(f"event_type IN ({_in(EVENT_TYPES)})", name="event_type"),
        # PUBLISHED / WITHDRAWN name what did it: a close of its settlement or an administrative entry.
        CheckConstraint(
            f"(close_operation_id IS NULL OR event_type IN ({_in(CLOSE_EVENT_TYPES)})) AND "
            f"(event_type NOT IN ({_in(CLOSE_EVENT_TYPES)}) OR num_nonnulls(close_operation_id, operation_id) = 1)",
            name="close_events",
        ),
        CheckConstraint(
            f"event_type NOT IN ({_in(REASON_EVENT_TYPES)}) OR length(btrim(coalesce(reason, ''))) > 0",
            name="reason_required",
        ),
        CheckConstraint("(settlement_id IS NULL) = (settlement_version IS NULL)", name="settlement_pair"),
        {"schema": SCHEMA},
    )

    cheque_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_CHEQUE_FK, ondelete="RESTRICT"), index=True)
    event_type: Mapped[str] = mapped_column(String(30))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    settlement_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"))
    settlement_version: Mapped[int | None] = mapped_column(Integer)
    close_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT")
    )
    # The operation that put it in the portfolio outside a settlement (opening balance, contribution).
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT")
    )
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettlementClosingCheque(IdMixin, Base):
    """Which cheques one close of a settlement put in the portfolio, and for how much. Append-only.

    The instrument is immutable once published, so the amount never differs from the cheque's; the row
    exists for membership: a correction close may add or withdraw cheques, and each close must say
    exactly which ones justified it.
    """

    __tablename__ = "settlement_closing_cheques"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint("close_operation_id", "cheque_id", name="uq_settlement_closing_cheques_close_cheque"),
        {"schema": SCHEMA},
    )

    close_operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), index=True
    )
    settlement_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_SETTLEMENT_FK, ondelete="RESTRICT"), index=True)
    settlement_version: Mapped[int] = mapped_column(Integer)
    cheque_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(_CHEQUE_FK, ondelete="RESTRICT"), index=True)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
