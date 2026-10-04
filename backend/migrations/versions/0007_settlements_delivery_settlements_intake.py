"""settlements: delivery settlements intake

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-04 17:56:36.184656

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Nothing of a settlement is deleted (ADR 007): rows are voided, superseded or audited instead.
NO_DELETE = """
CREATE FUNCTION settlements.forbid_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'settlements.% rows are never deleted', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_settlements_no_delete BEFORE DELETE ON settlements.settlements
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_cash_additions_no_delete BEFORE DELETE ON settlements.cash_additions
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_settlement_transfers_no_delete BEFORE DELETE ON settlements.settlement_transfers
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_expenses_no_delete BEFORE DELETE ON settlements.expenses
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_expense_categories_no_delete BEFORE DELETE ON settlements.expense_categories
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_transfer_status_events_no_change BEFORE UPDATE OR DELETE ON settlements.transfer_status_events
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# An addition is never edited: the only UPDATE allowed is the one that voids it (R2-D5).
ADDITION_VOID_ONLY = """
CREATE FUNCTION settlements.check_cash_addition_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.voided_at IS NOT NULL OR NEW.voided_at IS NULL
        OR (NEW.settlement_id, NEW.amount, NEW.destination_account_id, NEW.description,
            NEW.client_request_id, NEW.request_fingerprint, NEW.created_by, NEW.created_at)
           IS DISTINCT FROM
           (OLD.settlement_id, OLD.amount, OLD.destination_account_id, OLD.description,
            OLD.client_request_id, OLD.request_fingerprint, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'settlements.cash_additions: an addition is only voided, never edited'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cash_additions_void_only BEFORE UPDATE ON settlements.cash_additions
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cash_addition_update();
"""

