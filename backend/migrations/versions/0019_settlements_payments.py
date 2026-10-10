"""settlements: payments to third parties with cash and transfers (R6-1)

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-09 23:50:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0019"
down_revision: str | Sequence[str] | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE', 'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', "
    "'CHEQUES_IN_TRANSIT'"
)
NEW_KINDS = OLD_KINDS + ", 'MERCHANDISE_PAYMENTS', 'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS'"
OLD_TECHNICAL = (
    "'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE', "
    "'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', 'CHEQUES_IN_TRANSIT'"
)
NEW_TECHNICAL = OLD_TECHNICAL + ", 'MERCHANDISE_PAYMENTS', 'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS'"
OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION', 'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', "
    "'CHEQUE_RETURN', 'DIRECT_EXPENSE'"
)
NEW_TYPES = OLD_TYPES + ", 'PAYMENT'"

# Where a payment went (Enzo's R6 decision D1): technical classifiers, one row each, never negative. They
# are not accounts payable, debt, valued inventory nor accrued purchases. EXPENSE («Gastos operativos»)
# already exists and is reused for a payment of an expense.
SEED_ACCOUNTS = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('merchandise_payments', 'Pagos por mercadería', 'pagos por mercaderia', 'MERCHANDISE_PAYMENTS', false),
    ('third_party_refunds', 'Devoluciones a terceros', 'devoluciones a terceros', 'THIRD_PARTY_REFUNDS', false),
    ('other_payments', 'Otros pagos', 'otros pagos', 'OTHER_PAYMENTS', false)
"""

