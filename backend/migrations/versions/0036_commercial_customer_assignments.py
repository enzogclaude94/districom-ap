"""commercial: customer zone and salesperson assignments

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-05 12:30:00.000000

F1.3 (DATA_MODEL §5, docs/IMPORTS.md §3.2): who a customer belonged to, and when.

- `customer_zone_assignments` and `customer_salesperson_assignments`: one row per period, no overlaps for a customer
  (exclusion constraint over the rows not voided), never deleted (triggers). A change closes the open row the day
  before the snapshot and opens another; two snapshots of the same day void the row the first one opened.

The downgrade is refused while any assignment exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0036"
down_revision: str | Sequence[str] | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGERS = """
CREATE FUNCTION commercial.forbid_customer_salesperson_assignment_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'customer_salesperson_assignments rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_customer_salesperson_assignments_no_delete BEFORE DELETE ON commercial.customer_salesperson_assignments
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_customer_salesperson_assignment_delete();
CREATE FUNCTION commercial.forbid_customer_zone_assignment_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'customer_zone_assignments rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_customer_zone_assignments_no_delete BEFORE DELETE ON commercial.customer_zone_assignments
    FOR EACH ROW EXECUTE FUNCTION commercial.forbid_customer_zone_assignment_delete();
"""

DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM commercial.customer_salesperson_assignments) OR EXISTS (SELECT 1 FROM commercial.customer_zone_assignments) THEN
        RAISE EXCEPTION 'downgrade refused: customer assignments exist' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""


def upgrade() -> None:
    op.create_table(
        "customer_salesperson_assignments",
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("salesperson_id", sa.BigInteger(), nullable=True),
        sa.Column("source_seller_label", sa.String(length=200), nullable=True),
        sa.Column("source_seller_key", sa.String(length=200), nullable=True),
        sa.Column("seller_mapping_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("import_job_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_by_job_id", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        postgresql.ExcludeConstraint(
            (sa.column("customer_id"), "="),
            (sa.text("daterange(valid_from, valid_to, '[]')"), "&&"),
            where=sa.text("voided_by_job_id IS NULL"),
            using="gist",
            name="ex_customer_salesperson_assignments_no_overlap",
        ),
        sa.CheckConstraint(
            "source IN ('STORE_SYS', 'AXUM', 'MANUAL')", name=op.f("ck_customer_salesperson_assignments_source")
        ),
        sa.CheckConstraint(
            "salesperson_id IS NOT NULL OR (source_seller_label IS NOT NULL AND length(btrim(source_seller_label)) > 0)",
            name=op.f("ck_customer_salesperson_assignments_who"),
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_customer_salesperson_assignments_valid_range")
        ),
        sa.CheckConstraint(
            "voided_by_job_id IS NULL OR valid_to IS NULL",
            name=op.f("ck_customer_salesperson_assignments_voided_not_closed"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_customer_salesperson_assignments_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_salesperson_assignments_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_customer_salesperson_assignments_import_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["salesperson_id"],
            ["employees.id"],
            name=op.f("fk_customer_salesperson_assignments_salesperson_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["seller_mapping_id"],
            ["external_entity_mappings.id"],
            name=op.f("fk_customer_salesperson_assignments_seller_mapping_id_external_entity_mappings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_customer_salesperson_assignments_voided_by_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_salesperson_assignments")),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_customer_salesperson_assignments_customer_id"),
        "customer_salesperson_assignments",
        ["customer_id"],
        unique=False,
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_customer_salesperson_assignments_salesperson_id"),
        "customer_salesperson_assignments",
        ["salesperson_id"],
        unique=False,
        schema="commercial",
    )
    op.create_table(
        "customer_zone_assignments",
        sa.Column("customer_id", sa.BigInteger(), nullable=False),
        sa.Column("zone_id", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("import_job_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_by_job_id", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        postgresql.ExcludeConstraint(
            (sa.column("customer_id"), "="),
            (sa.text("daterange(valid_from, valid_to, '[]')"), "&&"),
            where=sa.text("voided_by_job_id IS NULL"),
            using="gist",
            name="ex_customer_zone_assignments_no_overlap",
        ),
        sa.CheckConstraint(
            "source IN ('STORE_SYS', 'AXUM', 'MANUAL')", name=op.f("ck_customer_zone_assignments_source")
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_customer_zone_assignments_valid_range")
        ),
        sa.CheckConstraint(
            "voided_by_job_id IS NULL OR valid_to IS NULL", name=op.f("ck_customer_zone_assignments_voided_not_closed")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_customer_zone_assignments_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["customer_id"],
            ["customers.id"],
            name=op.f("fk_customer_zone_assignments_customer_id_customers"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["import_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_customer_zone_assignments_import_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_customer_zone_assignments_voided_by_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["zone_id"],
            ["commercial.zones.id"],
            name=op.f("fk_customer_zone_assignments_zone_id_zones"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customer_zone_assignments")),
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_customer_zone_assignments_customer_id"),
        "customer_zone_assignments",
        ["customer_id"],
        unique=False,
        schema="commercial",
    )
    op.create_index(
        op.f("ix_commercial_customer_zone_assignments_zone_id"),
        "customer_zone_assignments",
        ["zone_id"],
        unique=False,
        schema="commercial",
    )
    op.execute(TRIGGERS)


def downgrade() -> None:
    """Downgrade schema. Refused while any row exists: never drops history."""
    op.execute(DOWNGRADE_GUARD)
    op.execute("DROP TRIGGER trg_customer_zone_assignments_no_delete ON commercial.customer_zone_assignments")
    op.execute("DROP FUNCTION commercial.forbid_customer_zone_assignment_delete()")
    op.execute(
        "DROP TRIGGER trg_customer_salesperson_assignments_no_delete ON commercial.customer_salesperson_assignments"
    )
    op.execute("DROP FUNCTION commercial.forbid_customer_salesperson_assignment_delete()")
    op.drop_index(
        op.f("ix_commercial_customer_zone_assignments_zone_id"),
        table_name="customer_zone_assignments",
        schema="commercial",
    )
    op.drop_index(
        op.f("ix_commercial_customer_zone_assignments_customer_id"),
        table_name="customer_zone_assignments",
        schema="commercial",
    )
    op.drop_table("customer_zone_assignments", schema="commercial")
    op.drop_index(
        op.f("ix_commercial_customer_salesperson_assignments_salesperson_id"),
        table_name="customer_salesperson_assignments",
        schema="commercial",
    )
    op.drop_index(
        op.f("ix_commercial_customer_salesperson_assignments_customer_id"),
        table_name="customer_salesperson_assignments",
        schema="commercial",
    )
    op.drop_table("customer_salesperson_assignments", schema="commercial")
