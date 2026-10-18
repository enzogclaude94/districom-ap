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
    func,
    text,
)
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


class EmployeeLoan(IdMixin, Base):
    """A loan to an employee (R8-3, ADR 010, Enzo's R8-3 decisions): interest-free, paid back in installments.

    The document of its own operation (`EMPLOYEE_LOAN`, source `employee_loan:<id>`): the cash box or bank that
    handed the money over -principal, the employee's internal account +principal, once. The schedule
    (`EmployeeLoanInstallment`) moves no money. Its state is derived, never stored: ACTIVE, or REVERSED once the
    whole loan was reversed (on its business date, every installment still scheduled cancelled). The database
    checks every rule (migration 0027).
    """

    __tablename__ = "employee_loans"
    __table_args__ = (
        CheckConstraint("principal > 0", name="principal_positive"),
        CheckConstraint("installment_count BETWEEN 1 AND 120", name="installment_count"),
        CheckConstraint("first_due_on >= business_date", name="first_due_on"),
        CheckConstraint(
            "description IS NULL OR (length(btrim(description)) > 0 AND length(description) <= 500)",
            name="description",
        ),
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

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    employee_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT")
    )
    business_date: Mapped[date] = mapped_column(Date)
    principal: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    # The active cash box or bank that handed the money over.
    funding_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT"), index=True
    )
    installment_count: Mapped[int]
    first_due_on: Mapped[date] = mapped_column(Date)
    description: Mapped[str | None] = mapped_column(Text)
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


class EmployeeLoanInstallment(IdMixin, Base):
    """One installment of a loan's schedule (R8-3). A schedule, never money: no installment has a ledger leg.

    SCHEDULED is in effect. DEFERRED is history: it keeps its original number, date and amount, says who deferred it,
    when and why, and is replaced by one or more new SCHEDULED installments (`deferred_from_id`) adding up to exactly
    its amount. CANCELLED: still scheduled when the whole loan was reversed. `number` is correlative within the
    loan, never reused nor changed. While the loan is in effect its SCHEDULED installments add up to its principal.
    The deferral's `client_request_id` and fingerprint live on the installment it deferred (idempotency).
    """

    __tablename__ = "employee_loan_installments"
    __table_args__ = (
        UniqueConstraint("loan_id", "number"),
        CheckConstraint("number > 0", name="number_positive"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("status IN ('SCHEDULED', 'DEFERRED', 'CANCELLED')", name="status"),
        CheckConstraint("deferred_from_id IS NULL OR deferred_from_id < id", name="deferred_from_earlier"),
        # Deferred: who, when, why and the request that did it, together; nothing of it otherwise.
        CheckConstraint(
            "(status = 'DEFERRED') = (deferred_by IS NOT NULL) AND (deferred_by IS NULL) = (deferred_at IS NULL) "
            "AND (deferred_at IS NULL) = (deferred_reason IS NULL) "
            "AND (deferred_reason IS NULL) = (deferral_request_id IS NULL) "
            "AND (deferral_request_id IS NULL) = (deferral_fingerprint IS NULL)",
            name="deferral_fields",
        ),
        CheckConstraint(
            "deferred_reason IS NULL OR length(btrim(deferred_reason)) > 0", name="deferred_reason_not_blank"
        ),
        CheckConstraint(
            "deferral_fingerprint IS NULL OR deferral_fingerprint ~ '^[0-9a-f]{64}$'",
            name="deferral_fingerprint_format",
        ),
        {"schema": SCHEMA},
    )

    # Indexed by the unique (loan_id, number).
    loan_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.employee_loans.id", ondelete="RESTRICT"))
    number: Mapped[int]
    due_on: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    status: Mapped[str] = mapped_column(String(10))
    # The DEFERRED installment of the same loan this one replaces (all or part of its amount).
    deferred_from_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_loan_installments.id", ondelete="RESTRICT"), index=True
    )
    deferred_reason: Mapped[str | None] = mapped_column(Text)
    deferred_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    deferred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    deferral_request_id: Mapped[UUID | None] = mapped_column(Uuid, unique=True)
    deferral_fingerprint: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
