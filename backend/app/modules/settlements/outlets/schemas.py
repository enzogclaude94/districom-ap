from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.settlements.outlets.models import REASON_LENGTH


class ResponsibilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    employee_id: int
    valid_from: date


class ResponsibilityEndIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid_to: date
    reason: str = Field(min_length=1, max_length=REASON_LENGTH)


class ResponsibilityOut(BaseModel):
    id: int
    outlet_id: int
    outlet_code: str
    outlet_name: str
    outlet_active: bool
    employee_id: int
    employee_name: str
    employee_active: bool
    valid_from: date
    valid_to: date | None  # inclusive; None while it lasts
    created_at: datetime
    created_by_name: str
    ended_at: datetime | None
    ended_by_name: str | None
    end_reason: str | None


class ResponsibilityPage(BaseModel):
    items: list[ResponsibilityOut]
    total: int


class SelectableEmployeeOut(BaseModel):
    id: int
    full_name: str
