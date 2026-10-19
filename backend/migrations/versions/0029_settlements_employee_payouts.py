"""settlements: employee payouts

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-19 13:30:00.000000

R8-5 (ADR 010, Enzo's R8-5 decisions): paying a closed internal payroll. A payout pays the net of the payroll's close
in effect when it is registered (`payroll_closing_id`), on or after the period's end and never in the future, in one or
more items: CASH from an active cash box, TRANSFER from an active bank. It publishes

    EMPLOYEE_PAYOUT   each cash box or bank -item   internal account +total

The payouts in effect of a close never add up to more than its net (the internal account's balance is never the
limit). A payout is reversed whole, with a reason, on its own date. A payroll with payouts in effect on its close does
not reopen. Nothing is deleted; a payout changes only once, to be reversed, and its items never.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0029"
down_revision: str | Sequence[str] | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION', 'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', "
    "'CHEQUE_RETURN', 'DIRECT_EXPENSE', 'PAYMENT', 'OUTLET_REMITTANCE', 'EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', "
    "'EMPLOYEE_ADJUSTMENT', 'EMPLOYEE_LOAN', 'PAYROLL_CLOSE'"
)
NEW_TYPES = f"{OLD_TYPES}, 'EMPLOYEE_PAYOUT'"

# The internal account now also takes a payout (and its reversal). The rest of 0028's discipline is unchanged.
NEW_LEG_DISCIPLINE = """
CREATE OR REPLACE FUNCTION settlements.check_employee_leg() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    account_kind text;
    op settlements.financial_operations%ROWTYPE;
    flip integer := 1;
