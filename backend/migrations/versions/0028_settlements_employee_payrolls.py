"""settlements: employee payrolls

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-18 15:30:00.000000

R8-4 (ADR 010 §4-§8, Enzo's R8-4 decisions): the internal payroll of an employee and what it applies of the internal
account. A payroll (REGULAR or EXTRAORDINARY, for an inclusive period) has a draft while OPEN: credits (haberes:
SALARY, BONUS, INCENTIVE, OTHER_CREDIT) and the preparer's choices on the legs and installments it proposes. Closing it
freezes one version: its credit lines, its applications (each pointing at one leg of the internal account, with the
sign of the leg, whole for a loan installment) and its snapshot, and publishes

    PAYROLL_CLOSE      «Sueldos y bonos internos» (PAYROLL_EXPENSE) +H   EMPLOYEE -H

when the credits H are > 0 (FINANCIAL), or nothing at all (NO_ACCRUAL). Applications move no money: those legs already
exist. Net = credits - deductions + credits applied >= 0. A reopening keeps the close in effect until the new close,
which supersedes it, reverses its operation if it had one and publishes its own. Nothing is deleted; a close, its
lines and its applications never change (a later close supersedes them once).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0028"
down_revision: str | Sequence[str] | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE', 'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', "
    "'CHEQUES_IN_TRANSIT', 'MERCHANDISE_PAYMENTS', 'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS', 'OUTLET_COLLECTIONS', "
    "'OUTLET_ADVANCES', 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS'"
)
NEW_KINDS = OLD_KINDS + ", 'PAYROLL_EXPENSE'"
OLD_TECHNICAL = (
    "'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE', "
    "'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', 'CHEQUES_IN_TRANSIT', 'MERCHANDISE_PAYMENTS', "
    "'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS', 'OUTLET_COLLECTIONS', 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS'"
)
NEW_TECHNICAL = OLD_TECHNICAL + ", 'PAYROLL_EXPENSE'"
OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION', 'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', "
    "'CHEQUE_RETURN', 'DIRECT_EXPENSE', 'PAYMENT', 'OUTLET_REMITTANCE', 'EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', "
    "'EMPLOYEE_ADJUSTMENT', 'EMPLOYEE_LOAN'"
)
NEW_TYPES = f"{OLD_TYPES}, 'PAYROLL_CLOSE'"

# Technical, never available money, outside the cash board: what the payrolls accrue.
SEED_ACCOUNT = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('payroll_expense', 'Sueldos y bonos internos', 'sueldos y bonos internos', 'PAYROLL_EXPENSE', true)
"""

