"""settlements: pending collection receipts and resolutions (R3-2)

Revision ID: 0010
Revises: 0009
Create Date: 2026-10-06 15:21:37.890109

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE', 'PENDING_COLLECTIONS'"
)
NEW_KINDS = OLD_KINDS + ", 'UNCOLLECTIBLE'"
OLD_TECHNICAL = (
    "'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE', 'PENDING_COLLECTIONS'"
)
NEW_TECHNICAL = OLD_TECHNICAL + ", 'UNCOLLECTIBLE'"
OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL'"
)
NEW_TYPES = OLD_TYPES + ", 'PENDING_COLLECTION', 'PENDING_WRITE_OFF'"
OLD_REASON_TYPES = "'ADJUSTMENT', 'REVERSAL'"
NEW_REASON_TYPES = OLD_REASON_TYPES + ", 'PENDING_WRITE_OFF'"
OLD_EVENTS = (
    "'CREATED', 'AMOUNT_CHANGED', 'CUSTOMER_CHANGED', 'RESPONSIBLE_CHANGED', 'REASON_CHANGED', "
    "'EXPECTED_DATE_CHANGED', 'VOIDED', 'PUBLISHED', 'WITHDRAWN'"
)
NEW_EVENTS = OLD_EVENTS + ", 'RECEIPT_RECORDED', 'RECEIPT_REVERSED', 'RESOLVED', 'RESOLUTION_REVERSED'"

# What the company gave up collecting (Enzo's R3 decision 6): technical, apart from settlement differences,
# never shown as available money.
SEED_ACCOUNT = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('uncollectible', 'Incobrables', 'incobrables', 'UNCOLLECTIBLE', false)
"""

# Receipts and resolutions are never deleted; the only change is their reversal, all four fields at once,
# once, pointing at the REVERSAL of their own operation. Resolution parts never change.
APPEND_ONLY = """
CREATE TRIGGER trg_pending_collection_receipts_no_delete BEFORE DELETE ON settlements.pending_collection_receipts
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_pending_collection_resolutions_no_delete
    BEFORE DELETE ON settlements.pending_collection_resolutions
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_pending_resolution_allocations_no_change
    BEFORE UPDATE OR DELETE ON settlements.pending_resolution_allocations
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

CREATE FUNCTION settlements.check_pending_application_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    reversal_fields text[] := ARRAY['reversal_operation_id', 'reversed_by', 'reversed_at', 'reversal_reason'];
BEGIN
    IF OLD.reversal_operation_id IS NOT NULL OR NEW.reversal_operation_id IS NULL
        OR (to_jsonb(NEW) - reversal_fields) IS DISTINCT FROM (to_jsonb(OLD) - reversal_fields)
    THEN
        RAISE EXCEPTION 'settlements.%: row % only changes to record its reversal, once', TG_TABLE_NAME, OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations r
        WHERE r.id = NEW.reversal_operation_id AND r.reverses_operation_id = NEW.operation_id
    ) THEN
        RAISE EXCEPTION 'settlements.%: operation % is not the reversal of %', TG_TABLE_NAME,
            NEW.reversal_operation_id, NEW.operation_id USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_pending_collection_receipts_reversal BEFORE UPDATE ON settlements.pending_collection_receipts
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_application_reversal();
CREATE TRIGGER trg_pending_collection_resolutions_reversal
    BEFORE UPDATE ON settlements.pending_collection_resolutions
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_application_reversal();
"""

