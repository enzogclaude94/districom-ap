"""Who answers for a boca de expendio, day by day (R7-1, Enzo's decisions D3 and Ajuste 5; ADR 009).

A responsibility is one employee over a range of days for one boca: `valid_from` to `valid_to`, both
inclusive, `valid_to` NULL while it lasts. There are no shifts and several employees may answer for the same
boca at once; the only exclusion is the same employee in the same boca over overlapping days. An employee
may answer for several bocas, also on the same day. No employee function is needed: any active employee.

History is never rewritten (migration 0022): the row is born open, is ended once with its reason, who and
when, and nothing else ever changes; nothing is deleted. A new one needs an active boca and an active
employee; one already open stays readable and can be ended after either is deactivated.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Text, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

SCHEMA = "settlements"
REASON_LENGTH = 500


class OutletResponsibility(IdMixin, TimestampMixin, Base):
    __tablename__ = "outlet_responsibilities"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        # Open: no end at all. Ended: the day, who, when and a reason, together.
        CheckConstraint(
            "(valid_to IS NULL AND ended_by IS NULL AND ended_at IS NULL AND end_reason IS NULL)"
            " OR (valid_to IS NOT NULL AND ended_by IS NOT NULL AND ended_at IS NOT NULL"
            " AND length(btrim(end_reason)) > 0)",
            name="end_complete",
        ),
        ExcludeConstraint(
            ("outlet_id", "="),
            ("employee_id", "="),
            (func.daterange(text("valid_from"), text("valid_to"), text("'[]'")), "&&"),
            name="ex_outlet_responsibilities_no_overlap",
            using="gist",
        ),
        {"schema": SCHEMA},
    )

    outlet_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("outlets.id", ondelete="RESTRICT"), index=True)
    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    ended_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)
