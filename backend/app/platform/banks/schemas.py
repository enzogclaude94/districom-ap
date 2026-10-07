from pydantic import BaseModel, ConfigDict, Field


class FinancialInstitutionOut(BaseModel):
    id: int
    bcra_code: int
    code: str  # zero-padded, as printed on a cheque
    name: str
    source: str
    active: bool


class FinancialInstitutionIn(BaseModel):
    """A missing entity added by an admin: a controlled addition, never an edit of an official one."""

    model_config = ConfigDict(extra="forbid")

    bcra_code: int = Field(ge=1, le=99999)
    name: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=500)


class ActiveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    active: bool
