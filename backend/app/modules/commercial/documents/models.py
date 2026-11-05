"""A document STORE SYS issued (DATA_MODEL §6.1). The source brings no items.

Its class, sign and commercial universe are always read from its `document_types` row, never stored here nor
decided by its name: the signed net is `net_amount * document_types.sign`, computed when read, so reclassifying a
type (admin, audited) changes how it is read without rewriting documents. A document is never updated nor deleted:
the same key with other content is an issue for a person, never an overwrite.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Index, LargeBinary, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin
from app.modules.commercial.config.models import SCHEMA


class Document(IdMixin, TimestampMixin, Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("length(btrim(document_key)) > 0", name="key_not_blank"),
        Index("ix_documents_customer_id_business_date", "customer_id", "business_date"),
        Index("ix_documents_document_type_id_business_date", "document_type_id", "business_date"),
        {"schema": SCHEMA},
    )

    document_type_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey(f"{SCHEMA}.document_types.id", ondelete="RESTRICT")
    )
    source_type_name: Mapped[str] = mapped_column(String(120))
    letter: Mapped[str | None] = mapped_column(String(4))
    point_of_sale: Mapped[str] = mapped_column(String(10))
    number: Mapped[str] = mapped_column(String(20))
    # Normalized type (its letter included, as STORE SYS names it) + point of sale + number.
    document_key: Mapped[str] = mapped_column(String(160), unique=True)
    business_date: Mapped[date] = mapped_column(Date, index=True)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # NULL when codcli is 0, when the type's codcli is not a customer code, or while that code is not mapped.
    customer_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("customers.id", ondelete="RESTRICT"))
    source_customer_code: Mapped[str | None] = mapped_column(String(40))
    source_customer_name: Mapped[str | None] = mapped_column(String(200))
    net_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # `netosa` as is: unsigned in the source
    total_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))  # `total` as is: signed in the source
    discount_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    import_job_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"), index=True
    )
    import_row_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_rows.id", ondelete="RESTRICT"))
    source_row_hash: Mapped[bytes] = mapped_column(LargeBinary)
