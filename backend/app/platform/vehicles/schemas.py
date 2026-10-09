from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class VehicleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plate: str = Field(min_length=1, max_length=20)
    description: str = Field(min_length=1, max_length=200)


class VehiclePatch(BaseModel):
    """Correct the plate or the description, or (de)activate. The id never changes, so history follows."""

    model_config = ConfigDict(extra="forbid")

    plate: str | None = Field(default=None, min_length=1, max_length=20)
    description: str | None = Field(default=None, min_length=1, max_length=200)
    active: bool | None = None


class VehicleOut(BaseModel):
    id: int
    plate: str  # normalized: the value that is compared and stored
    plate_display: str  # how screens print it, computed
    description: str
    active: bool
    created_at: datetime
    created_by_name: str
    updated_at: datetime
    updated_by_name: str


class VehicleEventOut(BaseModel):
    id: int
    event_type: str
    occurred_at: datetime
    user_name: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class VehicleDetail(VehicleOut):
    history: list[VehicleEventOut]