# A payment is born for an ACTIVE third party (locked FOR SHARE, so a concurrent deactivation serializes
# with it), with an active expense category that is not fuel when its concept is EXPENSE, and OTHER only by
# an admin of Rendiciones. Once written its money never changes: third party, date, concept, category,
# amount and request are fixed; the operation is set once, in the creating transaction; the reversal is
# recorded once; a reversed payment does not change at all. Only `reference` and `description` change (an
# admin's descriptive correction). Never deleted.
PAYMENT_GUARDS = """
CREATE FUNCTION settlements.check_payment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    third_party_active boolean;
    category record;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT active INTO third_party_active FROM public.third_parties WHERE id = NEW.third_party_id FOR SHARE;
        IF NOT coalesce(third_party_active, false) THEN
            RAISE EXCEPTION 'payments: third party % is not active', NEW.third_party_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.concept = 'EXPENSE' THEN
            SELECT active, code INTO category FROM settlements.expense_categories WHERE id = NEW.expense_category_id;
            IF NOT coalesce(category.active, false) THEN
                RAISE EXCEPTION 'payments: expense category % is not active', NEW.expense_category_id
                    USING ERRCODE = 'check_violation';
            END IF;
            IF category.code IS NOT DISTINCT FROM 'FUEL' THEN
                RAISE EXCEPTION 'payments: fuel is not paid as a payment (category %)', NEW.expense_category_id
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        IF NEW.concept = 'OTHER' AND NOT EXISTS (
            SELECT 1 FROM public.users u WHERE u.id = NEW.created_by AND (u.role = 'admin' OR EXISTS (
                SELECT 1 FROM public.user_module_roles r
                WHERE r.user_id = u.id AND r.module_key = 'settlements' AND r.role = 'admin'))) THEN
            RAISE EXCEPTION 'payments: OTHER is recorded only by an admin of Rendiciones'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.operation_id IS NOT NULL OR NEW.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'payments: a payment is born without its operation and in effect'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.reversal_operation_id IS NOT NULL THEN
        RAISE EXCEPTION 'payments: payment % is reversed and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.third_party_id, NEW.business_date, NEW.concept, NEW.expense_category_id, NEW.amount,
        NEW.client_request_id, NEW.request_fingerprint, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM
       (OLD.third_party_id, OLD.business_date, OLD.concept, OLD.expense_category_id, OLD.amount,
        OLD.client_request_id, OLD.request_fingerprint, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'payments: payment % is published and its money never changes; reverse it', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN
        RAISE EXCEPTION 'payments: the operation of payment % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_payments_guard BEFORE INSERT OR UPDATE ON settlements.payments
    FOR EACH ROW EXECUTE FUNCTION settlements.check_payment();
CREATE TRIGGER trg_payments_no_delete BEFORE DELETE ON settlements.payments
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

-- Items are written with their payment, before its operation, and never change after: only their
-- reference (an admin's descriptive correction, while the payment is in effect). CASH leaves an active cash
-- box, TRANSFER an active bank account. CHEQUE is not enabled in R6-1.
CREATE FUNCTION settlements.check_payment_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payment record;
    account_kind text;
BEGIN
    SELECT operation_id, reversal_operation_id INTO payment FROM settlements.payments WHERE id = NEW.payment_id;
    IF TG_OP = 'UPDATE' THEN
        IF (NEW.payment_id, NEW.method, NEW.account_id, NEW.cheque_id, NEW.amount, NEW.created_at)
            IS DISTINCT FROM (OLD.payment_id, OLD.method, OLD.account_id, OLD.cheque_id, OLD.amount, OLD.created_at)
        THEN
            RAISE EXCEPTION 'payment_items: item % is published; only its reference changes', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF payment.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'payment_items: payment % is reversed and does not change', OLD.payment_id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF payment.operation_id IS NOT NULL THEN
        RAISE EXCEPTION 'payment_items: payment % is published; its items never change', NEW.payment_id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.method = 'CHEQUE' THEN
        RAISE EXCEPTION 'payment_items: payments with cheques are not enabled yet'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT kind INTO account_kind FROM settlements.accounts WHERE id = NEW.account_id AND active;
    IF account_kind IS DISTINCT FROM (CASE NEW.method WHEN 'CASH' THEN 'CASH_BOX' ELSE 'BANK' END) THEN
        RAISE EXCEPTION 'payment_items: account % is not an active account of method %', NEW.account_id,
            NEW.method USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_payment_items_guard BEFORE INSERT OR UPDATE ON settlements.payment_items
    FOR EACH ROW EXECUTE FUNCTION settlements.check_payment_item();
CREATE TRIGGER trg_payment_items_no_delete BEFORE DELETE ON settlements.payment_items
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# At commit: a payment and its PAYMENT operation are exactly each other. The payment has at least one item
# and its amount is exactly their sum. The operation has the payment as source and its business date, and
# exactly these legs: each item's account -item amount, and ONE counter-leg +amount in the technical account
# of the concept. The payment is reversed exactly when a REVERSAL of that operation exists, on the same
# business date. A PAYMENT without its payment, or a REVERSAL of one without the payment marked reversed by
# it, is refused.
MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_payment(pid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    p settlements.payments%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    counter_account bigint;
    items_count int;
    items_total numeric;
    reversal bigint;
    mismatch boolean;
BEGIN
    SELECT * INTO p FROM settlements.payments WHERE id = pid;
    IF p.operation_id IS NULL THEN
        RAISE EXCEPTION 'payment % has no operation', pid USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*), coalesce(sum(amount), 0) INTO items_count, items_total
        FROM settlements.payment_items WHERE payment_id = pid;
    IF items_count = 0 THEN
        RAISE EXCEPTION 'payment % has no items', pid USING ERRCODE = 'check_violation';
    END IF;
    IF items_total <> p.amount THEN
        RAISE EXCEPTION 'payment %: amount % is not the sum of its items %', pid, p.amount, items_total
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM settlements.financial_operations WHERE id = p.operation_id;
    IF o.type <> 'PAYMENT' OR o.source_type IS DISTINCT FROM 'payment' OR o.source_id IS DISTINCT FROM pid
        OR o.business_date <> p.business_date THEN
        RAISE EXCEPTION 'operation % is not the PAYMENT of payment %', o.id, pid USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO counter_account FROM settlements.accounts WHERE kind = CASE p.concept
        WHEN 'MERCHANDISE' THEN 'MERCHANDISE_PAYMENTS' WHEN 'EXPENSE' THEN 'EXPENSE'
        WHEN 'REFUND' THEN 'THIRD_PARTY_REFUNDS' ELSE 'OTHER_PAYMENTS' END;
    WITH expected AS (
        SELECT account_id, NULL::bigint AS cheque_id, -amount AS amount
            FROM settlements.payment_items WHERE payment_id = pid
        UNION ALL
        SELECT counter_account, NULL::bigint, p.amount
    ), actual AS (
        SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id
    )
    SELECT EXISTS ((SELECT * FROM actual EXCEPT ALL SELECT * FROM expected)
                   UNION ALL (SELECT * FROM expected EXCEPT ALL SELECT * FROM actual)) INTO mismatch;
    IF mismatch THEN
        RAISE EXCEPTION 'operation % does not match payment %', o.id, pid USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO reversal FROM settlements.financial_operations WHERE reverses_operation_id = o.id;
    IF reversal IS DISTINCT FROM p.reversal_operation_id THEN
        RAISE EXCEPTION 'payment % is reversed exactly by the reversal of its operation', pid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations
        WHERE id = reversal AND type = 'REVERSAL' AND business_date = p.business_date) THEN
        RAISE EXCEPTION 'the reversal of payment % keeps its business date', pid USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_payment_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pid bigint;
BEGIN
    IF TG_TABLE_NAME = 'payments' THEN
        pid := NEW.id;
    ELSIF TG_TABLE_NAME = 'payment_items' THEN
        pid := NEW.payment_id;
    ELSIF NEW.type = 'PAYMENT' THEN
        SELECT id INTO pid FROM settlements.payments WHERE operation_id = NEW.id;
        IF pid IS NULL THEN
            RAISE EXCEPTION 'operation % of type PAYMENT has no payment', NEW.id USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT p.id INTO pid FROM settlements.payments p
            JOIN settlements.financial_operations o ON o.id = p.operation_id
            WHERE o.id = NEW.reverses_operation_id AND o.type = 'PAYMENT';
        IF pid IS NULL THEN
            IF EXISTS (SELECT 1 FROM settlements.financial_operations
                       WHERE id = NEW.reverses_operation_id AND type = 'PAYMENT') THEN
                RAISE EXCEPTION 'reversal % of a PAYMENT without its payment', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END IF;
    END IF;
    PERFORM settlements.verify_payment(pid);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_payments_operation
    AFTER INSERT OR UPDATE ON settlements.payments DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_payment_operation();
CREATE CONSTRAINT TRIGGER trg_payment_items_operation
    AFTER INSERT ON settlements.payment_items DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_payment_operation();
CREATE CONSTRAINT TRIGGER trg_financial_operations_payment
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('PAYMENT', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_payment_operation();
"""

