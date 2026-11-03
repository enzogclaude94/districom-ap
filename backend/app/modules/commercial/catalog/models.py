"""Products, families and prices of Inteligencia Comercial (DATA_MODEL §4.3, ADR 014 §2.10).

A product keeps what STORE SYS says (`source_*`, refreshed by every master import) apart from what DistriCom
decides (category, kind, analytics eligibility, family), which no import ever overwrites. Without a category a
product is not analytics eligible. Families are explicit: never inferred from the text.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    LargeBinary,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.modules.commercial.config.models import PRODUCT_KINDS, SCHEMA

CATEGORY_ORIGINS = ("SOURCE_MAPPING", "MANUAL", "NONE")
ELIGIBILITY_ORIGINS = ("NONE", "CATEGORY_DEFAULT", "MANUAL")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ProductFamily(IdMixin, TimestampMixin, Base):
    __tablename__ = "product_families"
    __table_args__ = (
        CheckConstraint("length(btrim(code)) > 0 AND length(btrim(name)) > 0", name="code_name_not_blank"),
        {"schema": SCHEMA},
    )

    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class Product(IdMixin, TimestampMixin, Base):
    __tablename__ = "products"
    __table_args__ = (
        CheckConstraint("length(btrim(source_code)) > 0", name="source_code_not_blank"),
        CheckConstraint(_in("category_origin", CATEGORY_ORIGINS), name="category_origin"),
        CheckConstraint(_in("eligibility_origin", ELIGIBILITY_ORIGINS), name="eligibility_origin"),
        CheckConstraint(_in("product_kind", PRODUCT_KINDS), name="product_kind"),
        CheckConstraint("(category_origin = 'NONE') = (canonical_category_id IS NULL)", name="category_origin_match"),
        # No category and nobody decided: never eligible (D-D).
        CheckConstraint("eligibility_origin <> 'NONE' OR NOT analytics_eligible", name="none_not_eligible"),
        CheckConstraint("NOT analytics_eligible OR canonical_category_id IS NOT NULL", name="eligible_needs_category"),
        CheckConstraint(
            "eligibility_origin <> 'CATEGORY_DEFAULT' OR canonical_category_id IS NOT NULL",
            name="default_needs_category",
        ),
        {"schema": SCHEMA},
    )

    source_code: Mapped[str] = mapped_column(String(40), unique=True)
    source_description: Mapped[str | None] = mapped_column(String(200))
    source_category: Mapped[str | None] = mapped_column(String(120))
    source_line: Mapped[str | None] = mapped_column(String(120))
    source_capacity: Mapped[str | None] = mapped_column(String(60))
    source_pack: Mapped[str | None] = mapped_column(String(60))
    source_barcode: Mapped[str | None] = mapped_column(String(60))
    source_internal_tax: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    source_discount_cap: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    source_vat: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    source_weight: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    source_extra: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    source_row_hash: Mapped[bytes] = mapped_column(LargeBinary)

    canonical_category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.product_categories.id", ondelete="RESTRICT"), index=True
    )
    category_origin: Mapped[str] = mapped_column(String(20), server_default="NONE")
    product_kind: Mapped[str] = mapped_column(String(20), server_default="UNCLASSIFIED")
    analytics_eligible: Mapped[bool] = mapped_column(Boolean, server_default="false")
    eligibility_origin: Mapped[str] = mapped_column(String(20), server_default="NONE")
    family_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.product_families.id", ondelete="RESTRICT"), index=True
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    first_seen_job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    last_seen_job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))


class ProductPrice(IdMixin, TimestampMixin, Base):
    """A product's price in one list over a range of days (both inclusive, `valid_to` NULL while it lasts). A new
    price closes the open one and opens another; the same price changes nothing. A row that would end before it
    started (two snapshots of the same day) is voided by the job that replaced it, and leaves the no-overlap rule."""

    __tablename__ = "product_prices"
    __table_args__ = (
        CheckConstraint("price >= 0", name="price_not_negative"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        CheckConstraint("voided_by_job_id IS NULL OR valid_to IS NULL", name="voided_not_closed"),
        ExcludeConstraint(
            ("product_id", "="),
            ("price_list_id", "="),
            (func.daterange(text("valid_from"), text("valid_to"), text("'[]'")), "&&"),
            name="ex_product_prices_no_overlap",
            using="gist",
            where=text("voided_by_job_id IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    product_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.products.id", ondelete="RESTRICT"), index=True
    )
    price_list_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.price_lists.id", ondelete="RESTRICT"))
    price: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    import_job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    voided_by_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
