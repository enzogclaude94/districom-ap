"""settlements: fuel loads published by each close (R5-4)

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-09 16:42:21.333751

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0017"
down_revision: str | Sequence[str] | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A fuel load is edited in place while its settlement is OPEN, also during a correction, so the liters,
# price and amount a close published would be lost when the settlement is reopened. As with pendings
# (0009) and cheques (0012), every close freezes what it published per load; the fuel in effect of a
# settlement is the rows of its close in effect. Append-only.
HISTORY = """
CREATE TRIGGER trg_settlement_closing_fuel_loads_no_change
    BEFORE UPDATE OR DELETE ON settlements.settlement_closing_fuel_loads
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

CREATE FUNCTION settlements.check_closing_fuel_load() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.fuel_loads l
        JOIN settlements.expenses e ON e.id = l.expense_id
        WHERE l.id = NEW.fuel_load_id AND e.id = NEW.expense_id AND e.settlement_id = NEW.settlement_id
    ) THEN
        RAISE EXCEPTION 'fuel load % is not of expense % of settlement %', NEW.fuel_load_id, NEW.expense_id,
            NEW.settlement_id USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations o
        WHERE o.id = NEW.close_operation_id AND o.type = 'SETTLEMENT_CLOSE' AND o.source_type = 'settlement'
            AND o.source_id = NEW.settlement_id
    ) THEN
        RAISE EXCEPTION 'operation % is not a close of settlement %', NEW.close_operation_id, NEW.settlement_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_settlement_closing_fuel_loads_check BEFORE INSERT ON settlements.settlement_closing_fuel_loads
    FOR EACH ROW EXECUTE FUNCTION settlements.check_closing_fuel_load();
"""

# Checked at commit: what a close published is exactly the fuel loads of its settlement's expenses in effect
# (load, expense, vehicle, employee, liters, price and the expense's amount), no more and no less.
CLOSE_MATCHES_LOADS = """
CREATE FUNCTION settlements.check_close_fuel_loads() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    op_id bigint;
    sid bigint;
BEGIN
    IF TG_TABLE_NAME = 'financial_operations' THEN
        op_id := NEW.id;
        sid := NEW.source_id;
    ELSE
        op_id := NEW.close_operation_id;
        sid := NEW.settlement_id;
    END IF;
    IF EXISTS (
        (SELECT l.id, e.id, l.vehicle_id, l.employee_id, l.liters, l.price_per_liter, e.amount
         FROM settlements.fuel_loads l JOIN settlements.expenses e ON e.id = l.expense_id
         WHERE e.settlement_id = sid AND e.voided_at IS NULL
         EXCEPT ALL
         SELECT fuel_load_id, expense_id, vehicle_id, employee_id, liters, price_per_liter, amount
         FROM settlements.settlement_closing_fuel_loads WHERE close_operation_id = op_id)
        UNION ALL
        (SELECT fuel_load_id, expense_id, vehicle_id, employee_id, liters, price_per_liter, amount
         FROM settlements.settlement_closing_fuel_loads WHERE close_operation_id = op_id
         EXCEPT ALL
         SELECT l.id, e.id, l.vehicle_id, l.employee_id, l.liters, l.price_per_liter, e.amount
         FROM settlements.fuel_loads l JOIN settlements.expenses e ON e.id = l.expense_id
         WHERE e.settlement_id = sid AND e.voided_at IS NULL)
    ) THEN
        RAISE EXCEPTION 'close %: published fuel loads differ from the fuel loads of settlement %', op_id, sid
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_close_fuel_loads
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'SETTLEMENT_CLOSE')
    EXECUTE FUNCTION settlements.check_close_fuel_loads();
CREATE CONSTRAINT TRIGGER trg_settlement_closing_fuel_loads_match
    AFTER INSERT ON settlements.settlement_closing_fuel_loads DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_close_fuel_loads();
