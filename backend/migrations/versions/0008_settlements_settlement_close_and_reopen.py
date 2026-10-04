"""settlements: settlement close and reopen

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-04 18:49:56.698568

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0008"
down_revision: str | Sequence[str] | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT'"
NEW_KINDS = OLD_KINDS + ", 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE', 'EMPLOYEE'"
OLD_TECHNICAL = "'OPENING_BALANCE', 'ADJUSTMENT'"
NEW_TECHNICAL = OLD_TECHNICAL + ", 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE'"
OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'REVERSAL'"
)
NEW_TYPES = OLD_TYPES + ", 'SETTLEMENT_CLOSE'"

# Technical counterparts of a settlement close (configuration, not business data).
SEED_ACCOUNTS = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('delivery_collections', 'Cobranzas de reparto', 'cobranzas de reparto', 'DELIVERY_COLLECTIONS', true),
    ('expense', 'Gastos operativos', 'gastos operativos', 'EXPENSE', true),
    ('settlement_difference', 'Diferencias de rendición', 'diferencias de rendicion', 'SETTLEMENT_DIFFERENCE', true)
"""

# Closings, differences and allocations are history: never deleted; a difference is only superseded.
HISTORY = """
CREATE TRIGGER trg_settlement_closings_no_change BEFORE UPDATE OR DELETE ON settlements.settlement_closings
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_difference_allocations_no_change BEFORE UPDATE OR DELETE ON settlements.difference_allocations
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_settlement_differences_no_delete BEFORE DELETE ON settlements.settlement_differences
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

CREATE FUNCTION settlements.check_difference_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.superseded_at IS NOT NULL OR NEW.superseded_at IS NULL OR NEW.superseded_by IS NULL
        OR (NEW.settlement_id, NEW.settlement_version, NEW.kind, NEW.amount, NEW.close_operation_id,
            NEW.created_by, NEW.created_at)
           IS DISTINCT FROM
           (OLD.settlement_id, OLD.settlement_version, OLD.kind, OLD.amount, OLD.close_operation_id,
            OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'settlements.settlement_differences: a difference is only superseded, never edited'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_settlement_differences_supersede_only BEFORE UPDATE ON settlements.settlement_differences
    FOR EACH ROW EXECUTE FUNCTION settlements.check_difference_update();
"""

# Checked at commit, once every allocation is in: the allocations of a difference add up exactly to it.
ALLOCATED = """
CREATE FUNCTION settlements.check_difference_allocated() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    diff_id bigint;
    expected numeric;
    allocated numeric;
BEGIN
    IF TG_TABLE_NAME = 'settlement_differences' THEN
        diff_id := NEW.id;
    ELSE
        diff_id := NEW.difference_id;
    END IF;
    SELECT amount INTO expected FROM settlements.settlement_differences WHERE id = diff_id;
    SELECT coalesce(sum(amount), 0) INTO allocated FROM settlements.difference_allocations WHERE difference_id = diff_id;
    IF allocated <> expected THEN
        RAISE EXCEPTION 'difference % is not fully allocated (difference: %, allocated: %)', diff_id, expected, allocated
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_settlement_differences_allocated
    AFTER INSERT ON settlements.settlement_differences DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_difference_allocated();
CREATE CONSTRAINT TRIGGER trg_difference_allocations_allocated
    AFTER INSERT ON settlements.difference_allocations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_difference_allocated();
"""

