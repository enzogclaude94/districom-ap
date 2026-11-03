from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_code: str
    source_description: str | None
    source_category: str | None
    source_line: str | None
    source_capacity: str | None
    source_pack: str | None
    source_barcode: str | None
    source_internal_tax: Decimal | None
    source_discount_cap: Decimal | None
    source_vat: Decimal | None
    source_weight: Decimal | None
    source_extra: dict[str, Any]
    canonical_category_id: int | None
    category_origin: str
    product_kind: str
    analytics_eligible: bool
    eligibility_origin: str
    family_id: int | None
    active: bool
    created_at: datetime
    updated_at: datetime


class ProductPage(BaseModel):
    items: list[ProductOut]
    total: int


class PriceOut(BaseModel):
    id: int
    list_number: int
    price_list_name: str
    price: Decimal
    valid_from: date
    valid_to: date | None
    voided: bool


class ProductDetail(ProductOut):
    prices: list[PriceOut]


class ProductPatch(BaseModel):
    """Only the keys sent are changed; `category_id` or `family_id` null removes it."""

    category_id: int | None = None
    product_kind: str | None = Field(default=None, max_length=20)
    analytics_eligible: bool | None = None
    family_id: int | None = None
    active: bool | None = None
    reason: str = Field(min_length=1, max_length=500)


class FamilyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    code: str
    name: str
    description: str | None
    active: bool


class FamilyIn(BaseModel):
    code: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)


class FamilyPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    active: bool | None = None


class SourceCategoryMappingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_category: str
    category_id: int
    decided_by: int
    decided_at: datetime


class SourceCategoryMappingIn(BaseModel):
    source_category: str = Field(min_length=1, max_length=120)
    category_id: int = Field(gt=0)


class UnmappedSourceCategory(BaseModel):
    source_category: str  # "" for products without rubro
    products: int
