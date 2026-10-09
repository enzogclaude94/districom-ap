"""settlements: fuel loads and expense origin (R5-2)

Revision ID: 0015
Revises: 0014
Create Date: 2026-10-09 01:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0015"
down_revision: str | Sequence[str] | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enzo's decision 1: the category the system recognizes as fuel is the one with code FUEL. An existing
# «Combustible» (same name key) becomes it, keeping its id, name, state and expenses; otherwise it is created.
SEED_FUEL = """
UPDATE settlements.expense_categories SET code = 'FUEL' WHERE name_key = 'combustible';
INSERT INTO settlements.expense_categories (name, name_key, active, code)
    SELECT 'Combustible', 'combustible', true, 'FUEL'
    WHERE NOT EXISTS (SELECT 1 FROM settlements.expense_categories WHERE code = 'FUEL');
"""

# The only rows that may ever be legacy: fuel expenses that existed before this migration, without detail
# (none has detail yet). Closed settlements are frozen by trg_expenses_open, so it is lifted for this one
# statement; nothing else changes on those rows.
MARK_LEGACY = """
ALTER TABLE settlements.expenses DISABLE TRIGGER trg_expenses_open;
UPDATE settlements.expenses SET legacy_fuel_without_detail = true
    WHERE category_id = (SELECT id FROM settlements.expense_categories WHERE code = 'FUEL');
ALTER TABLE settlements.expenses ENABLE TRIGGER trg_expenses_open;
"""

GUARDS = """
-- A category's code never changes once the row exists (the API never takes one).
CREATE FUNCTION settlements.check_expense_category_code() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.code IS DISTINCT FROM OLD.code THEN
        RAISE EXCEPTION 'expense_categories: code never changes (category %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_expense_categories_code BEFORE UPDATE ON settlements.expense_categories
    FOR EACH ROW EXECUTE FUNCTION settlements.check_expense_category_code();

-- Legacy is born only in this migration: no new row is legacy, and no row becomes legacy again. A legacy
-- row keeps the exception only while its money does not change: a new amount or category must clear it
-- (and then the fuel invariant asks for its load). Voiding is not a financial change of the row.
CREATE FUNCTION settlements.check_expense_legacy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NEW.legacy_fuel_without_detail THEN
            RAISE EXCEPTION 'expenses: a new expense is never legacy_fuel_without_detail'
                USING ERRCODE = 'restrict_violation';
        END IF;
    ELSIF NEW.legacy_fuel_without_detail AND NOT OLD.legacy_fuel_without_detail THEN
        RAISE EXCEPTION 'expenses: legacy_fuel_without_detail never goes from false to true (expense %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    ELSIF NEW.legacy_fuel_without_detail
        AND (NEW.amount, NEW.category_id) IS DISTINCT FROM (OLD.amount, OLD.category_id) THEN
        RAISE EXCEPTION 'expenses: a financial change of legacy expense % needs its fuel load', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_expenses_legacy BEFORE INSERT OR UPDATE ON settlements.expenses
    FOR EACH ROW EXECUTE FUNCTION settlements.check_expense_legacy();

