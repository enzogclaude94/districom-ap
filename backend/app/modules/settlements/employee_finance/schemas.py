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
    "OTHER",
]
DocumentKind = Literal[
    "DELIVERY_SETTLEMENT", "OUTLET_SETTLEMENT", "PENDING_COLLECTION", "EMPLOYEE_MOVEMENT", "EMPLOYEE_LOAN"
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
    balance: Money  # principal while in effect (R8-4: minus the installments a payroll applied); 0 reversed
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