# The technical accounts of R8 never change (0026's guard, now with «Sueldos y bonos internos»).
NEW_ACCOUNT_GUARD = """
CREATE OR REPLACE FUNCTION settlements.check_employee_technical_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.kind IN ('EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS', 'PAYROLL_EXPENSE')
        OR NEW.kind IN ('EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS', 'PAYROLL_EXPENSE'))
        AND (NEW.kind, NEW.code, NEW.active, NEW.allow_negative)
            IS DISTINCT FROM (OLD.kind, OLD.code, OLD.active, OLD.allow_negative)
    THEN
        RAISE EXCEPTION 'employee_technical_account_closed: account % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
"""
OLD_ACCOUNT_GUARD = """
CREATE OR REPLACE FUNCTION settlements.check_employee_technical_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.kind IN ('EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS')
        OR NEW.kind IN ('EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS'))
        AND (NEW.kind, NEW.code, NEW.active, NEW.allow_negative)
            IS DISTINCT FROM (OLD.kind, OLD.code, OLD.active, OLD.allow_negative)
    THEN
        RAISE EXCEPTION 'employee_technical_account_closed: account % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

# The internal account now also takes a payroll close (and its reversal); «Sueldos y bonos internos» takes only that,
# always +H (its reversal -H). The rest of 0027's discipline is unchanged.
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
# 0027's body, restored by the downgrade.
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

# A payroll is born OPEN, version 1, without a close, for the employee's own internal account. Who it is for, its
# kind and its request never change. Its period, payment date and notes change only while it is OPEN and was never
# closed. Status: OPEN -> CLOSED (its new close, same version), CLOSED -> OPEN (a reopening: version + 1, the close
# stays), OPEN -> CANCELLED (never closed; who, when, why). A cancelled payroll never changes; none is deleted.
PAYROLL_GUARDS = """
CREATE FUNCTION settlements.check_employee_payroll() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.employee_account_id
                       AND kind = 'EMPLOYEE' AND employee_id = NEW.employee_id) THEN
            RAISE EXCEPTION 'employee_payrolls: account % is not the internal account of employee %',
                NEW.employee_account_id, NEW.employee_id USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.status <> 'OPEN' OR NEW.version <> 1 OR NEW.current_closing_id IS NOT NULL THEN
            RAISE EXCEPTION 'employee_payrolls: a payroll is born OPEN, version 1, without a close'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF (NEW.employee_id, NEW.employee_account_id, NEW.kind, NEW.client_request_id, NEW.request_fingerprint,
        NEW.created_by, NEW.created_at)
       IS DISTINCT FROM
       (OLD.employee_id, OLD.employee_account_id, OLD.kind, OLD.client_request_id, OLD.request_fingerprint,
        OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'employee_payrolls: payroll % keeps its employee, kind and request', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF OLD.status = 'CANCELLED' THEN
        RAISE EXCEPTION 'employee_payrolls: payroll % is cancelled and never changes', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.period_start, NEW.period_end, NEW.payment_date, NEW.notes)
       IS DISTINCT FROM (OLD.period_start, OLD.period_end, OLD.payment_date, OLD.notes)
       AND NOT (OLD.status = 'OPEN' AND NEW.status = 'OPEN' AND OLD.current_closing_id IS NULL) THEN
        RAISE EXCEPTION 'employee_payrolls: the period of payroll % changes only while it was never closed', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = OLD.status THEN
        IF (NEW.version, NEW.current_closing_id) IS DISTINCT FROM (OLD.version, OLD.current_closing_id) THEN
            RAISE EXCEPTION 'employee_payrolls: payroll % changes its version or close only by closing or reopening',
                OLD.id USING ERRCODE = 'check_violation';
        END IF;
    ELSIF OLD.status = 'OPEN' AND NEW.status = 'CLOSED' THEN
        IF NEW.version <> OLD.version OR NOT EXISTS (
            SELECT 1 FROM settlements.employee_payroll_closings
            WHERE id = NEW.current_closing_id AND payroll_id = NEW.id AND payroll_version = NEW.version
                AND superseded_at IS NULL) THEN
            RAISE EXCEPTION 'employee_payrolls: payroll % closes with a close of its version', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF OLD.status = 'CLOSED' AND NEW.status = 'OPEN' THEN
        IF NEW.version <> OLD.version + 1 OR NEW.current_closing_id IS DISTINCT FROM OLD.current_closing_id THEN
            RAISE EXCEPTION 'employee_payrolls: reopening payroll % keeps its close and adds a version', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF OLD.status = 'OPEN' AND NEW.status = 'CANCELLED' THEN
        IF OLD.current_closing_id IS NOT NULL OR NEW.version <> OLD.version THEN
            RAISE EXCEPTION 'employee_payrolls: only a payroll never closed is cancelled' USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        RAISE EXCEPTION 'employee_payrolls: payroll % does not go from % to %', OLD.id, OLD.status, NEW.status
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_payrolls_check BEFORE INSERT OR UPDATE ON settlements.employee_payrolls
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll();
CREATE TRIGGER trg_employee_payrolls_no_delete BEFORE DELETE ON settlements.employee_payrolls
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# The draft (credit lines and choices) is written only while its payroll is OPEN: added, changed or removed. A line
# names a salary term of the payroll's employee; a choice names a leg of the payroll's internal account or an
# installment of one of the employee's loans, and an EXTRAORDINARY payroll never chooses an installment (Enzo's Q3).
DRAFT_GUARDS = """
CREATE FUNCTION settlements.check_employee_payroll_draft() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    row_payroll bigint;
    p settlements.employee_payrolls%ROWTYPE;
BEGIN
    IF TG_OP = 'DELETE' THEN
        row_payroll := OLD.payroll_id;
    ELSE
        row_payroll := NEW.payroll_id;
    END IF;
    IF TG_OP = 'UPDATE' AND NEW.payroll_id <> OLD.payroll_id THEN
        RAISE EXCEPTION '%: a draft row keeps its payroll', TG_TABLE_NAME USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO p FROM settlements.employee_payrolls WHERE id = row_payroll;
    IF p.status IS DISTINCT FROM 'OPEN' THEN
        RAISE EXCEPTION '%: payroll % is not open: its draft does not change', TG_TABLE_NAME, row_payroll
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    IF TG_TABLE_NAME = 'employee_payroll_lines' THEN
        IF NEW.compensation_term_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM settlements.employee_compensation_terms
            WHERE id = NEW.compensation_term_id AND employee_id = p.employee_id) THEN
            RAISE EXCEPTION 'employee_payroll_lines: the salary term is not of the payroll''s employee'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        IF NEW.ledger_entry_id IS NOT NULL AND NOT EXISTS (
            SELECT 1 FROM settlements.ledger_entries
            WHERE id = NEW.ledger_entry_id AND account_id = p.employee_account_id) THEN
            RAISE EXCEPTION 'employee_payroll_choices: the leg is not of the payroll''s internal account'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.installment_id IS NOT NULL THEN
            IF p.kind <> 'REGULAR' THEN
                RAISE EXCEPTION 'employee_payroll_choices: an EXTRAORDINARY payroll never applies loan installments'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NOT EXISTS (SELECT 1 FROM settlements.employee_loan_installments i
                           JOIN settlements.employee_loans l ON l.id = i.loan_id
                           WHERE i.id = NEW.installment_id AND l.employee_id = p.employee_id) THEN
                RAISE EXCEPTION 'employee_payroll_choices: the installment is not of the payroll''s employee'
                    USING ERRCODE = 'check_violation';
            END IF;
        END IF;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_payroll_lines_draft BEFORE INSERT OR UPDATE OR DELETE ON settlements.employee_payroll_lines
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_draft();
CREATE TRIGGER trg_employee_payroll_choices_draft
    BEFORE INSERT OR UPDATE OR DELETE ON settlements.employee_payroll_choices
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_draft();
"""

