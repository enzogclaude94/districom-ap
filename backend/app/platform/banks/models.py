"""Catalog of financial institutions by their BCRA code: one source of truth for every module.

A cheque names the bank it is drawn on through this table (Enzo's R4 decision D-I), never with a code
written into each row. Official entries come from the BCRA list (`source = BCRA`) and their code and
name never change; an admin may add a missing entity (`source = MANUAL`, with the reason) and activate
or deactivate any of them. Nothing is deleted (migration 0011).
"""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

SOURCES = ("BCRA", "MANUAL")


class FinancialInstitution(IdMixin, TimestampMixin, Base):
    __tablename__ = "financial_institutions"
    __table_args__ = (
        # BCRA entity codes go up to five digits; shown zero-padded to three (007), never stored as text.
        CheckConstraint("bcra_code BETWEEN 1 AND 99999", name="bcra_code_range"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("source IN ('BCRA', 'MANUAL')", name="source"),
        CheckConstraint(
            "(source = 'MANUAL') = (created_by IS NOT NULL AND length(btrim(coalesce(manual_reason, ''))) > 0)",
            name="manual_has_author",
        ),
    )

    bcra_code: Mapped[int] = mapped_column(Integer, unique=True)
    name: Mapped[str] = mapped_column(String(200))
    source: Mapped[str] = mapped_column(String(10))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    # Who added a missing entity, and why (only MANUAL entries).
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    manual_reason: Mapped[str | None] = mapped_column(Text)


def display_code(code: int) -> str:
    """The code as cheques and statements print it: at least three digits, zero-padded."""
    return f"{code:03d}"
