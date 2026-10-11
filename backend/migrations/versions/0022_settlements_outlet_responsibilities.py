"""settlements: outlet responsibilities

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-11 03:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0022"
down_revision: str | Sequence[str] | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A responsibility is born open, for an active boca and an active employee. Ending it (once: the day, who,
# when and the reason) is the only change it ever takes, also after the boca or the employee was
# deactivated; its history is never rewritten and nothing is deleted.
GUARDS = """
CREATE FUNCTION settlements.check_outlet_responsibility() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT coalesce((SELECT active FROM public.outlets WHERE id = NEW.outlet_id FOR SHARE), false) THEN
            RAISE EXCEPTION 'outlet_responsibilities: outlet % is not active', NEW.outlet_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT coalesce((SELECT active FROM public.employees WHERE id = NEW.employee_id FOR SHARE), false) THEN
            RAISE EXCEPTION 'outlet_responsibilities: employee % is not active', NEW.employee_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.valid_to IS NOT NULL THEN
            RAISE EXCEPTION 'outlet_responsibilities: a responsibility is born open and is ended later'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.valid_to IS NOT NULL THEN
        RAISE EXCEPTION 'outlet_responsibilities: responsibility % already ended; history is never rewritten',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.valid_to IS NULL THEN
        RAISE EXCEPTION 'outlet_responsibilities: the only change is ending responsibility %', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.outlet_id, NEW.employee_id, NEW.valid_from, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM (OLD.outlet_id, OLD.employee_id, OLD.valid_from, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'outlet_responsibilities: boca, employee, start and author never change (%)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_outlet_responsibilities_guard BEFORE INSERT OR UPDATE ON settlements.outlet_responsibilities
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_responsibility();
CREATE TRIGGER trg_outlet_responsibilities_no_delete BEFORE DELETE ON settlements.outlet_responsibilities
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "outlet_responsibilities",
        sa.Column("outlet_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("ended_by", sa.BigInteger(), nullable=True),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("outlet_id"), "="),
            (sa.column("employee_id"), "="),
            (sa.text("daterange(valid_from, valid_to, '[]')"), "&&"),
            using="gist",
            name="ex_outlet_responsibilities_no_overlap",
        ),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to >= valid_from", name=op.f("ck_outlet_responsibilities_valid_range")
        ),
        sa.CheckConstraint(
            "(valid_to IS NULL AND ended_by IS NULL AND ended_at IS NULL AND end_reason IS NULL)"
            " OR (valid_to IS NOT NULL AND ended_by IS NOT NULL AND ended_at IS NOT NULL"
            " AND length(btrim(end_reason)) > 0)",
            name=op.f("ck_outlet_responsibilities_end_complete"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_outlet_responsibilities_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_outlet_responsibilities_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ended_by"], ["users.id"], name=op.f("fk_outlet_responsibilities_ended_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["outlet_id"],
            ["outlets.id"],
            name=op.f("fk_outlet_responsibilities_outlet_id_outlets"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outlet_responsibilities")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_outlet_responsibilities_employee_id"),
        "outlet_responsibilities",
        ["employee_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_outlet_responsibilities_outlet_id"),
        "outlet_responsibilities",
        ["outlet_id"],
        unique=False,
        schema="settlements",
    )
    op.execute(GUARDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_outlet_responsibilities_no_delete ON settlements.outlet_responsibilities")
    op.execute("DROP TRIGGER trg_outlet_responsibilities_guard ON settlements.outlet_responsibilities")
    op.execute("DROP FUNCTION settlements.check_outlet_responsibility()")
    op.drop_index(
        op.f("ix_settlements_outlet_responsibilities_outlet_id"),
        table_name="outlet_responsibilities",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_outlet_responsibilities_employee_id"),
        table_name="outlet_responsibilities",
        schema="settlements",
    )
    op.drop_table("outlet_responsibilities", schema="settlements")
