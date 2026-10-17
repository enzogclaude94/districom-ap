"""Salary terms of an employee (R8-1, ADR 010).

`base_salary` is the **internal monthly nominal amount** DistriCom uses for its internal payroll. It is not
the legal gross nor net salary, not contributions, not a collective agreement and not an official payslip.
There is no frequency: the salary is monthly by definition (Enzo's D5; weekly money is an advance, R8-2).

A term covers `valid_from` to `valid_to`, both inclusive, `valid_to` NULL while it lasts; the terms of one
employee never overlap. History is never rewritten (migration 0025): a term is born open, for an employee
that exists and is active, may start in the future, and is ended once (the day, who, when and the reason);
nothing else ever changes and nothing is deleted. A salary change ends the term in effect and creates a new
one. Platform has no pay data (`public.employees` holds identity only), so nothing is duplicated.
"""

from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Numeric, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin

SCHEMA = "settlements"


class EmployeeCompensationTerm(IdMixin, Base):
    __tablename__ = "employee_compensation_terms"
    __table_args__ = (
        CheckConstraint("base_salary >= 0", name="base_salary_not_negative"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        # Open: no end at all. Ended: the day, who, when and a reason, together.
        CheckConstraint(
            "(valid_to IS NULL AND ended_by IS NULL AND ended_at IS NULL AND end_reason IS NULL)"
            " OR (valid_to IS NOT NULL AND ended_by IS NOT NULL AND ended_at IS NOT NULL"
            " AND length(btrim(end_reason)) > 0)",
            name="end_complete",
        ),
        ExcludeConstraint(
            ("employee_id", "="),
            (func.daterange(text("valid_from"), text("valid_to"), text("'[]'")), "&&"),
            name="ex_employee_compensation_terms_no_overlap",
            using="gist",
        ),
        {"schema": SCHEMA},
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    base_salary: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)


class EmployeeMovement(IdMixin, Base):
    """A consumption, an advance or an adjustment of an employee's internal account (R8-2, ADR 010).

    The document of its own operation (`EMPLOYEE_<kind>`, source `employee_movement:<id>`), which moves the
    employee's internal account by `employee_amount` (+ the employee owes it, - it is in the employee's favour)
    and one counterpart by the opposite: «Consumos de empleados», the cash box or bank that paid an advance, or
    «Ajustes a empleados». `employee_account_id` and `employee_amount` name that `EMPLOYEE` leg exactly, so the
    internal payroll (R8-4) can point at it. Published money never changes: a mistake is a reversal (the whole
    document, on its business date) plus a new movement. The database checks every rule (migration 0026).
    """

    __tablename__ = "employee_movements"
    __table_args__ = (
        CheckConstraint("kind IN ('CONSUMPTION', 'ADVANCE', 'ADJUSTMENT')", name="kind"),
        CheckConstraint(
            "(kind = 'ADJUSTMENT') = (direction IS NOT NULL)"
            " AND (direction IS NULL OR direction IN ('CHARGE', 'CREDIT'))",
            name="direction",
        ),
        CheckConstraint("(kind = 'ADVANCE') = (funding_account_id IS NOT NULL)", name="funding"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "employee_amount = CASE WHEN direction = 'CREDIT' THEN -amount ELSE amount END", name="employee_amount"
        ),
        CheckConstraint("length(btrim(description)) > 0 AND length(description) <= 500", name="description"),
        CheckConstraint("reference IS NULL OR length(btrim(reference)) > 0", name="reference_not_blank"),
        CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_by IS NULL) AND (reversed_by IS NULL) = (reversed_at IS NULL) "
            "AND (reversed_at IS NULL) = (reversal_reason IS NULL)",
            name="reversal_fields",
        ),
        CheckConstraint(
            "reversal_reason IS NULL OR length(btrim(reversal_reason)) > 0", name="reversal_reason_not_blank"
        ),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        {"schema": SCHEMA},
    )

    kind: Mapped[str] = mapped_column(String(20))
    # ADJUSTMENT only: CHARGE (the employee owes more) or CREDIT (in the employee's favour).
    direction: Mapped[str | None] = mapped_column(String(10))
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    employee_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT")
    )
    business_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    employee_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # ADVANCE only: the active cash box or bank that paid it.
    funding_account_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"), index=True
    )
    description: Mapped[str] = mapped_column(Text)
    reference: Mapped[str | None] = mapped_column(String(100))
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