# A receipt or resolution is exactly what its operation published: of its pending, on its date (never
# before the settlement's), with its amount leaving "Pendientes de cobro"; a receipt enters a cash box
# (CASH) or a bank (TRANSFER) and nothing else.
ROW_MATCHES_OPERATION = """
CREATE FUNCTION settlements.check_pending_application() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_type text := CASE TG_TABLE_NAME WHEN 'pending_collection_receipts' THEN 'PENDING_COLLECTION'
                                             ELSE 'PENDING_WRITE_OFF' END;
    settlement_date date;
BEGIN
    SELECT s.business_date INTO settlement_date FROM settlements.pending_collections p
        JOIN settlements.settlements s ON s.id = p.settlement_id WHERE p.id = NEW.pending_id;
    IF NEW.business_date < settlement_date THEN
        RAISE EXCEPTION 'settlements.%: date % is before the settlement (%)', TG_TABLE_NAME, NEW.business_date,
            settlement_date USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations o
        WHERE o.id = NEW.operation_id AND o.type = expected_type AND o.source_type = 'pending_collection'
            AND o.source_id = NEW.pending_id AND o.business_date = NEW.business_date
    ) OR NOT EXISTS (
        SELECT 1 FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
        WHERE e.operation_id = NEW.operation_id AND a.kind = 'PENDING_COLLECTIONS' AND e.amount = -NEW.amount
    ) THEN
        RAISE EXCEPTION 'settlements.%: operation % does not match the row', TG_TABLE_NAME, NEW.operation_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'pending_collection_receipts' THEN
        IF (SELECT count(*) FROM settlements.ledger_entries e WHERE e.operation_id = NEW.operation_id) <> 2
            OR NOT EXISTS (
                SELECT 1 FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
                WHERE e.operation_id = NEW.operation_id AND e.account_id = NEW.destination_account_id
                    AND e.amount = NEW.amount
                    AND a.kind = CASE NEW.method WHEN 'CASH' THEN 'CASH_BOX' ELSE 'BANK' END
            )
        THEN
            RAISE EXCEPTION 'settlements.pending_collection_receipts: operation % does not take % to its destination',
                NEW.operation_id, NEW.method USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_pending_collection_receipts_check BEFORE INSERT ON settlements.pending_collection_receipts
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_application();
CREATE TRIGGER trg_pending_collection_resolutions_check BEFORE INSERT ON settlements.pending_collection_resolutions
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_application();
"""

# Checked at commit: the parts of a resolution add up to it and are exactly the other legs of its
# operation (the company's part in "Incobrables", each employee's in their internal account).
RESOLUTION_PARTS = """
CREATE FUNCTION settlements.check_resolution_parts() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    res_id bigint;
    op_id bigint;
    total numeric;
BEGIN
    IF TG_TABLE_NAME = 'pending_collection_resolutions' THEN
        res_id := NEW.id;
    ELSE
        res_id := NEW.resolution_id;
    END IF;
    SELECT operation_id, amount INTO op_id, total FROM settlements.pending_collection_resolutions WHERE id = res_id;
    IF total IS DISTINCT FROM (
        SELECT sum(amount) FROM settlements.pending_resolution_allocations WHERE resolution_id = res_id
    ) OR EXISTS (
        (SELECT coalesce(a.employee_id, 0), e.amount FROM settlements.ledger_entries e
            JOIN settlements.accounts a ON a.id = e.account_id
            WHERE e.operation_id = op_id AND a.kind <> 'PENDING_COLLECTIONS'
         EXCEPT
         SELECT coalesce(employee_id, 0), amount FROM settlements.pending_resolution_allocations
            WHERE resolution_id = res_id)
        UNION ALL
        (SELECT coalesce(employee_id, 0), amount FROM settlements.pending_resolution_allocations
            WHERE resolution_id = res_id
         EXCEPT
         SELECT coalesce(a.employee_id, 0), e.amount FROM settlements.ledger_entries e
            JOIN settlements.accounts a ON a.id = e.account_id
            WHERE e.operation_id = op_id AND a.kind <> 'PENDING_COLLECTIONS')
    ) THEN
        RAISE EXCEPTION 'resolution %: its parts do not match its amount or its operation %', res_id, op_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_pending_collection_resolutions_parts
    AFTER INSERT ON settlements.pending_collection_resolutions DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_resolution_parts();
CREATE CONSTRAINT TRIGGER trg_pending_resolution_allocations_parts
    AFTER INSERT ON settlements.pending_resolution_allocations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_resolution_parts();
"""

# Checked at commit: no receipt or resolution operation without its row, and no reversal of one without
# its row marked reversed by it, so neither raw SQL nor Cajas can move a pending's money on their own.
NO_ORPHANS = """
CREATE FUNCTION settlements.check_pending_operation_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    original_type text;
BEGIN
    IF NEW.type = 'PENDING_COLLECTION' THEN
        IF NOT EXISTS (SELECT 1 FROM settlements.pending_collection_receipts WHERE operation_id = NEW.id) THEN
            RAISE EXCEPTION 'operation % has no pending collection receipt', NEW.id USING ERRCODE = 'check_violation';
        END IF;
    ELSIF NEW.type = 'PENDING_WRITE_OFF' THEN
        IF NOT EXISTS (SELECT 1 FROM settlements.pending_collection_resolutions WHERE operation_id = NEW.id) THEN
            RAISE EXCEPTION 'operation % has no pending resolution', NEW.id USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT type INTO original_type FROM settlements.financial_operations WHERE id = NEW.reverses_operation_id;
        IF original_type = 'PENDING_COLLECTION' AND NOT EXISTS (
            SELECT 1 FROM settlements.pending_collection_receipts
            WHERE operation_id = NEW.reverses_operation_id AND reversal_operation_id = NEW.id
        ) OR original_type = 'PENDING_WRITE_OFF' AND NOT EXISTS (
            SELECT 1 FROM settlements.pending_collection_resolutions
            WHERE operation_id = NEW.reverses_operation_id AND reversal_operation_id = NEW.id
        ) THEN
            RAISE EXCEPTION 'reversal % of a pending operation without its row marked reversed', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_pending_row
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('PENDING_COLLECTION', 'PENDING_WRITE_OFF', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_pending_operation_row();
"""

