from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    legal_name: str | None
    tax_id: str | None
    address: str | None
    phone: str | None
    is_generic: bool
    active: bool
    last_seen_on: date | None
    created_at: datetime
    updated_at: datetime


class CustomerPage(BaseModel):
    items: list[CustomerOut]
    total: int


class CustomerFlagsIn(BaseModel):
    is_generic: bool | None = None
    active: bool | None = None
    reason: str = Field(min_length=1, max_length=500)


class MappingOut(BaseModel):
    id: int
    entity_type: str
    source_system: str
    namespace: str
    external_id: str
    external_label: str | None
    entity_id: int | None
    status: str
    method: str | None
    candidates: list[int]
    decided_by: int | None
    decided_at: datetime | None
    decision_reason: str | None
    created_at: datetime
    updated_at: datetime


class MappingPage(BaseModel):
    items: list[MappingOut]
    total: int


class MappingLinkIn(BaseModel):
    entity_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=500)


class MappingReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
