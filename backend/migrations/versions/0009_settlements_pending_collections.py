"""settlements: pending collections in settlement and close (R3-1)

Revision ID: 0009
Revises: 0008
Create Date: 2026-10-05 22:15:52.317803

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE'"
)
NEW_KINDS = OLD_KINDS + ", 'PENDING_COLLECTIONS'"
OLD_TECHNICAL = "'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE'"
NEW_TECHNICAL = OLD_TECHNICAL + ", 'PENDING_COLLECTIONS'"

# What customers still owe from settlements: a receivable, never negative (Enzo's R3-1).
SEED_ACCOUNT = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('pending_collections', 'Pendientes de cobro', 'pendientes de cobro', 'PENDING_COLLECTIONS', false)
"""

# A pending is never deleted; what a close published and the domain history are append-only. The load
# changes only while its settlement is OPEN (the 0007 rule), keeps its identity and request, and a voided
# pending does not change again.
HISTORY = """
CREATE TRIGGER trg_pending_collections_no_delete BEFORE DELETE ON settlements.pending_collections
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_settlement_closing_pendings_no_change
    BEFORE UPDATE OR DELETE ON settlements.settlement_closing_pendings
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_pending_collection_events_no_change
    BEFORE UPDATE OR DELETE ON settlements.pending_collection_events
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_pending_collections_open BEFORE INSERT OR UPDATE ON settlements.pending_collections
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_open();

CREATE FUNCTION settlements.check_pending_collection_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.voided_at IS NOT NULL THEN
        RAISE EXCEPTION 'settlements.pending_collections: pending % is voided and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.settlement_id, NEW.client_request_id, NEW.request_fingerprint, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM (OLD.settlement_id, OLD.client_request_id, OLD.request_fingerprint, OLD.created_by,
                          OLD.created_at)
    THEN
        RAISE EXCEPTION 'settlements.pending_collections: the identity of a pending never changes'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_pending_collections_update BEFORE UPDATE ON settlements.pending_collections
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_collection_update();
"""

# A close publishes only live pendings of its own settlement, and only from a close of that settlement.
CLOSING_ROW = """
CREATE FUNCTION settlements.check_closing_pending() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.pending_collections p
        WHERE p.id = NEW.pending_id AND p.settlement_id = NEW.settlement_id AND p.voided_at IS NULL
    ) THEN
        RAISE EXCEPTION 'pending % is voided or not of settlement %', NEW.pending_id, NEW.settlement_id
            USING ERRCODE = 'check_violation';
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
CREATE TRIGGER trg_settlement_closing_pendings_check BEFORE INSERT ON settlements.settlement_closing_pendings
    FOR EACH ROW EXECUTE FUNCTION settlements.check_closing_pending();
"""

