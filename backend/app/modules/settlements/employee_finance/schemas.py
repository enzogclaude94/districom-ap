"""Requests and responses of Rendiciones > Empleados (R8-1). Amounts are decimal strings ("1500.00")."""

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.settlements.cash.schemas import Money, Reason

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


# Where a movement of the internal account comes from (R8-1 reads the ones R2-R7 already write).
Origin = Literal[
    "SETTLEMENT_SHORTAGE",  # faltante de una rendición asignado al empleado
    "SETTLEMENT_SURPLUS",  # sobrante de una rendición asignado al empleado
    "SETTLEMENT_CORRECTION",  # reverso de un cierre de rendición al corregirla
    "PENDING_WRITE_OFF",  # parte de un pendiente incobrable a cargo del empleado
    "PENDING_WRITE_OFF_REVERSAL",  # reverso de esa resolución
    "OTHER",
]
DocumentKind = Literal["DELIVERY_SETTLEMENT", "OUTLET_SETTLEMENT", "PENDING_COLLECTION"]


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
    created_at: datetime


class AccountMovementPage(BaseModel):
    items: list[AccountMovementOut]
    total: int