BEGIN
    SELECT kind INTO account_kind FROM settlements.accounts WHERE id = NEW.account_id;
    IF account_kind NOT IN ('EMPLOYEE', 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS', 'PAYROLL_EXPENSE') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO op FROM settlements.financial_operations WHERE id = NEW.operation_id;
    IF op.type = 'REVERSAL' THEN
        SELECT * INTO op FROM settlements.financial_operations WHERE id = op.reverses_operation_id;
        flip := -1;
    END IF;
    IF account_kind = 'EMPLOYEE' AND op.type NOT IN ('SETTLEMENT_CLOSE', 'PENDING_WRITE_OFF', 'EMPLOYEE_CONSUMPTION',
                                                     'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT', 'EMPLOYEE_LOAN',
                                                     'PAYROLL_CLOSE', 'EMPLOYEE_PAYOUT') THEN
        RAISE EXCEPTION 'employee_account_closed: an operation of type % does not move an internal account', op.type
            USING ERRCODE = 'check_violation';
    END IF;
    IF account_kind = 'EMPLOYEE_CONSUMPTIONS'
        AND (op.type IS DISTINCT FROM 'EMPLOYEE_CONSUMPTION' OR sign(NEW.amount) <> -flip) THEN
        RAISE EXCEPTION 'employee_consumptions_closed: only a consumption and its reversal move it'
            USING ERRCODE = 'check_violation';
    END IF;
    IF account_kind = 'EMPLOYEE_ADJUSTMENTS' AND op.type IS DISTINCT FROM 'EMPLOYEE_ADJUSTMENT' THEN
        RAISE EXCEPTION 'employee_adjustments_closed: only an adjustment and its reversal move it'
            USING ERRCODE = 'check_violation';
    END IF;
    IF account_kind = 'PAYROLL_EXPENSE' AND (op.type IS DISTINCT FROM 'PAYROLL_CLOSE' OR sign(NEW.amount) <> flip) THEN
        RAISE EXCEPTION 'payroll_expense_closed: only a payroll close and its reversal move it'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
"""
# 0028's body, restored by the downgrade.
OLD_LEG_DISCIPLINE = """
CREATE OR REPLACE FUNCTION settlements.check_employee_leg() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    account_kind text;
    op settlements.financial_operations%ROWTYPE;
    flip integer := 1;
BEGIN
    SELECT kind INTO account_kind FROM settlements.accounts WHERE id = NEW.account_id;
    IF account_kind NOT IN ('EMPLOYEE', 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS', 'PAYROLL_EXPENSE') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO op FROM settlements.financial_operations WHERE id = NEW.operation_id;
    IF op.type = 'REVERSAL' THEN
        SELECT * INTO op FROM settlements.financial_operations WHERE id = op.reverses_operation_id;
        flip := -1;
    END IF;
    IF account_kind = 'EMPLOYEE' AND op.type NOT IN ('SETTLEMENT_CLOSE', 'PENDING_WRITE_OFF', 'EMPLOYEE_CONSUMPTION',
                                                     'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT', 'EMPLOYEE_LOAN',
                                                     'PAYROLL_CLOSE') THEN
        RAISE EXCEPTION 'employee_account_closed: an operation of type % does not move an internal account', op.type
            USING ERRCODE = 'check_violation';
    END IF;
    IF account_kind = 'EMPLOYEE_CONSUMPTIONS'
        AND (op.type IS DISTINCT FROM 'EMPLOYEE_CONSUMPTION' OR sign(NEW.amount) <> -flip) THEN
        RAISE EXCEPTION 'employee_consumptions_closed: only a consumption and its reversal move it'
            USING ERRCODE = 'check_violation';
    END IF;
    IF account_kind = 'EMPLOYEE_ADJUSTMENTS' AND op.type IS DISTINCT FROM 'EMPLOYEE_ADJUSTMENT' THEN
        RAISE EXCEPTION 'employee_adjustments_closed: only an adjustment and its reversal move it'
            USING ERRCODE = 'check_violation';
    END IF;
    IF account_kind = 'PAYROLL_EXPENSE' AND (op.type IS DISTINCT FROM 'PAYROLL_CLOSE' OR sign(NEW.amount) <> flip) THEN
        RAISE EXCEPTION 'payroll_expense_closed: only a payroll close and its reversal move it'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

# Enzo's R8-5 invariants [1]-[7], [19], [20]: a payout is for the payroll's employee and internal account, for the
# close in effect of a CLOSED payroll (taken FOR SHARE, so a reopening waits for it), dated from the period's end up
# to today (business timezone), born before its operation. Afterwards it changes once to point at its operation and
# once to be reversed (who, when, why); nothing else, and a reversed payout never again.
PAYOUT_GUARDS = """
CREATE FUNCTION settlements.check_employee_payout() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p settlements.employee_payrolls%ROWTYPE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT * INTO p FROM settlements.employee_payrolls WHERE id = NEW.payroll_id FOR SHARE;
        IF p.employee_id IS DISTINCT FROM NEW.employee_id OR p.employee_account_id IS DISTINCT FROM NEW.employee_account_id
        THEN
            RAISE EXCEPTION 'employee_payouts: payroll % is not of employee % and its internal account',
                NEW.payroll_id, NEW.employee_id USING ERRCODE = 'check_violation';
        END IF;
        IF p.status <> 'CLOSED' OR p.current_closing_id IS DISTINCT FROM NEW.payroll_closing_id THEN
            RAISE EXCEPTION 'employee_payouts: a payout pays the close in effect of a closed payroll'
                USING ERRCODE = 'check_violation';
        END IF;
        -- Serializes the payouts of one close: the deferred check of the total sees the one committed before.
        PERFORM 1 FROM settlements.employee_payroll_closings WHERE id = NEW.payroll_closing_id FOR NO KEY UPDATE;
        IF NEW.business_date < p.period_end THEN
            RAISE EXCEPTION 'employee_payouts: business date % is before the period end %', NEW.business_date,
                p.period_end USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.business_date > (clock_timestamp() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date THEN
            RAISE EXCEPTION 'employee_payouts: business date % is in the future', NEW.business_date
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.operation_id IS NOT NULL OR NEW.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'employee_payouts: a payout is born in effect, before its operation'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.reversal_operation_id IS NOT NULL THEN
        RAISE EXCEPTION 'employee_payouts: payout % is reversed and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.payroll_id, NEW.payroll_closing_id, NEW.employee_id, NEW.employee_account_id, NEW.business_date,
        NEW.amount, NEW.reference, NEW.client_request_id, NEW.request_fingerprint, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM
       (OLD.payroll_id, OLD.payroll_closing_id, OLD.employee_id, OLD.employee_account_id, OLD.business_date,
        OLD.amount, OLD.reference, OLD.client_request_id, OLD.request_fingerprint, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'employee_payouts: payout % is published and never changes; reverse it', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN
        RAISE EXCEPTION 'employee_payouts: the operation of payout % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_payouts_check BEFORE INSERT OR UPDATE ON settlements.employee_payouts
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payout();
CREATE TRIGGER trg_employee_payouts_no_delete BEFORE DELETE ON settlements.employee_payouts
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# Invariants [8]-[10]: an item is written with its payout, before its operation, from an active cash box (CASH) or an
# active bank (TRANSFER), taken FOR SHARE; it never changes nor is deleted.
ITEM_GUARDS = """
CREATE FUNCTION settlements.check_employee_payout_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM settlements.employee_payouts WHERE id = NEW.payout_id AND operation_id IS NULL) THEN
        RAISE EXCEPTION 'employee_payout_items: payout % is already published', NEW.payout_id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.account_id AND active
                   AND kind = CASE NEW.method WHEN 'CASH' THEN 'CASH_BOX' WHEN 'TRANSFER' THEN 'BANK' END FOR SHARE)
    THEN
        RAISE EXCEPTION 'employee_payout_items: account % is not an active % account', NEW.account_id, NEW.method
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_payout_items_check BEFORE INSERT ON settlements.employee_payout_items
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payout_item();
CREATE TRIGGER trg_employee_payout_items_no_change BEFORE UPDATE OR DELETE ON settlements.employee_payout_items
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_change();
"""

# Invariant [21]: a payroll with payouts in effect on its close does not reopen (Enzo's R8-5 point 8). 0028's own
# payroll guard is left as it is.
REOPEN_GUARD = """
CREATE FUNCTION settlements.check_payroll_not_paid() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'CLOSED' AND NEW.status <> 'CLOSED' AND EXISTS (
        SELECT 1 FROM settlements.employee_payouts
        WHERE payroll_closing_id = OLD.current_closing_id AND reversal_operation_id IS NULL) THEN
        RAISE EXCEPTION 'payroll_has_payouts: payroll % has payouts in effect', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_payrolls_not_paid BEFORE UPDATE ON settlements.employee_payrolls
    FOR EACH ROW EXECUTE FUNCTION settlements.check_payroll_not_paid();
