"""settlements: outlet settlements

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-13 19:30:00.000000

R7-2 (ADR 009): a settlement is of a driver (DELIVERY) or of a boca de expendio (OUTLET). An OUTLET
settlement has its boca and no employee, an expected amount that may be unknown (NULL) or 0 while it is
loaded, no pending collections and no fuel; its close takes the expected amount out of «Cobranzas de boca»
(OUTLET_COLLECTIONS), a technical account only that close and its reversal touch. An OUTLET settlement of
$0 without any movement closes without a ledger operation: an administrative zero close (Enzo's R7-2-Q1),
which the database checks against the whole load. DELIVERY does not change.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0023"
down_revision: str | Sequence[str] | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE', 'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', "
    "'CHEQUES_IN_TRANSIT', 'MERCHANDISE_PAYMENTS', 'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS'"
)
NEW_KINDS = OLD_KINDS + ", 'OUTLET_COLLECTIONS'"
OLD_TECHNICAL = (
    "'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE', "
    "'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', 'CHEQUES_IN_TRANSIT', 'MERCHANDISE_PAYMENTS', "
    "'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS'"
)
NEW_TECHNICAL = OLD_TECHNICAL + ", 'OUTLET_COLLECTIONS'"

# «Cobranzas de boca» (Enzo's D9): technical, may be negative, never available money.
SEED_ACCOUNTS = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('outlet_collections', 'Cobranzas de boca', 'cobranzas de boca', 'OUTLET_COLLECTIONS', true)
"""

# The documentary close in effect and the close each reopening kept, for the rows that already exist: every
# close so far is financial, so they are found by their operation.
BACKFILL = """
UPDATE settlements.settlements s SET current_closing_id = c.id
    FROM settlements.settlement_closings c
    WHERE c.operation_id = s.current_close_operation_id AND c.settlement_id = s.id;
UPDATE settlements.settlement_reopenings r SET closing_id = c.id
    FROM settlements.settlement_closings c
    WHERE c.operation_id = r.close_operation_id AND c.settlement_id = r.settlement_id;
"""

# Who and what a settlement is never changes, and a CLOSED one is frozen (0007), now with its boca too.
FROZEN = """
CREATE OR REPLACE FUNCTION settlements.check_closed_settlement_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'CLOSED' AND NEW.status = 'CLOSED'
        AND (NEW.type, NEW.business_date, NEW.employee_id, NEW.outlet_id, NEW.sequence_number,
             NEW.expected_amount, NEW.expected_source, NEW.external_reference, NEW.notes, NEW.version)
            IS DISTINCT FROM
            (OLD.type, OLD.business_date, OLD.employee_id, OLD.outlet_id, OLD.sequence_number,
             OLD.expected_amount, OLD.expected_source, OLD.external_reference, OLD.notes, OLD.version)
    THEN
        RAISE EXCEPTION 'settlements.settlements: settlement % is CLOSED and cannot change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.type, NEW.business_date, NEW.employee_id, NEW.outlet_id, NEW.sequence_number, NEW.client_request_id)
        IS DISTINCT FROM
        (OLD.type, OLD.business_date, OLD.employee_id, OLD.outlet_id, OLD.sequence_number, OLD.client_request_id)
    THEN
        RAISE EXCEPTION 'settlements.settlements: driver, boca, date and number of a settlement never change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

FROZEN_0007 = """
CREATE OR REPLACE FUNCTION settlements.check_closed_settlement_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'CLOSED' AND NEW.status = 'CLOSED'
        AND (NEW.type, NEW.business_date, NEW.employee_id, NEW.sequence_number, NEW.expected_amount,
             NEW.expected_source, NEW.external_reference, NEW.notes, NEW.version)
            IS DISTINCT FROM
            (OLD.type, OLD.business_date, OLD.employee_id, OLD.sequence_number, OLD.expected_amount,
             OLD.expected_source, OLD.external_reference, OLD.notes, OLD.version)
    THEN
        RAISE EXCEPTION 'settlements.settlements: settlement % is CLOSED and cannot change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.type, NEW.business_date, NEW.employee_id, NEW.sequence_number, NEW.client_request_id)
        IS DISTINCT FROM (OLD.type, OLD.business_date, OLD.employee_id, OLD.sequence_number, OLD.client_request_id)
    THEN
        RAISE EXCEPTION 'settlements.settlements: driver, date and number of a settlement never change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

