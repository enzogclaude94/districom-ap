"""A line of STORE SYS's «Ventas artículos/clientes» (DATA_MODEL §6.2): units, no amounts, no document.

Identical lines are legitimate sales and are all kept (`occurrence`). There is no natural key: a re-import of the
same days replaces the earlier lines by period and scope (docs/IMPORTS.md §5.3), which become `SUPERSEDED` and are
never deleted. What the source said (`source_*`) is kept beside what it resolved to; a foreign key stays NULL while
its mapping is pending.
"""

from datetime import date
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.modules.commercial.config.models import SCHEMA

LINE_STATUSES = ("ACTIVE", "SUPERSEDED")


class SaleLine(IdMixin, TimestampMixin, Base):
    __tablename__ = "sale_lines"
    __table_args__ = (
        CheckConstraint("status IN ('ACTIVE', 'SUPERSEDED')", name="status"),
        CheckConstraint("(status = 'SUPERSEDED') = (superseded_by_job_id IS NOT NULL)", name="superseded_by_job"),
        CheckConstraint("occurrence >= 1", name="occurrence_positive"),
        Index("ix_sale_lines_active_business_date", "business_date", postgresql_where=text("status = 'ACTIVE'")),
        Index("ix_sale_lines_customer_id_business_date", "customer_id", "business_date"),
        Index("ix_sale_lines_product_id_business_date", "product_id", "business_date"),
        {"schema": SCHEMA},
    )

    business_date: Mapped[date] = mapped_column(Date)
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="RESTRICT"))
    source_customer_name: Mapped[str] = mapped_column(String(200))
    customer_mapping_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("external_entity_mappings.id", ondelete="RESTRICT")
    )
    product_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.products.id", ondelete="RESTRICT"))
    source_product_code: Mapped[str] = mapped_column(String(40))
    source_product_description: Mapped[str | None] = mapped_column(String(200))
    source_category: Mapped[str | None] = mapped_column(String(120))
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 3))  # negative = a return
    price_list_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.price_lists.id", ondelete="RESTRICT")
    )
    source_price_list: Mapped[str | None] = mapped_column(String(60))
    salesperson_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"))
    source_seller: Mapped[str | None] = mapped_column(String(200))
    seller_mapping_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("external_entity_mappings.id", ondelete="RESTRICT")
    )
    zone_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey(f"{SCHEMA}.zones.id", ondelete="RESTRICT"))
    source_zone: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(20), server_default="ACTIVE")
    superseded_by_job_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT")
    )
    import_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"), index=True
    )
    import_row_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_rows.id", ondelete="RESTRICT"))
    # 1 for the first line of the file with this content, 2 for an identical second one… never collapsed.
    occurrence: Mapped[int] = mapped_column(Integer)