# A close is written for the version being prepared of an OPEN payroll, before the payroll points at it; its lines
# and applications only then, while it is not yet in effect. Afterwards a close changes once: a later close of the same
# payroll supersedes it (who, when), and its applications take that same instant. Nothing else changes; none is
# deleted.
#
# An application ([4]-[11], Enzo's R8-4 invariants) points at a leg of the payroll's own internal account, with the
# same sign and never zero. The leg is of an operation a payroll applies: a settlement close, a pending write-off, a
# consumption, an advance, an adjustment, or a reversal of one of them; or the loan's own leg, only through one of its
# installments: SCHEDULED, of a loan in effect, applied whole and only by a REGULAR payroll.
CLOSING_GUARDS = """
CREATE FUNCTION settlements.check_employee_payroll_closing() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    p settlements.employee_payrolls%ROWTYPE;
BEGIN
    IF TG_OP = 'INSERT' THEN
        SELECT * INTO p FROM settlements.employee_payrolls WHERE id = NEW.payroll_id;
        IF p.status IS DISTINCT FROM 'OPEN' OR NEW.payroll_version <> p.version THEN
            RAISE EXCEPTION 'employee_payroll_closings: a close is for the open version of its payroll'
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.superseded_at IS NOT NULL THEN
            RAISE EXCEPTION 'employee_payroll_closings: a close is born in effect' USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.superseded_at IS NULL AND NEW.superseded_at IS NOT NULL
       AND (to_jsonb(NEW) - 'superseded_at' - 'superseded_by') = (to_jsonb(OLD) - 'superseded_at' - 'superseded_by')
    THEN
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'employee_payroll_closings: close % never changes (only a later close supersedes it, once)', OLD.id
        USING ERRCODE = 'check_violation';
END
$$;
CREATE TRIGGER trg_employee_payroll_closings_check BEFORE INSERT OR UPDATE ON settlements.employee_payroll_closings
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_closing();
CREATE TRIGGER trg_employee_payroll_closings_no_delete BEFORE DELETE ON settlements.employee_payroll_closings
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

CREATE FUNCTION settlements.check_employee_payroll_closing_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    c settlements.employee_payroll_closings%ROWTYPE;
    p settlements.employee_payrolls%ROWTYPE;
    e settlements.ledger_entries%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    i settlements.employee_loan_installments%ROWTYPE;
    l settlements.employee_loans%ROWTYPE;
    applied_type text;
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF TG_TABLE_NAME = 'employee_payroll_applications' AND OLD.superseded_at IS NULL
           AND NEW.superseded_at IS NOT NULL
           AND (to_jsonb(NEW) - 'superseded_at') = (to_jsonb(OLD) - 'superseded_at') THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION '%: row % of a close never changes', TG_TABLE_NAME, OLD.id USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO c FROM settlements.employee_payroll_closings WHERE id = NEW.closing_id;
    SELECT * INTO p FROM settlements.employee_payrolls WHERE id = c.payroll_id;
    IF p.status IS DISTINCT FROM 'OPEN' OR c.payroll_version <> p.version OR c.superseded_at IS NOT NULL
       OR p.current_closing_id IS NOT DISTINCT FROM c.id THEN
        RAISE EXCEPTION '%: rows are written with their close, before it is in effect', TG_TABLE_NAME
            USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'employee_payroll_closing_lines' THEN
        RETURN NEW;
    END IF;

    IF NEW.superseded_at IS NOT NULL THEN
        RAISE EXCEPTION 'employee_payroll_applications: an application is born in effect'
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.payroll_id <> c.payroll_id OR NEW.employee_account_id <> p.employee_account_id THEN
        RAISE EXCEPTION 'employee_payroll_applications: an application is of its close''s payroll and account'
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO e FROM settlements.ledger_entries WHERE id = NEW.ledger_entry_id;
    IF e.account_id IS DISTINCT FROM p.employee_account_id THEN
        RAISE EXCEPTION 'employee_payroll_applications: leg % is not of the payroll''s internal account',
            NEW.ledger_entry_id USING ERRCODE = 'check_violation';
    END IF;
    IF sign(NEW.amount) <> sign(e.amount) THEN
        RAISE EXCEPTION 'employee_payroll_applications: an application has the sign of its leg %', e.id
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM settlements.financial_operations WHERE id = e.operation_id;
    applied_type := o.type;
    IF o.type = 'REVERSAL' THEN
        SELECT type INTO applied_type FROM settlements.financial_operations WHERE id = o.reverses_operation_id;
        IF applied_type NOT IN ('SETTLEMENT_CLOSE', 'PENDING_WRITE_OFF', 'EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE',
                                'EMPLOYEE_ADJUSTMENT') THEN
            RAISE EXCEPTION 'employee_payroll_applications: a reversal of a % is not applied', applied_type
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF o.type = 'EMPLOYEE_LOAN' THEN
        IF NEW.installment_id IS NULL THEN
            RAISE EXCEPTION 'employee_payroll_applications: a loan is applied only through its installments'
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF o.type NOT IN ('SETTLEMENT_CLOSE', 'PENDING_WRITE_OFF', 'EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE',
                         'EMPLOYEE_ADJUSTMENT') THEN
        RAISE EXCEPTION 'employee_payroll_applications: a leg of a % is not applied', o.type
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.installment_id IS NOT NULL THEN
        SELECT * INTO i FROM settlements.employee_loan_installments WHERE id = NEW.installment_id;
        SELECT * INTO l FROM settlements.employee_loans WHERE id = i.loan_id;
        IF o.type <> 'EMPLOYEE_LOAN' OR l.operation_id IS DISTINCT FROM o.id THEN
            RAISE EXCEPTION 'employee_payroll_applications: installment % is applied on its own loan''s leg', i.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF p.kind <> 'REGULAR' THEN
            RAISE EXCEPTION 'employee_payroll_applications: an EXTRAORDINARY payroll never applies loan installments'
                USING ERRCODE = 'check_violation';
        END IF;
        IF i.status <> 'SCHEDULED' OR l.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'employee_payroll_applications: installment % is not scheduled in a loan in effect', i.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF abs(NEW.amount) <> i.amount THEN
            RAISE EXCEPTION 'employee_payroll_applications: installment % is applied whole', i.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_payroll_closing_lines_check
    BEFORE INSERT OR UPDATE ON settlements.employee_payroll_closing_lines
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_closing_row();
CREATE TRIGGER trg_employee_payroll_closing_lines_no_delete BEFORE DELETE ON settlements.employee_payroll_closing_lines
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_employee_payroll_applications_check
    BEFORE INSERT OR UPDATE ON settlements.employee_payroll_applications
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_closing_row();
CREATE TRIGGER trg_employee_payroll_applications_no_delete BEFORE DELETE ON settlements.employee_payroll_applications
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# A reopening is history: never changed nor deleted.
REOPENING_GUARD = """
CREATE FUNCTION settlements.forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'settlements.% rows never change', TG_TABLE_NAME USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_employee_payroll_reopenings_no_change
    BEFORE UPDATE OR DELETE ON settlements.employee_payroll_reopenings
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_change();
"""

# Checked at commit, whatever wrote it (Enzo's R8-4 invariants [6], [7], [14], [15], [17], [19]):
# - exactly the close the payroll points at is in effect: every other close of it is superseded, and so are its
#   applications. A CLOSED payroll's close is of its version; an OPEN one being corrected keeps the close of the
#   previous version, recorded by its reopening;
# - each close adds up to its rows: credits = its lines, deductions = its positive applications, credits applied =
#   its negative ones (net is a CHECK);
# - FINANCIAL: its PAYROLL_CLOSE is exactly «Sueldos y bonos internos» +credits and the internal account -credits, on
#   the period's end, for this payroll. Each later close reverses the previous one's operation (same date) when it had
#   one, and nothing when it had none (NO_ACCRUAL);
# - what the applications in effect take of a leg never exceeds it (a leg applied twice, from one payroll or two,
#   cannot add up to more than itself).
PAYROLL_MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_employee_payroll(pid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    p settlements.employee_payrolls%ROWTYPE;
    c settlements.employee_payroll_closings%ROWTYPE;
    prev settlements.employee_payroll_closings%ROWTYPE;
    expense_account bigint;
    first boolean := true;
BEGIN
    SELECT * INTO p FROM settlements.employee_payrolls WHERE id = pid;
    IF p.id IS NULL THEN
        RETURN;
    END IF;
    IF (SELECT count(*) FROM settlements.employee_payroll_closings WHERE payroll_id = pid AND superseded_at IS NULL)
           <> (CASE WHEN p.current_closing_id IS NULL THEN 0 ELSE 1 END)
       OR (p.current_closing_id IS NOT NULL AND NOT EXISTS (
           SELECT 1 FROM settlements.employee_payroll_closings
           WHERE id = p.current_closing_id AND payroll_id = pid AND superseded_at IS NULL)) THEN
        RAISE EXCEPTION 'payroll %: only the close it points at is in effect', pid USING ERRCODE = 'check_violation';
    END IF;
    IF p.current_closing_id IS NOT NULL THEN
        SELECT * INTO c FROM settlements.employee_payroll_closings WHERE id = p.current_closing_id;
        IF (p.status = 'CLOSED' AND c.payroll_version <> p.version)
           OR (p.status = 'OPEN' AND (c.payroll_version <> p.version - 1 OR NOT EXISTS (
               SELECT 1 FROM settlements.employee_payroll_reopenings
               WHERE payroll_id = pid AND from_version = c.payroll_version AND closing_id = c.id))) THEN
            RAISE EXCEPTION 'payroll %: its close in effect does not match its version', pid
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    SELECT id INTO expense_account FROM settlements.accounts WHERE kind = 'PAYROLL_EXPENSE';
    FOR c IN SELECT * FROM settlements.employee_payroll_closings WHERE payroll_id = pid ORDER BY payroll_version LOOP
        IF c.credits <> (SELECT coalesce(sum(amount), 0) FROM settlements.employee_payroll_closing_lines
                         WHERE closing_id = c.id)
           OR c.deductions <> (SELECT coalesce(sum(amount), 0) FROM settlements.employee_payroll_applications
                               WHERE closing_id = c.id AND amount > 0)
           OR c.credits_applied <> (SELECT coalesce(sum(-amount), 0) FROM settlements.employee_payroll_applications
                                    WHERE closing_id = c.id AND amount < 0)
           OR (c.snapshot ->> 'version')::integer IS DISTINCT FROM c.payroll_version
           OR (c.snapshot ->> 'net')::numeric IS DISTINCT FROM c.net THEN
            RAISE EXCEPTION 'payroll %: close % does not add up to its rows', pid, c.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF c.kind = 'FINANCIAL' AND (
            NOT EXISTS (SELECT 1 FROM settlements.financial_operations
                        WHERE id = c.operation_id AND type = 'PAYROLL_CLOSE' AND source_type = 'employee_payroll'
                            AND source_id = pid AND business_date = p.period_end)
            OR (SELECT count(*) FROM settlements.ledger_entries WHERE operation_id = c.operation_id) <> 2
            OR NOT EXISTS (SELECT 1 FROM settlements.ledger_entries WHERE operation_id = c.operation_id
                           AND account_id = expense_account AND amount = c.credits)
            OR NOT EXISTS (SELECT 1 FROM settlements.ledger_entries WHERE operation_id = c.operation_id
                           AND account_id = p.employee_account_id AND amount = -c.credits)) THEN
            RAISE EXCEPTION 'payroll %: the PAYROLL_CLOSE of close % is not exactly its credits', pid, c.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF EXISTS (SELECT 1 FROM settlements.employee_payroll_applications
                   WHERE closing_id = c.id AND superseded_at IS DISTINCT FROM c.superseded_at) THEN
            RAISE EXCEPTION 'payroll %: the applications of close % are in effect exactly while it is', pid, c.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF first THEN
            IF c.reversal_operation_id IS NOT NULL THEN
                RAISE EXCEPTION 'payroll %: its first close reverses nothing', pid USING ERRCODE = 'check_violation';
            END IF;
        ELSIF prev.superseded_at IS NULL
           OR (prev.kind = 'FINANCIAL' AND NOT EXISTS (
               SELECT 1 FROM settlements.financial_operations
               WHERE id = c.reversal_operation_id AND type = 'REVERSAL' AND reverses_operation_id = prev.operation_id
                   AND business_date = p.period_end))
           OR (prev.kind = 'NO_ACCRUAL' AND c.reversal_operation_id IS NOT NULL) THEN
            RAISE EXCEPTION 'payroll %: close % does not replace the previous one exactly', pid, c.id
                USING ERRCODE = 'check_violation';
        END IF;
        prev := c;
        first := false;
    END LOOP;

    IF EXISTS (
        SELECT 1 FROM settlements.employee_payroll_applications a
        JOIN settlements.ledger_entries e ON e.id = a.ledger_entry_id
        WHERE a.superseded_at IS NULL
            AND a.ledger_entry_id IN (SELECT ledger_entry_id FROM settlements.employee_payroll_applications
                                      WHERE payroll_id = pid)
        GROUP BY e.id, e.amount
        HAVING sum(abs(a.amount)) > abs(e.amount)) THEN
        RAISE EXCEPTION 'payroll %: the applications in effect of a leg exceed it', pid USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_employee_payroll_ledger() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pid bigint;
BEGIN
    IF TG_TABLE_NAME = 'employee_payrolls' THEN
        pid := NEW.id;
    ELSIF TG_TABLE_NAME IN ('employee_payroll_closings', 'employee_payroll_applications') THEN
        pid := NEW.payroll_id;
    ELSIF NEW.type = 'PAYROLL_CLOSE' THEN
        SELECT payroll_id INTO pid FROM settlements.employee_payroll_closings WHERE operation_id = NEW.id;
        IF pid IS NULL THEN
            RAISE EXCEPTION 'operation % of type PAYROLL_CLOSE has no payroll close', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT payroll_id INTO pid FROM settlements.employee_payroll_closings
        WHERE operation_id = NEW.reverses_operation_id;
        IF pid IS NULL THEN
            RETURN NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM settlements.employee_payroll_closings WHERE reversal_operation_id = NEW.id) THEN
            RAISE EXCEPTION 'reversal % of a payroll close without the close that replaces it', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    PERFORM settlements.verify_employee_payroll(pid);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_employee_payrolls_ledger
    AFTER INSERT OR UPDATE ON settlements.employee_payrolls DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_ledger();
CREATE CONSTRAINT TRIGGER trg_employee_payroll_closings_ledger
    AFTER INSERT OR UPDATE ON settlements.employee_payroll_closings DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_ledger();
CREATE CONSTRAINT TRIGGER trg_employee_payroll_applications_ledger
    AFTER INSERT OR UPDATE ON settlements.employee_payroll_applications DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_payroll_ledger();
CREATE CONSTRAINT TRIGGER trg_financial_operations_payroll
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('PAYROLL_CLOSE', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_employee_payroll_ledger();
"""

