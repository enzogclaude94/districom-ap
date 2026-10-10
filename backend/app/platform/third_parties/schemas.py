from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

Name = Annotated[str, Field(min_length=1, max_length=200)]
TaxId = Annotated[str, Field(max_length=20)]
Notes = Annotated[str, Field(max_length=1000)]


class DuplicateConfirmation(BaseModel):
    """Answer to `third_party_possible_duplicate`: it is another third party, and why (kept in its history)."""

    model_config = ConfigDict(extra="forbid")

    confirm_not_duplicate: bool = False
    duplicate_reason: Annotated[str, Field(max_length=500)] | None = None


class ThirdPartyIn(DuplicateConfirmation):
    name: Name
    # Optional; when given, 11 digits with a valid check digit, hyphens and spaces ignored.
    tax_id: TaxId | None = None
    notes: Notes | None = None


class ThirdPartyPatch(DuplicateConfirmation):
    """A platform admin's correction or (de)activation. The id never changes, so payments follow."""

    name: Name | None = None
    # An empty string removes the CUIT.
    tax_id: TaxId | None = None
    notes: Notes | None = None
    active: bool | None = None


class ThirdPartyOut(BaseModel):
    id: int
    name: str
    tax_id: str | None  # 11 digits, the stored value
    tax_id_display: str | None  # «30-71234567-1», computed
    notes: str | None
    active: bool
    created_at: datetime
    created_by_name: str
    updated_at: datetime
    updated_by_name: str


class ThirdPartyEventOut(BaseModel):
    id: int
    event_type: str
    occurred_at: datetime
    user_name: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class ThirdPartyDetail(ThirdPartyOut):
    history: list[ThirdPartyEventOut]
