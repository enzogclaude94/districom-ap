"""settlements: cheques, their portfolio and cheques in a settlement (R4-1)

Revision ID: 0012
Revises: 0011
Create Date: 2026-10-07 15:43:32.970274

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0012"
down_revision: str | Sequence[str] | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE', 'PENDING_COLLECTIONS', 'UNCOLLECTIBLE'"
)
NEW_KINDS = OLD_KINDS + ", 'CHEQUE_PORTFOLIO', 'CHEQUES_IN_TRANSIT'"
OLD_TECHNICAL = (
    "'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE', "
    "'PENDING_COLLECTIONS', 'UNCOLLECTIBLE'"
)
NEW_TECHNICAL = OLD_TECHNICAL + ", 'CHEQUE_PORTFOLIO', 'CHEQUES_IN_TRANSIT'"
OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF'"
)
NEW_TYPES = OLD_TYPES + ", 'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION'"

# Valores (Enzo's R4 decisions): technical accounts, never negative, never available money. Every leg in
# them names its cheque.
SEED_ACCOUNTS = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('cheque_portfolio', 'Cartera de cheques', 'cartera de cheques', 'CHEQUE_PORTFOLIO', false),
    ('cheques_in_transit', 'Cheques depositados', 'cheques depositados', 'CHEQUES_IN_TRANSIT', false)
"""

# A cheque is never deleted; its events and what each close published are append-only. A load of a
# settlement is inserted only while the settlement is OPEN.
HISTORY = """
CREATE TRIGGER trg_cheques_no_delete BEFORE DELETE ON settlements.cheques
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_cheque_events_no_change BEFORE UPDATE OR DELETE ON settlements.cheque_events
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_settlement_closing_cheques_no_change
    BEFORE UPDATE OR DELETE ON settlements.settlement_closing_cheques
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

CREATE FUNCTION settlements.check_cheque_insert() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.settlement_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.settlements WHERE id = NEW.settlement_id AND status = 'OPEN'
    ) THEN
        RAISE EXCEPTION 'settlements.cheques: settlement % is not OPEN', NEW.settlement_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheques_insert BEFORE INSERT ON settlements.cheques
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_insert();
"""

# What a cheque is never changes after the fact (Enzo's R4 decisions):
# - its origin, its request and who confirmed having it in hand, never;
# - a voided cheque, never again;
# - once it had any leg in the ledger (published): only descriptive data (drawer, notes) and, during a
#   correction of its settlement, its void (marked for removal; it leaves the portfolio with the new close);
# - before that, the load of a settlement changes only while it is OPEN.
UPDATE_GUARD = """
CREATE FUNCTION settlements.check_cheque_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    fixed text[] := ARRAY['kind', 'origin_type', 'settlement_id', 'received_business_date',
        'physical_receipt_confirmed_by', 'physical_receipt_confirmed_at', 'client_request_id',
        'request_fingerprint', 'created_by', 'created_at'];
    descriptive text[] := ARRAY['drawer_name', 'drawer_tax_id', 'notes', 'updated_by', 'updated_at'];
    voiding text[] := ARRAY['voided_by', 'voided_at', 'void_reason'];
    changed text[];
    published boolean;
BEGIN
    IF OLD.voided_at IS NOT NULL THEN
        RAISE EXCEPTION 'settlements.cheques: cheque % is voided and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    SELECT coalesce(array_agg(n.key), ARRAY[]::text[]) INTO changed
        FROM jsonb_each(to_jsonb(NEW)) n JOIN jsonb_each(to_jsonb(OLD)) o USING (key)
        WHERE n.value IS DISTINCT FROM o.value;
    IF changed && fixed THEN
        RAISE EXCEPTION 'settlements.cheques: % of cheque % never change', changed, OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.kind = 'ECHEQ' AND 'drawer_tax_id' = ANY(changed) AND EXISTS (
        SELECT 1 FROM settlements.ledger_entries WHERE cheque_id = OLD.id
    ) THEN
        RAISE EXCEPTION 'settlements.cheques: the drawer of a published ECHEQ is part of its identity'
            USING ERRCODE = 'restrict_violation';
    END IF;
    published := EXISTS (SELECT 1 FROM settlements.ledger_entries WHERE cheque_id = OLD.id);
    IF published AND NOT (changed <@ (descriptive || voiding)) THEN
        RAISE EXCEPTION 'settlements.cheques: cheque % had a financial effect; only descriptive data changes',
            OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.settlement_id IS NOT NULL AND NOT (changed <@ descriptive) AND NOT EXISTS (
        SELECT 1 FROM settlements.settlements WHERE id = OLD.settlement_id AND status = 'OPEN'
    ) THEN
        RAISE EXCEPTION 'settlements.cheques: the settlement of cheque % is not OPEN', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheques_update BEFORE UPDATE ON settlements.cheques
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_update();
"""