# Checked at commit: what was applied to a pending (receipts and resolutions not reversed) never exceeds
# its amount in effect (what its settlement's close in effect published), and nothing is applied to a
# pending that is not in effect. Rechecked when a receipt or resolution is added and when a close changes.
APPLIED_WITHIN_EFFECT = """
CREATE FUNCTION settlements.check_pending_applied() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    sid bigint;
    pid bigint;
    bad record;
BEGIN
    IF TG_TABLE_NAME = 'settlements' THEN
        sid := NEW.id;
    ELSE
        pid := NEW.pending_id;
    END IF;
    SELECT p.id, cp.amount AS in_effect, applied.amount AS applied INTO bad
    FROM settlements.pending_collections p
    JOIN settlements.settlements s ON s.id = p.settlement_id
    LEFT JOIN settlements.settlement_closing_pendings cp
        ON cp.pending_id = p.id AND cp.close_operation_id = s.current_close_operation_id
    CROSS JOIN LATERAL (
        SELECT coalesce((SELECT sum(amount) FROM settlements.pending_collection_receipts
                         WHERE pending_id = p.id AND reversal_operation_id IS NULL), 0)
             + coalesce((SELECT sum(amount) FROM settlements.pending_collection_resolutions
                         WHERE pending_id = p.id AND reversal_operation_id IS NULL), 0) AS amount
    ) applied
    WHERE (p.settlement_id = sid OR p.id = pid)
        AND applied.amount > 0 AND (cp.amount IS NULL OR applied.amount > cp.amount)
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'pending %: applied % exceeds its amount in effect %', bad.id, bad.applied, bad.in_effect
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_pending_collection_receipts_applied
    AFTER INSERT ON settlements.pending_collection_receipts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_applied();
CREATE CONSTRAINT TRIGGER trg_pending_collection_resolutions_applied
    AFTER INSERT ON settlements.pending_collection_resolutions DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_applied();
CREATE CONSTRAINT TRIGGER trg_settlements_pending_applied
    AFTER UPDATE OF current_close_operation_id ON settlements.settlements DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_applied();
"""