-- A load belongs to one expense forever, changes only while its settlement is OPEN, names an active
-- vehicle and an active employee when created or when they change, and keeps its author.
CREATE FUNCTION settlements.check_fuel_load() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_status text;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.expense_id, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM (OLD.expense_id, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'fuel_loads: expense, author and creation never change (load %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    SELECT s.status INTO current_status FROM settlements.expenses e
        JOIN settlements.settlements s ON s.id = e.settlement_id WHERE e.id = NEW.expense_id;
    IF current_status <> 'OPEN' THEN
        RAISE EXCEPTION 'fuel_loads: the settlement is % and cannot change', current_status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (TG_OP = 'INSERT' OR NEW.vehicle_id <> OLD.vehicle_id)
        AND NOT EXISTS (SELECT 1 FROM public.vehicles WHERE id = NEW.vehicle_id AND active) THEN
        RAISE EXCEPTION 'fuel_loads: vehicle % is not active', NEW.vehicle_id USING ERRCODE = 'check_violation';
    END IF;
    IF (TG_OP = 'INSERT' OR NEW.employee_id <> OLD.employee_id)
        AND NOT EXISTS (SELECT 1 FROM public.employees WHERE id = NEW.employee_id AND active) THEN
        RAISE EXCEPTION 'fuel_loads: employee % is not active', NEW.employee_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_fuel_loads_check BEFORE INSERT OR UPDATE ON settlements.fuel_loads
    FOR EACH ROW EXECUTE FUNCTION settlements.check_fuel_load();
CREATE TRIGGER trg_fuel_loads_no_delete BEFORE DELETE ON settlements.fuel_loads
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

-- At commit, for every expense or load written: a FUEL expense in force has exactly one load (unless it is
-- legacy), a load belongs to a FUEL expense, a legacy row has no load, and the amount is
-- round(liters x price per liter, 2), half away from zero (half up for these positive amounts).
CREATE FUNCTION settlements.check_fuel_expense() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    eid bigint;
    e record;
    load record;
    is_fuel boolean;
BEGIN
    IF TG_TABLE_NAME = 'expenses' THEN
        eid := NEW.id;
    ELSE
        eid := NEW.expense_id;
    END IF;
    SELECT x.amount, x.voided_at, x.legacy_fuel_without_detail, c.code = 'FUEL' AS fuel
        INTO e FROM settlements.expenses x JOIN settlements.expense_categories c ON c.id = x.category_id
        WHERE x.id = eid;
    is_fuel := coalesce(e.fuel, false);
    SELECT liters, price_per_liter INTO load FROM settlements.fuel_loads WHERE expense_id = eid;
    IF FOUND THEN
        IF NOT is_fuel THEN
            RAISE EXCEPTION 'expense %: only a FUEL expense has a fuel load', eid USING ERRCODE = 'check_violation';
        END IF;
        IF e.legacy_fuel_without_detail THEN
            RAISE EXCEPTION 'expense %: a legacy expense has no fuel load', eid USING ERRCODE = 'check_violation';
        END IF;
        IF e.amount <> round(load.liters * load.price_per_liter, 2) THEN
            RAISE EXCEPTION 'expense %: amount % is not liters x price per liter', eid, e.amount
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF is_fuel AND e.voided_at IS NULL AND NOT e.legacy_fuel_without_detail THEN
        RAISE EXCEPTION 'expense %: a FUEL expense needs its fuel load', eid USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_expenses_fuel AFTER INSERT OR UPDATE ON settlements.expenses
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION settlements.check_fuel_expense();
CREATE CONSTRAINT TRIGGER trg_fuel_loads_expense AFTER INSERT OR UPDATE ON settlements.fuel_loads
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION settlements.check_fuel_expense();
"""

DROP_GUARDS = """
DROP TRIGGER trg_fuel_loads_expense ON settlements.fuel_loads;
DROP TRIGGER trg_expenses_fuel ON settlements.expenses;
DROP FUNCTION settlements.check_fuel_expense();
DROP TRIGGER trg_fuel_loads_no_delete ON settlements.fuel_loads;
DROP TRIGGER trg_fuel_loads_check ON settlements.fuel_loads;
DROP FUNCTION settlements.check_fuel_load();
DROP TRIGGER trg_expenses_legacy ON settlements.expenses;
DROP FUNCTION settlements.check_expense_legacy();
DROP TRIGGER trg_expense_categories_code ON settlements.expense_categories;
DROP FUNCTION settlements.check_expense_category_code();
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("expense_categories", sa.Column("code", sa.String(length=40), nullable=True), schema="settlements")
    op.create_unique_constraint(
        op.f("uq_expense_categories_code"), "expense_categories", ["code"], schema="settlements"
    )
    op.create_check_constraint(
        op.f("ck_expense_categories_code_format"),
        "expense_categories",
        "code ~ '^[A-Z][A-Z0-9_]{0,39}$'",
        schema="settlements",
    )
    op.add_column(
        "expenses",
        sa.Column("origin", sa.String(length=20), server_default="SETTLEMENT", nullable=False),
        schema="settlements",
    )
    op.add_column(
        "expenses",
        sa.Column("legacy_fuel_without_detail", sa.Boolean(), server_default="false", nullable=False),
        schema="settlements",
    )
    op.create_check_constraint(op.f("ck_expenses_origin"), "expenses", "origin IN ('SETTLEMENT')", schema="settlements")
    op.create_table(
        "fuel_loads",
        sa.Column("expense_id", sa.BigInteger(), nullable=False),
        sa.Column("vehicle_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("liters", sa.Numeric(precision=10, scale=3), nullable=False),
        sa.Column("price_per_liter", sa.Numeric(precision=14, scale=4), nullable=False),
        sa.Column("odometer_km", sa.Integer(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("liters > 0", name=op.f("ck_fuel_loads_liters_positive")),
        sa.CheckConstraint("price_per_liter > 0", name=op.f("ck_fuel_loads_price_positive")),
        sa.CheckConstraint("odometer_km IS NULL OR odometer_km >= 0", name=op.f("ck_fuel_loads_odometer_not_negative")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_fuel_loads_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], name=op.f("fk_fuel_loads_employee_id_employees"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["expense_id"],
            ["settlements.expenses.id"],
            name=op.f("fk_fuel_loads_expense_id_expenses"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_fuel_loads_updated_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["vehicle_id"], ["vehicles.id"], name=op.f("fk_fuel_loads_vehicle_id_vehicles"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fuel_loads")),
        sa.UniqueConstraint("expense_id", name=op.f("uq_fuel_loads_expense_id")),
        schema="settlements",
    )
    op.create_index(op.f("ix_settlements_fuel_loads_vehicle_id"), "fuel_loads", ["vehicle_id"], schema="settlements")
    op.create_index(op.f("ix_settlements_fuel_loads_employee_id"), "fuel_loads", ["employee_id"], schema="settlements")
    op.execute(SEED_FUEL)
    op.execute(MARK_LEGACY)
    op.execute(GUARDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_GUARDS)
    op.drop_index(op.f("ix_settlements_fuel_loads_employee_id"), table_name="fuel_loads", schema="settlements")
    op.drop_index(op.f("ix_settlements_fuel_loads_vehicle_id"), table_name="fuel_loads", schema="settlements")
    op.drop_table("fuel_loads", schema="settlements")
    op.drop_constraint(op.f("ck_expenses_origin"), "expenses", schema="settlements")
    op.drop_column("expenses", "legacy_fuel_without_detail", schema="settlements")
    op.drop_column("expenses", "origin", schema="settlements")
    op.drop_constraint(op.f("ck_expense_categories_code_format"), "expense_categories", schema="settlements")
    op.drop_constraint(op.f("uq_expense_categories_code"), "expense_categories", schema="settlements")
    # The FUEL category stays (it is an ordinary «Combustible» again); its expenses are kept.
    op.drop_column("expense_categories", "code", schema="settlements")
