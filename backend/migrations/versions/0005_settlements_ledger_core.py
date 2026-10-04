"""settlements: ledger core

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-04 02:04:41.957352

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Invariants of ADR 007 §8 enforced by the database, whoever writes (ARCHITECTURE.md §4.6).
APPEND_ONLY = """
CREATE FUNCTION settlements.forbid_ledger_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'settlements.% is append-only: % is not allowed; correct with a reversal',
        TG_TABLE_NAME, TG_OP USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_financial_operations_append_only
    BEFORE UPDATE OR DELETE ON settlements.financial_operations
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_ledger_mutation();
CREATE TRIGGER trg_ledger_entries_append_only
    BEFORE UPDATE OR DELETE ON settlements.ledger_entries
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_ledger_mutation();
"""

# Checked at commit, once every leg of the operation is in: at least two legs that sum zero.
BALANCED = """
CREATE FUNCTION settlements.check_operation_balanced() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    op_id bigint;
    total numeric;
    legs integer;
BEGIN
    IF TG_TABLE_NAME = 'financial_operations' THEN
        op_id := NEW.id;
    ELSE
        op_id := NEW.operation_id;
    END IF;
    SELECT coalesce(sum(amount), 0), count(*) INTO total, legs
        FROM settlements.ledger_entries WHERE operation_id = op_id;
    IF legs < 2 OR total <> 0 THEN
        RAISE EXCEPTION 'financial operation % is not balanced (legs: %, sum: %)', op_id, legs, total
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_balanced
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_operation_balanced();
CREATE CONSTRAINT TRIGGER trg_ledger_entries_balanced
    AFTER INSERT ON settlements.ledger_entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_operation_balanced();
"""

# Checked at commit with the account row locked, so two concurrent transactions cannot both
# overdraw it (READ COMMITTED: the query after the lock sees what the other one committed). A debit
# dated `since` lowers the end-of-day balance of that day and every later one: all of them must stay
# >= 0, so a retroactive withdrawal cannot leave a past day negative.
NOT_NEGATIVE = """
CREATE FUNCTION settlements.check_account_not_negative() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    negative_allowed boolean;
    since date;
    lowest numeric;
    lowest_day date;
BEGIN
    IF NEW.amount > 0 THEN
        RETURN NULL;
    END IF;
    SELECT allow_negative INTO negative_allowed
        FROM settlements.accounts WHERE id = NEW.account_id FOR NO KEY UPDATE;
    IF negative_allowed THEN
        RETURN NULL;
    END IF;
    SELECT business_date INTO since FROM settlements.financial_operations WHERE id = NEW.operation_id;
    SELECT day, balance INTO lowest_day, lowest FROM (
        SELECT o.business_date AS day, sum(sum(e.amount)) OVER (ORDER BY o.business_date) AS balance
        FROM settlements.ledger_entries e
        JOIN settlements.financial_operations o ON o.id = e.operation_id
        WHERE e.account_id = NEW.account_id
        GROUP BY o.business_date
    ) daily
    WHERE day >= since
    ORDER BY balance, day
    LIMIT 1;
    IF lowest < 0 THEN
        RAISE EXCEPTION 'account % cannot have a negative balance (% on %)', NEW.account_id, lowest, lowest_day
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_ledger_entries_not_negative
    AFTER INSERT ON settlements.ledger_entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_account_not_negative();