# A leg names a cheque exactly when its account is a cheque account, and moves the whole cheque.
LEDGER_LEG = """
CREATE FUNCTION settlements.check_cheque_leg() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    account_kind text;
    cheque_amount numeric;
BEGIN
    SELECT kind INTO account_kind FROM settlements.accounts WHERE id = NEW.account_id;
    IF (account_kind IN ('CHEQUE_PORTFOLIO', 'CHEQUES_IN_TRANSIT')) <> (NEW.cheque_id IS NOT NULL) THEN
        RAISE EXCEPTION 'settlements.ledger_entries: a leg names a cheque iff its account is a cheque account'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.cheque_id IS NOT NULL THEN
        SELECT amount INTO cheque_amount FROM settlements.cheques WHERE id = NEW.cheque_id;
        IF abs(NEW.amount) <> cheque_amount THEN
            RAISE EXCEPTION 'settlements.ledger_entries: a leg moves cheque % whole (%), not %', NEW.cheque_id,
                cheque_amount, NEW.amount USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_ledger_entries_cheque_leg BEFORE INSERT ON settlements.ledger_entries
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_leg();
"""

# Checked at commit, for every cheque that got legs: in each cheque account it has nothing or its whole
# amount, it is in at most one of them, and a cheque that is somewhere is live and justifies (a physical
# cheque, or a VERIFIED ECHEQ). So "Cartera de cheques" is always the sum of the cheques in it.
POSITION = """
CREATE FUNCTION settlements.check_cheque_position() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    c settlements.cheques%ROWTYPE;
    held int;
BEGIN
    SELECT * INTO c FROM settlements.cheques WHERE id = NEW.cheque_id;
    IF EXISTS (
        SELECT 1 FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
        WHERE e.cheque_id = c.id GROUP BY a.id HAVING sum(e.amount) NOT IN (0, c.amount)
    ) THEN
        RAISE EXCEPTION 'cheque %: its legs leave a partial or negative position', c.id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*) INTO held FROM (
        SELECT a.id FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
        WHERE e.cheque_id = c.id GROUP BY a.id HAVING sum(e.amount) > 0
    ) x;
    IF held > 1 THEN
        RAISE EXCEPTION 'cheque % is in more than one place', c.id USING ERRCODE = 'check_violation';
    END IF;
    IF held = 1 AND (c.voided_at IS NOT NULL OR (c.kind = 'ECHEQ' AND c.verification_status <> 'VERIFIED')) THEN
        RAISE EXCEPTION 'cheque % is voided or an unverified ECHEQ and cannot be held', c.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_ledger_entries_cheque_position
    AFTER INSERT ON settlements.ledger_entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.cheque_id IS NOT NULL)
    EXECUTE FUNCTION settlements.check_cheque_position();
"""

