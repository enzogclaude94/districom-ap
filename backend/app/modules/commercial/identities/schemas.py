from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CustomerOut(BaseModel):
    """What any user of the module sees of a customer: no CUIT, address nor phone (minimum privilege)."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    display_name: str
    legal_name: str | None
    is_generic: bool
    active: bool
    last_seen_on: date | None
    created_at: datetime
    updated_at: datetime


class CustomerContactOut(CustomerOut):
    """The same customer with its personal data, only for an operator or admin of the module. The fields are
    required (no defaults) so a response without them can never be read as this schema."""

    tax_id: str | None
    address: str | None
    phone: str | None


class CustomerPage(BaseModel):
    items: list[CustomerContactOut | CustomerOut]
    total: int


class CustomerFlagsIn(BaseModel):
    is_generic: bool | None = None
    active: bool | None = None
    reason: str = Field(min_length=1, max_length=500)


class FactCountOut(BaseModel):
    """Facts that took their identity from a mapping, by kind (business wording in `label`)."""

    kind: str
    label: str
    total: int
    linked: int


class ResolutionOut(BaseModel):
    """What a decision re-resolved: rows written per kind of fact and quality issues closed."""

    facts_updated: dict[str, int]
    issues_closed: int


class LinkTargetOut(BaseModel):
    """A customer or employee as Mapeos shows it. Never CUIT, phone nor address; a viewer gets only the name (and a
    person's functions): `legal_name`, `store_sys_code` and `zone` stay null for them."""

    id: int
    name: str
    active: bool
    legal_name: str | None = None
    store_sys_code: str | None = None
    zone: str | None = None
    functions: list[str] = []
    is_salesperson: bool = False


class LinkTargetPage(BaseModel):
    items: list[LinkTargetOut]


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
    # F1.5, all additive: the business name of the kind of code («Preventista AXUM»), when it was first and last
    # seen, who decided, what the linked entity and candidates are, and how many facts depend on it.
    kind_label: str | None = None
    first_seen_job_id: int | None = None
    last_seen_job_id: int | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    decided_by_name: str | None = None
    entity: LinkTargetOut | None = None
    candidate_entities: list[LinkTargetOut] = []
    facts: list[FactCountOut] = []
    resolution: ResolutionOut | None = None  # only in the answer to a decision


class MappingPage(BaseModel):
    items: list[MappingOut]
    total: int


class MappingSummaryOut(BaseModel):
    counts: dict[str, int]  # by status, every status present


class MappingImpactFactOut(FactCountOut):
    on_target: int
    changes_on_link: int  # rows a confirm or reassign to `entity_id` writes
    changes_on_unlink: int  # rows an exclude writes


class MappingImpactOut(BaseModel):
    """What each decision would change, from the facts as they are now (the numbers the UI shows)."""

    mapping_id: int
    status: str
    entity_id: int | None  # the requested target, if any
    facts: list[MappingImpactFactOut]
    on_link: int
    on_unlink: int


class MappingLinkIn(BaseModel):
    entity_id: int = Field(gt=0)
    reason: str | None = Field(default=None, max_length=500)
    # The status the person saw: if someone decided the mapping since, the decision is refused (409), not applied.
    expected_status: str | None = Field(default=None, max_length=20)


class MappingReasonIn(BaseModel):
    reason: str | None = Field(default=None, max_length=500)
    expected_status: str | None = Field(default=None, max_length=20)


class BatchItemIn(BaseModel):
    mapping_id: int = Field(gt=0)
    entity_id: int = Field(gt=0)


class BatchConfirmIn(BaseModel):
    items: list[BatchItemIn] = Field(min_length=1, max_length=200)


class BatchItemOut(BaseModel):
    mapping_id: int
    entity_id: int
    facts_updated: dict[str, int]


class BatchConfirmOut(BaseModel):
    confirmed: int
    failed: int  # always 0: one failure rolls the whole batch back and answers with an error
    items: list[BatchItemOut]
    facts_updated: dict[str, int]
    issues_closed: int
