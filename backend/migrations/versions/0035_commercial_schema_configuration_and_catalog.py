"""commercial: schema, configuration and catalog

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-03 23:31:00.000000

F1.2 (ADR 014 §2.8-§2.10, DATA_MODEL §4.2-§4.3, Enzo 2026-11-03): the `commercial` schema and its masters.

- Configuration: `zones`, `zone_visit_days` (history, no overlaps), `zone_classification_rules`, `price_lists`,
  `product_categories`, `source_category_mappings`, `document_types`, `no_purchase_reason_categories`,
  `no_purchase_reason_mappings`.
- Catalog: `product_families`, `products` (source fields apart from canonical ones), `product_prices` (history, no
  overlaps).

Nothing is seeded here: `commercial seed-config` loads what Enzo confirmed. Zones, visit days, document types,
products and prices are never deleted (triggers). The downgrade is refused while any zone, product or decided
mapping exists (seeded configuration alone can be dropped and seeded again).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0035"
down_revision: str | Sequence[str] | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGERS = """
CREATE FUNCTION commercial.forbid_zone_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'zones rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_zones_no_delete BEFORE DELETE ON commercial.zones
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_zone_delete();
CREATE FUNCTION commercial.forbid_zone_visit_day_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'zone_visit_days rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_zone_visit_days_no_delete BEFORE DELETE ON commercial.zone_visit_days
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_zone_visit_day_delete();
CREATE FUNCTION commercial.forbid_document_type_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'document_types rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_document_types_no_delete BEFORE DELETE ON commercial.document_types
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_document_type_delete();
CREATE FUNCTION commercial.forbid_product_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'products rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_products_no_delete BEFORE DELETE ON commercial.products
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_product_delete();
CREATE FUNCTION commercial.forbid_product_price_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'product_prices rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_product_prices_no_delete BEFORE DELETE ON commercial.product_prices
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_product_price_delete();
"""

DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM commercial.zones) OR EXISTS (SELECT 1 FROM commercial.products) OR EXISTS (SELECT 1 FROM commercial.no_purchase_reason_mappings) OR EXISTS (SELECT 1 FROM commercial.source_category_mappings)
    THEN
        RAISE EXCEPTION 'downgrade refused: commercial zones, products or mappings exist'
            USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    # Inteligencia Comercial owns the `commercial` schema (ADR 006).
    op.execute("CREATE SCHEMA commercial")
    op.create_table(
        "no_purchase_reason_categories",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "length(btrim(code)) > 0 AND length(btrim(name)) > 0",
            name=op.f("ck_no_purchase_reason_categories_code_name_not_blank"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_no_purchase_reason_categories")),
        sa.UniqueConstraint("code", name=op.f("uq_no_purchase_reason_categories_code")),
        schema="commercial",
    )
    op.create_table(
        "price_lists",
        sa.Column("list_number", sa.SmallInteger(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("commercially_relevant", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('PRESALE_OFFICIAL', 'OUTLET', 'PROMO_VOLUME', 'SPECIAL', 'BANDERA', 'UNASSIGNED')",
            name=op.f("ck_price_lists_kind"),
        ),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_price_lists_name_not_blank")),
        sa.CheckConstraint("list_number > 0", name=op.f("ck_price_lists_list_number_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_lists")),
        sa.UniqueConstraint("code", name=op.f("uq_price_lists_code")),
        sa.UniqueConstraint("list_number", name=op.f("uq_price_lists_list_number")),
        schema="commercial",
    )
    op.create_table(
        "product_categories",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("default_product_kind", sa.String(length=20), nullable=True),
        sa.Column("default_analytics_eligible", sa.Boolean(), nullable=False),
        sa.Column("parent_id", sa.BigInteger(), nullable=True),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "default_product_kind IS NULL OR default_product_kind IN ('PRODUCT', 'CONTAINER', 'CRATE', 'SERVICE', 'BONUS', 'COMBO', 'PROMO', 'MERCH', 'UNCLASSIFIED')",
            name=op.f("ck_product_categories_default_product_kind"),
        ),
        sa.CheckConstraint(
            "length(btrim(code)) > 0 AND length(btrim(name)) > 0",
            name=op.f("ck_product_categories_code_name_not_blank"),
        ),
        sa.CheckConstraint("parent_id IS NULL OR parent_id <> id", name=op.f("ck_product_categories_not_own_parent")),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["commercial.product_categories.id"],
            name=op.f("fk_product_categories_parent_id_product_categories"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_categories")),
        sa.UniqueConstraint("code", name=op.f("uq_product_categories_code")),
        schema="commercial",
    )
    op.create_table(
        "product_families",
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "length(btrim(code)) > 0 AND length(btrim(name)) > 0", name=op.f("ck_product_families_code_name_not_blank")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_families")),
        sa.UniqueConstraint("code", name=op.f("uq_product_families_code")),
        schema="commercial",
    )
    op.create_table(
        "zone_classification_rules",
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("match_type", sa.String(length=20), server_default="CONTAINS", nullable=False),
        sa.Column("pattern", sa.String(length=120), nullable=False),
        sa.Column("zone_type", sa.String(length=20), nullable=False),
        sa.Column("requires_visit", sa.Boolean(), nullable=False),
        sa.Column("weekdays", postgresql.ARRAY(sa.SmallInteger()), server_default="{}", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("match_type IN ('CONTAINS')", name=op.f("ck_zone_classification_rules_match_type")),
        sa.CheckConstraint(
            "zone_type <> 'OUTLET' OR NOT requires_visit",
            name=op.f("ck_zone_classification_rules_outlet_without_visit"),
        ),
        sa.CheckConstraint(
            "zone_type IN ('PRESALE', 'BANDERA', 'OUTLET', 'OTHER')",
            name=op.f("ck_zone_classification_rules_zone_type"),
        ),
        sa.CheckConstraint(
            "(requires_visit AND cardinality(weekdays) > 0) OR (NOT requires_visit AND cardinality(weekdays) = 0)",
            name=op.f("ck_zone_classification_rules_weekdays_match_visit"),
        ),
        sa.CheckConstraint("length(btrim(pattern)) > 0", name=op.f("ck_zone_classification_rules_pattern_not_blank")),
        sa.CheckConstraint(
            "weekdays <@ ARRAY[1,2,3,4,5,6,7]::smallint[]", name=op.f("ck_zone_classification_rules_weekdays_valid")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zone_classification_rules")),
        sa.UniqueConstraint("match_type", "pattern", name=op.f("uq_zone_classification_rules_match_type_pattern")),
        schema="commercial",
    )
    op.create_table(
        "document_types",
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("source_name_key", sa.String(length=120), nullable=False),
        sa.Column("document_class", sa.String(length=20), nullable=False),
        sa.Column("sign", sa.SmallInteger(), nullable=False),
        sa.Column("commercial_universe", sa.Boolean(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "document_class <> 'UNCLASSIFIED' OR (NOT commercial_universe AND sign = 0)",
            name=op.f("ck_document_types_unclassified_outside"),
        ),
        sa.CheckConstraint(
            "document_class IN ('INVOICE', 'CREDIT_NOTE', 'DEBIT_NOTE', 'RETURN', 'RECEIPT', 'INTERNAL_MOVEMENT', 'EMPLOYEE_VOUCHER', 'OTHER', 'UNCLASSIFIED')",
            name=op.f("ck_document_types_document_class"),
        ),
        sa.CheckConstraint("NOT commercial_universe OR sign <> 0", name=op.f("ck_document_types_universe_has_sign")),
        sa.CheckConstraint("length(btrim(source_name_key)) > 0", name=op.f("ck_document_types_key_not_blank")),
        sa.CheckConstraint("sign IN (-1, 0, 1)", name=op.f("ck_document_types_sign")),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_document_types_decided_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_document_types")),
        sa.UniqueConstraint("source_name_key", name=op.f("uq_document_types_source_name_key")),
        schema="commercial",
    )
    op.create_table(
        "no_purchase_reason_mappings",
        sa.Column("source_system_id", sa.BigInteger(), nullable=False),
        sa.Column("reason_source", sa.String(length=200), nullable=False),
        sa.Column("reason_key", sa.String(length=200), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(category_id IS NULL) = (decided_at IS NULL)",
            name=op.f("ck_no_purchase_reason_mappings_decided_with_category"),
        ),
        sa.CheckConstraint("length(btrim(reason_key)) > 0", name=op.f("ck_no_purchase_reason_mappings_key_not_blank")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["commercial.no_purchase_reason_categories.id"],
            name="fk_no_purchase_reason_mappings_category_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"],
            ["users.id"],
            name=op.f("fk_no_purchase_reason_mappings_decided_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_no_purchase_reason_mappings_source_system_id_source_systems"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_no_purchase_reason_mappings")),
        sa.UniqueConstraint("source_system_id", "reason_key", name="uq_no_purchase_reason_mappings_key"),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_no_purchase_reason_mappings_category_id"),
        "no_purchase_reason_mappings",
        ["category_id"],
        unique=False,
        schema="commercial",
    )
    op.create_table(
        "source_category_mappings",
        sa.Column("source_system_id", sa.BigInteger(), nullable=False),
        sa.Column("source_category", sa.String(length=120), nullable=False),
        sa.Column("source_category_key", sa.String(length=120), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "length(btrim(source_category_key)) > 0", name=op.f("ck_source_category_mappings_key_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["commercial.product_categories.id"],
            name=op.f("fk_source_category_mappings_category_id_product_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_source_category_mappings_decided_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_source_category_mappings_source_system_id_source_systems"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_source_category_mappings")),
        sa.UniqueConstraint("source_system_id", "source_category_key", name="uq_source_category_mappings_key"),
        schema="commercial",
    )
    op.create_table(
        "products",
        sa.Column("source_code", sa.String(length=40), nullable=False),
        sa.Column("source_description", sa.String(length=200), nullable=True),
        sa.Column("source_category", sa.String(length=120), nullable=True),
        sa.Column("source_line", sa.String(length=120), nullable=True),
        sa.Column("source_capacity", sa.String(length=60), nullable=True),
        sa.Column("source_pack", sa.String(length=60), nullable=True),
        sa.Column("source_barcode", sa.String(length=60), nullable=True),
        sa.Column("source_internal_tax", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("source_discount_cap", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("source_vat", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("source_weight", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("source_extra", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("source_row_hash", sa.LargeBinary(), nullable=False),
        sa.Column("canonical_category_id", sa.BigInteger(), nullable=True),
        sa.Column("category_origin", sa.String(length=20), server_default="NONE", nullable=False),
        sa.Column("product_kind", sa.String(length=20), server_default="UNCLASSIFIED", nullable=False),
        sa.Column("analytics_eligible", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("eligibility_origin", sa.String(length=20), server_default="NONE", nullable=False),
        sa.Column("family_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("first_seen_job_id", sa.BigInteger(), nullable=False),
        sa.Column("last_seen_job_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(category_origin = 'NONE') = (canonical_category_id IS NULL)",
            name=op.f("ck_products_category_origin_match"),
        ),
        sa.CheckConstraint(
            "category_origin IN ('SOURCE_MAPPING', 'MANUAL', 'NONE')", name=op.f("ck_products_category_origin")
        ),
        sa.CheckConstraint(
            "eligibility_origin <> 'CATEGORY_DEFAULT' OR canonical_category_id IS NOT NULL",
            name=op.f("ck_products_default_needs_category"),
        ),
        sa.CheckConstraint(
            "eligibility_origin <> 'NONE' OR NOT analytics_eligible", name=op.f("ck_products_none_not_eligible")
        ),
        sa.CheckConstraint(
            "eligibility_origin IN ('NONE', 'CATEGORY_DEFAULT', 'MANUAL')", name=op.f("ck_products_eligibility_origin")
        ),
        sa.CheckConstraint(
            "product_kind IN ('PRODUCT', 'CONTAINER', 'CRATE', 'SERVICE', 'BONUS', 'COMBO', 'PROMO', 'MERCH', 'UNCLASSIFIED')",
            name=op.f("ck_products_product_kind"),
        ),
        sa.CheckConstraint(
            "NOT analytics_eligible OR canonical_category_id IS NOT NULL",
            name=op.f("ck_products_eligible_needs_category"),
        ),
        sa.CheckConstraint("length(btrim(source_code)) > 0", name=op.f("ck_products_source_code_not_blank")),
        sa.ForeignKeyConstraint(
            ["canonical_category_id"],
            ["commercial.product_categories.id"],
            name=op.f("fk_products_canonical_category_id_product_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["family_id"],
            ["commercial.product_families.id"],
            name=op.f("fk_products_family_id_product_families"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_products_first_seen_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_products_last_seen_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("source_code", name=op.f("uq_products_source_code")),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_products_canonical_category_id"),
        "products",
        ["canonical_category_id"],
        unique=False,
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_products_family_id"), "products", ["family_id"], unique=False, schema="commercial"
    )
    op.create_table(
        "zones",
        sa.Column("source_name", sa.String(length=120), nullable=False),
        sa.Column("name_key", sa.String(length=120), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column("zone_type", sa.String(length=20), nullable=False),
        sa.Column("requires_visit", sa.Boolean(), nullable=False),
        sa.Column("configuration_origin", sa.String(length=10), nullable=False),
        sa.Column("rule_id", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("configured_by", sa.BigInteger(), nullable=True),
        sa.Column("configured_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("first_seen_job_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(configuration_origin = 'NONE') = (zone_type = 'UNCLASSIFIED' AND rule_id IS NULL AND configured_by IS NULL)",
            name=op.f("ck_zones_origin_none_unconfigured"),
        ),
        sa.CheckConstraint(
            "configuration_origin <> 'MANUAL' OR configured_by IS NOT NULL", name=op.f("ck_zones_manual_has_author")
        ),
        sa.CheckConstraint(
            "configuration_origin <> 'RULE' OR rule_id IS NOT NULL", name=op.f("ck_zones_rule_origin_has_rule")
        ),
        sa.CheckConstraint(
            "configuration_origin IN ('RULE', 'MANUAL', 'NONE')", name=op.f("ck_zones_configuration_origin")
        ),
        sa.CheckConstraint(
            "zone_type IN ('PRESALE', 'BANDERA', 'OUTLET', 'OTHER', 'UNCLASSIFIED')", name=op.f("ck_zones_zone_type")
        ),
        sa.CheckConstraint(
            "zone_type NOT IN ('UNCLASSIFIED', 'OUTLET') OR NOT requires_visit", name=op.f("ck_zones_visit_by_type")
        ),
        sa.CheckConstraint("length(btrim(display_name)) > 0", name=op.f("ck_zones_display_name_not_blank")),
        sa.CheckConstraint("length(btrim(name_key)) > 0", name=op.f("ck_zones_name_key_not_blank")),
        sa.ForeignKeyConstraint(
            ["configured_by"], ["users.id"], name=op.f("fk_zones_configured_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_zones_first_seen_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["commercial.zone_classification_rules.id"],
            name=op.f("fk_zones_rule_id_zone_classification_rules"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zones")),
        sa.UniqueConstraint("name_key", name=op.f("uq_zones_name_key")),
        schema="commercial",
    )
    op.create_table(
        "product_prices",
        sa.Column("product_id", sa.BigInteger(), nullable=False),
        sa.Column("price_list_id", sa.BigInteger(), nullable=False),
        sa.Column("price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("import_job_id", sa.BigInteger(), nullable=False),
        sa.Column("voided_by_job_id", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("product_id"), "="),
            (sa.column("price_list_id"), "="),
            (sa.text("daterange(valid_from, valid_to, '[]')"), "&&"),
            where=sa.text("voided_by_job_id IS NULL"),
            using="gist",
            name="ex_product_prices_no_overlap",
        ),
        sa.CheckConstraint("price >= 0", name=op.f("ck_product_prices_price_not_negative")),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_product_prices_valid_range")),
        sa.CheckConstraint(
            "voided_by_job_id IS NULL OR valid_to IS NULL", name=op.f("ck_product_prices_voided_not_closed")
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_product_prices_import_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["price_list_id"],
            ["commercial.price_lists.id"],
            name=op.f("fk_product_prices_price_list_id_price_lists"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["commercial.products.id"],
            name=op.f("fk_product_prices_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_product_prices_voided_by_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_product_prices")),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_product_prices_product_id"),
        "product_prices",
        ["product_id"],
        unique=False,
        schema="commercial",
    )
    op.create_table(
        "zone_visit_days",
        sa.Column("zone_id", sa.BigInteger(), nullable=False),
        sa.Column("weekday", sa.SmallInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("zone_id"), "="),
            (sa.column("weekday"), "="),
            (sa.text("daterange(valid_from, valid_to, '[]')"), "&&"),
            where=sa.text("voided_at IS NULL"),
            using="gist",
            name="ex_zone_visit_days_no_overlap",
        ),
        sa.CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_zone_visit_days_valid_range")),
        sa.CheckConstraint("voided_at IS NULL OR valid_to IS NULL", name=op.f("ck_zone_visit_days_voided_not_closed")),
        sa.CheckConstraint("weekday BETWEEN 1 AND 7", name=op.f("ck_zone_visit_days_weekday")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_zone_visit_days_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["commercial.zones.id"], name=op.f("fk_zone_visit_days_zone_id_zones"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_zone_visit_days")),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_zone_visit_days_zone_id"), "zone_visit_days", ["zone_id"], unique=False, schema="commercial"
    )
    # ### end Alembic commands ###
    op.execute(TRIGGERS)


def downgrade() -> None:
    """Downgrade schema. Refused while any zone, product or decided mapping exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute("DROP TRIGGER trg_product_prices_no_delete ON commercial.product_prices")
    op.execute("DROP FUNCTION commercial.forbid_product_price_delete()")
    op.execute("DROP TRIGGER trg_products_no_delete ON commercial.products")
    op.execute("DROP FUNCTION commercial.forbid_product_delete()")
    op.execute("DROP TRIGGER trg_document_types_no_delete ON commercial.document_types")
    op.execute("DROP FUNCTION commercial.forbid_document_type_delete()")
    op.execute("DROP TRIGGER trg_zone_visit_days_no_delete ON commercial.zone_visit_days")
    op.execute("DROP FUNCTION commercial.forbid_zone_visit_day_delete()")
    op.execute("DROP TRIGGER trg_zones_no_delete ON commercial.zones")
    op.execute("DROP FUNCTION commercial.forbid_zone_delete()")
    op.drop_index(op.f("ix_commercial_zone_visit_days_zone_id"), table_name="zone_visit_days", schema="commercial")
    op.drop_table("zone_visit_days", schema="commercial")
    op.drop_index(op.f("ix_commercial_product_prices_product_id"), table_name="product_prices", schema="commercial")
    op.drop_table("product_prices", schema="commercial")
    op.drop_table("zones", schema="commercial")
    op.drop_index(op.f("ix_commercial_products_family_id"), table_name="products", schema="commercial")
    op.drop_index(op.f("ix_commercial_products_canonical_category_id"), table_name="products", schema="commercial")
    op.drop_table("products", schema="commercial")
    op.drop_table("source_category_mappings", schema="commercial")
    op.drop_index(
        op.f("ix_commercial_no_purchase_reason_mappings_category_id"),
        table_name="no_purchase_reason_mappings",
        schema="commercial",
    )
    op.drop_table("no_purchase_reason_mappings", schema="commercial")
    op.drop_table("document_types", schema="commercial")
    op.drop_table("zone_classification_rules", schema="commercial")
    op.drop_table("product_families", schema="commercial")
    op.drop_table("product_categories", schema="commercial")
    op.drop_table("price_lists", schema="commercial")
    op.drop_table("no_purchase_reason_categories", schema="commercial")
    # ### end Alembic commands ###
    op.execute("DROP SCHEMA commercial RESTRICT")
