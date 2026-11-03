"""Configuration of Inteligencia Comercial (DATA_MODEL §4.2, ADR 014 §2.8-§2.10): zones and their visit days,
the rules that propose how a new zone is configured, price lists, categories, document types and no-purchase
reasons. Loaded by `commercial seed-config` (idempotent) and edited by a module admin, audited
(`commercial.config.*`). No commercial rule lives in an `if` of the analytics.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

SCHEMA = "commercial"

ZONE_TYPES = ("PRESALE", "BANDERA", "OUTLET", "OTHER", "UNCLASSIFIED")
# RULE: proposed by a classification rule when the zone appeared; MANUAL: an admin set it; NONE: no rule matched
# and nobody configured it yet (the zone is UNCLASSIFIED).
ZONE_ORIGINS = ("RULE", "MANUAL", "NONE")
RULE_MATCH_TYPES = ("CONTAINS",)
PRICE_LIST_KINDS = ("PRESALE_OFFICIAL", "OUTLET", "PROMO_VOLUME", "SPECIAL", "BANDERA", "UNASSIGNED")
PRODUCT_KINDS = ("PRODUCT", "CONTAINER", "CRATE", "SERVICE", "BONUS", "COMBO", "PROMO", "MERCH", "UNCLASSIFIED")
DOCUMENT_CLASSES = (
    "INVOICE",
    "CREDIT_NOTE",
    "DEBIT_NOTE",
    "RETURN",
    "RECEIPT",
    "INTERNAL_MOVEMENT",
    "EMPLOYEE_VOUCHER",
    "OTHER",
    "UNCLASSIFIED",
)
WEEKDAYS = range(1, 8)  # 1 = Monday … 7 = Sunday (ISO)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Zone(IdMixin, TimestampMixin, Base):
    """A STORE SYS commercial zone (`RUTA 01 - LUVI`…). Created when an import first sees it; a rule configures it
    only then (§ZoneClassificationRule), and every later change is a manual, audited configuration."""

    __tablename__ = "zones"
    __table_args__ = (
        CheckConstraint(_in("zone_type", ZONE_TYPES), name="zone_type"),
        CheckConstraint(_in("configuration_origin", ZONE_ORIGINS), name="configuration_origin"),
        CheckConstraint("length(btrim(name_key)) > 0", name="name_key_not_blank"),
        CheckConstraint("length(btrim(display_name)) > 0", name="display_name_not_blank"),
        # No visits for a zone nobody classified nor for the boca de expendio (D-J).
        CheckConstraint("zone_type NOT IN ('UNCLASSIFIED', 'OUTLET') OR NOT requires_visit", name="visit_by_type"),
        CheckConstraint(
            "(configuration_origin = 'NONE') = (zone_type = 'UNCLASSIFIED' AND rule_id IS NULL "
            "AND configured_by IS NULL)",
            name="origin_none_unconfigured",
        ),
        CheckConstraint("configuration_origin <> 'RULE' OR rule_id IS NOT NULL", name="rule_origin_has_rule"),
        CheckConstraint("configuration_origin <> 'MANUAL' OR configured_by IS NOT NULL", name="manual_has_author"),
        {"schema": SCHEMA},
    )

    source_name: Mapped[str] = mapped_column(String(120))
    name_key: Mapped[str] = mapped_column(String(120), unique=True)
    display_name: Mapped[str] = mapped_column(String(120))
    zone_type: Mapped[str] = mapped_column(String(20))
    requires_visit: Mapped[bool] = mapped_column(Boolean)
    configuration_origin: Mapped[str] = mapped_column(String(10))
    rule_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.zone_classification_rules.id", ondelete="RESTRICT")
    )
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    configured_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    configured_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_seen_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))


class ZoneVisitDay(IdMixin, TimestampMixin, Base):
    """One weekday a zone is visited, over a range of days (both inclusive, `valid_to` NULL while it lasts).
    Changing the days closes the rows that end and opens new ones, never an update in place. A row that would end
    before it started (changed the same day it began) is voided instead: it stays, marked, out of the
    no-overlap rule."""

    __tablename__ = "zone_visit_days"
    __table_args__ = (
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        CheckConstraint("voided_at IS NULL OR valid_to IS NULL", name="voided_not_closed"),
        ExcludeConstraint(
            ("zone_id", "="),
            ("weekday", "="),
            (func.daterange(text("valid_from"), text("valid_to"), text("'[]'")), "&&"),
            name="ex_zone_visit_days_no_overlap",
            using="gist",
            where=text("voided_at IS NULL"),
        ),
        {"schema": SCHEMA},
    )

    zone_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.zones.id", ondelete="RESTRICT"), index=True)
    weekday: Mapped[int] = mapped_column(SmallInteger)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ZoneClassificationRule(IdMixin, TimestampMixin, Base):
    """How a **new** zone whose name contains `pattern` (as whole words of its `name_key`) is configured. Applied
    once, when the zone is created; it never changes an existing zone."""

    __tablename__ = "zone_classification_rules"
    __table_args__ = (
        CheckConstraint(_in("match_type", RULE_MATCH_TYPES), name="match_type"),
        CheckConstraint(_in("zone_type", tuple(t for t in ZONE_TYPES if t != "UNCLASSIFIED")), name="zone_type"),
        CheckConstraint("length(btrim(pattern)) > 0", name="pattern_not_blank"),
        CheckConstraint("weekdays <@ ARRAY[1,2,3,4,5,6,7]::smallint[]", name="weekdays_valid"),
        CheckConstraint(
            "(requires_visit AND cardinality(weekdays) > 0) OR (NOT requires_visit AND cardinality(weekdays) = 0)",
            name="weekdays_match_visit",
        ),
        CheckConstraint("zone_type <> 'OUTLET' OR NOT requires_visit", name="outlet_without_visit"),
        UniqueConstraint("match_type", "pattern"),
        {"schema": SCHEMA},
    )

    priority: Mapped[int] = mapped_column(Integer)
    match_type: Mapped[str] = mapped_column(String(20), server_default="CONTAINS")
    pattern: Mapped[str] = mapped_column(String(120))
    zone_type: Mapped[str] = mapped_column(String(20))
    requires_visit: Mapped[bool] = mapped_column(Boolean)
    weekdays: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), server_default="{}")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    description: Mapped[str | None] = mapped_column(Text)


class PriceList(IdMixin, TimestampMixin, Base):
    """A STORE SYS price list with its meaning. A new list is a new row, never code."""

    __tablename__ = "price_lists"
    __table_args__ = (
        CheckConstraint(_in("kind", PRICE_LIST_KINDS), name="kind"),
        CheckConstraint("list_number > 0", name="list_number_positive"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        {"schema": SCHEMA},
    )

    list_number: Mapped[int] = mapped_column(SmallInteger, unique=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(20))
    commercially_relevant: Mapped[bool] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class ProductCategory(IdMixin, TimestampMixin, Base):
    __tablename__ = "product_categories"
    __table_args__ = (
        CheckConstraint(
            f"default_product_kind IS NULL OR {_in('default_product_kind', PRODUCT_KINDS)}", name="default_product_kind"
        ),
        CheckConstraint("length(btrim(code)) > 0 AND length(btrim(name)) > 0", name="code_name_not_blank"),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="not_own_parent"),
        {"schema": SCHEMA},
    )

    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    default_product_kind: Mapped[str | None] = mapped_column(String(20))
    default_analytics_eligible: Mapped[bool] = mapped_column(Boolean)
    parent_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.product_categories.id", ondelete="RESTRICT")
    )
    sort_order: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class SourceCategoryMapping(IdMixin, TimestampMixin, Base):
    """Explicit translation of a source rubro into a canonical category. A blank rubro has none."""

    __tablename__ = "source_category_mappings"
    __table_args__ = (
        UniqueConstraint("source_system_id", "source_category_key", name="uq_source_category_mappings_key"),
        CheckConstraint("length(btrim(source_category_key)) > 0", name="key_not_blank"),
        {"schema": SCHEMA},
    )

    source_system_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_systems.id", ondelete="RESTRICT"))
    source_category: Mapped[str] = mapped_column(String(120))
    source_category_key: Mapped[str] = mapped_column(String(120))
    category_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.product_categories.id", ondelete="RESTRICT")
    )
    decided_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DocumentType(IdMixin, TimestampMixin, Base):
    """How DistriCom reads a STORE SYS document type. `commercial_universe` is the **only** definition of "counts as
    commercial activity"; an unclassified type is always outside it."""

    __tablename__ = "document_types"
    __table_args__ = (
        CheckConstraint(_in("document_class", DOCUMENT_CLASSES), name="document_class"),
        CheckConstraint("sign IN (-1, 0, 1)", name="sign"),
        CheckConstraint(
            "document_class <> 'UNCLASSIFIED' OR (NOT commercial_universe AND sign = 0)", name="unclassified_outside"
        ),
        CheckConstraint("NOT commercial_universe OR sign <> 0", name="universe_has_sign"),
        CheckConstraint("length(btrim(source_name_key)) > 0", name="key_not_blank"),
        {"schema": SCHEMA},
    )

    source_name: Mapped[str] = mapped_column(String(120))
    source_name_key: Mapped[str] = mapped_column(String(120), unique=True)
    document_class: Mapped[str] = mapped_column(String(20))
    sign: Mapped[int] = mapped_column(SmallInteger)
    commercial_universe: Mapped[bool] = mapped_column(Boolean)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class NoPurchaseReasonCategory(IdMixin, TimestampMixin, Base):
    __tablename__ = "no_purchase_reason_categories"
    __table_args__ = (
        CheckConstraint("length(btrim(code)) > 0 AND length(btrim(name)) > 0", name="code_name_not_blank"),
        {"schema": SCHEMA},
    )

    code: Mapped[str] = mapped_column(String(40), unique=True)
    name: Mapped[str] = mapped_column(String(120))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")


class NoPurchaseReasonMapping(IdMixin, TimestampMixin, Base):
    """A raw no-purchase reason of a source and its category; a new reason arrives without one."""

    __tablename__ = "no_purchase_reason_mappings"
    __table_args__ = (
        UniqueConstraint("source_system_id", "reason_key", name="uq_no_purchase_reason_mappings_key"),
        CheckConstraint("length(btrim(reason_key)) > 0", name="key_not_blank"),
        CheckConstraint("(category_id IS NULL) = (decided_at IS NULL)", name="decided_with_category"),
        {"schema": SCHEMA},
    )

    source_system_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("source_systems.id", ondelete="RESTRICT"))
    reason_source: Mapped[str] = mapped_column(String(200))
    reason_key: Mapped[str] = mapped_column(String(200))
    category_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey(
            f"{SCHEMA}.no_purchase_reason_categories.id",
            ondelete="RESTRICT",
            name="fk_no_purchase_reason_mappings_category_id",
        ),
        index=True,
    )
    decided_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