# Checked at commit: a settlement has at most one SETTLEMENT_CLOSE without reversal, and the close it
# points to as current is exactly that one (a reversal outside the settlement flow is refused).
ONE_CLOSE = """
CREATE FUNCTION settlements.check_one_settlement_close() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    active integer;
BEGIN
    SELECT count(*) INTO active FROM settlements.financial_operations o
        WHERE o.type = 'SETTLEMENT_CLOSE' AND o.source_type = 'settlement' AND o.source_id = NEW.source_id
        AND NOT EXISTS (SELECT 1 FROM settlements.financial_operations r WHERE r.reverses_operation_id = o.id);
    IF active > 1 THEN
        RAISE EXCEPTION 'settlement % has more than one close in effect', NEW.source_id
            USING ERRCODE = 'unique_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_one_settlement_close
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'SETTLEMENT_CLOSE')
    EXECUTE FUNCTION settlements.check_one_settlement_close();

CREATE FUNCTION settlements.check_current_close() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_id bigint;
    settlement_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'settlements' THEN
        current_id := NEW.current_close_operation_id;
        settlement_id := NEW.id;
    ELSE
        SELECT id, current_close_operation_id INTO settlement_id, current_id FROM settlements.settlements
            WHERE current_close_operation_id = NEW.reverses_operation_id;
        IF settlement_id IS NULL THEN
            RETURN NULL;
        END IF;
    END IF;
    IF current_id IS NULL THEN
        RETURN NULL;
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations o
        WHERE o.id = current_id AND o.type = 'SETTLEMENT_CLOSE' AND o.source_type = 'settlement'
            AND o.source_id = settlement_id
            AND NOT EXISTS (SELECT 1 FROM settlements.financial_operations r WHERE r.reverses_operation_id = o.id)
    ) THEN
        RAISE EXCEPTION 'settlement %: its current close % is not a close of it in effect', settlement_id, current_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_settlements_current_close
    AFTER INSERT OR UPDATE ON settlements.settlements DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_current_close();
CREATE CONSTRAINT TRIGGER trg_financial_operations_current_close_reversed
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'REVERSAL')
    EXECUTE FUNCTION settlements.check_current_close();
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_financial_operations_current_close_reversed ON settlements.financial_operations;
DROP TRIGGER trg_settlements_current_close ON settlements.settlements;
DROP FUNCTION settlements.check_current_close();
DROP TRIGGER trg_financial_operations_one_settlement_close ON settlements.financial_operations;
DROP FUNCTION settlements.check_one_settlement_close();
DROP TRIGGER trg_difference_allocations_allocated ON settlements.difference_allocations;
DROP TRIGGER trg_settlement_differences_allocated ON settlements.settlement_differences;
DROP FUNCTION settlements.check_difference_allocated();
DROP TRIGGER trg_settlement_differences_supersede_only ON settlements.settlement_differences;
DROP FUNCTION settlements.check_difference_update();
DROP TRIGGER trg_settlement_differences_no_delete ON settlements.settlement_differences;
DROP TRIGGER trg_difference_allocations_no_change ON settlements.difference_allocations;
DROP TRIGGER trg_settlement_closings_no_change ON settlements.settlement_closings;
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
    op.create_table(
        "settlement_closings",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.BigInteger(), nullable=False),
        sa.Column("reversal_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("closed_by", sa.BigInteger(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_settlement_closings_fingerprint_format")
        ),
        sa.ForeignKeyConstraint(
            ["closed_by"], ["users.id"], name=op.f("fk_settlement_closings_closed_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_settlement_closings_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_settlement_closings_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_settlement_closings_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_closings")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_settlement_closings_operation_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_settlement_closings_reversal_operation_id")),
        sa.UniqueConstraint("settlement_id", "settlement_version", name="uq_settlement_closings_version"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closings_settlement_id"),
        "settlement_closings",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "settlement_differences",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("close_operation_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("superseded_by", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("kind IN ('SHORTAGE', 'SURPLUS')", name=op.f("ck_settlement_differences_kind")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_settlement_differences_amount_positive")),
        sa.ForeignKeyConstraint(
            ["close_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_settlement_differences_close_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_settlement_differences_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_settlement_differences_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["users.id"],
            name=op.f("fk_settlement_differences_superseded_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_differences")),
        sa.UniqueConstraint("settlement_id", "settlement_version", name="uq_settlement_differences_version"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_differences_settlement_id"),
        "settlement_differences",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_settlement_differences_current",
        "settlement_differences",
        ["settlement_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.create_table(
        "difference_allocations",
        sa.Column("difference_id", sa.BigInteger(), nullable=False),
        sa.Column("target", sa.String(length=20), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("decided_by", sa.BigInteger(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(target = 'EMPLOYEE') = (employee_id IS NOT NULL)", name=op.f("ck_difference_allocations_employee_target")
        ),
        sa.CheckConstraint(
            "target <> 'COMPANY' OR length(btrim(coalesce(reason, ''))) > 0",
            name=op.f("ck_difference_allocations_company_reason_required"),
        ),
        sa.CheckConstraint("target IN ('EMPLOYEE', 'COMPANY')", name=op.f("ck_difference_allocations_target")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_difference_allocations_amount_positive")),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_difference_allocations_decided_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["difference_id"],
            ["settlements.settlement_differences.id"],
            name=op.f("fk_difference_allocations_difference_id_settlement_differences"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_difference_allocations_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_difference_allocations")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_difference_allocations_difference_id"),
        "difference_allocations",
        ["difference_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_difference_allocations_company",
        "difference_allocations",
        ["difference_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("target = 'COMPANY'"),
    )
    op.create_index(
        "uq_difference_allocations_employee",
        "difference_allocations",
        ["difference_id", "employee_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("employee_id IS NOT NULL"),
    )
    op.add_column("accounts", sa.Column("employee_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_index(
        "uq_accounts_employee_id",
        "accounts",
        ["employee_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("employee_id IS NOT NULL"),
    )
    op.create_foreign_key(
        op.f("fk_accounts_employee_id_employees"),
        "accounts",
        "employees",
        ["employee_id"],
        ["id"],
        source_schema="settlements",
        ondelete="RESTRICT",
    )
    op.add_column(
        "settlements", sa.Column("current_close_operation_id", sa.BigInteger(), nullable=True), schema="settlements"
    )
    op.add_column("settlements", sa.Column("closed_by", sa.BigInteger(), nullable=True), schema="settlements")
    op.add_column(
        "settlements", sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True), schema="settlements"
    )
    op.add_column(
        "settlements", sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True), schema="settlements"
    )
    op.add_column("settlements", sa.Column("reopened_by", sa.BigInteger(), nullable=True), schema="settlements")
    op.add_column("settlements", sa.Column("reopen_reason", sa.Text(), nullable=True), schema="settlements")
    op.create_foreign_key(
        op.f("fk_settlements_reopened_by_users"),
        "settlements",
        "users",
        ["reopened_by"],
        ["id"],
        source_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_settlements_current_close_operation_id_financial_operations"),
        "settlements",
        "financial_operations",
        ["current_close_operation_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_settlements_closed_by_users"),
        "settlements",
        "users",
        ["closed_by"],
        ["id"],
        source_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_settlements_closed_has_close"),
        "settlements",
        "status <> 'CLOSED' OR current_close_operation_id IS NOT NULL",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_settlements_reopen_complete"),
        "settlements",
        "(reopened_at IS NULL AND reopened_by IS NULL AND reopen_reason IS NULL) OR "
        "(reopened_at IS NOT NULL AND reopened_by IS NOT NULL AND length(btrim(coalesce(reopen_reason, ''))) > 0)",
        schema="settlements",
    )
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    op.create_check_constraint(
        op.f("ck_accounts_employee_link"),
        "accounts",
        "(kind = 'EMPLOYEE') = (employee_id IS NOT NULL)",
        schema="settlements",
    )
    _technical_index(NEW_TECHNICAL)
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    op.execute(SEED_ACCOUNTS)
    op.execute(HISTORY)
    op.execute(ALLOCATED)
    op.execute(ONE_CLOSE)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_TRIGGERS)
    # Fails if any settlement close or employee account exists: their history would be lost.
    op.execute(
        "DELETE FROM settlements.accounts WHERE code IN ('delivery_collections', 'expense', 'settlement_difference')"
    )
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
    _technical_index(OLD_TECHNICAL)
    op.drop_constraint(op.f("ck_accounts_employee_link"), "accounts", schema="settlements", type_="check")
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
    op.drop_constraint(op.f("ck_settlements_reopen_complete"), "settlements", schema="settlements", type_="check")
    op.drop_constraint(op.f("ck_settlements_closed_has_close"), "settlements", schema="settlements", type_="check")
    op.drop_constraint(op.f("fk_settlements_closed_by_users"), "settlements", schema="settlements", type_="foreignkey")
    op.drop_constraint(
        op.f("fk_settlements_current_close_operation_id_financial_operations"),
        "settlements",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_settlements_reopened_by_users"), "settlements", schema="settlements", type_="foreignkey"
    )
    op.drop_column("settlements", "reopen_reason", schema="settlements")
    op.drop_column("settlements", "reopened_by", schema="settlements")
    op.drop_column("settlements", "reopened_at", schema="settlements")
    op.drop_column("settlements", "closed_at", schema="settlements")
    op.drop_column("settlements", "closed_by", schema="settlements")
    op.drop_column("settlements", "current_close_operation_id", schema="settlements")
    op.drop_constraint(op.f("fk_accounts_employee_id_employees"), "accounts", schema="settlements", type_="foreignkey")
    op.drop_index(
        "uq_accounts_employee_id",
        table_name="accounts",
        schema="settlements",
        postgresql_where=sa.text("employee_id IS NOT NULL"),
    )
    op.drop_column("accounts", "employee_id", schema="settlements")
    op.drop_index(
        "uq_difference_allocations_employee",
        table_name="difference_allocations",
        schema="settlements",
        postgresql_where=sa.text("employee_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_difference_allocations_company",
        table_name="difference_allocations",
        schema="settlements",
        postgresql_where=sa.text("target = 'COMPANY'"),
    )
    op.drop_index(
        op.f("ix_settlements_difference_allocations_difference_id"),
        table_name="difference_allocations",
        schema="settlements",
    )
    op.drop_table("difference_allocations", schema="settlements")
    op.drop_index(
        "uq_settlement_differences_current",
        table_name="settlement_differences",
        schema="settlements",
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    op.drop_index(
        op.f("ix_settlements_settlement_differences_settlement_id"),
        table_name="settlement_differences",
        schema="settlements",
    )
    op.drop_table("settlement_differences", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_settlement_closings_settlement_id"), table_name="settlement_closings", schema="settlements"
    )
    op.drop_table("settlement_closings", schema="settlements")
