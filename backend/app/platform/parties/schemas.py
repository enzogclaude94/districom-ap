from datetime import date

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class EmployeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name: str = Field(min_length=1, max_length=200)
    hire_date: date | None = None
    phone: str | None = Field(default=None, max_length=50)

    _strip_name = field_validator("full_name", mode="before")(_strip)

    @field_validator("phone", mode="before")
    @classmethod
    def _empty_phone_is_null(cls, value: object) -> object:
        value = _strip(value)
        return None if value == "" else value


class DeactivateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Last working day; open functions end on this date. Defaults to today (business timezone).
    end_date: date | None = None


class FunctionAssignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    function_code: str = Field(min_length=1, max_length=60)
    valid_from: date


class FunctionEndIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_to: date


class FunctionCodeOut(BaseModel):
    code: str
    label: str


class EmployeeFunctionOut(BaseModel):
    id: int
    function_code: str
    label: str
    valid_from: date
    valid_to: date | None
    source: str
    current: bool


class EmployeeOut(BaseModel):
    id: int
    full_name: str
    active: bool
    hire_date: date | None
    phone: str | None
    external_axum_id: str | None
    external_store_id: str | None
    functions: list[EmployeeFunctionOut]


class EmployeePage(BaseModel):
    items: list[EmployeeOut]
    total: int
