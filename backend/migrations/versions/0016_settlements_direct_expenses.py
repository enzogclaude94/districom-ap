"""settlements: direct expenses from a cash box or the bank (R5-3)

Revision ID: 0016
Revises: 0015
Create Date: 2026-10-09 16:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0016"
down_revision: str | Sequence[str] | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION', 'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', "
    "'CHEQUE_RETURN'"
)
NEW_TYPES = OLD_TYPES + ", 'DIRECT_EXPENSE'"

# Each origin has exactly its own columns (Enzo's R5-3 decision 1). A settlement expense is justified by its
# settlement's close; a direct expense was paid from one cash box or bank account on a business date, by
# its own DIRECT_EXPENSE operation. The operation's source is the expense, so inside the creating
# transaction the row exists a moment before its operation: `operation_id` NOT NULL is checked at commit
# (check_direct_expense_operation), everything else here. A direct expense is never legacy fuel.
ORIGIN_FIELDS = (
    "(origin = 'SETTLEMENT' AND settlement_id IS NOT NULL AND paid_from_account_id IS NULL "
    "AND business_date IS NULL AND operation_id IS NULL AND reversal_operation_id IS NULL) OR "
    "(origin = 'DIRECT' AND settlement_id IS NULL AND paid_from_account_id IS NOT NULL "
    "AND business_date IS NOT NULL AND NOT legacy_fuel_without_detail)"
)
# A direct expense is voided only by its reversal: one exists exactly when the other does.
DIRECT_REVERSAL = "origin <> 'DIRECT' OR ((reversal_operation_id IS NULL) = (voided_at IS NULL))"

GUARDS = """
-- The settlement rules of 0007 (frozen once CLOSED) apply to settlement expenses only.
DROP TRIGGER trg_expenses_open ON settlements.expenses;
CREATE TRIGGER trg_expenses_open BEFORE INSERT OR UPDATE ON settlements.expenses
    FOR EACH ROW WHEN (NEW.origin = 'SETTLEMENT') EXECUTE FUNCTION settlements.check_settlement_open();

-- A direct expense is paid from an active cash box or bank account. Once written, its money never
-- changes: amount, category, account, date, origin and request are fixed; the operation is set once
-- (in the creating transaction); the reversal is recorded once, with the void; a reversed expense does
-- not change at all. Only the description changes (an admin's descriptive correction). No row changes
-- origin.
CREATE FUNCTION settlements.check_direct_expense() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.origin IS DISTINCT FROM OLD.origin THEN
        RAISE EXCEPTION 'expenses: origin never changes (expense %)', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.origin <> 'DIRECT' THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'INSERT' THEN
        IF NEW.paid_from_account_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM settlements.accounts WHERE id = NEW.paid_from_account_id
            AND kind IN ('CASH_BOX', 'BANK') AND active) THEN
            RAISE EXCEPTION 'expenses: account % is not an active cash box or bank', NEW.paid_from_account_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.voided_at IS NOT NULL OR NEW.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'expenses: a direct expense is born in effect' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.reversal_operation_id IS NOT NULL THEN
        RAISE EXCEPTION 'expenses: direct expense % is reversed and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.settlement_id, NEW.category_id, NEW.amount, NEW.paid_from_account_id, NEW.business_date,
        NEW.client_request_id, NEW.request_fingerprint, NEW.created_by, NEW.created_at,
        NEW.legacy_fuel_without_detail)
        IS DISTINCT FROM
       (OLD.settlement_id, OLD.category_id, OLD.amount, OLD.paid_from_account_id, OLD.business_date,
        OLD.client_request_id, OLD.request_fingerprint, OLD.created_by, OLD.created_at,
        OLD.legacy_fuel_without_detail)
    THEN
        RAISE EXCEPTION 'expenses: direct expense % is published and its money never changes; reverse it', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN
        RAISE EXCEPTION 'expenses: the operation of direct expense % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_expenses_direct BEFORE INSERT OR UPDATE ON settlements.expenses
    FOR EACH ROW EXECUTE FUNCTION settlements.check_direct_expense();

