"""A no-purchase event of AXUM's «Compró / No compró» report (ADR 018, DATA_MODEL §6.3).

One row of the report with a reason is one event: the report recorded that the salesperson registered that reason
for that customer at that moment. It is not a visit and nothing is inferred from it (visits, coverage, route
compliance, conversion). Rows where the customer bought (`Si`) are not events: they stay only in the import's
staging.

Identical rows are separate events (`occurrence`); there is no natural key and no UNIQUE over date, customer,
salesperson and reason. A re-import over a period already loaded always waits for an admin; replacing marks the
earlier events `SUPERSEDED`, never deletes them. What the source said (`source_*`, the report's own times) is kept
beside what it resolved to; a foreign key stays NULL while its mapping is pending.
"""

from datetime import date, datetime, time

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, Integer, String, Time, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.modules.commercial.config.models import SCHEMA

EVENT_STATUSES = ("ACTIVE", "SUPERSEDED")


class NoPurchaseEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "no_purchase_events"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED')", name="status"),
        CheckConstraint("(status = 'SUPERSEDED') = (superseded_by_job_id IS NOT NULL)", name="superseded_by_job"),
        CheckConstraint("occurrence >= 1", name="occurrence_positive"),
        CheckConstraint("length(btrim(reason_source)) > 0", name="reason_source_not_blank"),
        CheckConstraint("duration_seconds IS NULL OR duration_seconds >= 0", name="duration_not_negative"),
        Index(
            "ix_no_purchase_events_active_business_date", "business_date", postgresql_where=text("status = 'ACTIVE'")
        ),
        Index("ix_no_purchase_events_customer_id_business_date", "customer_id", "business_date"),
        Index("ix_no_purchase_events_salesperson_id_business_date", "salesperson_id", "business_date"),
        {"schema": SCHEMA},
    )

    # The day of «Fecha y Hora Subida»: the only full date the report has (ADR 018 §4).
    business_date: Mapped[date] = mapped_column(Date)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="RESTRICT"))
    source_customer_code: Mapped[str] = mapped_column(String(40))  # «Cliente Id»
    source_customer_name: Mapped[str | None] = mapped_column(String(200))  # PII: never logged
    source_customer_address: Mapped[str | None] = mapped_column(String(300))  # PII: never logged
    customer_mapping_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("external_entity_mappings.id", ondelete="RESTRICT")
    )
    salesperson_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"))
    source_salesperson: Mapped[str] = mapped_column(String(40))  # the code of the «Vendedor: NN» group
    salesperson_mapping_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("external_entity_mappings.id", ondelete="RESTRICT")
    )
    reason_source: Mapped[str] = mapped_column(String(200))  # «Compro?» as written, always kept
    reason_mapping_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.no_purchase_reason_mappings.id", ondelete="RESTRICT")
    )
    # The reason's category when the event was loaded; NULL while the reason has none. The mapping keeps the
    # current one.
    reason_category_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.no_purchase_reason_categories.id", ondelete="RESTRICT")
    )
    # The report's own clock, as AXUM wrote it (local time, no zone): never combined into an invented moment.
    uploaded_at_source: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    start_time_source: Mapped[time | None] = mapped_column(Time)
    end_time_source: Mapped[time | None] = mapped_column(Time)
    duration_source: Mapped[str | None] = mapped_column(String(20))  # «Minutos», m:ss
    duration_seconds: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), server_default="ACTIVE")
    superseded_by_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT")
    )
    import_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"), index=True
    )
    import_row_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_rows.id", ondelete="RESTRICT"))
    # 1 for the first event of the file with this content, 2 for an identical second one… never collapsed.
    occurrence: Mapped[int] = mapped_column(Integer)
