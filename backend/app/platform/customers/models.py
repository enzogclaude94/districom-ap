"""A customer is identified by its id. Codes of STORE SYS, AXUM or any other system are not columns: they live in
`external_entity_mappings` (namespace `customer`), which admits several per customer with status and decision.

Only the STORE SYS customer master creates customers (F1.3); no other import does. The CUIT is optional and **not
unique** (branches, data entry errors); when present it has a valid check digit (checked by the database too).
`display_name_key` only finds the exact-name candidate a sales line may propose; it never confirms an identity.
Nothing is deleted (migration 0034): a customer is deactivated.
"""

from datetime import date

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Date, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin


class Customer(IdMixin, TimestampMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("length(btrim(display_name)) > 0", name="display_name_not_blank"),
        CheckConstraint("tax_id IS NULL OR public.cuit_is_valid(tax_id)", name="tax_id_valid"),
        CheckConstraint("(first_seen_job_id IS NULL) = (last_seen_job_id IS NULL)", name="seen_jobs_together"),
    )

    display_name: Mapped[str] = mapped_column(String(200))
    display_name_key: Mapped[str] = mapped_column(String(200), index=True)
    legal_name: Mapped[str | None] = mapped_column(String(200))
    tax_id: Mapped[str | None] = mapped_column(String(11), index=True)  # PII: never logged
    address: Mapped[str | None] = mapped_column(Text)  # PII
    phone: Mapped[str | None] = mapped_column(String(60))  # PII
    is_generic: Mapped[bool] = mapped_column(Boolean, server_default="false")
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    first_seen_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    last_seen_job_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("import_jobs.id", ondelete="RESTRICT"))
    last_seen_on: Mapped[date | None] = mapped_column(Date)
