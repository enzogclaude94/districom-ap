"""settlements: employee loans

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-17 16:30:00.000000

R8-3 (ADR 010, Enzo's R8-3 decisions): interest-free loans to employees, their installment schedule and its
deferrals. A loan is the document of its own operation:

    EMPLOYEE_LOAN      the cash box or the bank that handed it over -P   EMPLOYEE +P

Its installments are a schedule, never money: no installment has a ledger leg. An installment is SCHEDULED, DEFERRED
(history: replaced by one or more new installments adding up to exactly its amount) or CANCELLED (still scheduled
when the whole loan was reversed). While a loan is in effect its SCHEDULED installments add up to its principal.
Nothing is deleted; a deferred or cancelled installment never changes; numbers are never reused.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0027"
down_revision: str | Sequence[str] | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION', 'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', "
    "'CHEQUE_RETURN', 'DIRECT_EXPENSE', 'PAYMENT', 'OUTLET_REMITTANCE', 'EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', "
    "'EMPLOYEE_ADJUSTMENT'"
)
NEW_TYPES = f"{OLD_TYPES}, 'EMPLOYEE_LOAN'"

# The internal account now also takes a loan (and its reversal); the rest of 0026's discipline is unchanged.
NEW_LEG_DISCIPLINE = """
CREATE OR REPLACE FUNCTION settlements.check_employee_leg() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    account_kind text;
    op settlements.financial_operations%ROWTYPE;
    flip integer := 1;