# What a close in effect applied is not undone underneath it (Enzo's R8-4 point 14, invariants [12], [13], [21]): a
# consumption, advance or adjustment applied is not reversed; an installment applied is neither deferred nor
# cancelled, so its loan is not reversed. The payroll is reopened and corrected first (D8). Settlement corrections
# (R2/R7) are never blocked: their reversals are new legs the payroll applies like any other.
APPLIED_GUARDS = """
CREATE FUNCTION settlements.check_not_applied() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'employee_movements' THEN
        IF OLD.reversal_operation_id IS NULL AND NEW.reversal_operation_id IS NOT NULL AND EXISTS (
            SELECT 1 FROM settlements.employee_payroll_applications a
            JOIN settlements.ledger_entries e ON e.id = a.ledger_entry_id
            WHERE e.operation_id = OLD.operation_id AND a.superseded_at IS NULL) THEN
            RAISE EXCEPTION 'movement_applied: employee movement % is applied by a payroll close in effect', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF TG_TABLE_NAME = 'employee_loan_installments' THEN
        IF OLD.status = 'SCHEDULED' AND NEW.status <> 'SCHEDULED' AND EXISTS (
            SELECT 1 FROM settlements.employee_payroll_applications
            WHERE installment_id = OLD.id AND superseded_at IS NULL) THEN
            RAISE EXCEPTION 'installment_applied: installment % is applied by a payroll close in effect', OLD.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSIF OLD.reversal_operation_id IS NULL AND NEW.reversal_operation_id IS NOT NULL AND EXISTS (
        SELECT 1 FROM settlements.employee_payroll_applications a
        JOIN settlements.employee_loan_installments i ON i.id = a.installment_id
        WHERE i.loan_id = OLD.id AND a.superseded_at IS NULL) THEN
        RAISE EXCEPTION 'loan_applied: employee loan % has installments applied by a payroll close in effect', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_movements_not_applied BEFORE UPDATE ON settlements.employee_movements
    FOR EACH ROW EXECUTE FUNCTION settlements.check_not_applied();
CREATE TRIGGER trg_employee_loan_installments_not_applied BEFORE UPDATE ON settlements.employee_loan_installments
    FOR EACH ROW EXECUTE FUNCTION settlements.check_not_applied();
CREATE TRIGGER trg_employee_loans_not_applied BEFORE UPDATE ON settlements.employee_loans
    FOR EACH ROW EXECUTE FUNCTION settlements.check_not_applied();
"""