"""

# Checked at commit, whatever wrote it (invariants [3], [8], [11]-[17]):
# - the payout has items adding up exactly to its amount;
# - its operation is EMPLOYEE_PAYOUT, for this payout, on its date, with exactly one leg per item (-item) and the
#   internal account +amount;
# - it is reversed exactly by the reversal of its operation, on the same date;
# - while in effect, its close is still in effect, and the payouts in effect of that close add up to <= its net.
PAYOUT_MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_employee_payout(pid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    x settlements.employee_payouts%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    c settlements.employee_payroll_closings%ROWTYPE;
    reversal bigint;
    mismatch boolean;
BEGIN
    SELECT * INTO x FROM settlements.employee_payouts WHERE id = pid;
    IF x.operation_id IS NULL THEN
        RAISE EXCEPTION 'employee payout % has no operation', pid USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM settlements.employee_payout_items WHERE payout_id = pid)
       OR x.amount <> (SELECT sum(amount) FROM settlements.employee_payout_items WHERE payout_id = pid) THEN
        RAISE EXCEPTION 'the items of employee payout % do not add up to it', pid USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM settlements.financial_operations WHERE id = x.operation_id;
    IF o.type <> 'EMPLOYEE_PAYOUT' OR o.source_type IS DISTINCT FROM 'employee_payout'
        OR o.source_id IS DISTINCT FROM pid OR o.business_date <> x.business_date THEN
        RAISE EXCEPTION 'operation % is not the operation of employee payout %', o.id, pid
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT EXISTS (
        (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id
         EXCEPT ALL
         (SELECT account_id, NULL::bigint, -amount FROM settlements.employee_payout_items WHERE payout_id = pid
          UNION ALL SELECT x.employee_account_id, NULL::bigint, x.amount))
        UNION ALL
        ((SELECT account_id, NULL::bigint, -amount FROM settlements.employee_payout_items WHERE payout_id = pid
          UNION ALL SELECT x.employee_account_id, NULL::bigint, x.amount)
         EXCEPT ALL
         SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id)
    ) INTO mismatch;
    IF mismatch THEN
        RAISE EXCEPTION 'operation % does not match employee payout %', o.id, pid USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO reversal FROM settlements.financial_operations WHERE reverses_operation_id = o.id;
    IF reversal IS DISTINCT FROM x.reversal_operation_id THEN
        RAISE EXCEPTION 'employee payout % is reversed exactly by the reversal of its operation', pid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations
        WHERE id = reversal AND type = 'REVERSAL' AND business_date = x.business_date) THEN
        RAISE EXCEPTION 'the reversal of employee payout % keeps its business date', pid
            USING ERRCODE = 'check_violation';
    END IF;
    IF x.reversal_operation_id IS NULL THEN
        SELECT * INTO c FROM settlements.employee_payroll_closings WHERE id = x.payroll_closing_id;
        IF c.payroll_id IS DISTINCT FROM x.payroll_id OR c.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'employee payout % is in effect for a close no longer in effect', pid
                USING ERRCODE = 'check_violation';
        END IF;
        IF (SELECT sum(amount) FROM settlements.employee_payouts
            WHERE payroll_closing_id = c.id AND reversal_operation_id IS NULL) > c.net THEN
            RAISE EXCEPTION 'payroll_overpaid: the payouts in effect of close % exceed its net', c.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
END
$$;

CREATE FUNCTION settlements.check_employee_payout_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pid bigint;
BEGIN
    IF TG_TABLE_NAME = 'employee_payouts' THEN
        pid := NEW.id;
    ELSIF TG_TABLE_NAME = 'employee_payout_items' THEN
        pid := NEW.payout_id;
    ELSIF NEW.type = 'EMPLOYEE_PAYOUT' THEN
        SELECT id INTO pid FROM settlements.employee_payouts WHERE operation_id = NEW.id;
        IF pid IS NULL THEN
            RAISE EXCEPTION 'operation % of type EMPLOYEE_PAYOUT has no employee payout', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT id INTO pid FROM settlements.employee_payouts WHERE operation_id = NEW.reverses_operation_id;
        IF pid IS NULL THEN
            IF EXISTS (SELECT 1 FROM settlements.financial_operations
                       WHERE id = NEW.reverses_operation_id AND type = 'EMPLOYEE_PAYOUT') THEN
                RAISE EXCEPTION 'reversal % of an employee payout without its payout', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END IF;
    END IF;
    PERFORM settlements.verify_employee_payout(pid);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_employee_payouts_operation
    AFTER INSERT OR UPDATE ON settlements.employee_payouts DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payout_operation();
CREATE CONSTRAINT TRIGGER trg_employee_payout_items_operation
    AFTER INSERT ON settlements.employee_payout_items DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payout_operation();
CREATE CONSTRAINT TRIGGER trg_financial_operations_employee_payout
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('EMPLOYEE_PAYOUT', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_employee_payout_operation();
"""