# Checked at commit: what a close published per pending adds up exactly to its "Pendientes de cobro" leg,
# so the account balance is always the sum of the amounts in effect.
CLOSE_MATCHES_LEDGER = """
CREATE FUNCTION settlements.check_close_pendings() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    op_id bigint;
    published numeric;
    posted numeric;
BEGIN
    IF TG_TABLE_NAME = 'financial_operations' THEN
        op_id := NEW.id;
    ELSE
        op_id := NEW.close_operation_id;
    END IF;
    SELECT coalesce(sum(amount), 0) INTO published FROM settlements.settlement_closing_pendings
        WHERE close_operation_id = op_id;
    SELECT coalesce(sum(e.amount), 0) INTO posted FROM settlements.ledger_entries e
        JOIN settlements.accounts a ON a.id = e.account_id
        WHERE e.operation_id = op_id AND a.kind = 'PENDING_COLLECTIONS';
    IF published <> posted THEN
        RAISE EXCEPTION 'close %: published pendings (%) differ from its pending collections leg (%)',
            op_id, published, posted USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_close_pendings
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'SETTLEMENT_CLOSE')
    EXECUTE FUNCTION settlements.check_close_pendings();
CREATE CONSTRAINT TRIGGER trg_settlement_closing_pendings_match_ledger
    AFTER INSERT ON settlements.settlement_closing_pendings DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_close_pendings();
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_settlement_closing_pendings_match_ledger ON settlements.settlement_closing_pendings;
DROP TRIGGER trg_financial_operations_close_pendings ON settlements.financial_operations;
DROP FUNCTION settlements.check_close_pendings();
DROP TRIGGER trg_settlement_closing_pendings_check ON settlements.settlement_closing_pendings;
DROP FUNCTION settlements.check_closing_pending();
DROP TRIGGER trg_pending_collections_update ON settlements.pending_collections;
DROP FUNCTION settlements.check_pending_collection_update();
DROP TRIGGER trg_pending_collections_open ON settlements.pending_collections;
DROP TRIGGER trg_pending_collection_events_no_change ON settlements.pending_collection_events;
DROP TRIGGER trg_settlement_closing_pendings_no_change ON settlements.settlement_closing_pendings;
DROP TRIGGER trg_pending_collections_no_delete ON settlements.pending_collections;
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
        "pending_collections",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("responsible_employee_id", sa.BigInteger(), nullable=False),
        sa.Column("expected_date", sa.Date(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_by", sa.BigInteger(), nullable=True),
        sa.Column("voided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("void_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) OR (voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(btrim(coalesce(void_reason, ''))) > 0)",
            name=op.f("ck_pending_collections_void_complete"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_pending_collections_fingerprint_format")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_pending_collections_amount_positive")),
        sa.CheckConstraint("length(btrim(customer_name)) > 0", name=op.f("ck_pending_collections_customer_not_blank")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_pending_collections_reason_not_blank")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_pending_collections_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["responsible_employee_id"],
            ["employees.id"],
            name=op.f("fk_pending_collections_responsible_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_pending_collections_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_pending_collections_updated_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["voided_by"], ["users.id"], name=op.f("fk_pending_collections_voided_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_collections")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_pending_collections_client_request_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_pending_collections_responsible_employee_id"),
        "pending_collections",
        ["responsible_employee_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_pending_collections_settlement_id"),
        "pending_collections",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "pending_collection_events",
        sa.Column("pending_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("close_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(event_type IN ('PUBLISHED', 'WITHDRAWN')) = (close_operation_id IS NOT NULL)",
            name=op.f("ck_pending_collection_events_close_events"),
        ),
        sa.CheckConstraint(
            "event_type <> 'VOIDED' OR length(btrim(coalesce(reason, ''))) > 0",
            name=op.f("ck_pending_collection_events_void_reason"),
        ),
        sa.CheckConstraint(
            "event_type IN ('CREATED', 'AMOUNT_CHANGED', 'CUSTOMER_CHANGED', 'RESPONSIBLE_CHANGED', 'REASON_CHANGED', 'EXPECTED_DATE_CHANGED', 'VOIDED', 'PUBLISHED', 'WITHDRAWN')",
            name=op.f("ck_pending_collection_events_event_type"),
        ),
        sa.CheckConstraint(
            "settlement_version >= 1", name=op.f("ck_pending_collection_events_settlement_version_positive")
        ),
        sa.ForeignKeyConstraint(
            ["close_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_pending_collection_events_close_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_pending_collection_events_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_id"],
            ["settlements.pending_collections.id"],
            name=op.f("fk_pending_collection_events_pending_id_pending_collections"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_pending_collection_events_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_collection_events")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_pending_collection_events_pending_id"),
        "pending_collection_events",
        ["pending_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "settlement_closing_pendings",
        sa.Column("close_operation_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("pending_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("customer_name", sa.String(length=200), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_settlement_closing_pendings_amount_positive")),
        sa.CheckConstraint(
            "length(btrim(customer_name)) > 0", name=op.f("ck_settlement_closing_pendings_customer_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["close_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_settlement_closing_pendings_close_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_id"],
            ["settlements.pending_collections.id"],
            name=op.f("fk_settlement_closing_pendings_pending_id_pending_collections"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_settlement_closing_pendings_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_closing_pendings")),
        sa.UniqueConstraint("close_operation_id", "pending_id", name="uq_settlement_closing_pendings_close_pending"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_pendings_pending_id"),
        "settlement_closing_pendings",
        ["pending_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_pendings_settlement_id"),
        "settlement_closing_pendings",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    _technical_index(NEW_TECHNICAL)
    op.execute(SEED_ACCOUNT)
    op.execute(HISTORY)
    op.execute(CLOSING_ROW)
    op.execute(CLOSE_MATCHES_LEDGER)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_TRIGGERS)
    # Fails if any close used it: its history would be lost.
    op.execute("DELETE FROM settlements.accounts WHERE code = 'pending_collections'")
    _technical_index(OLD_TECHNICAL)
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
    op.drop_index(
        op.f("ix_settlements_settlement_closing_pendings_settlement_id"),
        table_name="settlement_closing_pendings",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_settlement_closing_pendings_pending_id"),
        table_name="settlement_closing_pendings",
        schema="settlements",
    )
    op.drop_table("settlement_closing_pendings", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_pending_collection_events_pending_id"),
        table_name="pending_collection_events",
        schema="settlements",
    )
    op.drop_table("pending_collection_events", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_pending_collections_settlement_id"), table_name="pending_collections", schema="settlements"
    )
    op.drop_index(
        op.f("ix_settlements_pending_collections_responsible_employee_id"),
        table_name="pending_collections",
        schema="settlements",
    )
    op.drop_table("pending_collections", schema="settlements")
