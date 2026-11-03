from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin

ISSUE_SEVERITIES = ("error", "warning", "info")
ISSUE_STATUSES = ("open", "resolved", "ignored")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class DataQualityIssue(IdMixin, Base):
    """A problem in the data, open until its condition disappears or someone resolves or ignores it (DATA_MODEL.md
    §7.5). An open issue with the same `(code, dedupe_key)` counts another occurrence instead of a new row. `details`
    never carries PII."""

    __tablename__ = "data_quality_issues"
    __table_args__ = (
        CheckConstraint(_in("severity", ISSUE_SEVERITIES), name="severity"),
        CheckConstraint(_in("status", ISSUE_STATUSES), name="status"),
        CheckConstraint("occurrences > 0", name="occurrences_positive"),
        Index(
            "uq_data_quality_issues_open_code_dedupe_key",
            "code",
            "dedupe_key",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
        Index("ix_data_quality_issues_status_code", "status", "code"),
        Index("ix_data_quality_issues_job_id", "job_id"),
    )

    code: Mapped[str] = mapped_column(String(80))
    severity: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(10), server_default="open")
    dedupe_key: Mapped[str] = mapped_column(Text)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[int | None] = mapped_column(BigInteger)
    external_ref: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    row_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_rows.id", ondelete="RESTRICT"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    occurrences: Mapped[int] = mapped_column(Integer, server_default="1")
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_note: Mapped[str | None] = mapped_column(Text)