BEGIN
    SELECT kind INTO account_kind FROM settlements.accounts WHERE id = NEW.account_id;
    IF account_kind NOT IN ('EMPLOYEE', 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO op FROM settlements.financial_operations WHERE id = NEW.operation_id;
    IF op.type = 'REVERSAL' THEN
        SELECT * INTO op FROM settlements.financial_operations WHERE id = op.reverses_operation_id;
        flip := -1;
    END IF;
    IF account_kind = 'EMPLOYEE' AND op.type NOT IN ('SETTLEMENT_CLOSE', 'PENDING_WRITE_OFF', 'EMPLOYEE_CONSUMPTION',
                                                     'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT', 'EMPLOYEE_LOAN') THEN
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
    RETURN NEW;
END
$$;
"""
# 0026's body, restored by the downgrade.
OLD_LEG_DISCIPLINE = """
CREATE OR REPLACE FUNCTION settlements.check_employee_leg() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    account_kind text;
    op settlements.financial_operations%ROWTYPE;
    flip integer := 1;
BEGIN
    SELECT kind INTO account_kind FROM settlements.accounts WHERE id = NEW.account_id;
    IF account_kind NOT IN ('EMPLOYEE', 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS') THEN
        RETURN NEW;
    END IF;
    SELECT * INTO op FROM settlements.financial_operations WHERE id = NEW.operation_id;
    IF op.type = 'REVERSAL' THEN
        SELECT * INTO op FROM settlements.financial_operations WHERE id = op.reverses_operation_id;
        flip := -1;
    END IF;
    IF account_kind = 'EMPLOYEE' AND op.type NOT IN ('SETTLEMENT_CLOSE', 'PENDING_WRITE_OFF', 'EMPLOYEE_CONSUMPTION',
                                                     'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT') THEN
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
    RETURN NEW;
END
$$;
"""

# A loan is born for the employee's own internal account, on a business date that is not in the future (in Buenos
# Aires), handed over by an active cash box or bank (FOR SHARE, so a deactivation serializes with it), in effect and
# before its operation. Afterwards only two things happen to it, once each: its operation is recorded (in the
# creating transaction) and it is reversed (who, when, the reason). A reversed loan never changes; none is deleted.
LOAN_GUARDS = """
CREATE FUNCTION settlements.check_employee_loan() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.employee_account_id
                       AND kind = 'EMPLOYEE' AND employee_id = NEW.employee_id) THEN
            RAISE EXCEPTION 'employee_loans: account % is not the internal account of employee %',
                NEW.employee_account_id, NEW.employee_id USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.business_date > (clock_timestamp() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date THEN
            RAISE EXCEPTION 'employee_loans: business date % is in the future', NEW.business_date
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.funding_account_id
                       AND kind IN ('CASH_BOX', 'BANK') AND active FOR SHARE) THEN
            RAISE EXCEPTION 'employee_loans: account % is not an active cash box or bank', NEW.funding_account_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.operation_id IS NOT NULL OR NEW.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'employee_loans: a loan is born in effect, before its operation'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.reversal_operation_id IS NOT NULL THEN
        RAISE EXCEPTION 'employee_loans: loan % is reversed and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.employee_id, NEW.employee_account_id, NEW.business_date, NEW.principal, NEW.funding_account_id,
        NEW.installment_count, NEW.first_due_on, NEW.description, NEW.reference, NEW.client_request_id,
        NEW.request_fingerprint, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM
       (OLD.employee_id, OLD.employee_account_id, OLD.business_date, OLD.principal, OLD.funding_account_id,
        OLD.installment_count, OLD.first_due_on, OLD.description, OLD.reference, OLD.client_request_id,
        OLD.request_fingerprint, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'employee_loans: loan % is published and never changes; reverse it', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN
        RAISE EXCEPTION 'employee_loans: the operation of loan % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_loans_check BEFORE INSERT OR UPDATE ON settlements.employee_loans
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_loan();
CREATE TRIGGER trg_employee_loans_no_delete BEFORE DELETE ON settlements.employee_loans
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# An installment is born SCHEDULED, for a loan in effect, due on or after the loan's business date, with the next
# number of its loan (never reused). The initial schedule is written with the loan, before its operation; after
# that an installment is only born as the replacement of a DEFERRED installment of the same loan. Afterwards a
# SCHEDULED installment changes once: DEFERRED (the loan in effect; who, when, why) or CANCELLED (the loan reversed).
# Number, date, amount and origin never change; a DEFERRED or CANCELLED installment never changes; none is deleted.
INSTALLMENT_GUARDS = """
CREATE FUNCTION settlements.check_employee_loan_installment() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    loan settlements.employee_loans%ROWTYPE;
    parent settlements.employee_loan_installments%ROWTYPE;
BEGIN
    SELECT * INTO loan FROM settlements.employee_loans WHERE id = NEW.loan_id;
    IF TG_OP = 'INSERT' THEN
        IF loan.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'employee_loan_installments: loan % is reversed', NEW.loan_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status <> 'SCHEDULED' THEN
            RAISE EXCEPTION 'employee_loan_installments: an installment is born scheduled'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.due_on < loan.business_date THEN
            RAISE EXCEPTION 'employee_loan_installments: due on % before the loan (%)', NEW.due_on, loan.business_date
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.number <> 1 + coalesce((SELECT max(number) FROM settlements.employee_loan_installments
                                       WHERE loan_id = NEW.loan_id), 0) THEN
            RAISE EXCEPTION 'employee_loan_installments: number % is not the next one of loan %', NEW.number,
                NEW.loan_id USING ERRCODE = 'check_violation';
        END IF;
        IF (NEW.deferred_from_id IS NULL) <> (loan.operation_id IS NULL) THEN
            RAISE EXCEPTION 'employee_loan_installments: the initial schedule is written with the loan; later '
                'installments only replace a deferred one' USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.deferred_from_id IS NOT NULL THEN
            SELECT * INTO parent FROM settlements.employee_loan_installments WHERE id = NEW.deferred_from_id;
            IF parent.loan_id IS DISTINCT FROM NEW.loan_id OR parent.status IS DISTINCT FROM 'DEFERRED' THEN
                RAISE EXCEPTION 'employee_loan_installments: % replaces a deferred installment of the same loan',
                    NEW.number USING ERRCODE = 'check_violation';
            END IF;
        END IF;
        RETURN NEW;
    END IF;
    IF NEW IS NOT DISTINCT FROM OLD THEN
        RETURN NEW;
    END IF;
    IF OLD.status <> 'SCHEDULED' THEN
        RAISE EXCEPTION 'employee_loan_installments: installment % is % and never changes', OLD.id, OLD.status
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.loan_id, NEW.number, NEW.due_on, NEW.amount, NEW.deferred_from_id, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM
       (OLD.loan_id, OLD.number, OLD.due_on, OLD.amount, OLD.deferred_from_id, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'employee_loan_installments: the number, date and amount of installment % never change; '
            'defer it', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.status = 'DEFERRED' AND loan.reversal_operation_id IS NULL AND loan.operation_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF NEW.status = 'CANCELLED' AND loan.reversal_operation_id IS NOT NULL THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'employee_loan_installments: installment % cannot become %', OLD.id, NEW.status
        USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_employee_loan_installments_check BEFORE INSERT OR UPDATE ON settlements.employee_loan_installments
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_loan_installment();
CREATE TRIGGER trg_employee_loan_installments_no_delete BEFORE DELETE ON settlements.employee_loan_installments
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# At commit: a loan, its operation and its schedule are exactly each other.
# - The operation has type EMPLOYEE_LOAN, the loan as source, its business date and exactly two legs: the funding
#   account -principal and the internal account +principal (no cheque).
# - The loan is reversed exactly when a REVERSAL of that operation exists, on the same business date.
# - The initial schedule (no origin) has `installment_count` installments numbered 1..n, dated in order, the first
#   on `first_due_on`, adding up to the principal.
# - Every DEFERRED installment is replaced by installments adding up to exactly its amount.
# - In effect: no CANCELLED installment, and the SCHEDULED ones add up to the principal. Reversed: none SCHEDULED.
# An operation of type EMPLOYEE_LOAN without its loan, or a reversal of one without the loan marked reversed by it,
# is refused.
LOAN_MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_employee_loan(lid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    l settlements.employee_loans%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    reversal bigint;
    mismatch boolean;
BEGIN
    SELECT * INTO l FROM settlements.employee_loans WHERE id = lid;
    IF l.operation_id IS NULL THEN
        RAISE EXCEPTION 'employee loan % has no operation', lid USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM settlements.financial_operations WHERE id = l.operation_id;
    IF o.type <> 'EMPLOYEE_LOAN' OR o.source_type IS DISTINCT FROM 'employee_loan'
        OR o.source_id IS DISTINCT FROM lid OR o.business_date <> l.business_date THEN
        RAISE EXCEPTION 'operation % is not the operation of employee loan %', o.id, lid
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT EXISTS (
        (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id
         EXCEPT ALL
         SELECT * FROM (VALUES (l.employee_account_id, NULL::bigint, l.principal),
                               (l.funding_account_id, NULL::bigint, -l.principal)) x(account_id, cheque_id, amount))
        UNION ALL
        (SELECT * FROM (VALUES (l.employee_account_id, NULL::bigint, l.principal),
                               (l.funding_account_id, NULL::bigint, -l.principal)) x(account_id, cheque_id, amount)
         EXCEPT ALL
         SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id)
    ) INTO mismatch;
    IF mismatch THEN
        RAISE EXCEPTION 'operation % does not match employee loan %', o.id, lid USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO reversal FROM settlements.financial_operations WHERE reverses_operation_id = o.id;
    IF reversal IS DISTINCT FROM l.reversal_operation_id THEN
        RAISE EXCEPTION 'employee loan % is reversed exactly by the reversal of its operation', lid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations
        WHERE id = reversal AND type = 'REVERSAL' AND business_date = l.business_date) THEN
        RAISE EXCEPTION 'the reversal of employee loan % keeps its business date', lid
            USING ERRCODE = 'check_violation';
    END IF;

    IF (SELECT count(*) <> l.installment_count OR max(number) <> l.installment_count
               OR sum(amount) <> l.principal
               OR min(due_on) FILTER (WHERE number = 1) IS DISTINCT FROM l.first_due_on
        FROM settlements.employee_loan_installments WHERE loan_id = lid AND deferred_from_id IS NULL)
        OR EXISTS (SELECT 1 FROM settlements.employee_loan_installments a
                   JOIN settlements.employee_loan_installments b
                     ON b.loan_id = a.loan_id AND b.number = a.number + 1 AND b.deferred_from_id IS NULL
                   WHERE a.loan_id = lid AND a.deferred_from_id IS NULL AND b.due_on < a.due_on)
    THEN
        RAISE EXCEPTION 'the initial schedule of employee loan % does not add up to it', lid
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM settlements.employee_loan_installments d
        WHERE d.loan_id = lid AND d.status = 'DEFERRED'
            AND d.amount IS DISTINCT FROM (SELECT sum(r.amount) FROM settlements.employee_loan_installments r
                                           WHERE r.deferred_from_id = d.id))
    THEN
        RAISE EXCEPTION 'a deferred installment of employee loan % is not replaced by exactly its amount', lid
            USING ERRCODE = 'check_violation';
    END IF;
    IF l.reversal_operation_id IS NULL AND (
        EXISTS (SELECT 1 FROM settlements.employee_loan_installments WHERE loan_id = lid AND status = 'CANCELLED')
        OR (SELECT coalesce(sum(amount), 0) FROM settlements.employee_loan_installments
            WHERE loan_id = lid AND status = 'SCHEDULED') <> l.principal)
    THEN
        RAISE EXCEPTION 'the scheduled installments of employee loan % do not add up to its principal', lid
            USING ERRCODE = 'check_violation';
    END IF;
    IF l.reversal_operation_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM settlements.employee_loan_installments WHERE loan_id = lid AND status = 'SCHEDULED')
    THEN
        RAISE EXCEPTION 'employee loan % is reversed with installments still scheduled', lid
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_employee_loan_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    lid bigint;
BEGIN
    IF TG_TABLE_NAME = 'employee_loans' THEN
        lid := NEW.id;
    ELSIF TG_TABLE_NAME = 'employee_loan_installments' THEN
        lid := NEW.loan_id;
    ELSIF NEW.type = 'EMPLOYEE_LOAN' THEN
        SELECT id INTO lid FROM settlements.employee_loans WHERE operation_id = NEW.id;
        IF lid IS NULL THEN
            RAISE EXCEPTION 'operation % of type EMPLOYEE_LOAN has no employee loan', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT id INTO lid FROM settlements.employee_loans WHERE operation_id = NEW.reverses_operation_id;
        IF lid IS NULL THEN
            IF EXISTS (SELECT 1 FROM settlements.financial_operations
                       WHERE id = NEW.reverses_operation_id AND type = 'EMPLOYEE_LOAN') THEN
                RAISE EXCEPTION 'reversal % of an employee loan without its loan', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END IF;
    END IF;
    PERFORM settlements.verify_employee_loan(lid);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_employee_loans_operation
    AFTER INSERT OR UPDATE ON settlements.employee_loans DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_loan_operation();
CREATE CONSTRAINT TRIGGER trg_employee_loan_installments_schedule
    AFTER INSERT OR UPDATE ON settlements.employee_loan_installments DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_loan_operation();
CREATE CONSTRAINT TRIGGER trg_financial_operations_employee_loan
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('EMPLOYEE_LOAN', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_employee_loan_operation();
"""

# Downgrade only while nothing of R8-3 exists.
DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.employee_loans)
        OR EXISTS (SELECT 1 FROM settlements.employee_loan_installments)
        OR EXISTS (SELECT 1 FROM settlements.financial_operations WHERE type = 'EMPLOYEE_LOAN')
    THEN
        RAISE EXCEPTION 'employee loans exist: downgrade of 0027 refused' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_financial_operations_employee_loan ON settlements.financial_operations;
DROP TRIGGER trg_employee_loan_installments_schedule ON settlements.employee_loan_installments;
DROP TRIGGER trg_employee_loans_operation ON settlements.employee_loans;
DROP FUNCTION settlements.check_employee_loan_operation();
DROP FUNCTION settlements.verify_employee_loan(bigint);
DROP TRIGGER trg_employee_loan_installments_no_delete ON settlements.employee_loan_installments;
DROP TRIGGER trg_employee_loan_installments_check ON settlements.employee_loan_installments;
DROP FUNCTION settlements.check_employee_loan_installment();
DROP TRIGGER trg_employee_loans_no_delete ON settlements.employee_loans;
DROP TRIGGER trg_employee_loans_check ON settlements.employee_loans;
DROP FUNCTION settlements.check_employee_loan();
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def upgrade() -> None:
    """Upgrade schema."""
    # One operation per loan; its reversal points at it instead.
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    op.create_index(
        "uq_financial_operations_employee_loan",
        "financial_operations",
        ["source_type", "source_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type = 'EMPLOYEE_LOAN'"),
    )
    op.create_table(
        "employee_loans",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_account_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("principal", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("funding_account_id", sa.BigInteger(), nullable=False),
        sa.Column("installment_count", sa.Integer(), nullable=False),
        sa.Column("first_due_on", sa.Date(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
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
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_employee_loans_fingerprint_format")),
        sa.CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_by IS NULL) AND (reversed_by IS NULL) = (reversed_at IS NULL) AND (reversed_at IS NULL) = (reversal_reason IS NULL)",
            name=op.f("ck_employee_loans_reversal_fields"),
        ),
        sa.CheckConstraint(
            "description IS NULL OR (length(btrim(description)) > 0 AND length(description) <= 500)",
            name=op.f("ck_employee_loans_description"),
        ),
        sa.CheckConstraint("first_due_on >= business_date", name=op.f("ck_employee_loans_first_due_on")),
        sa.CheckConstraint("installment_count BETWEEN 1 AND 120", name=op.f("ck_employee_loans_installment_count")),
        sa.CheckConstraint("principal > 0", name=op.f("ck_employee_loans_principal_positive")),
        sa.CheckConstraint(
            "reference IS NULL OR length(btrim(reference)) > 0", name=op.f("ck_employee_loans_reference_not_blank")
        ),
        sa.CheckConstraint(
            "reversal_reason IS NULL OR length(btrim(reversal_reason)) > 0",
            name=op.f("ck_employee_loans_reversal_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_employee_loans_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["employee_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_loans_employee_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"], ["employees.id"], name=op.f("fk_employee_loans_employee_id_employees"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["funding_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_loans_funding_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_loans_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_loans_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_employee_loans_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_loans")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_employee_loans_client_request_id")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_employee_loans_operation_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_employee_loans_reversal_operation_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_loans_employee_id"),
        "employee_loans",
        ["employee_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_loans_funding_account_id"),
        "employee_loans",
        ["funding_account_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "employee_loan_installments",
        sa.Column("loan_id", sa.BigInteger(), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("deferred_from_id", sa.BigInteger(), nullable=True),
        sa.Column("deferred_reason", sa.Text(), nullable=True),
        sa.Column("deferred_by", sa.BigInteger(), nullable=True),
        sa.Column("deferred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deferral_request_id", sa.Uuid(), nullable=True),
        sa.Column("deferral_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(status = 'DEFERRED') = (deferred_by IS NOT NULL) AND (deferred_by IS NULL) = (deferred_at IS NULL) AND (deferred_at IS NULL) = (deferred_reason IS NULL) AND (deferred_reason IS NULL) = (deferral_request_id IS NULL) AND (deferral_request_id IS NULL) = (deferral_fingerprint IS NULL)",
            name=op.f("ck_employee_loan_installments_deferral_fields"),
        ),
        sa.CheckConstraint(
            "deferral_fingerprint IS NULL OR deferral_fingerprint ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_employee_loan_installments_deferral_fingerprint_format"),
        ),
        sa.CheckConstraint(
            "status IN ('SCHEDULED', 'DEFERRED', 'CANCELLED')", name=op.f("ck_employee_loan_installments_status")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_employee_loan_installments_amount_positive")),
        sa.CheckConstraint(
            "deferred_from_id IS NULL OR deferred_from_id < id",
            name=op.f("ck_employee_loan_installments_deferred_from_earlier"),
        ),
        sa.CheckConstraint(
            "deferred_reason IS NULL OR length(btrim(deferred_reason)) > 0",
            name=op.f("ck_employee_loan_installments_deferred_reason_not_blank"),
        ),
        sa.CheckConstraint("number > 0", name=op.f("ck_employee_loan_installments_number_positive")),
        sa.ForeignKeyConstraint(
            ["created_by"],
            ["users.id"],
            name=op.f("fk_employee_loan_installments_created_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deferred_by"],
            ["users.id"],
            name=op.f("fk_employee_loan_installments_deferred_by_users"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deferred_from_id"],
            ["settlements.employee_loan_installments.id"],
            name=op.f("fk_employee_loan_installments_deferred_from_id_employee_loan_installments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["loan_id"],
            ["settlements.employee_loans.id"],
            name=op.f("fk_employee_loan_installments_loan_id_employee_loans"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_loan_installments")),
        sa.UniqueConstraint("loan_id", "number", name=op.f("uq_employee_loan_installments_loan_id_number")),
        sa.UniqueConstraint("deferral_request_id", name=op.f("uq_employee_loan_installments_deferral_request_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_loan_installments_deferred_from_id"),
        "employee_loan_installments",
        ["deferred_from_id"],
        unique=False,
        schema="settlements",
    )

    op.execute(NEW_LEG_DISCIPLINE)
    op.execute(LOAN_GUARDS)
    op.execute(INSTALLMENT_GUARDS)
    op.execute(LOAN_MATCHES_LEDGER)


def downgrade() -> None:
    """Downgrade schema. Refused while any loan, installment or EMPLOYEE_LOAN operation exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute(DROP_TRIGGERS)
    op.execute(OLD_LEG_DISCIPLINE)
    op.drop_table("employee_loan_installments", schema="settlements")
    op.drop_table("employee_loans", schema="settlements")
    op.drop_index("uq_financial_operations_employee_loan", table_name="financial_operations", schema="settlements")
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
