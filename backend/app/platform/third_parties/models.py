"""Third parties: one platform master of whoever the company pays (Enzo's R6 decisions D2, D7 and D8).

A third party is identified by its id. The name is free text; `name_key` (lower case, no accents, no
punctuation, single spaces) is only for spotting a possible duplicate, never unique: two third parties may
share a name. The CUIT is optional, stored as its 11 digits with a valid check digit (checked by the
database too) and unique among the ones that have it. Nothing is deleted (migration 0018): a deactivated
third party admits no new payments and stays visible in the old ones.
"""

import unicodedata

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.db.base import Base, IdMixin, TimestampMixin


def name_key(name: str) -> str:
    """Comparison key of a name: «Distribuidora Pérez S.A.» and «distribuidora perez sa» are the same."""
    decomposed = unicodedata.normalize("NFKD", name.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    words = "".join(c if c.isalnum() or c.isspace() else ("" if c in ".'\u00b4`" else " ") for c in plain)
    return " ".join(words.split())


class ThirdParty(IdMixin, TimestampMixin, Base):
    __tablename__ = "third_parties"
    __table_args__ = (
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(name_key) > 0", name="name_key_not_blank"),
        CheckConstraint("tax_id IS NULL OR public.cuit_is_valid(tax_id)", name="tax_id_valid"),
        Index("uq_third_parties_tax_id", "tax_id", unique=True, postgresql_where=text("tax_id IS NOT NULL")),
    )

    name: Mapped[str] = mapped_column(String(200))
    name_key: Mapped[str] = mapped_column(String(200), index=True)
    tax_id: Mapped[str | None] = mapped_column(String(11))
    notes: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))

    @validates("name")
    def _set_name_key(self, _: str, name: str) -> str:
        self.name_key = name_key(name)
        return name