# A new cycle of a boca starts only while it is active (locked FOR SHARE, so a concurrent deactivation
# serializes with it). Its settlements already open keep being loaded, closed and corrected afterwards.
OUTLET_ACTIVE = """
CREATE FUNCTION settlements.check_outlet_settlement_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.outlets WHERE id = NEW.outlet_id AND active FOR SHARE) THEN
        RAISE EXCEPTION 'inactive_outlet: boca % is not active', NEW.outlet_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_settlements_outlet_active BEFORE INSERT ON settlements.settlements
    FOR EACH ROW WHEN (NEW.type = 'OUTLET') EXECUTE FUNCTION settlements.check_outlet_settlement_insert();
"""

# What a boca does not have (Enzo's D4 and R7-2): pending collections, fuel.
OUTLET_EXCLUSIONS = """
CREATE FUNCTION settlements.check_pending_not_outlet() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.settlement_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM settlements.settlements WHERE id = NEW.settlement_id AND type = 'OUTLET'
    ) THEN
        RAISE EXCEPTION 'pending_not_enabled_for_outlets: settlement % is of a boca', NEW.settlement_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_pending_collections_not_outlet BEFORE INSERT OR UPDATE ON settlements.pending_collections
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_not_outlet();

CREATE FUNCTION settlements.check_fuel_not_outlet() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'expenses' THEN
        IF NEW.settlement_id IS NOT NULL
            AND EXISTS (SELECT 1 FROM settlements.settlements WHERE id = NEW.settlement_id AND type = 'OUTLET')
            AND EXISTS (SELECT 1 FROM settlements.expense_categories WHERE id = NEW.category_id AND code = 'FUEL')
        THEN
            RAISE EXCEPTION 'fuel_not_allowed_for_outlet: settlement % is of a boca', NEW.settlement_id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF EXISTS (
        SELECT 1 FROM settlements.expenses e JOIN settlements.settlements s ON s.id = e.settlement_id
        WHERE e.id = NEW.expense_id AND s.type = 'OUTLET'
    ) THEN
        RAISE EXCEPTION 'fuel_not_allowed_for_outlet: expense % is of a boca', NEW.expense_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_expenses_fuel_not_outlet BEFORE INSERT OR UPDATE ON settlements.expenses
    FOR EACH ROW EXECUTE FUNCTION settlements.check_fuel_not_outlet();
CREATE TRIGGER trg_fuel_loads_not_outlet BEFORE INSERT OR UPDATE ON settlements.fuel_loads
    FOR EACH ROW EXECUTE FUNCTION settlements.check_fuel_not_outlet();
"""

# «Cobranzas de boca» is a closed account (Enzo's adjustment 2): only the close of an OUTLET settlement takes
# value out of it, and only the reversal of such a close puts it back. Every leg is checked on insert.
OUTLET_COLLECTIONS_DISCIPLINE = """
CREATE FUNCTION settlements.check_outlet_collections_leg() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    op settlements.financial_operations%ROWTYPE;
    sign integer := -1;
BEGIN
    IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.account_id AND kind = 'OUTLET_COLLECTIONS') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO op FROM settlements.financial_operations WHERE id = NEW.operation_id;
    IF op.type = 'REVERSAL' THEN
        SELECT * INTO op FROM settlements.financial_operations WHERE id = op.reverses_operation_id;
        sign := 1;
    END IF;
    IF op.type IS DISTINCT FROM 'SETTLEMENT_CLOSE' OR op.source_type IS DISTINCT FROM 'settlement'
        OR NOT EXISTS (SELECT 1 FROM settlements.settlements WHERE id = op.source_id AND type = 'OUTLET')
        OR sign(NEW.amount) <> sign
    THEN
        RAISE EXCEPTION 'outlet_collections_closed: only the close of a boca settlement and its reversal move it'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_ledger_entries_outlet_collections BEFORE INSERT ON settlements.ledger_entries
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_collections_leg();

-- Checked at commit: the close of an OUTLET settlement takes exactly its expected amount out of «Cobranzas de
-- boca» (no leg when it is 0) and never touches «Cobranzas de reparto».
CREATE FUNCTION settlements.check_outlet_close_collections() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected numeric;
    delivery_legs integer;
    outlet_legs integer;
    outlet_total numeric;
BEGIN
    SELECT expected_amount INTO expected FROM settlements.settlements
        WHERE id = NEW.source_id AND type = 'OUTLET' AND NEW.source_type = 'settlement';
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT count(*) FILTER (WHERE a.kind = 'DELIVERY_COLLECTIONS'), count(*) FILTER (WHERE a.kind = 'OUTLET_COLLECTIONS'),
           coalesce(sum(e.amount) FILTER (WHERE a.kind = 'OUTLET_COLLECTIONS'), 0)
        INTO delivery_legs, outlet_legs, outlet_total
        FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
        WHERE e.operation_id = NEW.id;
    IF expected IS NULL OR delivery_legs > 0
        OR (expected > 0 AND (outlet_legs <> 1 OR outlet_total <> -expected))
        OR (expected = 0 AND outlet_legs <> 0)
    THEN
        RAISE EXCEPTION 'close %: a boca close takes exactly its expected amount out of Cobranzas de boca', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_outlet_close_collections
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'SETTLEMENT_CLOSE')
    EXECUTE FUNCTION settlements.check_outlet_close_collections();
"""

