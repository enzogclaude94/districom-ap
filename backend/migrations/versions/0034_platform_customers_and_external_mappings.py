"""platform: customers and external entity mappings

Revision ID: 0034
Revises: 0033
Create Date: 2026-11-03 23:30:00.000000

F1.2 (ADR 014 §2.6, Enzo 2026-11-03): the platform's customer identity and the one table that links an external
code or name of any source to a platform entity.

- `customers`: one row per real customer; CUIT optional, validated and **not unique**; `display_name_key` only for
  exact-name candidates (never a match by itself). Created and refreshed only by the STORE SYS master (F1.3).
- `external_entity_mappings`: `(entity_type, source_system, namespace, external_id)` unique, with status
  (pending, ambiguous, confirmed, rejected, excluded), method and the decision's author, instant and reason.

Neither table allows DELETE (triggers): a customer or a decision is history. The downgrade is refused while any row
exists.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0034"
down_revision: str | Sequence[str] | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRIGGERS = """
CREATE FUNCTION forbid_customer_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'customers rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_customers_no_delete BEFORE DELETE ON customers
    FOR EACH ROW EXECUTE FUNCTION forbid_customer_delete();
CREATE FUNCTION forbid_external_entity_mapping_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'external_entity_mappings rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_external_entity_mappings_no_delete BEFORE DELETE ON external_entity_mappings
    FOR EACH ROW EXECUTE FUNCTION forbid_external_entity_mapping_delete();
"""

DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM customers) OR EXISTS (SELECT 1 FROM external_entity_mappings)
    THEN
        RAISE EXCEPTION 'downgrade refused: customers or external entity mappings exist'
            USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "customers",
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("display_name_key", sa.String(length=200), nullable=False),
        sa.Column("legal_name", sa.String(length=200), nullable=True),
        sa.Column("tax_id", sa.String(length=11), nullable=True),
        sa.Column("address", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=60), nullable=True),
        sa.Column("is_generic", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("first_seen_job_id", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_job_id", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_on", sa.Date(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(first_seen_job_id IS NULL) = (last_seen_job_id IS NULL)", name=op.f("ck_customers_seen_jobs_together")
        ),
        sa.CheckConstraint("length(btrim(display_name)) > 0", name=op.f("ck_customers_display_name_not_blank")),
        sa.CheckConstraint("tax_id IS NULL OR public.cuit_is_valid(tax_id)", name=op.f("ck_customers_tax_id_valid")),
        sa.ForeignKeyConstraint(
            ["first_seen_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_customers_first_seen_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_customers_last_seen_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_customers")),
    )
    op.create_index(op.f("ix_customers_display_name_key"), "customers", ["display_name_key"], unique=False)
    op.create_index(op.f("ix_customers_tax_id"), "customers", ["tax_id"], unique=False)
    op.create_table(
        "external_entity_mappings",
        sa.Column("entity_type", sa.String(length=40), nullable=False),
        sa.Column("source_system_id", sa.BigInteger(), nullable=False),
        sa.Column("namespace", sa.String(length=40), nullable=False),
        sa.Column("external_id", sa.String(length=200), nullable=False),
        sa.Column("external_label", sa.String(length=200), nullable=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("method", sa.String(length=30), nullable=True),
        sa.Column("candidates", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("first_seen_job_id", sa.BigInteger(), nullable=True),
        sa.Column("last_seen_job_id", sa.BigInteger(), nullable=True),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(status = 'confirmed') = (entity_id IS NOT NULL) AND (status <> 'confirmed' OR method IS NOT NULL)",
            name=op.f("ck_external_entity_mappings_confirmed_has_entity"),
        ),
        sa.CheckConstraint(
            "method IS NULL OR method IN ('created_from_master', 'exact_external_id', 'rule', 'manual')",
            name=op.f("ck_external_entity_mappings_method"),
        ),
        sa.CheckConstraint(
            "status <> 'ambiguous' OR jsonb_array_length(candidates) >= 2",
            name=op.f("ck_external_entity_mappings_ambiguous_has_candidates"),
        ),
        sa.CheckConstraint(
            "status <> 'pending' OR jsonb_array_length(candidates) <= 1",
            name=op.f("ck_external_entity_mappings_pending_one_candidate"),
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'ambiguous', 'confirmed', 'rejected', 'excluded')",
            name=op.f("ck_external_entity_mappings_status"),
        ),
        sa.CheckConstraint(
            "length(btrim(external_id)) > 0", name=op.f("ck_external_entity_mappings_external_id_not_blank")
        ),
        sa.CheckConstraint(
            "length(btrim(namespace)) > 0", name=op.f("ck_external_entity_mappings_namespace_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_external_entity_mappings_decided_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_external_entity_mappings_first_seen_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_external_entity_mappings_last_seen_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_external_entity_mappings_source_system_id_source_systems"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_entity_mappings")),
        sa.UniqueConstraint(
            "entity_type", "source_system_id", "namespace", "external_id", name="uq_external_entity_mappings_key"
        ),
    )
    op.create_index(
        "ix_external_entity_mappings_entity", "external_entity_mappings", ["entity_type", "entity_id"], unique=False
    )
    op.create_index("ix_external_entity_mappings_status", "external_entity_mappings", ["status"], unique=False)
    op.execute(TRIGGERS)


def downgrade() -> None:
    """Downgrade schema. Refused while any customer or mapping exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute("DROP TRIGGER trg_external_entity_mappings_no_delete ON external_entity_mappings")
    op.execute("DROP FUNCTION forbid_external_entity_mapping_delete()")
    op.execute("DROP TRIGGER trg_customers_no_delete ON customers")
    op.execute("DROP FUNCTION forbid_customer_delete()")
    op.drop_index("ix_external_entity_mappings_status", table_name="external_entity_mappings")
    op.drop_index("ix_external_entity_mappings_entity", table_name="external_entity_mappings")
    op.drop_table("external_entity_mappings")
    op.drop_index(op.f("ix_customers_tax_id"), table_name="customers")
    op.drop_index(op.f("ix_customers_display_name_key"), table_name="customers")
    op.drop_table("customers")