# A close puts in the portfolio only live, justifying cheques of its own settlement, and what it froze in
# `settlement_closing_cheques` is exactly its legs on "Cartera de cheques", cheque by cheque.
CLOSING_ROW = """
CREATE FUNCTION settlements.check_closing_cheque() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.cheques c
        WHERE c.id = NEW.cheque_id AND c.settlement_id = NEW.settlement_id AND c.voided_at IS NULL
            AND c.amount = NEW.amount AND (c.kind = 'PHYSICAL' OR c.verification_status = 'VERIFIED')
    ) THEN
        RAISE EXCEPTION 'cheque % is voided, unverified, of another amount or not of settlement %',
            NEW.cheque_id, NEW.settlement_id USING ERRCODE = 'check_violation';
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
CREATE TRIGGER trg_settlement_closing_cheques_check BEFORE INSERT ON settlements.settlement_closing_cheques
    FOR EACH ROW EXECUTE FUNCTION settlements.check_closing_cheque();

CREATE FUNCTION settlements.check_close_cheques() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    op_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'financial_operations' THEN
        op_id := NEW.id;
    ELSE
        op_id := NEW.close_operation_id;
    END IF;
    IF EXISTS (
        (SELECT cheque_id, amount FROM settlements.settlement_closing_cheques WHERE close_operation_id = op_id
         EXCEPT
         SELECT e.cheque_id, e.amount FROM settlements.ledger_entries e
            JOIN settlements.accounts a ON a.id = e.account_id
            WHERE e.operation_id = op_id AND a.kind = 'CHEQUE_PORTFOLIO')
        UNION ALL
        (SELECT e.cheque_id, e.amount FROM settlements.ledger_entries e
            JOIN settlements.accounts a ON a.id = e.account_id
            WHERE e.operation_id = op_id AND a.kind = 'CHEQUE_PORTFOLIO'
         EXCEPT
         SELECT cheque_id, amount FROM settlements.settlement_closing_cheques WHERE close_operation_id = op_id)
    ) THEN
        RAISE EXCEPTION 'close %: the cheques it published differ from its portfolio legs', op_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_close_cheques
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'SETTLEMENT_CLOSE')
    EXECUTE FUNCTION settlements.check_close_cheques();
CREATE CONSTRAINT TRIGGER trg_settlement_closing_cheques_match_ledger
    AFTER INSERT ON settlements.settlement_closing_cheques DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_close_cheques();
"""

