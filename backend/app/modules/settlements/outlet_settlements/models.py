"""Remittances of a boca de expendio (R7-3, ADR 009, DATA_MODEL.md §13.7.2).

A remittance is cash a boca hands over to a cash box during the day, before its settlement closes. It is the
document of its own `OUTLET_REMITTANCE` operation («Entregas a cuenta — {Boca}» -X, the cash box +X). Its money
never changes and it is never deleted: a mistake is a reversal (admin, with a reason) and, if needed, a new
remittance. The close of the settlement consolidates the remittances in effect, and
`settlement_closing_remittances` freezes which ones each close consolidated (migration 0024).
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
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin

SCHEMA = "settlements"


class OutletRemittance(IdMixin, Base):
    """Cash a boca handed over to a cash box. `outlet_id` and `business_date` are those of its settlement (the
    database checks it); `delivered_at` is when it happened, `created_at` when it was recorded."""

    __tablename__ = "outlet_remittances"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        CheckConstraint("reference IS NULL OR length(btrim(reference)) > 0", name="reference_not_blank"),
        CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_by IS NULL) AND (reversed_by IS NULL) = (reversed_at IS NULL) "
            "AND (reversed_at IS NULL) = (reversal_reason IS NULL)",
            name="reversal_fields",
        ),
        CheckConstraint(
            "reversal_reason IS NULL OR length(btrim(reversal_reason)) > 0", name="reversal_reason_not_blank"
        ),
        {"schema": SCHEMA},
    )

    settlement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.settlements.id", ondelete="RESTRICT"), index=True
    )
    outlet_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("outlets.id", ondelete="RESTRICT"), index=True)
    business_date: Mapped[date] = mapped_column(Date)
    delivered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    destination_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"), index=True
    )
    # Who carried the cash (optional): a fact of that day, never a split of a difference.
    delivered_by_employee_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    reference: Mapped[str | None] = mapped_column(String(100))
    # Set in the creating transaction; the database requires it at commit.
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SettlementClosingRemittance(IdMixin, Base):
    """A remittance one close consolidated, for its amount. Append-only.

    As with pendings and cheques, the remittances **consolidated in effect** are the rows of the settlement's close
    in effect: a remittance reversed during a correction stays consolidated until the new close; one added during
    it is not consolidated until then.
    """

    __tablename__ = "settlement_closing_remittances"
    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        UniqueConstraint(
            "close_operation_id", "remittance_id", name="uq_settlement_closing_remittances_close_remittance"
        ),
        {"schema": SCHEMA},
    )

    close_operation_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), index=True
    )
    settlement_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.settlements.id", ondelete="RESTRICT"), index=True
    )
    settlement_version: Mapped[int] = mapped_column(Integer)
    remittance_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.outlet_remittances.id", ondelete="RESTRICT"), index=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
