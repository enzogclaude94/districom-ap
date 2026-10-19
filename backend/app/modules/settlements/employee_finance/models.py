"""Models of Rendiciones > Empleados (R8, ADR 010): salary terms, movements, loans and internal payrolls.

Salary terms of an employee (R8-1, ADR 010).

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
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
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


class EmployeePayroll(IdMixin, Base):
    """An internal payroll of one employee for a period (R8-4, ADR 010 §4-§8, Enzo's R8-4 decisions).

    «Liquidación interna», never a legal payslip. `kind`: REGULAR (the employee's normal cycle; two REGULAR of the same
    employee never overlap unless one is CANCELLED) or EXTRAORDINARY (may overlap; never applies loan installments).
    The period is inclusive; there is no frequency column (the salary is monthly by policy). `payment_date` is
    informative.

    `status` is the editing state: OPEN (a draft, or a correction while `current_closing_id` still names the close in
    effect), CLOSED, or CANCELLED (only a payroll never closed). `version` grows with each reopening; each close
    freezes one version (`EmployeePayrollClosing`). The draft (lines and choices) is editable only while OPEN; what a
    close froze never changes. The database checks every rule (migration 0028).
    """

    __tablename__ = "employee_payrolls"
    __table_args__ = (
        CheckConstraint("kind IN ('REGULAR', 'EXTRAORDINARY')", name="kind"),
        CheckConstraint("period_start <= period_end", name="period"),
        CheckConstraint("status IN ('OPEN', 'CLOSED', 'CANCELLED')", name="status"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("status <> 'CLOSED' OR current_closing_id IS NOT NULL", name="closed_has_closing"),
        CheckConstraint("status <> 'CANCELLED' OR current_closing_id IS NULL", name="cancelled_never_closed"),
        CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_by IS NOT NULL) AND (cancelled_by IS NULL) = (cancelled_at IS NULL) "
            "AND (cancelled_at IS NULL) = (cancel_reason IS NULL)",
            name="cancel_fields",
        ),
        CheckConstraint("cancel_reason IS NULL OR length(btrim(cancel_reason)) > 0", name="cancel_reason_not_blank"),
        CheckConstraint("notes IS NULL OR length(btrim(notes)) > 0", name="notes_not_blank"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        ExcludeConstraint(
            ("employee_id", "="),
            (func.daterange(text("period_start"), text("period_end"), text("'[]'")), "&&"),
            name="ex_employee_payrolls_regular_no_overlap",
            using="gist",
            where=text("kind = 'REGULAR' AND status <> 'CANCELLED'"),
        ),
        {"schema": SCHEMA},
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    employee_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(15))
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    payment_date: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(10))
    version: Mapped[int]
    # The close in effect: the last one, also while a correction (OPEN, version + 1) is being prepared.
    current_closing_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            f"{SCHEMA}.employee_payroll_closings.id",
            ondelete="RESTRICT",
            use_alter=True,
            deferrable=True,
            initially="DEFERRED",
        ),
        unique=True,
    )
    cancelled_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_reason: Mapped[str | None] = mapped_column(Text)
    client_request_id: Mapped[UUID] = mapped_column(Uuid, unique=True)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


PAYROLL_LINE_KINDS = "'SALARY', 'BONUS', 'INCENTIVE', 'OTHER_CREDIT'"


class EmployeePayrollLine(IdMixin, Base):
    """A credit of the draft (haber): SALARY, BONUS, INCENTIVE or OTHER_CREDIT (R8-4).

    Editable (added, changed, removed) only while its payroll is OPEN; a close copies it into
    `EmployeePayrollClosingLine`, which never changes. A SALARY proposed from the salary term in effect names it
    (`compensation_term_id`); one typed by hand (a partial period, a change of salary) carries a note. An INCENTIVE is
    MANUAL in R8 (nothing is read from Comercial). OTHER_CREDIT needs a description.
    """

    __tablename__ = "employee_payroll_lines"
    __table_args__ = (
        CheckConstraint(f"kind IN ({PAYROLL_LINE_KINDS})", name="kind"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("source IN ('TERM', 'MANUAL')", name="source"),
        CheckConstraint("(source = 'TERM') = (compensation_term_id IS NOT NULL)", name="term_source"),
        CheckConstraint("source = 'MANUAL' OR kind = 'SALARY'", name="term_only_salary"),
        CheckConstraint("description IS NULL OR length(btrim(description)) > 0", name="description_not_blank"),
        CheckConstraint("description IS NULL OR length(description) <= 500", name="description_length"),
        CheckConstraint("kind <> 'OTHER_CREDIT' OR description IS NOT NULL", name="other_credit_described"),
        CheckConstraint(
            "kind <> 'SALARY' OR source = 'TERM' OR description IS NOT NULL", name="manual_salary_explained"
        ),
        {"schema": SCHEMA},
    )

    payroll_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payrolls.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(15))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10))
    compensation_term_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_compensation_terms.id", ondelete="RESTRICT")
    )
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeePayrollChoice(IdMixin, Base):
    """What the preparer decided about one item of the draft (R8-4): a leg of the internal account, or a loan
    installment. Without a choice, a REGULAR payroll applies what it proposes in full (an EXTRAORDINARY one proposes
    nothing). `included` false leaves the item out; `amount` (positive, absolute) applies part of a leg; an installment
    is always whole. A choice is a draft: editable only while its payroll is OPEN, never an application. The close turns
    the effective selection into `EmployeePayrollApplication` rows.
    """

    __tablename__ = "employee_payroll_choices"
    __table_args__ = (
        CheckConstraint("(ledger_entry_id IS NULL) <> (installment_id IS NULL)", name="one_item"),
        CheckConstraint("amount IS NULL OR amount > 0", name="amount_positive"),
        CheckConstraint("installment_id IS NULL OR amount IS NULL", name="installment_whole"),
        CheckConstraint("included OR amount IS NULL", name="excluded_without_amount"),
        Index(
            "uq_employee_payroll_choices_entry",
            "payroll_id",
            "ledger_entry_id",
            unique=True,
            postgresql_where=text("ledger_entry_id IS NOT NULL"),
        ),
        Index(
            "uq_employee_payroll_choices_installment",
            "payroll_id",
            "installment_id",
            unique=True,
            postgresql_where=text("installment_id IS NOT NULL"),
        ),
        {"schema": SCHEMA},
    )

    payroll_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payrolls.id", ondelete="RESTRICT")
    )
    ledger_entry_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.ledger_entries.id", ondelete="RESTRICT")
    )
    installment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_loan_installments.id", ondelete="RESTRICT")
    )
    included: Mapped[bool]
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    updated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EmployeePayrollClosing(IdMixin, Base):
    """One close of a payroll version (R8-4): what it accrued and applied, frozen.

    FINANCIAL: credits (haberes) > 0, published as a PAYROLL_CLOSE (`operation_id`). NO_ACCRUAL: no credits, no
    operation and no zero legs, but it may still apply legs and installments and have a net > 0 (Enzo's Q1).
    `reversal_operation_id` is the reversal of the previous close's operation this close replaced or withdrew. Net =
    credits - deductions + credits applied, never negative. The rows (`EmployeePayrollClosingLine`,
    `EmployeePayrollApplication`) carry the invariants; `snapshot` is the readable copy. Never changes, except that a
    later close supersedes it once (who and when).
    """

    __tablename__ = "employee_payroll_closings"
    __table_args__ = (
        UniqueConstraint("payroll_id", "payroll_version"),
        CheckConstraint("kind IN ('FINANCIAL', 'NO_ACCRUAL')", name="kind"),
        CheckConstraint(
            "(kind = 'FINANCIAL' AND operation_id IS NOT NULL AND credits > 0)"
            " OR (kind = 'NO_ACCRUAL' AND operation_id IS NULL AND credits = 0)",
            name="kind_operation",
        ),
        CheckConstraint("credits >= 0 AND deductions >= 0 AND credits_applied >= 0", name="totals_not_negative"),
        CheckConstraint("net = credits - deductions + credits_applied", name="net_formula"),
        CheckConstraint("net >= 0", name="net_not_negative"),
        CheckConstraint("(superseded_at IS NULL) = (superseded_by IS NULL)", name="superseded_fields"),
        CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name="fingerprint_format"),
        {"schema": SCHEMA},
    )

    # Indexed by the unique (payroll_id, payroll_version).
    payroll_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payrolls.id", ondelete="RESTRICT")
    )
    payroll_version: Mapped[int]
    kind: Mapped[str] = mapped_column(String(15))
    operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    reversal_operation_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("settlements.financial_operations.id", ondelete="RESTRICT"), unique=True
    )
    credits: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    deductions: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    credits_applied: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    net: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB)
    request_fingerprint: Mapped[str] = mapped_column(String(64))
    closed_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    superseded_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmployeePayrollClosingLine(IdMixin, Base):
    """A credit (haber) as a close froze it (R8-4). Never changes."""

    __tablename__ = "employee_payroll_closing_lines"
    __table_args__ = (
        CheckConstraint(f"kind IN ({PAYROLL_LINE_KINDS})", name="kind"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("source IN ('TERM', 'MANUAL')", name="source"),
        CheckConstraint("(source = 'TERM') = (compensation_term_id IS NOT NULL)", name="term_source"),
        {"schema": SCHEMA},
    )

    closing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payroll_closings.id", ondelete="RESTRICT"), index=True
    )
    kind: Mapped[str] = mapped_column(String(15))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    description: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10))
    compensation_term_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_compensation_terms.id", ondelete="RESTRICT")
    )


class EmployeePayrollApplication(IdMixin, Base):
    """What a close applied of one leg of the internal account (R8-4, ADR 010 §7): never a copy of the movement.

    `amount` has the sign of the leg (+ a deduction: the employee owed it; - a credit in the employee's favour) and
    0 < |amount| <= what was still applicable of the leg. A loan installment is applied whole, on the loan's leg
    (`installment_id`, |amount| = the installment). An application is in effect while its close is
    (`superseded_at` NULL); a later close of the same payroll supersedes it, once. Never changes otherwise.
    """

    __tablename__ = "employee_payroll_applications"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="amount_not_zero"),
        Index(
            "uq_employee_payroll_applications_installment",
            "installment_id",
            unique=True,
            postgresql_where=text("installment_id IS NOT NULL AND superseded_at IS NULL"),
        ),
        Index(
            "uq_employee_payroll_applications_closing_entry",
            "closing_id",
            "ledger_entry_id",
            unique=True,
            postgresql_where=text("installment_id IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    closing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payroll_closings.id", ondelete="RESTRICT"), index=True
    )
    payroll_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payrolls.id", ondelete="RESTRICT")
    )
    employee_account_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.accounts.id", ondelete="RESTRICT")
    )
    ledger_entry_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("settlements.ledger_entries.id", ondelete="RESTRICT"), index=True
    )
    installment_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_loan_installments.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EmployeePayrollReopening(IdMixin, Base):
    """A reopening of a closed payroll for correction (R8-4): admin, with a reason. The close stays in effect."""

    __tablename__ = "employee_payroll_reopenings"
    __table_args__ = (
        UniqueConstraint("payroll_id", "from_version"),
        CheckConstraint("to_version = from_version + 1", name="next_version"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        {"schema": SCHEMA},
    )

    payroll_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payrolls.id", ondelete="RESTRICT")
    )
    from_version: Mapped[int]
    to_version: Mapped[int]
    closing_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.employee_payroll_closings.id", ondelete="RESTRICT")
    )
    reason: Mapped[str] = mapped_column(Text)
    reopened_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    reopened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