# An opening balance or a partner contribution with a cheque is the cheque's own document: its source is
# that cheque, its only cheque leg is that cheque, and the cheque's origin says the same.
ADMINISTRATIVE_ENTRY = """
CREATE FUNCTION settlements.check_cheque_entry() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.source_type IS DISTINCT FROM 'cheque' OR NOT EXISTS (
        SELECT 1 FROM settlements.cheques c
        WHERE c.id = NEW.source_id AND c.settlement_id IS NULL AND c.origin_type =
            CASE NEW.type WHEN 'CHEQUE_OPENING_BALANCE' THEN 'OPENING_BALANCE' ELSE 'OWNER_CONTRIBUTION' END
    ) OR EXISTS (
        SELECT 1 FROM settlements.ledger_entries e
        WHERE e.operation_id = NEW.id AND e.cheque_id IS NOT NULL AND e.cheque_id <> NEW.source_id
    ) OR NOT EXISTS (
        SELECT 1 FROM settlements.ledger_entries e WHERE e.operation_id = NEW.id AND e.cheque_id = NEW.source_id
    ) THEN
        RAISE EXCEPTION 'operation %: a cheque entry moves exactly its own cheque', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_cheque_entry
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION'))
    EXECUTE FUNCTION settlements.check_cheque_entry();
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_financial_operations_cheque_entry ON settlements.financial_operations;
DROP FUNCTION settlements.check_cheque_entry();
DROP TRIGGER trg_settlement_closing_cheques_match_ledger ON settlements.settlement_closing_cheques;
DROP TRIGGER trg_financial_operations_close_cheques ON settlements.financial_operations;
DROP FUNCTION settlements.check_close_cheques();
DROP TRIGGER trg_settlement_closing_cheques_check ON settlements.settlement_closing_cheques;
DROP FUNCTION settlements.check_closing_cheque();
DROP TRIGGER trg_ledger_entries_cheque_position ON settlements.ledger_entries;
DROP FUNCTION settlements.check_cheque_position();
DROP TRIGGER trg_ledger_entries_cheque_leg ON settlements.ledger_entries;
DROP FUNCTION settlements.check_cheque_leg();
DROP TRIGGER trg_cheques_update ON settlements.cheques;
DROP FUNCTION settlements.check_cheque_update();
DROP TRIGGER trg_cheques_insert ON settlements.cheques;
DROP FUNCTION settlements.check_cheque_insert();
DROP TRIGGER trg_settlement_closing_cheques_no_change ON settlements.settlement_closing_cheques;
DROP TRIGGER trg_cheque_events_no_change ON settlements.cheque_events;
DROP TRIGGER trg_cheques_no_delete ON settlements.cheques;
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
        "cheques",
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("payment_type", sa.String(length=10), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("issuer_bank_id", sa.BigInteger(), nullable=False),
        sa.Column("branch_code", sa.String(length=10), nullable=True),
        sa.Column("account_ref", sa.String(length=34), nullable=True),
        sa.Column("cheque_number", sa.String(length=20), nullable=False),
        sa.Column("echeq_external_id", sa.String(length=64), nullable=True),
        sa.Column("instrument_key", sa.String(length=200), nullable=True),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("drawer_name", sa.String(length=200), nullable=False),
        sa.Column("drawer_tax_id", sa.String(length=11), nullable=True),
        sa.Column("delivered_by_name", sa.String(length=200), nullable=False),
        sa.Column("origin_type", sa.String(length=30), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=True),
        sa.Column("received_business_date", sa.Date(), nullable=False),
        sa.Column("received_by_employee_id", sa.BigInteger(), nullable=True),
        sa.Column("physical_receipt_confirmed_by", sa.BigInteger(), nullable=True),
        sa.Column("physical_receipt_confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_status", sa.String(length=30), nullable=True),
        sa.Column("verification_changed_by", sa.BigInteger(), nullable=True),
        sa.Column("verification_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verification_reason", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            "(kind = 'ECHEQ') = (verification_status IS NOT NULL) AND (verification_status IS NULL OR verification_status IN ('DECLARED', 'VERIFIED', 'VERIFICATION_REJECTED'))",
            name=op.f("ck_cheques_verification_by_kind"),
        ),
        sa.CheckConstraint(
            "(kind = 'PHYSICAL') = (physical_receipt_confirmed_by IS NOT NULL AND physical_receipt_confirmed_at IS NOT NULL)",
            name=op.f("ck_cheques_physical_receipt"),
        ),
        sa.CheckConstraint(
            "(origin_type = 'SETTLEMENT') = (settlement_id IS NOT NULL)", name=op.f("ck_cheques_settlement_origin")
        ),
        sa.CheckConstraint(
            "(payment_type = 'DEFERRED') = (payment_date IS NOT NULL) AND "
            "(payment_date IS NULL OR (payment_date > issue_date AND payment_date <= issue_date + 360))",
            name=op.f("ck_cheques_payment_date_by_type"),
        ),
        sa.CheckConstraint(
            "(voided_at IS NULL AND voided_by IS NULL AND void_reason IS NULL) OR (voided_at IS NOT NULL AND voided_by IS NOT NULL AND length(btrim(coalesce(void_reason, ''))) > 0)",
            name=op.f("ck_cheques_void_complete"),
        ),
        sa.CheckConstraint(
            "account_ref IS NULL OR account_ref ~ '^[0-9A-Z]{1,34}$'", name=op.f("ck_cheques_account_format")
        ),
        sa.CheckConstraint(
            "branch_code IS NULL OR branch_code ~ '^[0-9]{1,10}$'", name=op.f("ck_cheques_branch_digits")
        ),
        sa.CheckConstraint("cheque_number ~ '^[0-9]{1,20}$'", name=op.f("ck_cheques_number_digits")),
        sa.CheckConstraint(
            "drawer_tax_id IS NULL OR drawer_tax_id ~ '^[0-9]{11}$'", name=op.f("ck_cheques_drawer_tax_id_format")
        ),
        sa.CheckConstraint(
            "kind = 'ECHEQ' OR echeq_external_id IS NULL", name=op.f("ck_cheques_external_id_only_echeq")
        ),
        sa.CheckConstraint(
            "kind = 'PHYSICAL' OR drawer_tax_id IS NOT NULL", name=op.f("ck_cheques_echeq_drawer_tax_id")
        ),
        sa.CheckConstraint("kind IN ('PHYSICAL', 'ECHEQ')", name=op.f("ck_cheques_kind")),
        sa.CheckConstraint(
            "origin_type IN ('SETTLEMENT', 'OPENING_BALANCE', 'OWNER_CONTRIBUTION')",
            name=op.f("ck_cheques_origin_type"),
        ),
        sa.CheckConstraint("payment_type IN ('COMMON', 'DEFERRED')", name=op.f("ck_cheques_payment_type")),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_cheques_fingerprint_format")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_cheques_amount_positive")),
        sa.CheckConstraint("issue_date <= received_business_date", name=op.f("ck_cheques_issued_before_received")),
        sa.CheckConstraint("length(btrim(delivered_by_name)) > 0", name=op.f("ck_cheques_delivered_by_not_blank")),
        sa.CheckConstraint("length(btrim(drawer_name)) > 0", name=op.f("ck_cheques_drawer_not_blank")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cheques_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["issuer_bank_id"],
            ["financial_institutions.id"],
            name=op.f("fk_cheques_issuer_bank_id_financial_institutions"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["physical_receipt_confirmed_by"],
            ["users.id"],
            name=op.f("fk_cheques_physical_receipt_confirmed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["received_by_employee_id"],
            ["employees.id"],
            name=op.f("fk_cheques_received_by_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_cheques_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_cheques_updated_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["verification_changed_by"],
            ["users.id"],
            name=op.f("fk_cheques_verification_changed_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["voided_by"], ["users.id"], name=op.f("fk_cheques_voided_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheques")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_cheques_client_request_id")),
        schema="settlements",
    )
    op.create_index(
        "ix_cheques_bank_number", "cheques", ["issuer_bank_id", "cheque_number"], unique=False, schema="settlements"
    )
    op.create_index(
        op.f("ix_settlements_cheques_settlement_id"), "cheques", ["settlement_id"], unique=False, schema="settlements"
    )
    op.create_index(
        "uq_cheques_echeq_external_id",
        "cheques",
        ["echeq_external_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text(
            "echeq_external_id IS NOT NULL AND voided_at IS NULL AND coalesce(verification_status, '') <> 'VERIFICATION_REJECTED'"
        ),
    )
    op.create_index(
        "uq_cheques_instrument_key",
        "cheques",
        ["instrument_key"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text(
            "instrument_key IS NOT NULL AND voided_at IS NULL AND coalesce(verification_status, '') <> 'VERIFICATION_REJECTED'"
        ),
    )
    op.create_table(
        "cheque_events",
        sa.Column("cheque_id", sa.BigInteger(), nullable=False),
        sa.Column("event_type", sa.String(length=30), nullable=False),
        sa.Column("before", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("after", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("settlement_id", sa.BigInteger(), nullable=True),
        sa.Column("settlement_version", sa.Integer(), nullable=True),
        sa.Column("close_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("operation_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(close_operation_id IS NULL OR event_type IN ('PUBLISHED', 'WITHDRAWN')) AND (event_type NOT IN ('PUBLISHED', 'WITHDRAWN') OR num_nonnulls(close_operation_id, operation_id) = 1)",
            name=op.f("ck_cheque_events_close_events"),
        ),
        sa.CheckConstraint(
            "event_type IN ('RECEIVED', 'CORRECTED', 'VERIFIED', 'VERIFICATION_REJECTED', 'VOIDED', 'PUBLISHED', 'WITHDRAWN', 'DESCRIPTIVE_CORRECTED')",
            name=op.f("ck_cheque_events_event_type"),
        ),
        sa.CheckConstraint(
            "event_type NOT IN ('VOIDED', 'VERIFICATION_REJECTED', 'DESCRIPTIVE_CORRECTED') OR length(btrim(coalesce(reason, ''))) > 0",
            name=op.f("ck_cheque_events_reason_required"),
        ),
        sa.CheckConstraint(
            "(settlement_id IS NULL) = (settlement_version IS NULL)", name=op.f("ck_cheque_events_settlement_pair")
        ),
        sa.ForeignKeyConstraint(
            ["cheque_id"],
            ["settlements.cheques.id"],
            name=op.f("fk_cheque_events_cheque_id_cheques"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["close_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_cheque_events_close_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cheque_events_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_cheque_events_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_cheque_events_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheque_events")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cheque_events_cheque_id"),
        "cheque_events",
        ["cheque_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "settlement_closing_cheques",
        sa.Column("close_operation_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("cheque_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_settlement_closing_cheques_amount_positive")),
        sa.ForeignKeyConstraint(
            ["cheque_id"],
            ["settlements.cheques.id"],
            name=op.f("fk_settlement_closing_cheques_cheque_id_cheques"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["close_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_settlement_closing_cheques_close_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_settlement_closing_cheques_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_closing_cheques")),
        sa.UniqueConstraint("close_operation_id", "cheque_id", name="uq_settlement_closing_cheques_close_cheque"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_cheques_cheque_id"),
        "settlement_closing_cheques",
        ["cheque_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_cheques_close_operation_id"),
        "settlement_closing_cheques",
        ["close_operation_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_settlement_closing_cheques_settlement_id"),
        "settlement_closing_cheques",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.add_column("ledger_entries", sa.Column("cheque_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_index(
        op.f("ix_settlements_ledger_entries_cheque_id"),
        "ledger_entries",
        ["cheque_id"],
        unique=False,
        schema="settlements",
    )
    op.create_foreign_key(
        op.f("fk_ledger_entries_cheque_id_cheques"),
        "ledger_entries",
        "cheques",
        ["cheque_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    _technical_index(NEW_TECHNICAL)
    op.execute(SEED_ACCOUNTS)
    op.execute(HISTORY)
    op.execute(UPDATE_GUARD)
    op.execute(LEDGER_LEG)
    op.execute(POSITION)
    op.execute(CLOSING_ROW)
    op.execute(ADMINISTRATIVE_ENTRY)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_TRIGGERS)
    # Fails if any operation used them: their history would be lost.
    op.execute("DELETE FROM settlements.accounts WHERE code IN ('cheque_portfolio', 'cheques_in_transit')")
    _technical_index(OLD_TECHNICAL)
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
    op.drop_constraint(
        op.f("fk_ledger_entries_cheque_id_cheques"), "ledger_entries", schema="settlements", type_="foreignkey"
    )
    op.drop_index(op.f("ix_settlements_ledger_entries_cheque_id"), table_name="ledger_entries", schema="settlements")
    op.drop_column("ledger_entries", "cheque_id", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_settlement_closing_cheques_settlement_id"),
        table_name="settlement_closing_cheques",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_settlement_closing_cheques_close_operation_id"),
        table_name="settlement_closing_cheques",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_settlement_closing_cheques_cheque_id"),
        table_name="settlement_closing_cheques",
        schema="settlements",
    )
    op.drop_table("settlement_closing_cheques", schema="settlements")
    op.drop_index(op.f("ix_settlements_cheque_events_cheque_id"), table_name="cheque_events", schema="settlements")
    op.drop_table("cheque_events", schema="settlements")
    op.drop_index(
        "uq_cheques_instrument_key",
        table_name="cheques",
        schema="settlements",
        postgresql_where=sa.text(
            "instrument_key IS NOT NULL AND voided_at IS NULL AND coalesce(verification_status, '') <> 'VERIFICATION_REJECTED'"
        ),
    )
    op.drop_index(
        "uq_cheques_echeq_external_id",
        table_name="cheques",
        schema="settlements",
        postgresql_where=sa.text(
            "echeq_external_id IS NOT NULL AND voided_at IS NULL AND coalesce(verification_status, '') <> 'VERIFICATION_REJECTED'"
        ),
    )
    op.drop_index(op.f("ix_settlements_cheques_settlement_id"), table_name="cheques", schema="settlements")
    op.drop_index("ix_cheques_bank_number", table_name="cheques", schema="settlements")
    op.drop_table("cheques", schema="settlements")
