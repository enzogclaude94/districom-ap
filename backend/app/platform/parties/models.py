from datetime import date

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin, TimestampMixin

FUNCTION_SOURCES = ("AXUM", "STORE_SYS", "MANUAL")


class Employee(IdMixin, TimestampMixin, Base):
    """Single identity of a person who works for the company, whatever their function or module."""

    __tablename__ = "employees"
    __table_args__ = (
        Index(
            "uq_employees_external_axum_id",
            "external_axum_id",
            unique=True,
            postgresql_where=text("external_axum_id IS NOT NULL"),
        ),
        Index(
            "uq_employees_external_store_id",
            "external_store_id",
            unique=True,
            postgresql_where=text("external_store_id IS NOT NULL"),
        ),
    )

    full_name: Mapped[str] = mapped_column(String(200))
    external_axum_id: Mapped[str | None] = mapped_column(String(100))
    external_store_id: Mapped[str | None] = mapped_column(String(100))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    hire_date: Mapped[date | None] = mapped_column(Date)
    phone: Mapped[str | None] = mapped_column(Text)  # PII: never logged nor audited


class EmployeeFunction(IdMixin, TimestampMixin, Base):
    """A function an employee holds over a period. Closing `valid_to` is the only allowed change."""

    __tablename__ = "employee_functions"
    __table_args__ = (
        CheckConstraint("valid_to IS NULL OR valid_to >= valid_from", name="valid_range"),
        CheckConstraint("source IN ('AXUM', 'STORE_SYS', 'MANUAL')", name="source"),
        ExcludeConstraint(
            ("employee_id", "="),
            ("function_code", "="),
            (func.daterange(text("valid_from"), text("valid_to"), text("'[]'")), "&&"),
            name="ex_employee_functions_no_overlap",
            using="gist",
        ),
    )

    employee_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("employees.id", ondelete="RESTRICT"), index=True)
    function_code: Mapped[str] = mapped_column(String(60))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str] = mapped_column(String(20), server_default="MANUAL")
    created_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