# Downgrade only while nothing of R8-5 exists.
DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.employee_payouts)
        OR EXISTS (SELECT 1 FROM settlements.employee_payout_items)
        OR EXISTS (SELECT 1 FROM settlements.financial_operations WHERE type = 'EMPLOYEE_PAYOUT')
    THEN
        RAISE EXCEPTION 'employee payouts exist: downgrade of 0029 refused' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_financial_operations_employee_payout ON settlements.financial_operations;
DROP TRIGGER trg_employee_payout_items_operation ON settlements.employee_payout_items;
DROP TRIGGER trg_employee_payouts_operation ON settlements.employee_payouts;
DROP FUNCTION settlements.check_employee_payout_operation();
DROP FUNCTION settlements.verify_employee_payout(bigint);
DROP TRIGGER trg_employee_payrolls_not_paid ON settlements.employee_payrolls;
DROP FUNCTION settlements.check_payroll_not_paid();
DROP TRIGGER trg_employee_payout_items_no_change ON settlements.employee_payout_items;
DROP TRIGGER trg_employee_payout_items_check ON settlements.employee_payout_items;
DROP FUNCTION settlements.check_employee_payout_item();
DROP TRIGGER trg_employee_payouts_no_delete ON settlements.employee_payouts;
DROP TRIGGER trg_employee_payouts_check ON settlements.employee_payouts;
DROP FUNCTION settlements.check_employee_payout();
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def upgrade() -> None:
    """Upgrade schema."""
    # One operation per payout; its reversal points at it instead.
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    op.create_index(
        "uq_financial_operations_employee_payout",
        "financial_operations",
        ["source_type", "source_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type = 'EMPLOYEE_PAYOUT'"),
    )
    op.create_table(
        "employee_payouts",
        sa.Column("payroll_id", sa.BigInteger(), nullable=False),
        sa.Column("payroll_closing_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_account_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("operation_id", sa.BigInteger(), nullable=True),
        sa.Column("reversal_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("reversed_by", sa.BigInteger(), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversal_reason", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_employee_payouts_fingerprint_format")
        ),
        sa.CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_by IS NULL) AND (reversed_by IS NULL) = (reversed_at IS NULL) AND (reversed_at IS NULL) = (reversal_reason IS NULL)",
            name=op.f("ck_employee_payouts_reversal_fields"),
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_employee_payouts_amount_positive")),
        sa.CheckConstraint(
            "reference IS NULL OR length(btrim(reference)) > 0", name=op.f("ck_employee_payouts_reference_not_blank")
        ),
        sa.CheckConstraint(
            "reversal_reason IS NULL OR length(btrim(reversal_reason)) > 0",
            name=op.f("ck_employee_payouts_reversal_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_employee_payouts_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["employee_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_payouts_employee_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_employee_payouts_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_payouts_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payroll_closing_id"],
            ["settlements.employee_payroll_closings.id"],
            name=op.f("fk_employee_payouts_payroll_closing_id_employee_payroll_closings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payroll_id"],
            ["settlements.employee_payrolls.id"],
            name=op.f("fk_employee_payouts_payroll_id_employee_payrolls"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_payouts_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_employee_payouts_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payouts")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_employee_payouts_client_request_id")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_employee_payouts_operation_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_employee_payouts_reversal_operation_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payouts_employee_id"),
        "employee_payouts",
        ["employee_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payouts_payroll_closing_id"),
        "employee_payouts",
        ["payroll_closing_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payouts_payroll_id"),
        "employee_payouts",
        ["payroll_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "employee_payout_items",
        sa.Column("payout_id", sa.BigInteger(), nullable=False),
        sa.Column("method", sa.String(length=10), nullable=False),
        sa.Column("account_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("method IN ('CASH', 'TRANSFER')", name=op.f("ck_employee_payout_items_method")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_employee_payout_items_amount_positive")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_payout_items_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payout_id"],
            ["settlements.employee_payouts.id"],
            name=op.f("fk_employee_payout_items_payout_id_employee_payouts"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payout_items")),
        sa.UniqueConstraint("payout_id", "account_id", name=op.f("uq_employee_payout_items_payout_id_account_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payout_items_account_id"),
        "employee_payout_items",
        ["account_id"],
        unique=False,
        schema="settlements",
    )
    op.execute(NEW_LEG_DISCIPLINE)
    op.execute(PAYOUT_GUARDS)
    op.execute(ITEM_GUARDS)
    op.execute(REOPEN_GUARD)
    op.execute(PAYOUT_MATCHES_LEDGER)


def downgrade() -> None:
    """Downgrade schema. Refused while any payout, item or EMPLOYEE_PAYOUT operation exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute(DROP_TRIGGERS)
    op.execute(OLD_LEG_DISCIPLINE)
    op.drop_table("employee_payout_items", schema="settlements")
    op.drop_table("employee_payouts", schema="settlements")
    op.drop_index("uq_financial_operations_employee_payout", table_name="financial_operations", schema="settlements")
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