# The load changes only while its settlement is OPEN (0007/0009), with one exception (Enzo's R3-2): an
# admin may change who answers for collecting it after the close. Nothing else moves with it.
RESPONSIBLE_AFTER_CLOSE = """
CREATE FUNCTION settlements.check_pending_collection_open() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_status text;
    responsible_fields text[] := ARRAY['responsible_employee_id', 'updated_by', 'updated_at'];
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.settlement_id <> OLD.settlement_id THEN
        RAISE EXCEPTION 'settlements.pending_collections: a row cannot move to another settlement'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND (to_jsonb(NEW) - responsible_fields) = (to_jsonb(OLD) - responsible_fields) THEN
        RETURN NEW;
    END IF;
    SELECT status INTO current_status FROM settlements.settlements WHERE id = NEW.settlement_id;
    IF current_status <> 'OPEN' THEN
        RAISE EXCEPTION 'settlements.pending_collections: the settlement is % and cannot change', current_status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
DROP TRIGGER trg_pending_collections_open ON settlements.pending_collections;
CREATE TRIGGER trg_pending_collections_open BEFORE INSERT OR UPDATE ON settlements.pending_collections
    FOR EACH ROW EXECUTE FUNCTION settlements.check_pending_collection_open();
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_pending_collections_open ON settlements.pending_collections;
CREATE TRIGGER trg_pending_collections_open BEFORE INSERT OR UPDATE ON settlements.pending_collections
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_open();
DROP FUNCTION settlements.check_pending_collection_open();
DROP TRIGGER trg_settlements_pending_applied ON settlements.settlements;
DROP TRIGGER trg_pending_collection_resolutions_applied ON settlements.pending_collection_resolutions;
DROP TRIGGER trg_pending_collection_receipts_applied ON settlements.pending_collection_receipts;
DROP FUNCTION settlements.check_pending_applied();
DROP TRIGGER trg_financial_operations_pending_row ON settlements.financial_operations;
DROP FUNCTION settlements.check_pending_operation_row();
DROP TRIGGER trg_pending_resolution_allocations_parts ON settlements.pending_resolution_allocations;
DROP TRIGGER trg_pending_collection_resolutions_parts ON settlements.pending_collection_resolutions;
DROP FUNCTION settlements.check_resolution_parts();
DROP TRIGGER trg_pending_collection_resolutions_check ON settlements.pending_collection_resolutions;
DROP TRIGGER trg_pending_collection_receipts_check ON settlements.pending_collection_receipts;
DROP FUNCTION settlements.check_pending_application();
DROP TRIGGER trg_pending_collection_resolutions_reversal ON settlements.pending_collection_resolutions;
DROP TRIGGER trg_pending_collection_receipts_reversal ON settlements.pending_collection_receipts;
DROP FUNCTION settlements.check_pending_application_reversal();
DROP TRIGGER trg_pending_resolution_allocations_no_change ON settlements.pending_resolution_allocations;
DROP TRIGGER trg_pending_collection_resolutions_no_delete ON settlements.pending_collection_resolutions;
DROP TRIGGER trg_pending_collection_receipts_no_delete ON settlements.pending_collection_receipts;
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
        "pending_collection_receipts",
        sa.Column("pending_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("destination_account_id", sa.BigInteger(), nullable=False),
        sa.Column("operation_id", sa.BigInteger(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reversal_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("reversed_by", sa.BigInteger(), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(reversal_operation_id IS NULL AND reversed_by IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL) OR (reversal_operation_id IS NOT NULL AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL AND length(btrim(coalesce(reversal_reason, ''))) > 0)",
            name=op.f("ck_pending_collection_receipts_reversal_complete"),
        ),
        sa.CheckConstraint("method IN ('CASH', 'TRANSFER')", name=op.f("ck_pending_collection_receipts_method")),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_pending_collection_receipts_fingerprint_format")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_pending_collection_receipts_amount_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_pending_collection_receipts_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["destination_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_pending_collection_receipts_destination_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_pending_collection_receipts_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_id"],
            ["settlements.pending_collections.id"],
            name=op.f("fk_pending_collection_receipts_pending_id_pending_collections"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_pending_collection_receipts_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"],
            ["users.id"],
            name=op.f("fk_pending_collection_receipts_reversed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_collection_receipts")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_pending_collection_receipts_client_request_id")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_pending_collection_receipts_operation_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_pending_collection_receipts_reversal_operation_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_pending_collection_receipts_pending_id"),
        "pending_collection_receipts",
        ["pending_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "pending_collection_resolutions",
        sa.Column("pending_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.BigInteger(), nullable=False),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("reversal_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("reversed_by", sa.BigInteger(), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(reversal_operation_id IS NULL AND reversed_by IS NULL AND reversed_at IS NULL AND reversal_reason IS NULL) OR (reversal_operation_id IS NOT NULL AND reversed_by IS NOT NULL AND reversed_at IS NOT NULL AND length(btrim(coalesce(reversal_reason, ''))) > 0)",
            name=op.f("ck_pending_collection_resolutions_reversal_complete"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_pending_collection_resolutions_fingerprint_format")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_pending_collection_resolutions_amount_positive")),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0", name=op.f("ck_pending_collection_resolutions_reason_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_pending_collection_resolutions_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_pending_collection_resolutions_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["pending_id"],
            ["settlements.pending_collections.id"],
            name=op.f("fk_pending_collection_resolutions_pending_id_pending_collections"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_pending_collection_resolutions_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"],
            ["users.id"],
            name=op.f("fk_pending_collection_resolutions_reversed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_collection_resolutions")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_pending_collection_resolutions_client_request_id")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_pending_collection_resolutions_operation_id")),
        sa.UniqueConstraint(
            "reversal_operation_id", name=op.f("uq_pending_collection_resolutions_reversal_operation_id")
        ),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_pending_collection_resolutions_pending_id"),
        "pending_collection_resolutions",
        ["pending_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "pending_resolution_allocations",
        sa.Column("resolution_id", sa.BigInteger(), nullable=False),
        sa.Column("target", sa.String(length=20), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(target = 'EMPLOYEE') = (employee_id IS NOT NULL)",
            name=op.f("ck_pending_resolution_allocations_employee_target"),
        ),
        sa.CheckConstraint("target IN ('COMPANY', 'EMPLOYEE')", name=op.f("ck_pending_resolution_allocations_target")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_pending_resolution_allocations_amount_positive")),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_pending_resolution_allocations_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolution_id"],
            ["settlements.pending_collection_resolutions.id"],
            name=op.f("fk_pending_resolution_allocations_resolution_id_pending_collection_resolutions"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_pending_resolution_allocations")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_pending_resolution_allocations_resolution_id"),
        "pending_resolution_allocations",
        ["resolution_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_pending_resolution_allocations_company",
        "pending_resolution_allocations",
        ["resolution_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("target = 'COMPANY'"),
    )
    op.create_index(
        "uq_pending_resolution_allocations_employee",
        "pending_resolution_allocations",
        ["resolution_id", "employee_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("employee_id IS NOT NULL"),
    )
    op.add_column(
        "pending_collection_events", sa.Column("receipt_id", sa.BigInteger(), nullable=True), schema="settlements"
    )
    op.add_column(
        "pending_collection_events", sa.Column("resolution_id", sa.BigInteger(), nullable=True), schema="settlements"
    )
    op.create_foreign_key(
        op.f("fk_pending_collection_events_receipt_id_pending_collection_receipts"),
        "pending_collection_events",
        "pending_collection_receipts",
        ["receipt_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_pending_collection_events_resolution_id_pending_collection_resolutions"),
        "pending_collection_events",
        "pending_collection_resolutions",
        ["resolution_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_pending_collection_events_receipt_events"),
        "pending_collection_events",
        "(event_type IN ('RECEIPT_RECORDED', 'RECEIPT_REVERSED')) = (receipt_id IS NOT NULL)",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_pending_collection_events_resolution_events"),
        "pending_collection_events",
        "(event_type IN ('RESOLVED', 'RESOLUTION_REVERSED')) = (resolution_id IS NOT NULL)",
        schema="settlements",
    )
    _replace_check(
        "pending_collection_events", "ck_pending_collection_events_event_type", f"event_type IN ({NEW_EVENTS})"
    )
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    _technical_index(NEW_TECHNICAL)
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    _replace_check(
        "financial_operations",
        "ck_financial_operations_reason_required",
        f"type NOT IN ({NEW_REASON_TYPES}) OR length(btrim(coalesce(description, ''))) > 0",
    )
    op.execute(SEED_ACCOUNT)
    op.execute(APPEND_ONLY)
    op.execute(ROW_MATCHES_OPERATION)
    op.execute(RESOLUTION_PARTS)
    op.execute(NO_ORPHANS)
    op.execute(APPLIED_WITHIN_EFFECT)
    op.execute(RESPONSIBLE_AFTER_CLOSE)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_TRIGGERS)
    # Fails if any resolution used it: its history would be lost.
    op.execute("DELETE FROM settlements.accounts WHERE code = 'uncollectible'")
    _replace_check(
        "financial_operations",
        "ck_financial_operations_reason_required",
        f"type NOT IN ({OLD_REASON_TYPES}) OR length(btrim(coalesce(description, ''))) > 0",
    )
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
    _technical_index(OLD_TECHNICAL)
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
    _replace_check(
        "pending_collection_events", "ck_pending_collection_events_event_type", f"event_type IN ({OLD_EVENTS})"
    )
    op.drop_constraint(
        op.f("ck_pending_collection_events_resolution_events"),
        "pending_collection_events",
        schema="settlements",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_pending_collection_events_receipt_events"),
        "pending_collection_events",
        schema="settlements",
        type_="check",
    )
    op.drop_constraint(
        op.f("fk_pending_collection_events_resolution_id_pending_collection_resolutions"),
        "pending_collection_events",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_pending_collection_events_receipt_id_pending_collection_receipts"),
        "pending_collection_events",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_column("pending_collection_events", "resolution_id", schema="settlements")
    op.drop_column("pending_collection_events", "receipt_id", schema="settlements")
    op.drop_index(
        "uq_pending_resolution_allocations_employee",
        table_name="pending_resolution_allocations",
        schema="settlements",
        postgresql_where=sa.text("employee_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_pending_resolution_allocations_company",
        table_name="pending_resolution_allocations",
        schema="settlements",
        postgresql_where=sa.text("target = 'COMPANY'"),
    )
    op.drop_index(
        op.f("ix_settlements_pending_resolution_allocations_resolution_id"),
        table_name="pending_resolution_allocations",
        schema="settlements",
    )
    op.drop_table("pending_resolution_allocations", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_pending_collection_resolutions_pending_id"),
        table_name="pending_collection_resolutions",
        schema="settlements",
    )
    op.drop_table("pending_collection_resolutions", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_pending_collection_receipts_pending_id"),
        table_name="pending_collection_receipts",
        schema="settlements",
    )
    op.drop_table("pending_collection_receipts", schema="settlements")
