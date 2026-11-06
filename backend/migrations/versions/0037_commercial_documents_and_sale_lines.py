"""commercial: documents and sale lines

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-05 12:30:00.000000

F1.3 (DATA_MODEL §6.1-§6.2, docs/IMPORTS.md §3.3-§3.4, §5.3): the STORE SYS facts.

- `documents`: Comprobantes emitidos, one per `document_key`, without items; class, sign and universe are read
  from `document_types`, never stored.
- `sale_lines`: Ventas artículos/clientes, units per customer, product and day; identical lines are all kept
  (`occurrence`); a re-import replaces by period and scope (`SUPERSEDED`), never deletes.

Neither is ever deleted (triggers). The downgrade is refused while any document or sale line exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0037"
down_revision: str | Sequence[str] | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGERS = """
CREATE FUNCTION commercial.forbid_document_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'documents rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_documents_no_delete BEFORE DELETE ON commercial.documents
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_document_delete();
CREATE FUNCTION commercial.forbid_sale_line_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'sale_lines rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_sale_lines_no_delete BEFORE DELETE ON commercial.sale_lines
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_sale_line_delete();
"""

DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM commercial.documents) OR EXISTS (SELECT 1 FROM commercial.sale_lines) THEN
        RAISE EXCEPTION 'downgrade refused: documents or sale lines exist' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.create_table(
        "documents",
        sa.Column("document_type_id", sa.BigInteger(), nullable=False),
        sa.Column("source_type_name", sa.String(length=120), nullable=False),
        sa.Column("letter", sa.String(length=4), nullable=True),
        sa.Column("point_of_sale", sa.String(length=10), nullable=False),
        sa.Column("number", sa.String(length=20), nullable=False),
        sa.Column("document_key", sa.String(length=160), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("issued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("source_customer_code", sa.String(length=40), nullable=True),
        sa.Column("source_customer_name", sa.String(length=200), nullable=True),
        sa.Column("net_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("total_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("discount_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("import_job_id", sa.BigInteger(), nullable=False),
        sa.Column("import_row_id", sa.BigInteger(), nullable=True),
        sa.Column("source_row_hash", sa.LargeBinary(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("length(btrim(document_key)) > 0", name=op.f("ck_documents_key_not_blank")),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name=op.f("fk_documents_customer_id_customers"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["document_type_id"],
            ["commercial.document_types.id"],
            name=op.f("fk_documents_document_type_id_document_types"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_documents_import_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_row_id"],
            ["import_rows.id"],
            name=op.f("fk_documents_import_row_id_import_rows"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("document_key", name=op.f("uq_documents_document_key")),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_documents_business_date"), "documents", ["business_date"], unique=False, schema="commercial"
    )
    op.create_index(
        op.f("ix_commercial_documents_import_job_id"), "documents", ["import_job_id"], unique=False, schema="commercial"
    )
    op.create_index(
        "ix_documents_customer_id_business_date",
        "documents",
        ["customer_id", "business_date"],
        unique=False,
        schema="commercial",
    )
    op.create_index(
        "ix_documents_document_type_id_business_date",
        "documents",
        ["document_type_id", "business_date"],
        unique=False,
        schema="commercial",
    )
    op.create_table(
        "sale_lines",
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("customer_id", sa.BigInteger(), nullable=True),
        sa.Column("source_customer_code", sa.String(length=40), nullable=False),
        sa.Column("source_customer_name", sa.String(length=200), nullable=True),
        sa.Column("customer_mapping_id", sa.BigInteger(), nullable=True),
        sa.Column("product_id", sa.BigInteger(), nullable=True),
        sa.Column("source_product_code", sa.String(length=40), nullable=False),
        sa.Column("source_product_description", sa.String(length=200), nullable=True),
        sa.Column("source_category", sa.String(length=120), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=12, scale=3), nullable=False),
        sa.Column("price_list_id", sa.BigInteger(), nullable=True),
        sa.Column("source_price_list", sa.String(length=60), nullable=True),
        sa.Column("salesperson_id", sa.BigInteger(), nullable=True),
        sa.Column("source_seller", sa.String(length=200), nullable=True),
        sa.Column("seller_mapping_id", sa.BigInteger(), nullable=True),
        sa.Column("zone_id", sa.BigInteger(), nullable=True),
        sa.Column("source_zone", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="ACTIVE", nullable=False),
        sa.Column("superseded_by_job_id", sa.BigInteger(), nullable=True),
        sa.Column("import_job_id", sa.BigInteger(), nullable=False),
        sa.Column("import_row_id", sa.BigInteger(), nullable=True),
        sa.Column("occurrence", sa.Integer(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'SUPERSEDED') = (superseded_by_job_id IS NOT NULL)", name=op.f("ck_sale_lines_superseded_by_job")
        ),
        sa.CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED')", name=op.f("ck_sale_lines_status")),
        sa.CheckConstraint("occurrence >= 1", name=op.f("ck_sale_lines_occurrence_positive")),
        sa.ForeignKeyConstraint(
            ["customer_id"], ["customers.id"], name=op.f("fk_sale_lines_customer_id_customers"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["customer_mapping_id"],
            ["external_entity_mappings.id"],
            name=op.f("fk_sale_lines_customer_mapping_id_external_entity_mappings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_sale_lines_import_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_row_id"],
            ["import_rows.id"],
            name=op.f("fk_sale_lines_import_row_id_import_rows"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["price_list_id"],
            ["commercial.price_lists.id"],
            name=op.f("fk_sale_lines_price_list_id_price_lists"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["product_id"],
            ["commercial.products.id"],
            name=op.f("fk_sale_lines_product_id_products"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["salesperson_id"],
            ["employees.id"],
            name=op.f("fk_sale_lines_salesperson_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["seller_mapping_id"],
            ["external_entity_mappings.id"],
            name=op.f("fk_sale_lines_seller_mapping_id_external_entity_mappings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_sale_lines_superseded_by_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"], ["commercial.zones.id"], name=op.f("fk_sale_lines_zone_id_zones"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sale_lines")),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_sale_lines_import_job_id"),
        "sale_lines",
        ["import_job_id"],
        unique=False,
        schema="commercial",
    )
    op.create_index(
        "ix_sale_lines_active_business_date",
        "sale_lines",
        ["business_date"],
        unique=False,
        schema="commercial",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.create_index(
        "ix_sale_lines_customer_id_business_date",
        "sale_lines",
        ["customer_id", "business_date"],
        unique=False,
        schema="commercial",
    )
    op.create_index(
        "ix_sale_lines_product_id_business_date",
        "sale_lines",
        ["product_id", "business_date"],
        unique=False,
        schema="commercial",
    )
    op.execute(TRIGGERS)


def downgrade() -> None:
    """Downgrade schema. Refused while any row exists: never drops history."""
    op.execute(DOWNGRADE_GUARD)
    op.execute("DROP TRIGGER trg_sale_lines_no_delete ON commercial.sale_lines")
    op.execute("DROP FUNCTION commercial.forbid_sale_line_delete()")
    op.execute("DROP TRIGGER trg_documents_no_delete ON commercial.documents")
    op.execute("DROP FUNCTION commercial.forbid_document_delete()")
    op.drop_index("ix_sale_lines_product_id_business_date", table_name="sale_lines", schema="commercial")
    op.drop_index("ix_sale_lines_customer_id_business_date", table_name="sale_lines", schema="commercial")
    op.drop_index(
        "ix_sale_lines_active_business_date",
        table_name="sale_lines",
        schema="commercial",
        postgresql_where=sa.text("status = 'ACTIVE'"),
    )
    op.drop_index(op.f("ix_commercial_sale_lines_import_job_id"), table_name="sale_lines", schema="commercial")
    op.drop_table("sale_lines", schema="commercial")
    op.drop_index("ix_documents_document_type_id_business_date", table_name="documents", schema="commercial")
    op.drop_index("ix_documents_customer_id_business_date", table_name="documents", schema="commercial")
    op.drop_index(op.f("ix_commercial_documents_import_job_id"), table_name="documents", schema="commercial")
    op.drop_index(op.f("ix_commercial_documents_business_date"), table_name="documents", schema="commercial")
    op.drop_table("documents", schema="commercial")
