"""platform: import files, jobs, staging rows and quality issues

Revision ID: 0033
Revises: 0032
Create Date: 2026-11-02 22:44:15.430483

F1.1b (ADR 014, Enzo's approval of F1.1a, 2026-11-02): the platform's import framework.

- `import_files`: every uploaded original, stored under UPLOAD_DIR with a UUID name (never modified nor deleted).
- `import_jobs`: every attempt to import a file as a type, with its status, counters, period or snapshot, declared
  scope, reader and detection metadata and, when it failed, why.
- `import_rows`: staging, one row per row of the file, with its raw cells, what the adapter proposed, its status,
  messages and the outcome of the confirmation.
- `data_quality_issues`: persistent issues, deduplicated by `(code, dedupe_key)` while open.

No import type exists yet (the Comercial adapters come in F1.3); nothing is seeded. The downgrade is refused while
any import job or quality issue exists: they are the history of what was imported and why.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0033"
down_revision: str | Sequence[str] | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM import_jobs) OR EXISTS (SELECT 1 FROM data_quality_issues)
    THEN
        RAISE EXCEPTION 'downgrade refused: import jobs or data quality issues exist'
            USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "import_files",
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("detected_format", sa.String(length=10), nullable=True),
        sa.Column("content_type", sa.String(length=120), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("uploaded_by", sa.BigInteger(), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "detected_format IS NULL OR detected_format IN ('XLS', 'XLSX', 'CSV')", name=op.f("ck_import_files_format")
        ),
        sa.CheckConstraint("size_bytes >= 0", name=op.f("ck_import_files_size_not_negative")),
        sa.ForeignKeyConstraint(
            ["uploaded_by"], ["users.id"], name=op.f("fk_import_files_uploaded_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_files")),
        sa.UniqueConstraint("storage_key", name=op.f("uq_import_files_storage_key")),
    )
    op.create_index("ix_import_files_sha256", "import_files", ["sha256"], unique=False)
    op.create_table(
        "import_jobs",
        sa.Column("source_system_id", sa.BigInteger(), nullable=True),
        sa.Column("import_type", sa.String(length=80), nullable=True),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("file_id", sa.BigInteger(), nullable=False),
        sa.Column("parser_version", sa.String(length=30), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("confirmed_by", sa.BigInteger(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("analyzed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rows_detected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_processed", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_valid", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_rejected", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_duplicate", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_skipped", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_pending_resolution", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_inserted", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_updated", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_unchanged", sa.Integer(), server_default="0", nullable=False),
        sa.Column("rows_superseded", sa.Integer(), server_default="0", nullable=False),
        sa.Column("warning_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=True),
        sa.Column("period_from", sa.Date(), nullable=True),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reprocess_of_job_id", sa.BigInteger(), nullable=True),
        sa.Column("duplicate_of_job_id", sa.BigInteger(), nullable=True),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("failure_reason", sa.String(length=40), nullable=True),
        sa.Column("failure_detail", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status <> 'duplicate' OR duplicate_of_job_id IS NOT NULL",
            name=op.f("ck_import_jobs_duplicate_has_original"),
        ),
        sa.CheckConstraint(
            "status <> 'failed' OR failure_reason IS NOT NULL", name=op.f("ck_import_jobs_failed_has_reason")
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'completed_with_warnings', 'failed', 'duplicate', 'cancelled')",
            name=op.f("ck_import_jobs_status"),
        ),
        sa.CheckConstraint(
            "period_from IS NULL OR period_to IS NULL OR period_from <= period_to",
            name=op.f("ck_import_jobs_period_order"),
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], name=op.f("fk_import_jobs_confirmed_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_import_jobs_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["duplicate_of_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_import_jobs_duplicate_of_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["file_id"], ["import_files.id"], name=op.f("fk_import_jobs_file_id_import_files"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reprocess_of_job_id"],
            ["import_jobs.id"],
            name=op.f("fk_import_jobs_reprocess_of_job_id_import_jobs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_system_id"],
            ["source_systems.id"],
            name=op.f("fk_import_jobs_source_system_id_source_systems"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_jobs")),
    )
    op.create_index("ix_import_jobs_file_id", "import_jobs", ["file_id"], unique=False)
    op.create_index("ix_import_jobs_type_status", "import_jobs", ["import_type", "status"], unique=False)
    op.create_table(
        "import_rows",
        sa.Column("job_id", sa.BigInteger(), nullable=False),
        sa.Column("sheet", sa.String(length=100), nullable=False),
        sa.Column("row_number", sa.Integer(), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("normalized", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("business_key", sa.Text(), nullable=True),
        sa.Column("row_hash", sa.LargeBinary(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=20), nullable=True),
        sa.Column("entity_type", sa.String(length=60), nullable=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("messages", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "outcome IS NULL OR outcome IN ('inserted', 'updated', 'unchanged', 'superseded')",
            name=op.f("ck_import_rows_outcome"),
        ),
        sa.CheckConstraint(
            "status IN ('valid', 'warning', 'rejected', 'duplicate', 'pending_resolution', 'skipped')",
            name=op.f("ck_import_rows_status"),
        ),
        sa.ForeignKeyConstraint(
            ["job_id"], ["import_jobs.id"], name=op.f("fk_import_rows_job_id_import_jobs"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_import_rows")),
        sa.UniqueConstraint("job_id", "sheet", "row_number", name=op.f("uq_import_rows_job_id_sheet_row_number")),
    )
    op.create_index("ix_import_rows_job_id_status", "import_rows", ["job_id", "status"], unique=False)
    op.create_table(
        "data_quality_issues",
        sa.Column("code", sa.String(length=80), nullable=False),
        sa.Column("severity", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=10), server_default="open", nullable=False),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.String(length=60), nullable=True),
        sa.Column("entity_id", sa.BigInteger(), nullable=True),
        sa.Column("external_ref", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("job_id", sa.BigInteger(), nullable=True),
        sa.Column("row_id", sa.BigInteger(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False),
        sa.Column("occurrences", sa.Integer(), server_default="1", nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_by", sa.BigInteger(), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolution_note", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("severity IN ('error', 'warning', 'info')", name=op.f("ck_data_quality_issues_severity")),
        sa.CheckConstraint("status IN ('open', 'resolved', 'ignored')", name=op.f("ck_data_quality_issues_status")),
        sa.CheckConstraint("occurrences > 0", name=op.f("ck_data_quality_issues_occurrences_positive")),
        sa.ForeignKeyConstraint(
            ["job_id"], ["import_jobs.id"], name=op.f("fk_data_quality_issues_job_id_import_jobs"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["resolved_by"], ["users.id"], name=op.f("fk_data_quality_issues_resolved_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["row_id"], ["import_rows.id"], name=op.f("fk_data_quality_issues_row_id_import_rows"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_data_quality_issues")),
    )
    op.create_index("ix_data_quality_issues_job_id", "data_quality_issues", ["job_id"], unique=False)
    op.create_index("ix_data_quality_issues_status_code", "data_quality_issues", ["status", "code"], unique=False)
    op.create_index(
        "uq_data_quality_issues_open_code_dedupe_key",
        "data_quality_issues",
        ["code", "dedupe_key"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    """Downgrade schema. Refused while any import job or quality issue exists."""
    op.execute(DOWNGRADE_GUARD)
    op.drop_index(
        "uq_data_quality_issues_open_code_dedupe_key",
        table_name="data_quality_issues",
        postgresql_where=sa.text("status = 'open'"),
    )
    op.drop_index("ix_data_quality_issues_status_code", table_name="data_quality_issues")
    op.drop_index("ix_data_quality_issues_job_id", table_name="data_quality_issues")
    op.drop_table("data_quality_issues")
    op.drop_index("ix_import_rows_job_id_status", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_jobs_type_status", table_name="import_jobs")
    op.drop_index("ix_import_jobs_file_id", table_name="import_jobs")
    op.drop_table("import_jobs")
    op.drop_index("ix_import_files_sha256", table_name="import_files")
    op.drop_table("import_files")
