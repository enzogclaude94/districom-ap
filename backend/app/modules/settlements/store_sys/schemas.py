"""Employees' Store Sys codes in Rendiciones > Integración Store Sys (R9-1, ADR 011)."""

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class EmployeeCodeOut(BaseModel):
    employee_id: int
    full_name: str
    active: bool
    # `employees.external_store_id`: DistriCom's canonical code (how Store Sys names the employee is P4).
    store_code: str | None


class EmployeeCodePage(BaseModel):
    items: list[EmployeeCodeOut]
    total: int


class EmployeeCodeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Null or blank removes it.
    store_code: str | None = Field(default=None, max_length=100)
    # Required to correct or remove a code already loaded.
    reason: str | None = Field(default=None, max_length=500)


class EmployeeCodeChangeOut(BaseModel):
    before: str | None
    after: str | None
    reason: str | None
    changed_by_name: str | None
    changed_at: datetime