# The administrative zero close (Enzo's R7-2-Q1). Valid only for an OUTLET settlement whose closed version
# has: expected = 0; no counted cash; no live additions; no verified (nor declared) transfers; no cheque that
# justifies or waits for verification; no live expenses; no pendings; no difference; no close of it in effect
# in the ledger; and a snapshot that says exactly that. Remittances arrive with R7-3 and will add their check.
ZERO_CLOSE = """
CREATE FUNCTION settlements.assert_zero_close(closing_id bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    c settlements.settlement_closings%ROWTYPE;
    s settlements.settlements%ROWTYPE;
BEGIN
    SELECT * INTO c FROM settlements.settlement_closings WHERE id = closing_id;
    SELECT * INTO s FROM settlements.settlements WHERE id = c.settlement_id;
    IF c.kind <> 'ADMINISTRATIVE_ZERO' OR s.type <> 'OUTLET' OR s.version <> c.settlement_version
        OR s.expected_amount IS DISTINCT FROM 0
        OR EXISTS (SELECT 1 FROM settlements.cash_count_lines WHERE settlement_id = s.id)
        OR EXISTS (SELECT 1 FROM settlements.cash_additions WHERE settlement_id = s.id AND voided_at IS NULL)
        OR EXISTS (SELECT 1 FROM settlements.settlement_transfers
                   WHERE settlement_id = s.id AND status IN ('VERIFIED', 'DECLARED'))
        OR EXISTS (SELECT 1 FROM settlements.cheques WHERE settlement_id = s.id AND voided_at IS NULL
                   AND (kind = 'PHYSICAL' OR verification_status IN ('VERIFIED', 'DECLARED')))
        OR EXISTS (SELECT 1 FROM settlements.expenses WHERE settlement_id = s.id AND voided_at IS NULL)
        OR EXISTS (SELECT 1 FROM settlements.pending_collections WHERE settlement_id = s.id AND voided_at IS NULL)
        OR EXISTS (SELECT 1 FROM settlements.settlement_differences
                   WHERE settlement_id = s.id AND settlement_version = c.settlement_version)
        OR EXISTS (SELECT 1 FROM settlements.financial_operations o
                   WHERE o.type = 'SETTLEMENT_CLOSE' AND o.source_type = 'settlement' AND o.source_id = s.id
                   AND NOT EXISTS (SELECT 1 FROM settlements.financial_operations r WHERE r.reverses_operation_id = o.id))
        OR c.snapshot->>'type' IS DISTINCT FROM 'OUTLET'
        OR c.snapshot->>'outlet_id' IS DISTINCT FROM s.outlet_id::text
        OR c.snapshot->>'version' IS DISTINCT FROM c.settlement_version::text
        OR EXISTS (SELECT 1 FROM unnest(ARRAY['expected_amount', 'cash', 'additions', 'transfers_verified', 'cheques',
                   'expenses', 'pending', 'difference']) k WHERE c.snapshot->>k IS DISTINCT FROM '0.00')
    THEN
        RAISE EXCEPTION 'settlement %: an administrative zero close needs a boca settlement of $0 without movements',
            c.settlement_id USING ERRCODE = 'check_violation';
    END IF;
    IF c.reversal_operation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations r JOIN settlements.financial_operations o
            ON o.id = r.reverses_operation_id
        WHERE r.id = c.reversal_operation_id AND r.type = 'REVERSAL' AND o.type = 'SETTLEMENT_CLOSE'
            AND o.source_type = 'settlement' AND o.source_id = s.id
    ) THEN
        RAISE EXCEPTION 'settlement %: the reversal of an administrative close is not of its previous close', s.id
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_zero_closing() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM settlements.assert_zero_close(NEW.id);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_settlement_closings_zero
    AFTER INSERT ON settlements.settlement_closings DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.kind = 'ADMINISTRATIVE_ZERO')
    EXECUTE FUNCTION settlements.check_zero_closing();

-- Checked at commit: the documentary close in effect is a close of this settlement with the same ledger
-- operation; a CLOSED settlement points to the close of its current version; one closed without an operation
-- is an administrative zero close that holds.
CREATE FUNCTION settlements.check_current_closing() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    c settlements.settlement_closings%ROWTYPE;
BEGIN
    IF NEW.current_closing_id IS NULL THEN
        RETURN NULL;
    END IF;
    SELECT * INTO c FROM settlements.settlement_closings WHERE id = NEW.current_closing_id;
    IF c.settlement_id <> NEW.id OR c.operation_id IS DISTINCT FROM NEW.current_close_operation_id
        OR (NEW.status = 'CLOSED' AND c.settlement_version <> NEW.version)
    THEN
        RAISE EXCEPTION 'settlement %: its close in effect % does not match its state', NEW.id, NEW.current_closing_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'CLOSED' AND NEW.current_close_operation_id IS NULL THEN
        PERFORM settlements.assert_zero_close(c.id);
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_settlements_current_closing
    AFTER INSERT OR UPDATE ON settlements.settlements DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_current_closing();

-- Checked at commit: a reopening names the close that stays in effect while the settlement is corrected.
CREATE FUNCTION settlements.check_reopening_close() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.settlement_closings c
        WHERE c.id = NEW.closing_id AND c.settlement_id = NEW.settlement_id
            AND c.operation_id IS NOT DISTINCT FROM NEW.close_operation_id
            AND c.settlement_version = NEW.from_version
    ) THEN
        RAISE EXCEPTION 'reopening of settlement %: % is not its close of version %', NEW.settlement_id,
            NEW.closing_id, NEW.from_version USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_settlement_reopenings_close
    AFTER INSERT ON settlements.settlement_reopenings DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_reopening_close();
"""

