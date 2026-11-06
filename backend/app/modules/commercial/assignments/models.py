"""Who a customer belonged to, and when (DATA_MODEL §5).

Each master import is a snapshot with a date: an assignment that changed is closed the day before the snapshot and
another opens; one that did not change is left alone. Rows are never updated in place nor deleted (a trigger forbids
it): two snapshots of the same day void the row the first one opened (`voided_by_job_id`), which then leaves the
no-overlap rule, as product prices do.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, String, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.modules.commercial.config.models import SCHEMA

ASSIGNMENT_SOURCES = ("STORE_SYS", "AXUM", "MANUAL")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


def _no_overlap(name: str) -> ExcludeConstraint:
    return ExcludeConstraint(
        ("customer_id", "="),
        (func.daterange(text("valid_from"), text("valid_to"), text("'[]'")), "&&"),
        name=name,
        using="gist",
        where=text("voided_by_job_id IS NULL"),
    )


class _History:
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20))
    import_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    voided_by_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    voided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerZoneAssignment(IdMixin, TimestampMixin, _History, Base):
    __tablename__ = "customer_zone_assignments"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        CheckConstraint("voided_by_job_id IS NULL OR valid_to IS NULL", name="voided_not_closed"),
        CheckConstraint(_in("source", ASSIGNMENT_SOURCES), name="source"),
        _no_overlap("ex_customer_zone_assignments_no_overlap"),
        {"schema": SCHEMA},
    )

    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    zone_id: Mapped[int] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.zones.id", ondelete="RESTRICT"), index=True)


class CustomerSalespersonAssignment(IdMixin, TimestampMixin, _History, Base):
    """`salesperson_id` is the employee the seller's mapping pointed to when the snapshot was applied; NULL while the
    STORE SYS seller is not mapped, or for a pseudo-seller (BOCA DE EXPENDIO) whose mapping is `excluded`. The label
    and the mapping stay, so the assignment can be resolved once someone maps it. Whether the assignment changed is
    decided by the seller's label, never by a guess about the person."""

    __tablename__ = "customer_salesperson_assignments"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        CheckConstraint("voided_by_job_id IS NULL OR valid_to IS NULL", name="voided_not_closed"),
        CheckConstraint(_in("source", ASSIGNMENT_SOURCES), name="source"),
        CheckConstraint(
            "salesperson_id IS NOT NULL"
            " OR (source_seller_label IS NOT NULL AND length(btrim(source_seller_label)) > 0)",
            name="who",
        ),
        _no_overlap("ex_customer_salesperson_assignments_no_overlap"),
        {"schema": SCHEMA},
    )

    customer_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="RESTRICT"), index=True)
    salesperson_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True
    )
    source_seller_label: Mapped[str | None] = mapped_column(String(200))
    source_seller_key: Mapped[str | None] = mapped_column(String(200))
    seller_mapping_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("external_entity_mappings.id", ondelete="RESTRICT")
    )
