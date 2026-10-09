"""Payments made to third parties (R6, Enzo's approved proposal): what left a cash box or the bank, to whom,
and why. Never accounts payable: there is no debt, invoice or balance owed here, only payments made.

A payment is the document of its own `PAYMENT` operation. Its items are the means it was paid with
(several from the start): each CASH or TRANSFER item names one cash box or bank account, at most once per
payment; a CHEQUE item will name one cheque of the portfolio (R6-2, not enabled in R6-1: the database
refuses it). The amount is exactly the sum of the items, and the operation is exactly the items leaving
plus one counter-leg for the whole amount, in the technical account of the concept (migration 0019).

Concepts are fixed, not a table (MERCHANDISE, EXPENSE, REFUND, OTHER). Their counter-accounts only
classify where the money went: they are not accounts payable, debt, valued inventory nor accrued purchases.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

SCHEMA = "settlements"

CONCEPTS = ("MERCHANDISE", "EXPENSE", "REFUND", "OTHER")
METHODS = ("CASH", "TRANSFER", "CHEQUE")
# The code of the technical account each concept's counter-leg goes to (Enzo's R6 decision D1).
COUNTER_ACCOUNTS = {
    "MERCHANDISE": "merchandise_payments",
    "EXPENSE": "expense",
    "REFUND": "third_party_refunds",
    "OTHER": "other_payments",
}
REFERENCE_LENGTH = 100


def _in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class Payment(IdMixin, TimestampMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint(f"concept IN ({_in(CONCEPTS)})", name="concept"),
        CheckConstraint("amount > 0", name="amount_positive"),
        # EXPENSE carries its expense category; no other concept has one.
        CheckConstraint("(concept = 'EXPENSE') = (expense_category_id IS NOT NULL)", name="expense_category"),
        # OTHER always says what it was (Enzo's R6 decision D3).
        CheckConstraint("concept <> 'OTHER' OR length(btrim(coalesce(description, ''))) > 0", name="other_description"),
        # A payment is reversed with all of its data at once.
        CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_at IS NULL) AND (reversed_at IS NULL) = (reversed_by IS NULL)"
            " AND (reversed_by IS NULL) = (reversal_reason IS NULL)",
            name="reversal_fields",
        ),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        {"schema": SCHEMA},
    )

    third_party_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("third_parties.id", ondelete="RESTRICT"), index=True
    )
    business_date: Mapped[date] = mapped_column(Date, index=True)
    concept: Mapped[str] = mapped_column(String(20))
    expense_category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.expense_categories.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reference: Mapped[str | None] = mapped_column(String(REFERENCE_LENGTH))
    description: Mapped[str | None] = mapped_column(Text)
    # Set in the creating transaction, right after the operation is published (checked at commit).
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    reversal_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    reversed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    reversed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reversal_reason: Mapped[str | None] = mapped_column(Text)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))


class PaymentItem(IdMixin, TimestampMixin, Base):
    __tablename__ = "payment_items"
    __table_args__ = (
        CheckConstraint(f"method IN ({_in(METHODS)})", name="method"),
        CheckConstraint("amount > 0", name="amount_positive"),
        # CASH and TRANSFER leave an account; a CHEQUE item is one cheque of the portfolio.
        CheckConstraint(
            "(method IN ('CASH', 'TRANSFER') AND account_id IS NOT NULL AND cheque_id IS NULL) OR "
            "(method = 'CHEQUE' AND cheque_id IS NOT NULL AND account_id IS NULL)",
            name="method_fields",
        ),
        # At most one item per cash box or bank account, and per cheque, in a payment (Enzo's precision 1).
        UniqueConstraint("payment_id", "account_id"),
        UniqueConstraint("payment_id", "cheque_id"),
        {"schema": SCHEMA},
    )

    payment_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.payments.id", ondelete="RESTRICT"), index=True
    )
    method: Mapped[str] = mapped_column(String(20))
    account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"), index=True
    )
    cheque_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.cheques.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    reference: Mapped[str | None] = mapped_column(String(REFERENCE_LENGTH))