# Downgrade only while nothing of R8-4 exists.
DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.employee_payrolls)
        OR EXISTS (SELECT 1 FROM settlements.financial_operations WHERE type = 'PAYROLL_CLOSE')
        OR EXISTS (SELECT 1 FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
                   WHERE a.kind = 'PAYROLL_EXPENSE')
    THEN
        RAISE EXCEPTION 'employee payrolls exist: downgrade of 0028 refused' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_employee_loans_not_applied ON settlements.employee_loans;
DROP TRIGGER trg_employee_loan_installments_not_applied ON settlements.employee_loan_installments;
DROP TRIGGER trg_employee_movements_not_applied ON settlements.employee_movements;
DROP FUNCTION settlements.check_not_applied();
DROP TRIGGER trg_financial_operations_payroll ON settlements.financial_operations;
DROP TRIGGER trg_employee_payroll_applications_ledger ON settlements.employee_payroll_applications;
DROP TRIGGER trg_employee_payroll_closings_ledger ON settlements.employee_payroll_closings;
DROP TRIGGER trg_employee_payrolls_ledger ON settlements.employee_payrolls;
DROP FUNCTION settlements.check_employee_payroll_ledger();
DROP FUNCTION settlements.verify_employee_payroll(bigint);
DROP TRIGGER trg_employee_payroll_reopenings_no_change ON settlements.employee_payroll_reopenings;
DROP FUNCTION settlements.forbid_change();
DROP TRIGGER trg_employee_payroll_applications_no_delete ON settlements.employee_payroll_applications;
DROP TRIGGER trg_employee_payroll_applications_check ON settlements.employee_payroll_applications;
DROP TRIGGER trg_employee_payroll_closing_lines_no_delete ON settlements.employee_payroll_closing_lines;
DROP TRIGGER trg_employee_payroll_closing_lines_check ON settlements.employee_payroll_closing_lines;
DROP FUNCTION settlements.check_employee_payroll_closing_row();
DROP TRIGGER trg_employee_payroll_closings_no_delete ON settlements.employee_payroll_closings;
DROP TRIGGER trg_employee_payroll_closings_check ON settlements.employee_payroll_closings;
DROP FUNCTION settlements.check_employee_payroll_closing();
DROP TRIGGER trg_employee_payroll_choices_draft ON settlements.employee_payroll_choices;
DROP TRIGGER trg_employee_payroll_lines_draft ON settlements.employee_payroll_lines;
DROP FUNCTION settlements.check_employee_payroll_draft();
DROP TRIGGER trg_employee_payrolls_no_delete ON settlements.employee_payrolls;
DROP TRIGGER trg_employee_payrolls_check ON settlements.employee_payrolls;
DROP FUNCTION settlements.check_employee_payroll();
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
    op.execute(SEED_ACCOUNT)
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    op.create_table(
        "employee_payrolls",
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_account_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=10), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("current_closing_id", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_by", sa.BigInteger(), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_reason", sa.Text(), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        postgresql.ExcludeConstraint(
            (sa.column("employee_id"), "="),
            (sa.text("daterange(period_start, period_end, '[]')"), "&&"),
            where=sa.text("kind = 'REGULAR' AND status <> 'CANCELLED'"),
            using="gist",
            name="ex_employee_payrolls_regular_no_overlap",
        ),
        sa.CheckConstraint(
            "(status = 'CANCELLED') = (cancelled_by IS NOT NULL) AND (cancelled_by IS NULL) = (cancelled_at IS NULL) AND (cancelled_at IS NULL) = (cancel_reason IS NULL)",
            name=op.f("ck_employee_payrolls_cancel_fields"),
        ),
        sa.CheckConstraint("kind IN ('REGULAR', 'EXTRAORDINARY')", name=op.f("ck_employee_payrolls_kind")),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_employee_payrolls_fingerprint_format")
        ),
        sa.CheckConstraint(
            "status <> 'CANCELLED' OR current_closing_id IS NULL",
            name=op.f("ck_employee_payrolls_cancelled_never_closed"),
        ),
        sa.CheckConstraint(
            "status <> 'CLOSED' OR current_closing_id IS NOT NULL", name=op.f("ck_employee_payrolls_closed_has_closing")
        ),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED', 'CANCELLED')", name=op.f("ck_employee_payrolls_status")),
        sa.CheckConstraint(
            "cancel_reason IS NULL OR length(btrim(cancel_reason)) > 0",
            name=op.f("ck_employee_payrolls_cancel_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "notes IS NULL OR length(btrim(notes)) > 0", name=op.f("ck_employee_payrolls_notes_not_blank")
        ),
        sa.CheckConstraint("period_start <= period_end", name=op.f("ck_employee_payrolls_period")),
        sa.CheckConstraint("version >= 1", name=op.f("ck_employee_payrolls_version_positive")),
        sa.ForeignKeyConstraint(
            ["cancelled_by"], ["users.id"], name=op.f("fk_employee_payrolls_cancelled_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_employee_payrolls_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["employee_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_payrolls_employee_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_employee_payrolls_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payrolls")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_employee_payrolls_client_request_id")),
        sa.UniqueConstraint("current_closing_id", name=op.f("uq_employee_payrolls_current_closing_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payrolls_employee_id"),
        "employee_payrolls",
        ["employee_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "employee_payroll_closings",
        sa.Column("payroll_id", sa.BigInteger(), nullable=False),
        sa.Column("payroll_version", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("operation_id", sa.BigInteger(), nullable=True),
        sa.Column("reversal_operation_id", sa.BigInteger(), nullable=True),
        sa.Column("credits", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("deductions", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("credits_applied", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("net", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("closed_by", sa.BigInteger(), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("superseded_by", sa.BigInteger(), nullable=True),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(kind = 'FINANCIAL' AND operation_id IS NOT NULL AND credits > 0) OR (kind = 'NO_ACCRUAL' AND operation_id IS NULL AND credits = 0)",
            name=op.f("ck_employee_payroll_closings_kind_operation"),
        ),
        sa.CheckConstraint("kind IN ('FINANCIAL', 'NO_ACCRUAL')", name=op.f("ck_employee_payroll_closings_kind")),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_employee_payroll_closings_fingerprint_format")
        ),
        sa.CheckConstraint(
            "(superseded_at IS NULL) = (superseded_by IS NULL)",
            name=op.f("ck_employee_payroll_closings_superseded_fields"),
        ),
        sa.CheckConstraint(
            "credits >= 0 AND deductions >= 0 AND credits_applied >= 0",
            name=op.f("ck_employee_payroll_closings_totals_not_negative"),
        ),
        sa.CheckConstraint(
            "net = credits - deductions + credits_applied", name=op.f("ck_employee_payroll_closings_net_formula")
        ),
        sa.CheckConstraint("net >= 0", name=op.f("ck_employee_payroll_closings_net_not_negative")),
        sa.ForeignKeyConstraint(
            ["closed_by"], ["users.id"], name=op.f("fk_employee_payroll_closings_closed_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_payroll_closings_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payroll_id"],
            ["settlements.employee_payrolls.id"],
            name=op.f("fk_employee_payroll_closings_payroll_id_employee_payrolls"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_payroll_closings_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by"],
            ["users.id"],
            name=op.f("fk_employee_payroll_closings_superseded_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payroll_closings")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_employee_payroll_closings_operation_id")),
        sa.UniqueConstraint(
            "payroll_id", "payroll_version", name=op.f("uq_employee_payroll_closings_payroll_id_payroll_version")
        ),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_employee_payroll_closings_reversal_operation_id")),
        schema="settlements",
    )
    op.create_table(
        "employee_payroll_lines",
        sa.Column("payroll_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("compensation_term_id", sa.BigInteger(), nullable=True),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(source = 'TERM') = (compensation_term_id IS NOT NULL)", name=op.f("ck_employee_payroll_lines_term_source")
        ),
        sa.CheckConstraint(
            "kind <> 'OTHER_CREDIT' OR description IS NOT NULL",
            name=op.f("ck_employee_payroll_lines_other_credit_described"),
        ),
        sa.CheckConstraint(
            "kind <> 'SALARY' OR source = 'TERM' OR description IS NOT NULL",
            name=op.f("ck_employee_payroll_lines_manual_salary_explained"),
        ),
        sa.CheckConstraint(
            "kind IN ('SALARY', 'BONUS', 'INCENTIVE', 'OTHER_CREDIT')", name=op.f("ck_employee_payroll_lines_kind")
        ),
        sa.CheckConstraint(
            "source = 'MANUAL' OR kind = 'SALARY'", name=op.f("ck_employee_payroll_lines_term_only_salary")
        ),
        sa.CheckConstraint("source IN ('TERM', 'MANUAL')", name=op.f("ck_employee_payroll_lines_source")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_employee_payroll_lines_amount_positive")),
        sa.CheckConstraint(
            "description IS NULL OR length(btrim(description)) > 0",
            name=op.f("ck_employee_payroll_lines_description_not_blank"),
        ),
        sa.CheckConstraint(
            "description IS NULL OR length(description) <= 500",
            name=op.f("ck_employee_payroll_lines_description_length"),
        ),
        sa.ForeignKeyConstraint(
            ["compensation_term_id"],
            ["settlements.employee_compensation_terms.id"],
            name=op.f("fk_employee_payroll_lines_compensation_term_id_employee_compensation_terms"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_employee_payroll_lines_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["payroll_id"],
            ["settlements.employee_payrolls.id"],
            name=op.f("fk_employee_payroll_lines_payroll_id_employee_payrolls"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payroll_lines")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payroll_lines_payroll_id"),
        "employee_payroll_lines",
        ["payroll_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "employee_payroll_applications",
        sa.Column("closing_id", sa.BigInteger(), nullable=False),
        sa.Column("payroll_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_account_id", sa.BigInteger(), nullable=False),
        sa.Column("ledger_entry_id", sa.BigInteger(), nullable=False),
        sa.Column("installment_id", sa.BigInteger(), nullable=True),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("amount <> 0", name=op.f("ck_employee_payroll_applications_amount_not_zero")),
        sa.ForeignKeyConstraint(
            ["closing_id"],
            ["settlements.employee_payroll_closings.id"],
            name=op.f("fk_employee_payroll_applications_closing_id_employee_payroll_closings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_payroll_applications_employee_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["installment_id"],
            ["settlements.employee_loan_installments.id"],
            name=op.f("fk_employee_payroll_applications_installment_id_employee_loan_installments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ledger_entry_id"],
            ["settlements.ledger_entries.id"],
            name=op.f("fk_employee_payroll_applications_ledger_entry_id_ledger_entries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payroll_id"],
            ["settlements.employee_payrolls.id"],
            name=op.f("fk_employee_payroll_applications_payroll_id_employee_payrolls"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payroll_applications")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payroll_applications_closing_id"),
        "employee_payroll_applications",
        ["closing_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payroll_applications_ledger_entry_id"),
        "employee_payroll_applications",
        ["ledger_entry_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_employee_payroll_applications_closing_entry",
        "employee_payroll_applications",
        ["closing_id", "ledger_entry_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("installment_id IS NULL"),
    )
    op.create_index(
        "uq_employee_payroll_applications_installment",
        "employee_payroll_applications",
        ["installment_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("installment_id IS NOT NULL AND superseded_at IS NULL"),
    )
    op.create_table(
        "employee_payroll_choices",
        sa.Column("payroll_id", sa.BigInteger(), nullable=False),
        sa.Column("ledger_entry_id", sa.BigInteger(), nullable=True),
        sa.Column("installment_id", sa.BigInteger(), nullable=True),
        sa.Column("included", sa.Boolean(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(ledger_entry_id IS NULL) <> (installment_id IS NULL)", name=op.f("ck_employee_payroll_choices_one_item")
        ),
        sa.CheckConstraint("amount IS NULL OR amount > 0", name=op.f("ck_employee_payroll_choices_amount_positive")),
        sa.CheckConstraint(
            "included OR amount IS NULL", name=op.f("ck_employee_payroll_choices_excluded_without_amount")
        ),
        sa.CheckConstraint(
            "installment_id IS NULL OR amount IS NULL", name=op.f("ck_employee_payroll_choices_installment_whole")
        ),
        sa.ForeignKeyConstraint(
            ["installment_id"],
            ["settlements.employee_loan_installments.id"],
            name=op.f("fk_employee_payroll_choices_installment_id_employee_loan_installments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["ledger_entry_id"],
            ["settlements.ledger_entries.id"],
            name=op.f("fk_employee_payroll_choices_ledger_entry_id_ledger_entries"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payroll_id"],
            ["settlements.employee_payrolls.id"],
            name=op.f("fk_employee_payroll_choices_payroll_id_employee_payrolls"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_employee_payroll_choices_updated_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payroll_choices")),
        schema="settlements",
    )
    op.create_index(
        "uq_employee_payroll_choices_entry",
        "employee_payroll_choices",
        ["payroll_id", "ledger_entry_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("ledger_entry_id IS NOT NULL"),
    )
    op.create_index(
        "uq_employee_payroll_choices_installment",
        "employee_payroll_choices",
        ["payroll_id", "installment_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("installment_id IS NOT NULL"),
    )
    op.create_table(
        "employee_payroll_closing_lines",
        sa.Column("closing_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=15), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("compensation_term_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(source = 'TERM') = (compensation_term_id IS NOT NULL)",
            name=op.f("ck_employee_payroll_closing_lines_term_source"),
        ),
        sa.CheckConstraint(
            "kind IN ('SALARY', 'BONUS', 'INCENTIVE', 'OTHER_CREDIT')",
            name=op.f("ck_employee_payroll_closing_lines_kind"),
        ),
        sa.CheckConstraint("source IN ('TERM', 'MANUAL')", name=op.f("ck_employee_payroll_closing_lines_source")),
        sa.CheckConstraint("amount > 0", name=op.f("ck_employee_payroll_closing_lines_amount_positive")),
        sa.ForeignKeyConstraint(
            ["closing_id"],
            ["settlements.employee_payroll_closings.id"],
            name=op.f("fk_employee_payroll_closing_lines_closing_id_employee_payroll_closings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["compensation_term_id"],
            ["settlements.employee_compensation_terms.id"],
            name=op.f("fk_employee_payroll_closing_lines_compensation_term_id_employee_compensation_terms"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payroll_closing_lines")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_employee_payroll_closing_lines_closing_id"),
        "employee_payroll_closing_lines",
        ["closing_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "employee_payroll_reopenings",
        sa.Column("payroll_id", sa.BigInteger(), nullable=False),
        sa.Column("from_version", sa.Integer(), nullable=False),
        sa.Column("to_version", sa.Integer(), nullable=False),
        sa.Column("closing_id", sa.BigInteger(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("reopened_by", sa.BigInteger(), nullable=False),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_employee_payroll_reopenings_reason_not_blank")),
        sa.CheckConstraint("to_version = from_version + 1", name=op.f("ck_employee_payroll_reopenings_next_version")),
        sa.ForeignKeyConstraint(
            ["closing_id"],
            ["settlements.employee_payroll_closings.id"],
            name=op.f("fk_employee_payroll_reopenings_closing_id_employee_payroll_closings"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["payroll_id"],
            ["settlements.employee_payrolls.id"],
            name=op.f("fk_employee_payroll_reopenings_payroll_id_employee_payrolls"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reopened_by"],
            ["users.id"],
            name=op.f("fk_employee_payroll_reopenings_reopened_by_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_payroll_reopenings")),
        sa.UniqueConstraint(
            "payroll_id", "from_version", name=op.f("uq_employee_payroll_reopenings_payroll_id_from_version")
        ),
        schema="settlements",
    )
    op.create_foreign_key(
        op.f("fk_employee_payrolls_current_closing_id_employee_payroll_closings"),
        "employee_payrolls",
        "employee_payroll_closings",
        ["current_closing_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
        deferrable=True,
        initially="DEFERRED",
    )

    op.execute(NEW_ACCOUNT_GUARD)
    op.execute(NEW_LEG_DISCIPLINE)
    op.execute(PAYROLL_GUARDS)
    op.execute(DRAFT_GUARDS)
    op.execute(CLOSING_GUARDS)
    op.execute(REOPENING_GUARD)
    op.execute(PAYROLL_MATCHES_LEDGER)
    op.execute(APPLIED_GUARDS)


def downgrade() -> None:
    """Downgrade schema. Refused while any payroll, PAYROLL_CLOSE or leg on «Sueldos y bonos internos» exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute(DROP_TRIGGERS)
    op.execute(OLD_LEG_DISCIPLINE)
    op.execute(OLD_ACCOUNT_GUARD)
    op.drop_constraint(
        op.f("fk_employee_payrolls_current_closing_id_employee_payroll_closings"),
        "employee_payrolls",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_table("employee_payroll_reopenings", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_employee_payroll_closing_lines_closing_id"),
        table_name="employee_payroll_closing_lines",
        schema="settlements",
    )
    op.drop_table("employee_payroll_closing_lines", schema="settlements")
    op.drop_index(
        "uq_employee_payroll_choices_installment",
        table_name="employee_payroll_choices",
        schema="settlements",
        postgresql_where=sa.text("installment_id IS NOT NULL"),
    )
    op.drop_index(
        "uq_employee_payroll_choices_entry",
        table_name="employee_payroll_choices",
        schema="settlements",
        postgresql_where=sa.text("ledger_entry_id IS NOT NULL"),
    )
    op.drop_table("employee_payroll_choices", schema="settlements")
    op.drop_index(
        "uq_employee_payroll_applications_installment",
        table_name="employee_payroll_applications",
        schema="settlements",
        postgresql_where=sa.text("installment_id IS NOT NULL AND superseded_at IS NULL"),
    )
    op.drop_index(
        "uq_employee_payroll_applications_closing_entry",
        table_name="employee_payroll_applications",
        schema="settlements",
        postgresql_where=sa.text("installment_id IS NULL"),
    )
    op.drop_index(
        op.f("ix_settlements_employee_payroll_applications_ledger_entry_id"),
        table_name="employee_payroll_applications",
        schema="settlements",
    )
    op.drop_index(
        op.f("ix_settlements_employee_payroll_applications_closing_id"),
        table_name="employee_payroll_applications",
        schema="settlements",
    )
    op.drop_table("employee_payroll_applications", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_employee_payroll_lines_payroll_id"),
        table_name="employee_payroll_lines",
        schema="settlements",
    )
    op.drop_table("employee_payroll_lines", schema="settlements")
    op.drop_table("employee_payroll_closings", schema="settlements")
    op.drop_index(
        op.f("ix_settlements_employee_payrolls_employee_id"), table_name="employee_payrolls", schema="settlements"
    )
    op.drop_table("employee_payrolls", schema="settlements")
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
    op.execute("DELETE FROM settlements.accounts WHERE code = 'payroll_expense'")
    _technical_index(OLD_TECHNICAL)
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