# What a CLOSED settlement holds does not change, whoever writes: its count, additions, transfers and
# expenses, and its own data. Reopening (R2-2) is the only way back to OPEN.
CLOSED_IS_FROZEN = """
CREATE FUNCTION settlements.check_settlement_open() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_status text;
BEGIN
    SELECT status INTO current_status FROM settlements.settlements
        WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.settlement_id ELSE NEW.settlement_id END;
    IF current_status <> 'OPEN' THEN
        RAISE EXCEPTION 'settlements.%: the settlement is % and cannot change', TG_TABLE_NAME, current_status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.settlement_id <> OLD.settlement_id THEN
        RAISE EXCEPTION 'settlements.%: a row cannot move to another settlement', TG_TABLE_NAME
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
END
$$;
CREATE TRIGGER trg_cash_count_lines_open BEFORE INSERT OR UPDATE OR DELETE ON settlements.cash_count_lines
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_open();
CREATE TRIGGER trg_cash_additions_open BEFORE INSERT OR UPDATE ON settlements.cash_additions
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_open();
CREATE TRIGGER trg_settlement_transfers_open BEFORE INSERT OR UPDATE ON settlements.settlement_transfers
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_open();
CREATE TRIGGER trg_expenses_open BEFORE INSERT OR UPDATE ON settlements.expenses
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_open();

CREATE FUNCTION settlements.check_closed_settlement_update() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE TRIGGER trg_settlements_closed_frozen BEFORE UPDATE ON settlements.settlements
    FOR EACH ROW EXECUTE FUNCTION settlements.check_closed_settlement_update();
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_settlements_closed_frozen ON settlements.settlements;
DROP FUNCTION settlements.check_closed_settlement_update();
DROP TRIGGER trg_expenses_open ON settlements.expenses;
DROP TRIGGER trg_settlement_transfers_open ON settlements.settlement_transfers;
DROP TRIGGER trg_cash_additions_open ON settlements.cash_additions;
DROP TRIGGER trg_cash_count_lines_open ON settlements.cash_count_lines;
DROP FUNCTION settlements.check_settlement_open();
DROP TRIGGER trg_cash_additions_void_only ON settlements.cash_additions;
DROP FUNCTION settlements.check_cash_addition_update();
DROP TRIGGER trg_transfer_status_events_no_change ON settlements.transfer_status_events;
DROP TRIGGER trg_expense_categories_no_delete ON settlements.expense_categories;
DROP TRIGGER trg_expenses_no_delete ON settlements.expenses;
DROP TRIGGER trg_settlement_transfers_no_delete ON settlements.settlement_transfers;
DROP TRIGGER trg_cash_additions_no_delete ON settlements.cash_additions;
DROP TRIGGER trg_settlements_no_delete ON settlements.settlements;
DROP FUNCTION settlements.forbid_delete();
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "expense_categories",
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("name_key", sa.String(length=100), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expense_categories")),
        sa.UniqueConstraint("name_key", name=op.f("uq_expense_categories_name_key")),
        schema="settlements",
    )
    op.create_table(
        "settlements",
        sa.Column("type", sa.String(length=20), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("expected_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("expected_source", sa.String(length=20), server_default="MANUAL", nullable=False),
        sa.Column("external_reference", sa.String(length=100), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="OPEN", nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("expected_source IN ('MANUAL')", name=op.f("ck_settlements_expected_source")),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_settlements_fingerprint_format")),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name=op.f("ck_settlements_status")),
        sa.CheckConstraint("type IN ('DELIVERY')", name=op.f("ck_settlements_type")),
        sa.CheckConstraint("expected_amount > 0", name=op.f("ck_settlements_expected_amount_positive")),
        sa.CheckConstraint("sequence_number >= 1", name=op.f("ck_settlements_sequence_number_positive")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_settlements_version_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_settlements_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], name=op.f("fk_settlements_employee_id_employees"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlements")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_settlements_client_request_id")),
        sa.UniqueConstraint("type", "employee_id", "business_date", "sequence_number", name="uq_settlements_sequence"),
        schema="settlements",
    )
    op.create_index(
        "ix_settlements_business_date", "settlements", ["business_date"], unique=False, schema="settlements"
    )
    op.create_index(
        op.f("ix_settlements_settlements_employee_id"),
        "settlements",
        ["employee_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "cash_additions",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("destination_account_id", sa.BigInteger(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("voided_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) OR (voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(btrim(coalesce(void_reason, ''))) > 0)",
            name=op.f("ck_cash_additions_void_complete"),
        ),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_cash_additions_fingerprint_format")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_cash_additions_amount_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cash_additions_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["destination_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_cash_additions_destination_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_cash_additions_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by"], ["users.id"], name=op.f("fk_cash_additions_voided_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cash_additions")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_cash_additions_client_request_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cash_additions_settlement_id"),
        "cash_additions",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "cash_count_lines",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("denomination_code", sa.String(length=40), nullable=False),
        sa.Column("denomination_value", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("destination_account_id", sa.BigInteger(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("denomination_value > 0", name=op.f("ck_cash_count_lines_denomination_value_positive")),
        sa.CheckConstraint("quantity > 0", name=op.f("ck_cash_count_lines_quantity_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cash_count_lines_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["destination_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_cash_count_lines_destination_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_cash_count_lines_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cash_count_lines")),
        sa.UniqueConstraint("settlement_id", "denomination_code", name="uq_cash_count_lines_denomination"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cash_count_lines_settlement_id"),
        "cash_count_lines",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "expenses",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("category_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("voided_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) OR (voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(btrim(coalesce(void_reason, ''))) > 0)",
            name=op.f("ck_expenses_void_complete"),
        ),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_expenses_fingerprint_format")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_expenses_amount_positive")),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["settlements.expense_categories.id"],
            name=op.f("fk_expenses_category_id_expense_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_expenses_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_expenses_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by"], ["users.id"], name=op.f("fk_expenses_voided_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expenses")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_expenses_client_request_id")),
        schema="settlements",
    )
    op.create_index("ix_expenses_category_id", "expenses", ["category_id"], unique=False, schema="settlements")
    op.create_index(
        op.f("ix_settlements_expenses_settlement_id"), "expenses", ["settlement_id"], unique=False, schema="settlements"
    )
    op.create_table(
        "settlement_transfers",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("payer_name", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="DECLARED", nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("status_changed_by", sa.BigInteger(), nullable=False),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_settlement_transfers_fingerprint_format")
        ),
        sa.CheckConstraint(
            "status <> 'REJECTED' OR length(btrim(coalesce(status_reason, ''))) > 0",
            name=op.f("ck_settlement_transfers_rejection_reason"),
        ),
        sa.CheckConstraint(
            "status IN ('DECLARED', 'VERIFIED', 'REJECTED')", name=op.f("ck_settlement_transfers_status")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_settlement_transfers_amount_positive")),
        sa.ForeignKeyConstraint(
            ["bank_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_settlement_transfers_bank_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_settlement_transfers_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_settlement_transfers_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["status_changed_by"],
            ["users.id"],
            name=op.f("fk_settlement_transfers_status_changed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_transfers")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_settlement_transfers_client_request_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_transfers_settlement_id"),
        "settlement_transfers",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "transfer_status_events",
        sa.Column("transfer_id", sa.BigInteger(), nullable=False),
        sa.Column("from_status", sa.String(length=20), nullable=True),
        sa.Column("to_status", sa.String(length=20), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("is_correction", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("changed_by", sa.BigInteger(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "from_status IS NULL OR from_status IN ('DECLARED', 'VERIFIED', 'REJECTED')",
            name=op.f("ck_transfer_status_events_from_status"),
        ),
        sa.CheckConstraint(
            "to_status IN ('DECLARED', 'VERIFIED', 'REJECTED')", name=op.f("ck_transfer_status_events_to_status")
        ),
        sa.ForeignKeyConstraint(
            ["changed_by"], ["users.id"], name=op.f("fk_transfer_status_events_changed_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["transfer_id"],
            ["settlements.settlement_transfers.id"],
            name=op.f("fk_transfer_status_events_transfer_id_settlement_transfers"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_transfer_status_events")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_transfer_status_events_transfer_id"),
        "transfer_status_events",
        ["transfer_id"],
        unique=False,
        schema="settlements",
    )
    op.execute(NO_DELETE)
    op.execute(ADDITION_VOID_ONLY)
    op.execute(CLOSED_IS_FROZEN)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_TRIGGERS)
    op.drop_index(
        op.f("ix_settlements_transfer_status_events_transfer_id"),
        table_name="transfer_status_events",
        schema="settlements",
    )
    op.drop_table("transfer_status_events", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_settlement_transfers_settlement_id"),
        table_name="settlement_transfers",
        schema="settlements",
    )
    op.drop_table("settlement_transfers", schema="settlements")
    op.drop_index(op.f("ix_settlements_expenses_settlement_id"), table_name="expenses", schema="settlements")
    op.drop_index("ix_expenses_category_id", table_name="expenses", schema="settlements")
    op.drop_table("expenses", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_cash_count_lines_settlement_id"), table_name="cash_count_lines", schema="settlements"
    )
    op.drop_table("cash_count_lines", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_cash_additions_settlement_id"), table_name="cash_additions", schema="settlements"
    )
    op.drop_table("cash_additions", schema="settlements")
    op.drop_index(op.f("ix_settlements_settlements_employee_id"), table_name="settlements", schema="settlements")
    op.drop_index("ix_settlements_business_date", table_name="settlements", schema="settlements")
    op.drop_table("settlements", schema="settlements")
    op.drop_table("expense_categories", schema="settlements")