-- The load of a direct fuel expense is frozen with its money: only the odometer is corrected (an admin's
-- descriptive correction), and nothing once the expense is reversed. A settlement's load keeps the rules
-- of 0015 (changes only while the settlement is OPEN).
CREATE OR REPLACE FUNCTION settlements.check_fuel_load() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expense record;
    current_status text;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.expense_id, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM (OLD.expense_id, OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'fuel_loads: expense, author and creation never change (load %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    SELECT origin, settlement_id, reversal_operation_id INTO expense
        FROM settlements.expenses WHERE id = NEW.expense_id;
    IF expense.origin = 'DIRECT' THEN
        IF expense.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'fuel_loads: direct expense % is reversed and its load does not change', NEW.expense_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF TG_OP = 'UPDATE' AND (NEW.vehicle_id, NEW.employee_id, NEW.liters, NEW.price_per_liter)
            IS DISTINCT FROM (OLD.vehicle_id, OLD.employee_id, OLD.liters, OLD.price_per_liter) THEN
            RAISE EXCEPTION 'fuel_loads: the load of direct expense % is published; only the odometer changes',
                NEW.expense_id USING ERRCODE = 'restrict_violation';
        END IF;
    ELSE
        SELECT status INTO current_status FROM settlements.settlements WHERE id = expense.settlement_id;
        IF current_status <> 'OPEN' THEN
            RAISE EXCEPTION 'fuel_loads: the settlement is % and cannot change', current_status
                USING ERRCODE = 'restrict_violation';
        END IF;
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
"""

# At commit: a direct expense and its DIRECT_EXPENSE operation are exactly each other. The operation has
# the expense as source and its business date, and exactly two legs: the paying account -amount and
# «Gastos operativos» +amount (no cheque). The expense is reversed exactly when a REVERSAL of that
# operation exists, on the same business date. A DIRECT_EXPENSE without its expense, or a REVERSAL of one
# without the expense marked reversed by it, is refused.
MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_direct_expense(eid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    e settlements.expenses%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    expense_account bigint := (SELECT id FROM settlements.accounts WHERE kind = 'EXPENSE');
    reversal bigint;
    mismatch boolean;
BEGIN
    SELECT * INTO e FROM settlements.expenses WHERE id = eid;
    IF e.operation_id IS NULL THEN
        RAISE EXCEPTION 'direct expense % has no operation', eid USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM settlements.financial_operations WHERE id = e.operation_id;
    IF o.type <> 'DIRECT_EXPENSE' OR o.source_type IS DISTINCT FROM 'expense' OR o.source_id IS DISTINCT FROM eid
        OR o.business_date <> e.business_date THEN
        RAISE EXCEPTION 'operation % is not the DIRECT_EXPENSE of expense %', o.id, eid
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = e.paid_from_account_id
                   AND kind IN ('CASH_BOX', 'BANK')) THEN
        RAISE EXCEPTION 'direct expense %: account % is not a cash box or bank', eid, e.paid_from_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT EXISTS (
        (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id
         EXCEPT ALL
         SELECT * FROM (VALUES (e.paid_from_account_id, NULL::bigint, -e.amount),
                               (expense_account, NULL::bigint, e.amount)) x(account_id, cheque_id, amount))
        UNION ALL
        (SELECT * FROM (VALUES (e.paid_from_account_id, NULL::bigint, -e.amount),
                               (expense_account, NULL::bigint, e.amount)) x(account_id, cheque_id, amount)
         EXCEPT ALL
         SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id)
    ) INTO mismatch;
    IF mismatch THEN
        RAISE EXCEPTION 'operation % does not match direct expense %', o.id, eid USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO reversal FROM settlements.financial_operations WHERE reverses_operation_id = o.id;
    IF reversal IS DISTINCT FROM e.reversal_operation_id THEN
        RAISE EXCEPTION 'direct expense % is reversed exactly by the reversal of its operation', eid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations
        WHERE id = reversal AND type = 'REVERSAL' AND business_date = e.business_date) THEN
        RAISE EXCEPTION 'the reversal of direct expense % keeps its business date', eid
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_direct_expense_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    eid bigint;
BEGIN
    IF TG_TABLE_NAME = 'expenses' THEN
        eid := NEW.id;
    ELSIF NEW.type = 'DIRECT_EXPENSE' THEN
        SELECT id INTO eid FROM settlements.expenses WHERE operation_id = NEW.id;
        IF eid IS NULL THEN
            RAISE EXCEPTION 'operation % of type DIRECT_EXPENSE has no direct expense', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT e.id INTO eid FROM settlements.expenses e
            JOIN settlements.financial_operations o ON o.id = e.operation_id
            WHERE o.id = NEW.reverses_operation_id AND o.type = 'DIRECT_EXPENSE';
        IF eid IS NULL THEN
            IF EXISTS (SELECT 1 FROM settlements.financial_operations
                       WHERE id = NEW.reverses_operation_id AND type = 'DIRECT_EXPENSE') THEN
                RAISE EXCEPTION 'reversal % of a DIRECT_EXPENSE without its expense', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END IF;
    END IF;
    PERFORM settlements.verify_direct_expense(eid);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_expenses_direct_operation
    AFTER INSERT OR UPDATE ON settlements.expenses DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.origin = 'DIRECT')
    EXECUTE FUNCTION settlements.check_direct_expense_operation();
CREATE CONSTRAINT TRIGGER trg_financial_operations_direct_expense
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('DIRECT_EXPENSE', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_direct_expense_operation();
"""

DROP_GUARDS = """
DROP TRIGGER trg_financial_operations_direct_expense ON settlements.financial_operations;
DROP TRIGGER trg_expenses_direct_operation ON settlements.expenses;
DROP FUNCTION settlements.check_direct_expense_operation();
DROP FUNCTION settlements.verify_direct_expense(bigint);
DROP TRIGGER trg_expenses_direct ON settlements.expenses;
DROP FUNCTION settlements.check_direct_expense();
DROP TRIGGER trg_expenses_open ON settlements.expenses;
CREATE TRIGGER trg_expenses_open BEFORE INSERT OR UPDATE ON settlements.expenses
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_open();
CREATE OR REPLACE FUNCTION settlements.check_fuel_load() RETURNS trigger LANGUAGE plpgsql AS $$
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
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def upgrade() -> None:
    """Upgrade schema."""
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    # A direct expense has exactly one operation (and is its only source).
    op.create_index(
        "uq_financial_operations_direct_expense",
        "financial_operations",
        ["source_type", "source_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type = 'DIRECT_EXPENSE'"),
    )
    op.alter_column("expenses", "settlement_id", existing_type=sa.BigInteger(), nullable=True, schema="settlements")
    op.add_column("expenses", sa.Column("paid_from_account_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.add_column("expenses", sa.Column("business_date", sa.Date(), nullable=True), schema="settlements")
    op.add_column("expenses", sa.Column("operation_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.add_column("expenses", sa.Column("reversal_operation_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_foreign_key(
        op.f("fk_expenses_paid_from_account_id_accounts"),
        "expenses",
        "accounts",
        ["paid_from_account_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_expenses_operation_id_financial_operations"),
        "expenses",
        "financial_operations",
        ["operation_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_expenses_reversal_operation_id_financial_operations"),
        "expenses",
        "financial_operations",
        ["reversal_operation_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(op.f("uq_expenses_operation_id"), "expenses", ["operation_id"], schema="settlements")
    op.create_unique_constraint(
        op.f("uq_expenses_reversal_operation_id"), "expenses", ["reversal_operation_id"], schema="settlements"
    )
    op.create_index(
        op.f("ix_settlements_expenses_paid_from_account_id"), "expenses", ["paid_from_account_id"], schema="settlements"
    )
    op.create_index(op.f("ix_settlements_expenses_business_date"), "expenses", ["business_date"], schema="settlements")
    _replace_check("expenses", "ck_expenses_origin", "origin IN ('SETTLEMENT', 'DIRECT')")
    op.create_check_constraint(op.f("ck_expenses_origin_fields"), "expenses", ORIGIN_FIELDS, schema="settlements")
    op.create_check_constraint(op.f("ck_expenses_direct_reversal"), "expenses", DIRECT_REVERSAL, schema="settlements")
    op.execute(GUARDS)
    op.execute(MATCHES_LEDGER)


def downgrade() -> None:
    """Downgrade schema. Refused by the database while direct expenses exist (settlement_id NOT NULL)."""
    op.execute(DROP_GUARDS)
    op.drop_constraint(op.f("ck_expenses_direct_reversal"), "expenses", schema="settlements", type_="check")
    op.drop_constraint(op.f("ck_expenses_origin_fields"), "expenses", schema="settlements", type_="check")
    _replace_check("expenses", "ck_expenses_origin", "origin IN ('SETTLEMENT')")
    op.drop_index(op.f("ix_settlements_expenses_business_date"), table_name="expenses", schema="settlements")
    op.drop_index(op.f("ix_settlements_expenses_paid_from_account_id"), table_name="expenses", schema="settlements")
    op.drop_constraint(op.f("uq_expenses_reversal_operation_id"), "expenses", schema="settlements", type_="unique")
    op.drop_constraint(op.f("uq_expenses_operation_id"), "expenses", schema="settlements", type_="unique")
    op.drop_constraint(
        op.f("fk_expenses_reversal_operation_id_financial_operations"),
        "expenses",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_expenses_operation_id_financial_operations"), "expenses", schema="settlements", type_="foreignkey"
    )
    op.drop_constraint(
        op.f("fk_expenses_paid_from_account_id_accounts"), "expenses", schema="settlements", type_="foreignkey"
    )
    op.drop_column("expenses", "reversal_operation_id", schema="settlements")
    op.drop_column("expenses", "operation_id", schema="settlements")
    op.drop_column("expenses", "business_date", schema="settlements")
    op.drop_column("expenses", "paid_from_account_id", schema="settlements")
    op.alter_column("expenses", "settlement_id", existing_type=sa.BigInteger(), nullable=False, schema="settlements")
    op.drop_index("uq_financial_operations_direct_expense", table_name="financial_operations", schema="settlements")
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
