from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.platform.outlets.models import DESCRIPTION_LENGTH, NAME_LENGTH


class OutletIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=NAME_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_LENGTH)


class OutletPatch(BaseModel):
    """Correct the name or the description, or (de)activate. The code is not here: it never changes."""

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=NAME_LENGTH)
    description: str | None = Field(default=None, max_length=DESCRIPTION_LENGTH)  # "" clears it
    active: bool | None = None


class OutletOut(BaseModel):
    id: int
    code: str
    name: str
    description: str | None
    active: bool
    created_at: datetime
    created_by_name: str
    updated_at: datetime
    updated_by_name: str


class OutletEventOut(BaseModel):
    id: int
    event_type: str
    occurred_at: datetime
    user_name: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class OutletDetail(OutletOut):
    history: list[OutletEventOut]
