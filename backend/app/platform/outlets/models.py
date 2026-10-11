"""Bocas de expendio (outlets): one platform master (Enzo's R7 decision, ADR 009).

A boca is identified by its id and by its code, never by its name. The code is mandatory, unique and never
changes (migration 0021 refuses it); the name is corrected freely, but two bocas never share an
operationally identical name: `name_key` (lower case, no accents nor punctuation, single spaces) is unique.
There is no address nor locality. Nothing is deleted: a deactivated boca opens no new rendición and takes
no new responsable, and stays visible with its history. Who answers for a boca on a day is Rendiciones'
business (`settlements.outlet_responsibilities`), not the platform's.
"""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

CODE_PATTERN = "^[A-Z0-9-]{2,20}$"
NAME_LENGTH = 100
DESCRIPTION_LENGTH = 500


class Outlet(IdMixin, TimestampMixin, Base):
    __tablename__ = "outlets"
    __table_args__ = (
        CheckConstraint(f"code ~ '{CODE_PATTERN}'", name="code_format"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("length(name_key) > 0", name="name_key_not_blank"),
        CheckConstraint("description IS NULL OR length(btrim(description)) > 0", name="description_not_blank"),
    )

    code: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(NAME_LENGTH))
    name_key: Mapped[str] = mapped_column(String(NAME_LENGTH), unique=True)
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