"""

# System accounts (configuration, not business data). Partner accounts are created by an admin (P3).
SEED_ACCOUNTS = """
INSERT INTO settlements.accounts (code, name, kind, allow_negative) VALUES
    ('main_cash', 'Caja principal', 'CASH_BOX', false),
    ('change_cash', 'Caja de cambio', 'CASH_BOX', false),
    ('bank', 'Banco', 'BANK', false),
    ('opening_balance', 'Saldo inicial', 'OPENING_BALANCE', true),
    ('adjustment', 'Ajustes', 'ADJUSTMENT', true)
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "accounts",
        sa.Column("code", sa.String(length=40), nullable=True),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("allow_negative", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind <> 'CASH_BOX' OR NOT allow_negative", name=op.f("ck_accounts_cash_box_not_negative")),
        sa.CheckConstraint(
            "kind IN ('CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT')",
            name=op.f("ck_accounts_kind"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
        sa.UniqueConstraint("name", name=op.f("uq_accounts_name")),
        schema="settlements",
    )
    op.create_index(
        "uq_accounts_code",
        "accounts",
        ["code"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("code IS NOT NULL"),
    )
    op.create_index(
        "uq_accounts_technical_kind",
        "accounts",
        ["kind"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("kind IN ('OPENING_BALANCE', 'ADJUSTMENT')"),
    )
    op.create_table(
        "financial_operations",
        sa.Column("type", sa.String(length=30), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("source_type", sa.String(length=40), nullable=True),
        sa.Column("source_id", sa.BigInteger(), nullable=True),
        sa.Column("reverses_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(type = 'REVERSAL') = (reverses_operation_id IS NOT NULL)",
            name=op.f("ck_financial_operations_reversal_link"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_financial_operations_fingerprint_format")
        ),
        sa.CheckConstraint(
            "type IN ('OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', 'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'REVERSAL')",
            name=op.f("ck_financial_operations_type"),
        ),
        sa.CheckConstraint(
            "type NOT IN ('ADJUSTMENT', 'REVERSAL') OR length(btrim(coalesce(description, ''))) > 0",
            name=op.f("ck_financial_operations_reason_required"),
        ),
        sa.CheckConstraint(
            "(source_type IS NULL) = (source_id IS NULL)", name=op.f("ck_financial_operations_source_pair")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_financial_operations_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reverses_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_financial_operations_reverses_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_operations")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_financial_operations_client_request_id")),
        schema="settlements",
    )
    op.create_index(
        "ix_financial_operations_source",
        "financial_operations",
        ["source_type", "source_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_financial_operations_business_date"),
        "financial_operations",
        ["business_date"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_financial_operations_reverses_operation_id",
        "financial_operations",
        ["reverses_operation_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("reverses_operation_id IS NOT NULL"),
    )
    op.create_table(
        "ledger_entries",
        sa.Column("operation_id", sa.BigInteger(), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_ledger_entries_amount_not_zero")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_ledger_entries_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_ledger_entries_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ledger_entries")),
        schema="settlements",
    )
    op.create_index(
        "ix_ledger_entries_account_id_operation_id",
        "ledger_entries",
        ["account_id", "operation_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_ledger_entries_operation_id"),
        "ledger_entries",
        ["operation_id"],
        unique=False,
        schema="settlements",
    )
    op.execute(APPEND_ONLY)
    op.execute(BALANCED)
    op.execute(NOT_NEGATIVE)
    op.execute(SEED_ACCOUNTS)


def downgrade() -> None:
    """Downgrade schema."""
    # Dropping the tables drops their triggers; the functions go after.
    op.drop_index(op.f("ix_settlements_ledger_entries_operation_id"), table_name="ledger_entries", schema="settlements")
    op.drop_index("ix_ledger_entries_account_id_operation_id", table_name="ledger_entries", schema="settlements")
    op.drop_table("ledger_entries", schema="settlements")
    op.drop_index(
        "uq_financial_operations_reverses_operation_id",
        table_name="financial_operations",
        schema="settlements",
        postgresql_where=sa.text("reverses_operation_id IS NOT NULL"),
    )
    op.drop_index(
        op.f("ix_settlements_financial_operations_business_date"),
        table_name="financial_operations",
        schema="settlements",
    )
    op.drop_index("ix_financial_operations_source", table_name="financial_operations", schema="settlements")
    op.drop_table("financial_operations", schema="settlements")
    op.drop_index(
        "uq_accounts_technical_kind",
        table_name="accounts",
        schema="settlements",
        postgresql_where=sa.text("kind IN ('OPENING_BALANCE', 'ADJUSTMENT')"),
    )
    op.drop_index(
        "uq_accounts_code", table_name="accounts", schema="settlements", postgresql_where=sa.text("code IS NOT NULL")
    )
    op.drop_table("accounts", schema="settlements")
    op.execute(
        "DROP FUNCTION settlements.check_account_not_negative(), settlements.check_operation_balanced(), "
        "settlements.forbid_ledger_mutation()"
    )
