"""platform: employees and employee functions

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-04 01:09:50.198327

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Employees are the single platform identity of a person; functions keep their history (ADR 007).
    op.create_table(
        "employees",
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("external_axum_id", sa.String(length=100), nullable=True),
        sa.Column("external_store_id", sa.String(length=100), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("hire_date", sa.Date(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employees")),
    )
    op.create_index(
        "uq_employees_external_axum_id",
        "employees",
        ["external_axum_id"],
        unique=True,
        postgresql_where=sa.text("external_axum_id IS NOT NULL"),
    )
    op.create_index(
        "uq_employees_external_store_id",
        "employees",
        ["external_store_id"],
        unique=True,
        postgresql_where=sa.text("external_store_id IS NOT NULL"),
    )
    op.create_table(
        "employee_functions",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("function_code", sa.String(length=60), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=20), server_default="MANUAL", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("employee_id"), "="),
            (sa.column("function_code"), "="),
            (sa.text("daterange(valid_from, valid_to, '[]')"), "&&"),
            using="gist",
            name="ex_employee_functions_no_overlap",
        ),
        sa.CheckConstraint("source IN ('AXUM', 'STORE_SYS', 'MANUAL')", name=op.f("ck_employee_functions_source")),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_employee_functions_valid_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_employee_functions_created_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_employee_functions_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_functions")),
    )
    op.create_index(op.f("ix_employee_functions_employee_id"), "employee_functions", ["employee_id"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(op.f("ix_employee_functions_employee_id"), table_name="employee_functions")
    op.drop_table("employee_functions")
    op.drop_index(
        "uq_employees_external_store_id",
        table_name="employees",
        postgresql_where=sa.text("external_store_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_employees_external_axum_id",
        table_name="employees",
        postgresql_where=sa.text("external_axum_id IS NOT NULL"),
    )
    op.drop_table("employees")