DROP_GUARDS = """
DROP TRIGGER trg_financial_operations_payment ON settlements.financial_operations;
DROP TRIGGER trg_payment_items_operation ON settlements.payment_items;
DROP TRIGGER trg_payments_operation ON settlements.payments;
DROP FUNCTION settlements.check_payment_operation();
DROP FUNCTION settlements.verify_payment(bigint);
DROP TRIGGER trg_payment_items_no_delete ON settlements.payment_items;
DROP TRIGGER trg_payment_items_guard ON settlements.payment_items;
DROP FUNCTION settlements.check_payment_item();
DROP TRIGGER trg_payments_no_delete ON settlements.payments;
DROP TRIGGER trg_payments_guard ON settlements.payments;
DROP FUNCTION settlements.check_payment();
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
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    _technical_index(NEW_TECHNICAL)
    op.execute(SEED_ACCOUNTS)
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    op.create_index(
        "uq_financial_operations_payment",
        "financial_operations",
        ["source_type", "source_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type = 'PAYMENT'"),
    )
    op.create_table(
        "payments",
        sa.Column("third_party_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("concept", sa.String(length=20), nullable=False),
        sa.Column("expense_category_id", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("operation_id", sa.BigInteger(), nullable=True),
        sa.Column("reversal_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("reversed_by", sa.BigInteger(), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "concept IN ('MERCHANDISE', 'EXPENSE', 'REFUND', 'OTHER')", name=op.f("ck_payments_concept")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payments_amount_positive")),
        sa.CheckConstraint(
            "(concept = 'EXPENSE') = (expense_category_id IS NOT NULL)", name=op.f("ck_payments_expense_category")
        ),
        sa.CheckConstraint(
            "concept <> 'OTHER' OR length(btrim(coalesce(description, ''))) > 0",
            name=op.f("ck_payments_other_description"),
        ),
        sa.CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_at IS NULL) AND (reversed_at IS NULL) = (reversed_by IS NULL)"
            " AND (reversed_by IS NULL) = (reversal_reason IS NULL)",
            name=op.f("ck_payments_reversal_fields"),
        ),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_payments_fingerprint_format")),
        sa.ForeignKeyConstraint(
            ["third_party_id"],
            ["third_parties.id"],
            name=op.f("fk_payments_third_party_id_third_parties"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["expense_category_id"],
            ["settlements.expense_categories.id"],
            name=op.f("fk_payments_expense_category_id_expense_categories"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_payments_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_payments_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_payments_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_payments_created_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_payments_client_request_id")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_payments_operation_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_payments_reversal_operation_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_payments_third_party_id"), "payments", ["third_party_id"], schema="settlements"
    )
    op.create_index(op.f("ix_settlements_payments_business_date"), "payments", ["business_date"], schema="settlements")
    op.create_table(
        "payment_items",
        sa.Column("payment_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=20), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=True),
        sa.Column("cheque_id", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("method IN ('CASH', 'TRANSFER', 'CHEQUE')", name=op.f("ck_payment_items_method")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_payment_items_amount_positive")),
        sa.CheckConstraint(
            "(method IN ('CASH', 'TRANSFER') AND account_id IS NOT NULL AND cheque_id IS NULL) OR "
            "(method = 'CHEQUE' AND cheque_id IS NOT NULL AND account_id IS NULL)",
            name=op.f("ck_payment_items_method_fields"),
        ),
        sa.ForeignKeyConstraint(
            ["payment_id"],
            ["settlements.payments.id"],
            name=op.f("fk_payment_items_payment_id_payments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_payment_items_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cheque_id"],
            ["settlements.cheques.id"],
            name=op.f("fk_payment_items_cheque_id_cheques"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payment_items")),
        sa.UniqueConstraint("payment_id", "account_id", name=op.f("uq_payment_items_payment_id_account_id")),
        sa.UniqueConstraint("payment_id", "cheque_id", name=op.f("uq_payment_items_payment_id_cheque_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_payment_items_payment_id"), "payment_items", ["payment_id"], schema="settlements"
    )
    op.create_index(
        op.f("ix_settlements_payment_items_account_id"), "payment_items", ["account_id"], schema="settlements"
    )
    op.create_index(
        op.f("ix_settlements_payment_items_cheque_id"), "payment_items", ["cheque_id"], schema="settlements"
    )
    op.execute(PAYMENT_GUARDS)
    op.execute(MATCHES_LEDGER)


def downgrade() -> None:
    """Downgrade schema. Refused while payments exist (their operations name the new accounts)."""
    op.execute(DROP_GUARDS)
    op.drop_index(op.f("ix_settlements_payment_items_cheque_id"), table_name="payment_items", schema="settlements")
    op.drop_index(op.f("ix_settlements_payment_items_account_id"), table_name="payment_items", schema="settlements")
    op.drop_index(op.f("ix_settlements_payment_items_payment_id"), table_name="payment_items", schema="settlements")
    op.drop_table("payment_items", schema="settlements")
    op.drop_index(op.f("ix_settlements_payments_business_date"), table_name="payments", schema="settlements")
    op.drop_index(op.f("ix_settlements_payments_third_party_id"), table_name="payments", schema="settlements")
    op.drop_table("payments", schema="settlements")
    op.drop_index(
        "uq_financial_operations_payment",
        table_name="financial_operations",
        schema="settlements",
        postgresql_where=sa.text("type = 'PAYMENT'"),
    )
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
    # Fails if any operation used them: their history would be lost.
    op.execute(
        "DELETE FROM settlements.accounts WHERE code IN ('merchandise_payments', 'third_party_refunds', 'other_payments')"
    )
    _technical_index(OLD_TECHNICAL)
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
