"""Vehicles: one platform master for every module (Enzo's R5 decision D4).

A vehicle is identified by its id, never by its plate: correcting a plate keeps its history. The plate is
stored normalized (upper case, no spaces, dots or hyphens) and is unique through `plate_key`, the comparison
key; how a screen prints it ("AB 123 CD") is computed, never stored. Nothing is deleted (migration 0014): a
deactivated vehicle is not offered for new records and stays visible in the old ones.
"""

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

PLATE_PATTERN = "^[A-Z0-9]{5,10}$"


class Vehicle(IdMixin, TimestampMixin, Base):
    __tablename__ = "vehicles"
    __table_args__ = (
        CheckConstraint(f"plate ~ '{PLATE_PATTERN}'", name="plate_normalized"),
        CheckConstraint(f"plate_key ~ '{PLATE_PATTERN}'", name="plate_key_normalized"),
        CheckConstraint("length(btrim(description)) > 0", name="description_not_blank"),
    )

    plate: Mapped[str] = mapped_column(String(10))
    plate_key: Mapped[str] = mapped_column(String(10), unique=True)
    description: Mapped[str] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    updated_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
