from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

Reason = Field(min_length=1, max_length=500)


class ZoneOut(BaseModel):
    id: int
    source_name: str
    display_name: str
    zone_type: str
    requires_visit: bool
    weekdays: list[int]  # in force today
    configuration_origin: str
    rule_id: int | None
    active: bool
    configured_by: int | None
    configured_at: datetime | None
    created_at: datetime


class VisitDayOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    weekday: int
    valid_from: date
    valid_to: date | None
    voided_at: datetime | None
    created_by: int | None


class ZoneDetail(ZoneOut):
    visit_days: list[VisitDayOut]


class ZoneConfigurationIn(BaseModel):
    zone_type: str = Field(max_length=20)
    requires_visit: bool
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    effective_from: date
    display_name: str | None = Field(default=None, max_length=120)
    active: bool | None = None
    reason: str = Reason


class ZoneRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    priority: int
    match_type: str
    pattern: str
    zone_type: str
    requires_visit: bool
    weekdays: list[int]
    active: bool
    description: str | None


class ZoneRuleIn(BaseModel):
    priority: int = Field(ge=0, le=10_000)
    pattern: str = Field(min_length=1, max_length=120)
    zone_type: str = Field(max_length=20)
    requires_visit: bool
    weekdays: list[int] = Field(default_factory=list, max_length=7)
    description: str | None = Field(default=None, max_length=500)


class ZoneRulePatch(BaseModel):
    priority: int | None = Field(default=None, ge=0, le=10_000)
    zone_type: str | None = Field(default=None, max_length=20)
    requires_visit: bool | None = None
    weekdays: list[int] | None = Field(default=None, max_length=7)
    active: bool | None = None
    description: str | None = Field(default=None, max_length=500)


class PriceListOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    list_number: int
    code: str
    name: str
    description: str | None
    kind: str
    commercially_relevant: bool
    active: bool


class PriceListIn(BaseModel):
    list_number: int = Field(ge=1, le=999)
    name: str = Field(min_length=1, max_length=120)
    kind: str = Field(max_length=20)
    commercially_relevant: bool
    description: str | None = Field(default=None, max_length=500)


class PriceListPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    kind: str | None = Field(default=None, max_length=20)
    commercially_relevant: bool | None = None
    active: bool | None = None
    description: str | None = Field(default=None, max_length=500)


class ArticlesPriceListIn(BaseModel):
    list_number: int | None = Field(default=None, gt=0)
    reason: str = Field(max_length=300)


class ArticlesPriceListOut(BaseModel):
    list_number: int | None


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    default_product_kind: str | None
    default_analytics_eligible: bool
    parent_id: int | None
    sort_order: int
    active: bool


class CategoryIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    default_product_kind: str | None = Field(default=None, max_length=20)
    default_analytics_eligible: bool
    parent_id: int | None = None
    sort_order: int = 0


class CategoryPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    default_product_kind: str | None = Field(default=None, max_length=20)
    default_analytics_eligible: bool | None = None
    sort_order: int | None = None
    active: bool | None = None


class DocumentTypeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_name: str
    document_class: str
    sign: int
    commercial_universe: bool
    active: bool
    decided_by: int | None
    decided_at: datetime | None
    notes: str | None


class DocumentTypeClassificationIn(BaseModel):
    document_class: str = Field(max_length=20)
    sign: int = Field(ge=-1, le=1)
    commercial_universe: bool
    reason: str = Reason


class ReasonCategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    active: bool


class ReasonCategoryIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)


class ReasonCategoryPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    active: bool | None = None


class ReasonMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_system_id: int
    reason_source: str
    category_id: int | None
    decided_by: int | None
    decided_at: datetime | None


class ReasonMappingIn(BaseModel):
    category_id: int | None
    reason: str | None = Field(default=None, max_length=500)
