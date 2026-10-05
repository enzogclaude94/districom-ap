"""Ledger of values of Rendiciones: where every peso is and why it moved (ADR 007 §8, DATA_MODEL.md §13.1).

Only `ledger.service` writes `financial_operations` and `ledger_entries` (single writer, verified by
`tests/modules/settlements/test_ledger_single_writer.py`). Both tables are append-only and every
operation balances to zero; the database enforces it (migration 0005).
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.db.base import Base, IdMixin, TimestampMixin

SCHEMA = "settlements"

# Kinds grow phase by phase (cheque portfolio R4, pending receivables R3...).
ACCOUNT_KINDS = (
    "CASH_BOX",
    "BANK",
    "OWNER_EQUITY",
    "OPENING_BALANCE",
    "ADJUSTMENT",
    # R2: counterparts of a delivery settlement close, and each employee's internal account.
    "DELIVERY_COLLECTIONS",
    "EXPENSE",
    "SETTLEMENT_DIFFERENCE",
    "EMPLOYEE",
)
# Technical counter-accounts: exactly one of each.
TECHNICAL_KINDS = ("OPENING_BALANCE", "ADJUSTMENT", "DELIVERY_COLLECTIONS", "EXPENSE", "SETTLEMENT_DIFFERENCE")

OPERATION_TYPES = (
    "OPENING_BALANCE",
    "CASH_TRANSFER",
    "BANK_DEPOSIT",
    "BANK_WITHDRAWAL",
    "OWNER_CONTRIBUTION",
    "OWNER_WITHDRAWAL",
    "ADJUSTMENT",
    "SETTLEMENT_CLOSE",
    "REVERSAL",
)
# Operations whose description is the mandatory reason.
REASON_REQUIRED_TYPES = ("ADJUSTMENT", "REVERSAL")


_ACCENTS = str.maketrans("áàäâéèëêíìïîóòöôúùüû", "aaaaeeeeiiiioooouuuu")


def account_name_key(name: str) -> str:
    """Comparison key of an account name: case, accents and spacing do not make two accounts different.

    Migration 0006 computes the same key in SQL for existing rows.
    """
    return " ".join(name.split()).lower().translate(_ACCENTS)


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Account(IdMixin, TimestampMixin, Base):
    """A place or concept where value can be: a cash box, the bank, a partner's contributions.

    Few rows, not a chart of accounts. The balance is never stored: it is the sum of its entries.
    """

    __tablename__ = "accounts"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_in(ACCOUNT_KINDS)})", name="kind"),
        # Physical cash can never be negative, whatever the configuration says (Enzo, D5).
        CheckConstraint("kind <> 'CASH_BOX' OR NOT allow_negative", name="cash_box_not_negative"),
        # An internal account belongs to exactly one employee, and only internal accounts have one.
        CheckConstraint("(kind = 'EMPLOYEE') = (employee_id IS NOT NULL)", name="employee_link"),
        Index("uq_accounts_employee_id", "employee_id", unique=True, postgresql_where=text("employee_id IS NOT NULL")),
        Index("uq_accounts_code", "code", unique=True, postgresql_where=text("code IS NOT NULL")),
        Index(
            "uq_accounts_technical_kind",
            "kind",
            unique=True,
            postgresql_where=text(f"kind IN ({_in(TECHNICAL_KINDS)})"),
        ),
        {"schema": SCHEMA},
    )

    # Stable code of system accounts (seeded); NULL for accounts created by an admin.
    code: Mapped[str | None] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(120), unique=True)
    # Unique: "Juan Pérez" and "juan  perez" are the same partner, active or not (no duplicate accounts).
    name_key: Mapped[str] = mapped_column(String(120), unique=True)
    kind: Mapped[str] = mapped_column(String(30))
    allow_negative: Mapped[bool] = mapped_column(Boolean, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # EMPLOYEE accounts only (R2): positive balance = the employee owes the company, negative = the opposite.
    employee_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"))

    @validates("name")
    def _set_name_key(self, _: str, name: str) -> str:
        self.name_key = account_name_key(name)
        return name


class FinancialOperation(IdMixin, Base):
    """One economic fact. Immutable: corrections are a REVERSAL pointing at the original."""

    __tablename__ = "financial_operations"
    __table_args__ = (
        CheckConstraint(f"type IN ({_in(OPERATION_TYPES)})", name="type"),
        CheckConstraint("(type = 'REVERSAL') = (reverses_operation_id IS NOT NULL)", name="reversal_link"),
        CheckConstraint(
            f"type NOT IN ({_in(REASON_REQUIRED_TYPES)}) OR length(btrim(coalesce(description, ''))) > 0",
            name="reason_required",
        ),
        CheckConstraint("(source_type IS NULL) = (source_id IS NULL)", name="source_pair"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        # An operation is reversed at most once.
        Index(
            "uq_financial_operations_reverses_operation_id",
            "reverses_operation_id",
            unique=True,
            postgresql_where=text("reverses_operation_id IS NOT NULL"),
        ),
        Index("ix_financial_operations_source", "source_type", "source_id"),
        {"schema": SCHEMA},
    )

    type: Mapped[str] = mapped_column(String(30))
    business_date: Mapped[date] = mapped_column(Date, index=True)
    description: Mapped[str | None] = mapped_column(Text)
    # Idempotency key sent by the client; the fingerprint tells a retry from a reused key (ledger.service).
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    # Source document (settlement, payment, pending...) from R2 on; NULL when the operation is the document.
    source_type: Mapped[str | None] = mapped_column(String(40))
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    reverses_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.financial_operations.id", ondelete="RESTRICT")
    )
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="operation", lazy="selectin", order_by="LedgerEntry.id"
    )


class LedgerEntry(IdMixin, Base):
    """A signed, immutable leg: positive = value enters the account, negative = value leaves it."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_not_zero"),
        Index("ix_ledger_entries_account_id_operation_id", "account_id", "operation_id"),
        {"schema": SCHEMA},
    )

    operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.financial_operations.id", ondelete="RESTRICT"), index=True
    )
    account_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    operation: Mapped[FinancialOperation] = relationship(back_populates="entries")