"""

# The closes in effect before this migration: the loads of the expenses that existed when the close was
# published and were not voided before it. A closed settlement cannot have changed since; a settlement being
# corrected may have, and its rows take the load as it is now (no earlier fuel snapshot exists to read).
BACKFILL = """
INSERT INTO settlements.settlement_closing_fuel_loads
    (close_operation_id, settlement_id, settlement_version, fuel_load_id, expense_id, vehicle_id, employee_id,
     liters, price_per_liter, amount, created_at)
SELECT s.current_close_operation_id, s.id, c.settlement_version, l.id, e.id, l.vehicle_id, l.employee_id,
       l.liters, l.price_per_liter, e.amount, c.closed_at
FROM settlements.settlements s
JOIN settlements.settlement_closings c ON c.operation_id = s.current_close_operation_id
JOIN settlements.expenses e ON e.settlement_id = s.id
JOIN settlements.fuel_loads l ON l.expense_id = e.id
WHERE e.created_at <= c.closed_at AND (e.voided_at IS NULL OR e.voided_at > c.closed_at)
"""


def upgrade() -> None:
    op.create_table(
        "settlement_closing_fuel_loads",
        sa.Column("close_operation_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("fuel_load_id", sa.BigInteger(), nullable=False),
        sa.Column("expense_id", sa.BigInteger(), nullable=False),
        sa.Column("vehicle_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("liters", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("price_per_liter", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_settlement_closing_fuel_loads_amount_positive")),
        sa.CheckConstraint("liters > 0", name=op.f("ck_settlement_closing_fuel_loads_liters_positive")),
        sa.CheckConstraint("price_per_liter > 0", name=op.f("ck_settlement_closing_fuel_loads_price_positive")),
        sa.ForeignKeyConstraint(
            ["close_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_settlement_closing_fuel_loads_close_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_settlement_closing_fuel_loads_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["expense_id"],
            ["settlements.expenses.id"],
            name=op.f("fk_settlement_closing_fuel_loads_expense_id_expenses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["fuel_load_id"],
            ["settlements.fuel_loads.id"],
            name=op.f("fk_settlement_closing_fuel_loads_fuel_load_id_fuel_loads"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_settlement_closing_fuel_loads_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"],
            ["vehicles.id"],
            name=op.f("fk_settlement_closing_fuel_loads_vehicle_id_vehicles"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_closing_fuel_loads")),
        sa.UniqueConstraint("close_operation_id", "fuel_load_id", name="uq_settlement_closing_fuel_loads_close_load"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_close_operation_id"),
        "settlement_closing_fuel_loads",
        ["close_operation_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_employee_id"),
        "settlement_closing_fuel_loads",
        ["employee_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_fuel_load_id"),
        "settlement_closing_fuel_loads",
        ["fuel_load_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_settlement_id"),
        "settlement_closing_fuel_loads",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_vehicle_id"),
        "settlement_closing_fuel_loads",
        ["vehicle_id"],
        unique=False,
        schema="settlements",
    )
    op.execute(BACKFILL)
    op.execute(HISTORY)
    op.execute(CLOSE_MATCHES_LOADS)


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_settlement_closing_fuel_loads_match ON settlements.settlement_closing_fuel_loads")
    op.execute("DROP TRIGGER trg_financial_operations_close_fuel_loads ON settlements.financial_operations")
    op.execute("DROP FUNCTION settlements.check_close_fuel_loads()")
    op.execute("DROP TRIGGER trg_settlement_closing_fuel_loads_check ON settlements.settlement_closing_fuel_loads")
    op.execute("DROP FUNCTION settlements.check_closing_fuel_load()")
    op.execute("DROP TRIGGER trg_settlement_closing_fuel_loads_no_change ON settlements.settlement_closing_fuel_loads")
    op.drop_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_vehicle_id"),
        table_name="settlement_closing_fuel_loads",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_settlement_id"),
        table_name="settlement_closing_fuel_loads",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_fuel_load_id"),
        table_name="settlement_closing_fuel_loads",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_employee_id"),
        table_name="settlement_closing_fuel_loads",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_settlement_closing_fuel_loads_close_operation_id"),
        table_name="settlement_closing_fuel_loads",
        schema="settlements",
    )
    op.drop_table("settlement_closing_fuel_loads", schema="settlements")
