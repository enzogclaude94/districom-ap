"""settlements: employee compensation terms

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-15 16:10:23.222165

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# A salary term is born open, for an active employee (it may start in the future). Ending it (once: the day,
# who, when and the reason) is the only change it ever takes, also after the employee was deactivated; its
# employee, start, salary, notes and author never change and nothing is deleted (R8-1, ADR 010).
GUARDS = """
CREATE FUNCTION settlements.check_employee_compensation_term() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT coalesce((SELECT active FROM public.employees WHERE id = NEW.employee_id FOR SHARE), false) THEN
            RAISE EXCEPTION 'employee_compensation_terms: employee % is not active', NEW.employee_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.valid_to IS NOT NULL THEN
            RAISE EXCEPTION 'employee_compensation_terms: a term is born open and is ended later'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.valid_to IS NOT NULL THEN
        RAISE EXCEPTION 'employee_compensation_terms: term % already ended; history is never rewritten', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.valid_to IS NULL THEN
        RAISE EXCEPTION 'employee_compensation_terms: the only change is ending term %', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.employee_id, NEW.valid_from, NEW.base_salary, NEW.notes, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM (OLD.employee_id, OLD.valid_from, OLD.base_salary, OLD.notes, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'employee_compensation_terms: employee, start, salary, notes and author never change (%)',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_compensation_terms_guard BEFORE INSERT OR UPDATE
    ON settlements.employee_compensation_terms
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_compensation_term();
CREATE TRIGGER trg_employee_compensation_terms_no_delete BEFORE DELETE ON settlements.employee_compensation_terms
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# revision identifiers, used by Alembic.
revision: str = "0025"
down_revision: str | Sequence[str] | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "employee_compensation_terms",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("base_salary", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("ended_by", sa.BigInteger(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("employee_id"), "="),
            (sa.text("daterange(valid_from, valid_to, '[]')"), "&&"),
            using="gist",
            name="ex_employee_compensation_terms_no_overlap",
        ),
        sa.CheckConstraint(
            "(valid_to IS NULL AND ended_by IS NULL AND ended_at IS NULL AND end_reason IS NULL) OR (valid_to IS NOT NULL AND ended_by IS NOT NULL AND ended_at IS NOT NULL AND length(btrim(end_reason)) > 0)",
            name=op.f("ck_employee_compensation_terms_end_complete"),
        ),
        sa.CheckConstraint("base_salary >= 0", name=op.f("ck_employee_compensation_terms_base_salary_not_negative")),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_employee_compensation_terms_valid_range")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_employee_compensation_terms_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_employee_compensation_terms_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ended_by"], ["users.id"], name=op.f("fk_employee_compensation_terms_ended_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_compensation_terms")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_compensation_terms_employee_id"),
        "employee_compensation_terms",
        ["employee_id"],
        unique=False,
        schema="settlements",
    )
    op.execute(GUARDS)


def downgrade() -> None:
    """Downgrade schema."""
    # Salary history is never dropped silently: going below 0025 with terms recorded is refused.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM settlements.employee_compensation_terms) THEN
                RAISE EXCEPTION 'downgrade below 0025 refused: salary terms exist'
                    USING ERRCODE = 'restrict_violation';
            END IF;
        END
        $$
    """)
    op.execute("DROP TRIGGER trg_employee_compensation_terms_no_delete ON settlements.employee_compensation_terms")
    op.execute("DROP TRIGGER trg_employee_compensation_terms_guard ON settlements.employee_compensation_terms")
    op.execute("DROP FUNCTION settlements.check_employee_compensation_term()")
    op.drop_index(
        op.f("ix_settlements_employee_compensation_terms_employee_id"),
        table_name="employee_compensation_terms",
        schema="settlements",
    )
    op.drop_table("employee_compensation_terms", schema="settlements")