# Downgrade only while no boca settlement and no administrative close exist: their history would not fit the
# previous schema.
DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.settlements WHERE type = 'OUTLET')
        OR EXISTS (SELECT 1 FROM settlements.settlement_closings WHERE kind <> 'FINANCIAL')
    THEN
        RAISE EXCEPTION 'outlet settlements exist: downgrade of 0023 refused' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_settlement_reopenings_close ON settlements.settlement_reopenings;
DROP FUNCTION settlements.check_reopening_close();
DROP TRIGGER trg_settlements_current_closing ON settlements.settlements;
DROP FUNCTION settlements.check_current_closing();
DROP TRIGGER trg_settlement_closings_zero ON settlements.settlement_closings;
DROP FUNCTION settlements.check_zero_closing();
DROP FUNCTION settlements.assert_zero_close(bigint);
DROP TRIGGER trg_financial_operations_outlet_close_collections ON settlements.financial_operations;
DROP FUNCTION settlements.check_outlet_close_collections();
DROP TRIGGER trg_ledger_entries_outlet_collections ON settlements.ledger_entries;
DROP FUNCTION settlements.check_outlet_collections_leg();
DROP TRIGGER trg_fuel_loads_not_outlet ON settlements.fuel_loads;
DROP TRIGGER trg_expenses_fuel_not_outlet ON settlements.expenses;
DROP FUNCTION settlements.check_fuel_not_outlet();
DROP TRIGGER trg_pending_collections_not_outlet ON settlements.pending_collections;
DROP FUNCTION settlements.check_pending_not_outlet();
DROP TRIGGER trg_settlements_outlet_active ON settlements.settlements;
DROP FUNCTION settlements.check_outlet_settlement_insert();
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def _technical_index(kinds: str) -> None:
    op.drop_index("uq_accounts_technical_kind", table_name="accounts", schema="settlements")
    op.create_index(
        "uq_accounts_technical_kind",
        "accounts",
        ["kind"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text(f"kind IN ({kinds})"),
    )


def upgrade() -> None:
    """Upgrade schema."""
    # «Cobranzas de boca».
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    _technical_index(NEW_TECHNICAL)
    op.execute(SEED_ACCOUNTS)

    # A settlement of a driver or of a boca.
    op.add_column("settlements", sa.Column("outlet_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_foreign_key(
        op.f("fk_settlements_outlet_id_outlets"),
        "settlements",
        "outlets",
        ["outlet_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="public",
        ondelete="RESTRICT",
    )
    op.create_index(op.f("ix_settlements_settlements_outlet_id"), "settlements", ["outlet_id"], schema="settlements")
    op.alter_column("settlements", "employee_id", nullable=True, schema="settlements")
    op.alter_column("settlements", "expected_amount", nullable=True, schema="settlements")
    _replace_check("settlements", "ck_settlements_type", "type IN ('DELIVERY', 'OUTLET')")
    op.drop_constraint(op.f("ck_settlements_expected_amount_positive"), "settlements", schema="settlements")
    op.create_check_constraint(
        op.f("ck_settlements_identity_by_type"),
        "settlements",
        "(type = 'DELIVERY' AND employee_id IS NOT NULL AND outlet_id IS NULL) OR "
        "(type = 'OUTLET' AND outlet_id IS NOT NULL AND employee_id IS NULL)",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_settlements_expected_amount_by_type"),
        "settlements",
        "(type = 'DELIVERY' AND expected_amount IS NOT NULL AND expected_amount > 0) OR "
        "(type = 'OUTLET' AND (expected_amount IS NULL OR expected_amount >= 0))",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_settlements_closed_has_expected"),
        "settlements",
        "status <> 'CLOSED' OR expected_amount IS NOT NULL",
        schema="settlements",
    )
    op.drop_constraint("uq_settlements_sequence", "settlements", schema="settlements", type_="unique")
    op.create_index(
        "uq_settlements_sequence",
        "settlements",
        ["employee_id", "business_date", "sequence_number"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type = 'DELIVERY'"),
    )
    op.create_index(
        "uq_settlements_outlet_sequence",
        "settlements",
        ["outlet_id", "business_date", "sequence_number"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type = 'OUTLET'"),
    )

    # The documentary close: financial or administrative zero.
    op.add_column(
        "settlement_closings",
        sa.Column("kind", sa.String(length=30), server_default="FINANCIAL", nullable=False),
        schema="settlements",
    )
    op.add_column(
        "settlement_closings",
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        schema="settlements",
    )
    op.alter_column("settlement_closings", "operation_id", nullable=True, schema="settlements")
    op.create_check_constraint(
        op.f("ck_settlement_closings_kind"),
        "settlement_closings",
        "kind IN ('FINANCIAL', 'ADMINISTRATIVE_ZERO')",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_settlement_closings_operation_by_kind"),
        "settlement_closings",
        "(kind = 'FINANCIAL') = (operation_id IS NOT NULL)",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_settlement_closings_administrative_snapshot"),
        "settlement_closings",
        "kind = 'FINANCIAL' OR snapshot IS NOT NULL",
        schema="settlements",
    )

    # The documentary close in effect, and the one each reopening kept.
    op.add_column("settlements", sa.Column("current_closing_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_foreign_key(
        "fk_settlements_current_closing_id_settlement_closings",
        "settlements",
        "settlement_closings",
        ["current_closing_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.add_column(
        "settlement_reopenings", sa.Column("closing_id", sa.BigInteger(), nullable=True), schema="settlements"
    )
    op.create_foreign_key(
        op.f("fk_settlement_reopenings_closing_id_settlement_closings"),
        "settlement_reopenings",
        "settlement_closings",
        ["closing_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    # Reopenings are append-only: lift their guard just for the backfill.
    op.execute("ALTER TABLE settlements.settlement_reopenings DISABLE TRIGGER trg_settlement_reopenings_no_change")
    op.execute(BACKFILL)
    op.execute("ALTER TABLE settlements.settlement_reopenings ENABLE TRIGGER trg_settlement_reopenings_no_change")
    # Run now the commit-time checks the backfill queued: a table with pending trigger events cannot be altered.
    op.execute("SET CONSTRAINTS ALL IMMEDIATE")
    op.alter_column("settlement_reopenings", "closing_id", nullable=False, schema="settlements")
    op.alter_column("settlement_reopenings", "close_operation_id", nullable=True, schema="settlements")
    _replace_check(
        "settlements",
        "ck_settlements_closed_has_close",
        "status <> 'CLOSED' OR current_close_operation_id IS NOT NULL "
        "OR (type = 'OUTLET' AND current_closing_id IS NOT NULL)",
    )
    op.create_check_constraint(
        op.f("ck_settlements_close_documented"),
        "settlements",
        "current_close_operation_id IS NULL OR current_closing_id IS NOT NULL",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_settlements_delivery_close_financial"),
        "settlements",
        "type <> 'DELIVERY' OR current_closing_id IS NULL OR current_close_operation_id IS NOT NULL",
        schema="settlements",
    )

    op.execute(FROZEN)
    op.execute(OUTLET_ACTIVE)
    op.execute(OUTLET_EXCLUSIONS)
    op.execute(OUTLET_COLLECTIONS_DISCIPLINE)
    op.execute(ZERO_CLOSE)


def downgrade() -> None:
    """Downgrade schema. Refused while any boca settlement exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute(DROP_TRIGGERS)
    op.execute(FROZEN_0007)
    op.drop_constraint(op.f("ck_settlements_delivery_close_financial"), "settlements", schema="settlements")
    op.drop_constraint(op.f("ck_settlements_close_documented"), "settlements", schema="settlements")
    _replace_check(
        "settlements", "ck_settlements_closed_has_close", "status <> 'CLOSED' OR current_close_operation_id IS NOT NULL"
    )
    op.alter_column("settlement_reopenings", "close_operation_id", nullable=False, schema="settlements")
    op.drop_constraint(
        op.f("fk_settlement_reopenings_closing_id_settlement_closings"),
        "settlement_reopenings",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_column("settlement_reopenings", "closing_id", schema="settlements")
    op.drop_constraint(
        "fk_settlements_current_closing_id_settlement_closings", "settlements", schema="settlements", type_="foreignkey"
    )
    op.drop_column("settlements", "current_closing_id", schema="settlements")
    op.drop_constraint(
        op.f("ck_settlement_closings_administrative_snapshot"), "settlement_closings", schema="settlements"
    )
    op.drop_constraint(op.f("ck_settlement_closings_operation_by_kind"), "settlement_closings", schema="settlements")
    op.drop_constraint(op.f("ck_settlement_closings_kind"), "settlement_closings", schema="settlements")
    op.alter_column("settlement_closings", "operation_id", nullable=False, schema="settlements")
    op.drop_column("settlement_closings", "snapshot", schema="settlements")
    op.drop_column("settlement_closings", "kind", schema="settlements")

    op.drop_index(
        "uq_settlements_outlet_sequence",
        table_name="settlements",
        schema="settlements",
        postgresql_where=sa.text("type = 'OUTLET'"),
    )
    op.drop_index(
        "uq_settlements_sequence",
        table_name="settlements",
        schema="settlements",
        postgresql_where=sa.text("type = 'DELIVERY'"),
    )
    op.create_unique_constraint(
        "uq_settlements_sequence",
        "settlements",
        ["type", "employee_id", "business_date", "sequence_number"],
        schema="settlements",
    )
    op.drop_constraint(op.f("ck_settlements_closed_has_expected"), "settlements", schema="settlements")
    op.drop_constraint(op.f("ck_settlements_expected_amount_by_type"), "settlements", schema="settlements")
    op.drop_constraint(op.f("ck_settlements_identity_by_type"), "settlements", schema="settlements")
    op.create_check_constraint(
        op.f("ck_settlements_expected_amount_positive"), "settlements", "expected_amount > 0", schema="settlements"
    )
    _replace_check("settlements", "ck_settlements_type", "type IN ('DELIVERY')")
    op.alter_column("settlements", "expected_amount", nullable=False, schema="settlements")
    op.alter_column("settlements", "employee_id", nullable=False, schema="settlements")
    op.drop_index(op.f("ix_settlements_settlements_outlet_id"), table_name="settlements", schema="settlements")
    op.drop_constraint(
        op.f("fk_settlements_outlet_id_outlets"), "settlements", schema="settlements", type_="foreignkey"
    )
    op.drop_column("settlements", "outlet_id", schema="settlements")

    # Fails if any operation used it: its history would be lost.
    op.execute("DELETE FROM settlements.accounts WHERE code = 'outlet_collections'")
    _technical_index(OLD_TECHNICAL)
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
