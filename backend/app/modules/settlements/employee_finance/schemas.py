"""Requests and responses of Rendiciones > Empleados (R8-1). Amounts are decimal strings ("1500.00")."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.settlements.cash.schemas import Amount, Money, Reason

# Sueldo base interno (mensual): >= 0, never negative.
Salary = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]
# How the internal balance reads: > 0 the employee owes the company, < 0 the company owes the employee.
BalanceReading = Literal["OWES_COMPANY", "COMPANY_OWES", "NONE"]
TermStatus = Literal["CURRENT", "FUTURE", "PAST"]


class CompensationTermIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_from: date
    base_salary: Salary
    notes: str | None = Field(default=None, max_length=1000)


class CompensationTermEndIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_to: date
    reason: Reason


class CompensationTermOut(BaseModel):
    id: int
    employee_id: int
    valid_from: date
    valid_to: date | None  # inclusive; None while it lasts
    base_salary: Money
    notes: str | None
    status: TermStatus  # against today's business date
    created_at: datetime
    created_by_name: str
    ended_at: datetime | None
    ended_by_name: str | None
    end_reason: str | None


class EmployeeFinanceRow(BaseModel):
    employee_id: int
    full_name: str
    active: bool
    balance: Money
    balance_reading: BalanceReading
    current_term: CompensationTermOut | None
    next_term: CompensationTermOut | None


class EmployeeFinancePage(BaseModel):
    items: list[EmployeeFinanceRow]
    total: int


class EmployeeFinanceDetail(EmployeeFinanceRow):
    hire_date: date | None
    has_account: bool  # the internal account is created the first time something is assigned (R2-D6)


# Where a movement of the internal account comes from: R2-R7 and the movements of R8-2.
Origin = Literal[
    "SETTLEMENT_SHORTAGE",  # faltante de una rendición asignado al empleado
    "SETTLEMENT_SURPLUS",  # sobrante de una rendición asignado al empleado
    "SETTLEMENT_CORRECTION",  # reverso de un cierre de rendición al corregirla
    "PENDING_WRITE_OFF",  # parte de un pendiente incobrable a cargo del empleado
    "PENDING_WRITE_OFF_REVERSAL",  # reverso de esa resolución
    "CONSUMPTION",  # consumo (R8-2)
    "ADVANCE",  # adelanto (R8-2)
    "ADJUSTMENT_CHARGE",  # ajuste en contra (R8-2)
    "ADJUSTMENT_CREDIT",  # ajuste a favor (R8-2)
    "CONSUMPTION_REVERSAL",
    "ADVANCE_REVERSAL",
    "ADJUSTMENT_REVERSAL",
    "LOAN",  # préstamo (R8-3)
    "LOAN_REVERSAL",
    "PAYROLL",  # haberes devengados por una liquidación interna (R8-4)
    "PAYROLL_CORRECTION",  # reverso de esos haberes al corregir la liquidación
    "OTHER",
]
DocumentKind = Literal[
    "DELIVERY_SETTLEMENT",
    "OUTLET_SETTLEMENT",
    "PENDING_COLLECTION",
    "EMPLOYEE_MOVEMENT",
    "EMPLOYEE_LOAN",
    "EMPLOYEE_PAYROLL",
]


class AccountMovementOut(BaseModel):
    entry_id: int
    operation_id: int
    business_date: date
    amount: Money  # signed: + the employee owes it, - it is in the employee's favour
    reading: Literal["CHARGE", "CREDIT"]
    origin: Origin
    document_kind: DocumentKind | None
    document_id: int | None
    document_date: date | None
    document_number: int | None  # settlement sequence of its day
    # Employee movements (R8-2) and loans (R8-3): what it was and whether the whole document was reversed since.
    description: str | None = None
    document_reversed: bool | None = None
    # What the closes in effect of internal payrolls applied of it (absolute, R8-4).
    applied: Money = Decimal("0.00")
    created_at: datetime


class AccountMovementPage(BaseModel):
    items: list[AccountMovementOut]
    total: int


# --- consumptions, advances and adjustments (R8-2) ---------------------------------------------------

MovementKind = Literal["CONSUMPTION", "ADVANCE", "ADJUSTMENT"]
Direction = Literal["CHARGE", "CREDIT"]
Description = Annotated[str, Field(min_length=1, max_length=500)]
Reference = Annotated[str | None, Field(default=None, max_length=100)]


class _MovementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Generated once per user action and resent on retries.
    client_request_id: UUID
    business_date: date
    amount: Amount
    # What it was (a consumption, an advance) or, for an adjustment, its mandatory reason.
    description: Description
    reference: Reference = None


class ConsumptionIn(_MovementIn):
    pass


class AdvanceIn(_MovementIn):
    funding_account_id: int  # an active cash box or bank


class AdjustmentIn(_MovementIn):
    direction: Direction


class MovementPreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: MovementKind
    business_date: date
    amount: Amount
    direction: Direction | None = None
    funding_account_id: int | None = None


class MovementLegOut(BaseModel):
    account_id: int | None  # None: the internal account, created with its first movement
    account_name: str
    account_kind: str
    amount: Money


class MovementPreviewOut(BaseModel):
    kind: MovementKind
    direction: Direction | None
    amount: Money
    employee_amount: Money  # + the employee will owe it, - in the employee's favour
    balance_before: Money
    balance_after: Money
    legs: list[MovementLegOut]


class MovementReversalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    reason: Reason


class EmployeeMovementOut(BaseModel):
    id: int
    kind: MovementKind
    direction: Direction | None
    employee_id: int
    employee_name: str
    business_date: date
    amount: Money
    employee_amount: Money
    funding_account_id: int | None
    funding_account_name: str | None
    description: str
    reference: str | None
    status: Literal["IN_EFFECT", "REVERSED"]
    operation_id: int
    reversal_operation_id: int | None
    reversed_at: datetime | None
    reversed_by_name: str | None
    reversal_reason: str | None
    created_at: datetime
    created_by_name: str
    legs: list[MovementLegOut]


# --- loans, installments and deferrals (R8-3) --------------------------------------------------------

InstallmentStatus = Literal["SCHEDULED", "DEFERRED", "CANCELLED"]
InstallmentCount = Annotated[int, Field(ge=1, le=120)]


class LoanInstallmentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    due_on: date
    amount: Amount


class _LoanTermsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_date: date
    principal: Amount
    funding_account_id: int  # an active cash box or bank
    installment_count: InstallmentCount
    first_due_on: date
    # The schedule as confirmed (dates and amounts maybe edited); None: the default one (equal, monthly).
    installments: Annotated[list[LoanInstallmentIn] | None, Field(default=None, max_length=120)] = None


class LoanPreviewIn(_LoanTermsIn):
    pass


class LoanIn(_LoanTermsIn):
    # Generated once per user action and resent on retries.
    client_request_id: UUID
    description: Annotated[str | None, Field(default=None, max_length=500)] = None
    reference: Reference = None


class ScheduledInstallmentOut(BaseModel):
    number: int
    due_on: date
    amount: Money


class LoanPreviewOut(BaseModel):
    principal: Money
    business_date: date
    funding_account_id: int
    funding_account_name: str
    balance_before: Money  # internal account
    balance_after: Money
    legs: list[MovementLegOut]
    installments: list[ScheduledInstallmentOut]
    total: Money  # what the installments add up to: always the principal


class LoanInstallmentOut(BaseModel):
    id: int
    number: int
    due_on: date
    amount: Money
    status: InstallmentStatus
    # SCHEDULED and applied by a close in effect («Aplicada»), or not yet («Pendiente de aplicar»): derived, never
    # stored (R8-4). The payroll that applied it, when it did.
    applied: bool
    applied_payroll_id: int | None
    # The DEFERRED installment this one replaces (all or part of it).
    deferred_from_id: int | None
    deferred_from_number: int | None
    # DEFERRED: who, when, why, and what replaced it.
    deferred_reason: str | None
    deferred_at: datetime | None
    deferred_by_name: str | None
    replaced_by: list[ScheduledInstallmentOut]
    created_at: datetime


class EmployeeLoanRow(BaseModel):
    id: int
    business_date: date
    principal: Money
    installment_count: int
    status: Literal["ACTIVE", "REVERSED"]
    balance: Money  # principal minus the installments a close in effect applied; 0 reversed
    applied_total: Money
    # SCHEDULED installments still to apply, and the first one due.
    scheduled_count: int
    next_due_on: date | None
    description: str | None


class EmployeeLoanOut(EmployeeLoanRow):
    employee_id: int
    employee_name: str
    funding_account_id: int
    funding_account_name: str
    first_due_on: date
    reference: str | None
    operation_id: int
    reversal_operation_id: int | None
    reversed_at: datetime | None
    reversed_by_name: str | None
    reversal_reason: str | None
    created_at: datetime
    created_by_name: str
    legs: list[MovementLegOut]
    installments: list[LoanInstallmentOut]  # every one, by number: in effect, deferred (history) and cancelled


class LoanDeferralIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    reason: Reason
    replacements: Annotated[list[LoanInstallmentIn], Field(min_length=1, max_length=24)]


# --- internal payrolls (R8-4) --------------------------------------------------------------------------------

PayrollKind = Literal["REGULAR", "EXTRAORDINARY"]
PayrollStatus = Literal["OPEN", "CLOSED", "CANCELLED"]
PayrollLineKind = Literal["SALARY", "BONUS", "INCENTIVE", "OTHER_CREDIT"]
LineDescription = Annotated[str | None, Field(default=None, max_length=300)]


class PayrollIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_request_id: UUID
    employee_id: int
    kind: PayrollKind
    period_start: date
    period_end: date  # inclusive
    payment_date: date | None = None  # informative: the payout is R8-5
    notes: str | None = Field(default=None, max_length=1000)


class PayrollPeriodIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: date
    period_end: date
    payment_date: date | None = None
    notes: str | None = Field(default=None, max_length=1000)


class PayrollLineIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: PayrollLineKind
    amount: Amount
    description: LineDescription = None


class PayrollLineUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Amount
    description: LineDescription = None


class PayrollChoiceIn(BaseModel):
    """Include or exclude one item of the draft; `amount` applies part of a leg (absolute, never of an installment)."""

    model_config = ConfigDict(extra="forbid")

    ledger_entry_id: int | None = None
    installment_id: int | None = None
    included: bool
    amount: Amount | None = None


class PayrollCloseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    preview_token: str = Field(min_length=64, max_length=64)
    confirm: bool = False


class PayrollReopenIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    reason: Reason


class PayrollCancelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: Reason


class PayrollTotals(BaseModel):
    credits: Money  # haberes
    deductions: Money  # descuentos: what the payroll applies in the company's favour
    credits_applied: Money  # créditos: what it applies in the employee's favour (absolute)
    net: Money  # credits - deductions + credits_applied
    deduction_pct: Decimal | None  # deductions over credits, shown, never capped (D6); None without credits


class PayrollRow(BaseModel):
    id: int
    employee_id: int
    employee_name: str
    kind: PayrollKind
    period_start: date
    period_end: date
    payment_date: date | None
    status: PayrollStatus
    version: int
    # A CLOSED payroll's are its close's; an OPEN one's are its draft's (`totals_from_draft`); CANCELLED: zero.
    totals: PayrollTotals
    totals_from_draft: bool
    in_correction: bool  # OPEN after a reopening: its previous close is still in effect


class PayrollPage(BaseModel):
    items: list[PayrollRow]
    total: int


class SalaryHintOut(BaseModel):
    status: Literal["PROPOSED", "MANUAL", "NONE"]
    message: str
    amount: Money | None
    compensation_term_id: int | None


class PayrollLineOut(BaseModel):
    id: int
    kind: PayrollLineKind
    amount: Money
    description: str | None
    source: Literal["TERM", "MANUAL"]
    compensation_term_id: int | None


class PayrollLegOut(BaseModel):
    """A leg of the internal account the payroll may apply, and what the draft does with it."""

    entry_id: int
    operation_id: int
    origin: Origin
    business_date: date
    document_kind: DocumentKind | None
    document_id: int | None
    description: str | None
    # The original operation: a leg and its reversal share it, so the screen can show them as one correction.
    group_operation_id: int
    reversed: bool  # its operation was reversed since (the reversal is another leg)
    original_amount: Money  # signed, as on the account
    applied_elsewhere: Money  # signed: what closes in effect of other payrolls already applied
    available: Money  # signed: what is still applicable
    proposed: bool
    included: bool
    amount: Money  # signed amount the close would apply (when included)
    partial: bool  # a chosen amount below what is available


class PayrollInstallmentOut(BaseModel):
    installment_id: int
    loan_id: int
    loan_description: str | None
    number: int
    due_on: date
    amount: Money  # whole: never reduced
    overdue: bool  # due before the period starts
    included: bool


class PayrollBlocker(BaseModel):
    code: str
    message: str


class PayrollPreviewOut(PayrollTotals):
    preview_token: str
    blockers: list[PayrollBlocker]
    accrual: Literal["FINANCIAL", "NO_ACCRUAL"]  # whether the close publishes PAYROLL_CLOSE


class PayrollApplicationOut(BaseModel):
    entry_id: int
    origin: Origin
    business_date: date
    amount: Money  # signed
    installment_id: int | None
    installment_number: int | None
    loan_id: int | None


class PayrollClosingOut(PayrollTotals):
    id: int
    version: int
    kind: Literal["FINANCIAL", "NO_ACCRUAL"]
    operation_id: int | None
    reversal_operation_id: int | None
    closed_at: datetime
    closed_by_name: str
    superseded_at: datetime | None
    lines: list[PayrollLineOut]
    applications: list[PayrollApplicationOut]


class PayrollReopeningOut(BaseModel):
    from_version: int
    to_version: int
    reason: str
    reopened_at: datetime
    reopened_by_name: str


class PayrollOut(PayrollRow):
    notes: str | None
    created_at: datetime
    created_by_name: str
    cancelled_at: datetime | None
    cancelled_by_name: str | None
    cancel_reason: str | None
    salary_hint: SalaryHintOut
    # The draft (OPEN only; empty otherwise).
    lines: list[PayrollLineOut]
    legs: list[PayrollLegOut]
    installments: list[PayrollInstallmentOut]
    preview: PayrollPreviewOut | None
    # The close in effect, then the history: every close (superseded ones too) and every reopening.
    closing: PayrollClosingOut | None
    closings: list[PayrollClosingOut]
    reopenings: list[PayrollReopeningOut]
