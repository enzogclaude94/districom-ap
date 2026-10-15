"""Salary terms of an employee (R8-1, ADR 010).

`base_salary` is the **internal monthly nominal amount** DistriCom uses for its internal payroll. It is not
the legal gross nor net salary, not contributions, not a collective agreement and not an official payslip.
There is no frequency: the salary is monthly by definition (Enzo's D5; weekly money is an advance, R8-2).

A term covers `valid_from` to `valid_to`, both inclusive, `valid_to` NULL while it lasts; the terms of one
employee never overlap. History is never rewritten (migration 0025): a term is born open, for an employee
that exists and is active, may start in the future, and is ended once (the day, who, when and the reason);
nothing else ever changes and nothing is deleted. A salary change ends the term in effect and creates a new
one. Platform has no pay data (`public.employees` holds identity only), so nothing is duplicated.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, CheckConstraint, Date, DateTime, ForeignKey, Numeric, Text, func, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin

SCHEMA = "settlements"


class EmployeeCompensationTerm(IdMixin, Base):
    __tablename__ = "employee_compensation_terms"
    __table_args__ = (
        CheckConstraint("base_salary >= 0", name="base_salary_not_negative"),
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        # Open: no end at all. Ended: the day, who, when and a reason, together.
        CheckConstraint(
            "(valid_to IS NULL AND ended_by IS NULL AND ended_at IS NULL AND end_reason IS NULL)"
            " OR (valid_to IS NOT NULL AND ended_by IS NOT NULL AND ended_at IS NOT NULL"
            " AND length(btrim(end_reason)) > 0)",
            name="end_complete",
        ),
        ExcludeConstraint(
            ("employee_id", "="),
            (func.daterange(text("valid_from"), text("valid_to"), text("'[]'")), "&&"),
            name="ex_employee_compensation_terms_no_overlap",
            using="gist",
        ),
        {"schema": SCHEMA},
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    base_salary: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ended_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="RESTRICT"))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_reason: Mapped[str | None] = mapped_column(Text)
