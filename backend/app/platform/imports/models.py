from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.platform.imports.registry import ROW_OUTCOMES, ROW_STATUSES

FILE_FORMATS = ("XLS", "XLSX", "CSV")
JOB_STATUSES = ("pending", "processing", "completed", "completed_with_warnings", "failed", "duplicate", "cancelled")
# A job in one of these wrote everything it will ever write (or nothing): it never changes again.
FINAL_STATUSES = ("completed", "completed_with_warnings", "failed", "duplicate", "cancelled")
COMPLETED_STATUSES = ("completed", "completed_with_warnings")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class ImportFile(IdMixin, Base):
    """An uploaded file, stored once under UPLOAD_DIR with a UUID name, read-only, never modified nor deleted by the
    app (DATA_MODEL.md §7.1). `detected_format` is NULL when its signature matched no reader."""

    __tablename__ = "import_files"
    __table_args__ = (
        CheckConstraint(f"detected_format IS NULL OR {_in('detected_format', FILE_FORMATS)}", name="format"),
        CheckConstraint("size_bytes >= 0", name="size_not_negative"),
        Index("ix_import_files_sha256", "sha256"),
    )

    original_filename: Mapped[str] = mapped_column(String(255))  # sanitized, only shown
    storage_key: Mapped[str] = mapped_column(String(64), unique=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    detected_format: Mapped[str | None] = mapped_column(String(10))
    content_type: Mapped[str | None] = mapped_column(String(120))
    sha256: Mapped[str] = mapped_column(String(64))
    uploaded_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ImportJob(IdMixin, TimestampMixin, Base):
    """One attempt to import one file as one import type (DATA_MODEL.md §7.2). Every attempt is kept, also the
    failed, duplicate and cancelled ones."""

    __tablename__ = "import_jobs"
    __table_args__ = (
        CheckConstraint(_in("status", JOB_STATUSES), name="status"),
        CheckConstraint("status <> 'duplicate' OR duplicate_of_job_id IS NOT NULL", name="duplicate_has_original"),
        CheckConstraint("status <> 'failed' OR failure_reason IS NOT NULL", name="failed_has_reason"),
        CheckConstraint("period_from IS NULL OR period_to IS NULL OR period_from <= period_to", name="period_order"),
        Index("ix_import_jobs_type_status", "import_type", "status"),
        Index("ix_import_jobs_file_id", "file_id"),
    )

    source_system_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("source_systems.id", ondelete="RESTRICT")
    )
    import_type: Mapped[str | None] = mapped_column(String(80))  # NULL until detected (or when detection failed)
    status: Mapped[str] = mapped_column(String(30))
    file_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_files.id", ondelete="RESTRICT"))
    parser_version: Mapped[str | None] = mapped_column(String(30))
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    confirmed_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    analyzed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rows_detected: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_processed: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_valid: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_rejected: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_duplicate: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_skipped: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_pending_resolution: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_inserted: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_updated: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_unchanged: Mapped[int] = mapped_column(Integer, server_default="0")
    rows_superseded: Mapped[int] = mapped_column(Integer, server_default="0")
    warning_count: Mapped[int] = mapped_column(Integer, server_default="0")
    error_count: Mapped[int] = mapped_column(Integer, server_default="0")
    snapshot_date: Mapped[date | None] = mapped_column(Date)
    period_from: Mapped[date | None] = mapped_column(Date)
    period_to: Mapped[date | None] = mapped_column(Date)
    scope: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reprocess_of_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT")
    )
    duplicate_of_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT")
    )
    # How the file was read and detected, and the adapter's preview; never row values.
    job_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, server_default="{}")
    failure_reason: Mapped[str | None] = mapped_column(String(40))
    failure_detail: Mapped[str | None] = mapped_column(Text)


class ImportRow(IdMixin, Base):
    """Staging: every row of the file, title and blank rows included (`skipped`), with what the adapter made of it
    (DATA_MODEL.md §7.3). Not purged by default; the source of reprocessing and audit."""

    __tablename__ = "import_rows"
    __table_args__ = (
        UniqueConstraint("job_id", "sheet", "row_number"),
        CheckConstraint(_in("status", ROW_STATUSES), name="status"),
        CheckConstraint(f"outcome IS NULL OR {_in('outcome', ROW_OUTCOMES)}", name="outcome"),
        Index("ix_import_rows_job_id_status", "job_id", "status"),
    )

    job_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    sheet: Mapped[str] = mapped_column(String(100))
    row_number: Mapped[int] = mapped_column(Integer)
    raw: Mapped[list[Any]] = mapped_column(JSONB)
    normalized: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    business_key: Mapped[str | None] = mapped_column(Text)
    row_hash: Mapped[bytes] = mapped_column(LargeBinary)
    status: Mapped[str] = mapped_column(String(20))
    outcome: Mapped[str | None] = mapped_column(String(20))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, server_default="[]")
