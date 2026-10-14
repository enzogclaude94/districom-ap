"""settlements: outlet remittances

Revision ID: 0024
Revises: 0023
Create Date: 2026-10-13 22:00:00.000000

R7-3 (ADR 009, Enzo's R7-3 decisions): cash a boca hands over during the day to a cash box, before its
settlement closes. Each remittance is the document of its own `OUTLET_REMITTANCE` operation: «Entregas a
cuenta — {Boca}» (OUTLET_ADVANCES, one account per boca, created on first use) -X and the destination cash box
+X. The close of the boca's settlement consolidates the remittances in effect: +their total on that account,
without moving the cash box again. `settlement_closing_remittances` freezes which remittances each close
consolidated, so the balance of each boca's account is always

    - (remittances in effect of the boca) + (what the closes in effect of its settlements consolidated)

which the database checks at commit for every boca a transaction touches.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0024"
down_revision: str | Sequence[str] | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE', 'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', "
    "'CHEQUES_IN_TRANSIT', 'MERCHANDISE_PAYMENTS', 'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS', 'OUTLET_COLLECTIONS'"
)
NEW_KINDS = OLD_KINDS + ", 'OUTLET_ADVANCES'"
OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION', 'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', "
    "'CHEQUE_RETURN', 'DIRECT_EXPENSE', 'PAYMENT'"
)
NEW_TYPES = OLD_TYPES + ", 'OUTLET_REMITTANCE'"

# «Entregas a cuenta — {Boca}» belongs to exactly one boca and may be negative (cash already handed over, not yet
# consolidated by a close). Its kind, boca and state never change: it is the boca's for ever.
ACCOUNT_GUARD = """
CREATE FUNCTION settlements.check_outlet_advances_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.kind = 'OUTLET_ADVANCES' OR NEW.kind = 'OUTLET_ADVANCES')
        AND (NEW.kind, NEW.outlet_id, NEW.active, NEW.allow_negative)
            IS DISTINCT FROM (OLD.kind, OLD.outlet_id, OLD.active, OLD.allow_negative)
    THEN
        RAISE EXCEPTION 'outlet_advances_closed: the account of a boca never changes (account %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_accounts_outlet_advances BEFORE UPDATE ON settlements.accounts
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_advances_account();
"""

# A remittance is born on an OPEN boca settlement (never closed, or reopened for correction), for its boca and
# business date, to an active cash box (locked FOR SHARE, so a concurrent close or deactivation serializes with
# it), delivered at an instant that is not in the future and falls on the business date in Buenos Aires, and in
# effect. Afterwards only two things happen to it, once each: its operation is recorded (in the creating
# transaction) and it is reversed (admin, with a reason, while the settlement is OPEN). A reversed remittance
# never changes; none is deleted.
REMITTANCE_GUARDS = """
CREATE FUNCTION settlements.check_outlet_remittance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    s settlements.settlements%ROWTYPE;
BEGIN
    SELECT * INTO s FROM settlements.settlements WHERE id = NEW.settlement_id FOR SHARE;
    IF TG_OP = 'INSERT' THEN
        IF s.type IS DISTINCT FROM 'OUTLET' THEN
            RAISE EXCEPTION 'outlet_remittances: settlement % is not of a boca', NEW.settlement_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF s.status <> 'OPEN' THEN
            RAISE EXCEPTION 'outlet_remittances: settlement % is % and takes no remittance', s.id, s.status
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.outlet_id IS DISTINCT FROM s.outlet_id OR NEW.business_date IS DISTINCT FROM s.business_date THEN
            RAISE EXCEPTION 'outlet_remittances: boca and date are those of settlement %', s.id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.destination_account_id
                       AND kind = 'CASH_BOX' AND active FOR SHARE) THEN
            RAISE EXCEPTION 'outlet_remittances: account % is not an active cash box', NEW.destination_account_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.delivered_at > clock_timestamp()
            OR (NEW.delivered_at AT TIME ZONE 'America/Argentina/Buenos_Aires')::date <> NEW.business_date
        THEN
            RAISE EXCEPTION 'outlet_remittances: delivered_at % is in the future or not on %', NEW.delivered_at,
                NEW.business_date USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.operation_id IS NOT NULL OR NEW.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'outlet_remittances: a remittance is born in effect, before its operation'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.reversal_operation_id IS NOT NULL THEN
        RAISE EXCEPTION 'outlet_remittances: remittance % is reversed and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.settlement_id, NEW.outlet_id, NEW.business_date, NEW.delivered_at, NEW.amount,
        NEW.destination_account_id, NEW.delivered_by_employee_id, NEW.reference, NEW.client_request_id,
        NEW.request_fingerprint, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM
       (OLD.settlement_id, OLD.outlet_id, OLD.business_date, OLD.delivered_at, OLD.amount,
        OLD.destination_account_id, OLD.delivered_by_employee_id, OLD.reference, OLD.client_request_id,
        OLD.request_fingerprint, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'outlet_remittances: remittance % is published and never changes; reverse it', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN
        RAISE EXCEPTION 'outlet_remittances: the operation of remittance % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.reversal_operation_id IS NOT NULL AND s.status <> 'OPEN' THEN
        RAISE EXCEPTION 'outlet_remittances: settlement % is CLOSED: reopen it to reverse remittance %', s.id, OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_outlet_remittances_check BEFORE INSERT OR UPDATE ON settlements.outlet_remittances
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_remittance();
CREATE TRIGGER trg_outlet_remittances_no_delete BEFORE DELETE ON settlements.outlet_remittances
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_settlement_closing_remittances_no_change
    BEFORE UPDATE OR DELETE ON settlements.settlement_closing_remittances
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_ledger_mutation();
"""

# Only an OUTLET_REMITTANCE of the boca's own remittance (-X), the close of one of the boca's settlements (+X)
# and the documentary reversals of those (opposite sign) move a boca's «Entregas a cuenta». Checked on every leg.
ADVANCES_DISCIPLINE = """
CREATE FUNCTION settlements.check_outlet_advances_leg() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    account_outlet bigint;
    op settlements.financial_operations%ROWTYPE;
    flip integer := 1;
    allowed boolean;
BEGIN
    SELECT outlet_id INTO account_outlet FROM settlements.accounts
        WHERE id = NEW.account_id AND kind = 'OUTLET_ADVANCES';
    IF NOT FOUND THEN
        RETURN NEW;
    END IF;
    SELECT * INTO op FROM settlements.financial_operations WHERE id = NEW.operation_id;
    IF op.type = 'REVERSAL' THEN
        SELECT * INTO op FROM settlements.financial_operations WHERE id = op.reverses_operation_id;
        flip := -1;
    END IF;
    allowed := (op.type = 'OUTLET_REMITTANCE' AND op.source_type = 'outlet_remittance'
                AND sign(NEW.amount) = -flip
                AND EXISTS (SELECT 1 FROM settlements.outlet_remittances
                            WHERE id = op.source_id AND outlet_id = account_outlet))
            OR (op.type = 'SETTLEMENT_CLOSE' AND op.source_type = 'settlement'
                AND sign(NEW.amount) = flip
                AND EXISTS (SELECT 1 FROM settlements.settlements
                            WHERE id = op.source_id AND type = 'OUTLET' AND outlet_id = account_outlet));
    IF NOT coalesce(allowed, false) THEN
        RAISE EXCEPTION 'outlet_advances_closed: only the remittances and the closes of boca % and their reversals '
            'move its account', account_outlet USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_ledger_entries_outlet_advances BEFORE INSERT ON settlements.ledger_entries
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_advances_leg();
"""

# At commit: a remittance and its OUTLET_REMITTANCE are exactly each other. The operation has the remittance as
# source and its business date, and exactly two legs: the boca's «Entregas a cuenta» -amount and the destination
# cash box +amount (no cheque). The remittance is reversed exactly when a REVERSAL of that operation exists, on the
# same business date. An OUTLET_REMITTANCE without its remittance, or a reversal of one without the remittance
# marked reversed by it, is refused.
REMITTANCE_MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_outlet_remittance(rid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    r settlements.outlet_remittances%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    advances bigint;
    reversal bigint;
    mismatch boolean;
BEGIN
    SELECT * INTO r FROM settlements.outlet_remittances WHERE id = rid;
    IF r.operation_id IS NULL THEN
        RAISE EXCEPTION 'remittance % has no operation', rid USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM settlements.financial_operations WHERE id = r.operation_id;
    IF o.type <> 'OUTLET_REMITTANCE' OR o.source_type IS DISTINCT FROM 'outlet_remittance'
        OR o.source_id IS DISTINCT FROM rid OR o.business_date <> r.business_date THEN
        RAISE EXCEPTION 'operation % is not the OUTLET_REMITTANCE of remittance %', o.id, rid
            USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO advances FROM settlements.accounts WHERE kind = 'OUTLET_ADVANCES' AND outlet_id = r.outlet_id;
    SELECT EXISTS (
        (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id
         EXCEPT ALL
         SELECT * FROM (VALUES (advances, NULL::bigint, -r.amount),
                               (r.destination_account_id, NULL::bigint, r.amount)) x(account_id, cheque_id, amount))
        UNION ALL
        (SELECT * FROM (VALUES (advances, NULL::bigint, -r.amount),
                               (r.destination_account_id, NULL::bigint, r.amount)) x(account_id, cheque_id, amount)
         EXCEPT ALL
         SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id)
    ) INTO mismatch;
    IF advances IS NULL OR mismatch THEN
        RAISE EXCEPTION 'operation % does not match remittance %', o.id, rid USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO reversal FROM settlements.financial_operations WHERE reverses_operation_id = o.id;
    IF reversal IS DISTINCT FROM r.reversal_operation_id THEN
        RAISE EXCEPTION 'remittance % is reversed exactly by the reversal of its operation', rid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations
        WHERE id = reversal AND type = 'REVERSAL' AND business_date = r.business_date) THEN
        RAISE EXCEPTION 'the reversal of remittance % keeps its business date', rid USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_outlet_remittance_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    rid bigint;
BEGIN
    IF TG_TABLE_NAME = 'outlet_remittances' THEN
        rid := NEW.id;
    ELSIF NEW.type = 'OUTLET_REMITTANCE' THEN
        SELECT id INTO rid FROM settlements.outlet_remittances WHERE operation_id = NEW.id;
        IF rid IS NULL THEN
            RAISE EXCEPTION 'operation % of type OUTLET_REMITTANCE has no remittance', NEW.id
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT r.id INTO rid FROM settlements.outlet_remittances r
            JOIN settlements.financial_operations o ON o.id = r.operation_id
            WHERE o.id = NEW.reverses_operation_id AND o.type = 'OUTLET_REMITTANCE';
        IF rid IS NULL THEN
            IF EXISTS (SELECT 1 FROM settlements.financial_operations
                       WHERE id = NEW.reverses_operation_id AND type = 'OUTLET_REMITTANCE') THEN
                RAISE EXCEPTION 'reversal % of an OUTLET_REMITTANCE without its remittance', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END IF;
    END IF;
    PERFORM settlements.verify_outlet_remittance(rid);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_outlet_remittances_operation
    AFTER INSERT OR UPDATE ON settlements.outlet_remittances DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_remittance_operation();
CREATE CONSTRAINT TRIGGER trg_financial_operations_outlet_remittance
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('OUTLET_REMITTANCE', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_outlet_remittance_operation();
"""

# A close consolidates only remittances of its own settlement, each for its own amount, in effect when it is
# published; and (at commit) what it froze in `settlement_closing_remittances` is exactly its settlement's
# remittances in effect and exactly its leg on the boca's «Entregas a cuenta» (no leg when there are none).
CLOSE_CONSOLIDATES = """
CREATE FUNCTION settlements.check_closing_remittance() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.outlet_remittances r
        WHERE r.id = NEW.remittance_id AND r.settlement_id = NEW.settlement_id AND r.amount = NEW.amount
            AND r.reversal_operation_id IS NULL
    ) THEN
        RAISE EXCEPTION 'remittance % is reversed, of another amount or not of settlement %', NEW.remittance_id,
            NEW.settlement_id USING ERRCODE = 'check_violation';
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
CREATE TRIGGER trg_settlement_closing_remittances_check BEFORE INSERT ON settlements.settlement_closing_remittances
    FOR EACH ROW EXECUTE FUNCTION settlements.check_closing_remittance();

CREATE FUNCTION settlements.check_close_remittances() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    op settlements.financial_operations%ROWTYPE;
    advance_legs integer;
    advance_total numeric;
    frozen numeric;
BEGIN
    IF TG_TABLE_NAME = 'financial_operations' THEN
        op := NEW;
    ELSE
        SELECT * INTO op FROM settlements.financial_operations WHERE id = NEW.close_operation_id;
    END IF;
    SELECT count(*), coalesce(sum(e.amount), 0) INTO advance_legs, advance_total
        FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
        WHERE e.operation_id = op.id AND a.kind = 'OUTLET_ADVANCES';
    SELECT coalesce(sum(amount), 0) INTO frozen FROM settlements.settlement_closing_remittances
        WHERE close_operation_id = op.id;
    IF advance_legs > 1 OR advance_total <> frozen
        OR EXISTS (
            (SELECT remittance_id, amount FROM settlements.settlement_closing_remittances
                WHERE close_operation_id = op.id
             EXCEPT
             SELECT id, amount FROM settlements.outlet_remittances
                WHERE settlement_id = op.source_id AND reversal_operation_id IS NULL)
            UNION ALL
            (SELECT id, amount FROM settlements.outlet_remittances
                WHERE settlement_id = op.source_id AND reversal_operation_id IS NULL
             EXCEPT
             SELECT remittance_id, amount FROM settlements.settlement_closing_remittances
                WHERE close_operation_id = op.id))
    THEN
        RAISE EXCEPTION 'close %: it consolidates exactly the remittances in effect of its settlement', op.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_close_remittances
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'SETTLEMENT_CLOSE')
    EXECUTE FUNCTION settlements.check_close_remittances();
CREATE CONSTRAINT TRIGGER trg_settlement_closing_remittances_match_ledger
    AFTER INSERT ON settlements.settlement_closing_remittances DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_close_remittances();
"""

# The global rule per boca (Enzo's R7-3 point 13), at commit, for every boca a transaction touched. A boca's
# «Entregas a cuenta» holds exactly
#
#     - (its remittances in effect) + (what the closes in effect of its settlements consolidated)
#
# which is the remittances not yet consolidated (negative), plus, only while a settlement is being corrected, a
# remittance its close in effect consolidated and that was reversed since (the old close stays in effect until
# the new one). And a CLOSED settlement's close in effect consolidated exactly its remittances in effect, so out of
# corrections the balance is exactly minus the remittances in effect not consolidated. Two settlements of the same
# boca never mix: each close counts only its own remittances.
GLOBAL_BALANCE = """
CREATE FUNCTION settlements.assert_outlet_advances(boca bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    balance numeric;
    in_effect numeric;
    consolidated numeric;
BEGIN
    SELECT coalesce(sum(e.amount), 0) INTO balance FROM settlements.ledger_entries e
        JOIN settlements.accounts a ON a.id = e.account_id
        WHERE a.kind = 'OUTLET_ADVANCES' AND a.outlet_id = boca;
    SELECT coalesce(sum(amount), 0) INTO in_effect FROM settlements.outlet_remittances
        WHERE outlet_id = boca AND reversal_operation_id IS NULL;
    SELECT coalesce(sum(cr.amount), 0) INTO consolidated FROM settlements.settlement_closing_remittances cr
        JOIN settlements.financial_operations o ON o.id = cr.close_operation_id
        JOIN settlements.settlements s ON s.id = o.source_id AND o.source_type = 'settlement'
        WHERE s.outlet_id = boca
            AND NOT EXISTS (SELECT 1 FROM settlements.financial_operations r WHERE r.reverses_operation_id = o.id);
    IF balance <> consolidated - in_effect THEN
        RAISE EXCEPTION 'outlet_advances_inconsistent: boca % holds % but its remittances say %', boca, balance,
            consolidated - in_effect USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        SELECT 1 FROM settlements.settlements s
        WHERE s.type = 'OUTLET' AND s.outlet_id = boca AND s.status = 'CLOSED'
            AND EXISTS (
                (SELECT id, amount FROM settlements.outlet_remittances
                    WHERE settlement_id = s.id AND reversal_operation_id IS NULL
                 EXCEPT
                 SELECT remittance_id, amount FROM settlements.settlement_closing_remittances
                    WHERE close_operation_id IS NOT DISTINCT FROM s.current_close_operation_id)
                UNION ALL
                (SELECT remittance_id, amount FROM settlements.settlement_closing_remittances
                    WHERE close_operation_id IS NOT DISTINCT FROM s.current_close_operation_id
                 EXCEPT
                 SELECT id, amount FROM settlements.outlet_remittances
                    WHERE settlement_id = s.id AND reversal_operation_id IS NULL))
    ) THEN
        RAISE EXCEPTION 'outlet_advances_inconsistent: a closed settlement of boca % does not consolidate exactly its '
            'remittances in effect', boca USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_outlet_advances_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    boca bigint;
BEGIN
    IF TG_TABLE_NAME = 'ledger_entries' THEN
        SELECT outlet_id INTO boca FROM settlements.accounts WHERE id = NEW.account_id AND kind = 'OUTLET_ADVANCES';
    ELSIF TG_TABLE_NAME = 'settlements' THEN
        boca := NEW.outlet_id;
    ELSE
        boca := NEW.outlet_id;
    END IF;
    IF boca IS NOT NULL THEN
        PERFORM settlements.assert_outlet_advances(boca);
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_ledger_entries_outlet_advances_balance
    AFTER INSERT ON settlements.ledger_entries DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_advances_balance();
CREATE CONSTRAINT TRIGGER trg_outlet_remittances_balance
    AFTER INSERT OR UPDATE ON settlements.outlet_remittances DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_outlet_advances_balance();
CREATE CONSTRAINT TRIGGER trg_settlements_outlet_advances_balance
    AFTER UPDATE ON settlements.settlements DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'OUTLET')
    EXECUTE FUNCTION settlements.check_outlet_advances_balance();
"""

# The administrative zero close of 0023, now also without remittances in effect (Enzo's R7-3 point 11). A reversed
# remittance does not count; one in effect of $0 cannot exist (amount > 0). Zero closes written before 0024 have no
# "remittances" in their snapshot; new ones say "0.00".
ZERO_CLOSE_BODY = """
CREATE OR REPLACE FUNCTION settlements.assert_zero_close(closing_id bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    c settlements.settlement_closings%ROWTYPE;
    s settlements.settlements%ROWTYPE;
BEGIN
    SELECT * INTO c FROM settlements.settlement_closings WHERE id = closing_id;
    SELECT * INTO s FROM settlements.settlements WHERE id = c.settlement_id;
    IF c.kind <> 'ADMINISTRATIVE_ZERO' OR s.type <> 'OUTLET' OR s.version <> c.settlement_version
        OR s.expected_amount IS DISTINCT FROM 0
        OR EXISTS (SELECT 1 FROM settlements.cash_count_lines WHERE settlement_id = s.id)
        OR EXISTS (SELECT 1 FROM settlements.cash_additions WHERE settlement_id = s.id AND voided_at IS NULL)
        OR EXISTS (SELECT 1 FROM settlements.settlement_transfers
                   WHERE settlement_id = s.id AND status IN ('VERIFIED', 'DECLARED'))
        OR EXISTS (SELECT 1 FROM settlements.cheques WHERE settlement_id = s.id AND voided_at IS NULL
                   AND (kind = 'PHYSICAL' OR verification_status IN ('VERIFIED', 'DECLARED')))
        OR EXISTS (SELECT 1 FROM settlements.expenses WHERE settlement_id = s.id AND voided_at IS NULL)
        OR EXISTS (SELECT 1 FROM settlements.pending_collections WHERE settlement_id = s.id AND voided_at IS NULL)
        {remittances}OR EXISTS (SELECT 1 FROM settlements.settlement_differences
                   WHERE settlement_id = s.id AND settlement_version = c.settlement_version)
        OR EXISTS (SELECT 1 FROM settlements.financial_operations o
                   WHERE o.type = 'SETTLEMENT_CLOSE' AND o.source_type = 'settlement' AND o.source_id = s.id
                   AND NOT EXISTS (SELECT 1 FROM settlements.financial_operations r WHERE r.reverses_operation_id = o.id))
        OR c.snapshot->>'type' IS DISTINCT FROM 'OUTLET'
        OR c.snapshot->>'outlet_id' IS DISTINCT FROM s.outlet_id::text
        OR c.snapshot->>'version' IS DISTINCT FROM c.settlement_version::text
        OR EXISTS (SELECT 1 FROM unnest(ARRAY['expected_amount', 'cash', 'additions', 'transfers_verified', 'cheques',
                   'expenses', 'pending', 'difference']) k WHERE c.snapshot->>k IS DISTINCT FROM '0.00')
        {snapshot}THEN
        RAISE EXCEPTION 'settlement %: an administrative zero close needs a boca settlement of $0 without movements',
            c.settlement_id USING ERRCODE = 'check_violation';
    END IF;
    IF c.reversal_operation_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations r JOIN settlements.financial_operations o
            ON o.id = r.reverses_operation_id
        WHERE r.id = c.reversal_operation_id AND r.type = 'REVERSAL' AND o.type = 'SETTLEMENT_CLOSE'
            AND o.source_type = 'settlement' AND o.source_id = s.id
    ) THEN
        RAISE EXCEPTION 'settlement %: the reversal of an administrative close is not of its previous close', s.id
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;
"""
ZERO_CLOSE = ZERO_CLOSE_BODY.format(
    remittances="OR EXISTS (SELECT 1 FROM settlements.outlet_remittances\n"
    "                   WHERE settlement_id = s.id AND reversal_operation_id IS NULL)\n        ",
    snapshot="OR coalesce(c.snapshot->>'remittances', '0.00') <> '0.00'\n    ",
)
ZERO_CLOSE_0023 = ZERO_CLOSE_BODY.format(remittances="", snapshot="")

# Downgrade only while nothing of R7-3 exists: a remittance, its operation or a boca's account would not fit the
# previous schema.
DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.outlet_remittances)
        OR EXISTS (SELECT 1 FROM settlements.financial_operations WHERE type = 'OUTLET_REMITTANCE')
        OR EXISTS (SELECT 1 FROM settlements.accounts WHERE kind = 'OUTLET_ADVANCES')
    THEN
        RAISE EXCEPTION 'outlet remittances exist: downgrade of 0024 refused' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_settlements_outlet_advances_balance ON settlements.settlements;
DROP TRIGGER trg_outlet_remittances_balance ON settlements.outlet_remittances;
DROP TRIGGER trg_ledger_entries_outlet_advances_balance ON settlements.ledger_entries;
DROP FUNCTION settlements.check_outlet_advances_balance();
DROP FUNCTION settlements.assert_outlet_advances(bigint);
DROP TRIGGER trg_settlement_closing_remittances_match_ledger ON settlements.settlement_closing_remittances;
DROP TRIGGER trg_financial_operations_close_remittances ON settlements.financial_operations;
DROP FUNCTION settlements.check_close_remittances();
DROP TRIGGER trg_settlement_closing_remittances_check ON settlements.settlement_closing_remittances;
DROP FUNCTION settlements.check_closing_remittance();
DROP TRIGGER trg_financial_operations_outlet_remittance ON settlements.financial_operations;
DROP TRIGGER trg_outlet_remittances_operation ON settlements.outlet_remittances;
DROP FUNCTION settlements.check_outlet_remittance_operation();
DROP FUNCTION settlements.verify_outlet_remittance(bigint);
DROP TRIGGER trg_ledger_entries_outlet_advances ON settlements.ledger_entries;
DROP FUNCTION settlements.check_outlet_advances_leg();
DROP TRIGGER trg_settlement_closing_remittances_no_change ON settlements.settlement_closing_remittances;
DROP TRIGGER trg_outlet_remittances_no_delete ON settlements.outlet_remittances;
DROP TRIGGER trg_outlet_remittances_check ON settlements.outlet_remittances;
DROP FUNCTION settlements.check_outlet_remittance();
DROP TRIGGER trg_accounts_outlet_advances ON settlements.accounts;
DROP FUNCTION settlements.check_outlet_advances_account();
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def _fk(table: str, column: str, referent: str, referent_schema: str = "settlements") -> None:
    op.create_foreign_key(
        op.f(f"fk_{table}_{column}_{referent}"),
        table,
        referent,
        [column],
        ["id"],
        source_schema="settlements",
        referent_schema=referent_schema,
        ondelete="RESTRICT",
    )


def upgrade() -> None:
    """Upgrade schema."""
    # «Entregas a cuenta — {Boca}»: one per boca, created on first use (not seeded).
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    op.add_column("accounts", sa.Column("outlet_id", sa.BigInteger(), nullable=True), schema="settlements")
    _fk("accounts", "outlet_id", "outlets", "public")
    op.create_check_constraint(
        op.f("ck_accounts_outlet_link"),
        "accounts",
        "(kind = 'OUTLET_ADVANCES') = (outlet_id IS NOT NULL)",
        schema="settlements",
    )
    op.create_check_constraint(
        op.f("ck_accounts_outlet_advances_may_be_negative"),
        "accounts",
        "kind <> 'OUTLET_ADVANCES' OR allow_negative",
        schema="settlements",
    )
    op.create_index(
        "uq_accounts_outlet_id",
        "accounts",
        ["outlet_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("outlet_id IS NOT NULL"),
    )

    # OUTLET_REMITTANCE: exactly one per remittance.
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    op.create_index(
        "uq_financial_operations_outlet_remittance",
        "financial_operations",
        ["source_type", "source_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type = 'OUTLET_REMITTANCE'"),
    )

    op.create_table(
        "outlet_remittances",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("outlet_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("destination_account_id", sa.BigInteger(), nullable=False),
        sa.Column("delivered_by_employee_id", sa.BigInteger(), nullable=True),
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
        sa.CheckConstraint("amount > 0", name=op.f("ck_outlet_remittances_amount_positive")),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_outlet_remittances_fingerprint_format")
        ),
        sa.CheckConstraint(
            "reference IS NULL OR length(btrim(reference)) > 0", name=op.f("ck_outlet_remittances_reference_not_blank")
        ),
        sa.CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_by IS NULL) AND (reversed_by IS NULL) = (reversed_at IS NULL) "
            "AND (reversed_at IS NULL) = (reversal_reason IS NULL)",
            name=op.f("ck_outlet_remittances_reversal_fields"),
        ),
        sa.CheckConstraint(
            "reversal_reason IS NULL OR length(btrim(reversal_reason)) > 0",
            name=op.f("ck_outlet_remittances_reversal_reason_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_outlet_remittances_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["outlet_id"], ["outlets.id"], name=op.f("fk_outlet_remittances_outlet_id_outlets"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["destination_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_outlet_remittances_destination_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["delivered_by_employee_id"],
            ["employees.id"],
            name=op.f("fk_outlet_remittances_delivered_by_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_outlet_remittances_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_outlet_remittances_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_outlet_remittances_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_outlet_remittances_created_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outlet_remittances")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_outlet_remittances_client_request_id")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_outlet_remittances_operation_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_outlet_remittances_reversal_operation_id")),
        schema="settlements",
    )
    for column in ("settlement_id", "outlet_id", "destination_account_id", "delivered_by_employee_id"):
        op.create_index(
            op.f(f"ix_settlements_outlet_remittances_{column}"), "outlet_remittances", [column], schema="settlements"
        )

    op.create_table(
        "settlement_closing_remittances",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("close_operation_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("remittance_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_settlement_closing_remittances_amount_positive")),
        sa.ForeignKeyConstraint(
            ["close_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_settlement_closing_remittances_close_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_settlement_closing_remittances_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["remittance_id"],
            ["settlements.outlet_remittances.id"],
            name=op.f("fk_settlement_closing_remittances_remittance_id_outlet_remittances"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_settlement_closing_remittances")),
        sa.UniqueConstraint(
            "close_operation_id", "remittance_id", name="uq_settlement_closing_remittances_close_remittance"
        ),
        schema="settlements",
    )
    for column in ("close_operation_id", "settlement_id", "remittance_id"):
        op.create_index(
            op.f(f"ix_settlements_settlement_closing_remittances_{column}"),
            "settlement_closing_remittances",
            [column],
            schema="settlements",
        )

    op.execute(ACCOUNT_GUARD)
    op.execute(REMITTANCE_GUARDS)
    op.execute(ADVANCES_DISCIPLINE)
    op.execute(REMITTANCE_MATCHES_LEDGER)
    op.execute(CLOSE_CONSOLIDATES)
    op.execute(GLOBAL_BALANCE)
    op.execute(ZERO_CLOSE)


def downgrade() -> None:
    """Downgrade schema. Refused while any remittance, its operation or a boca's account exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute(ZERO_CLOSE_0023)
    op.execute(DROP_TRIGGERS)
    op.drop_table("settlement_closing_remittances", schema="settlements")
    op.drop_table("outlet_remittances", schema="settlements")
    op.drop_index("uq_financial_operations_outlet_remittance", table_name="financial_operations", schema="settlements")
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
    op.drop_index("uq_accounts_outlet_id", table_name="accounts", schema="settlements")
    op.drop_constraint(op.f("ck_accounts_outlet_advances_may_be_negative"), "accounts", schema="settlements")
    op.drop_constraint(op.f("ck_accounts_outlet_link"), "accounts", schema="settlements")
    op.drop_constraint(op.f("fk_accounts_outlet_id_outlets"), "accounts", schema="settlements", type_="foreignkey")
    op.drop_column("accounts", "outlet_id", schema="settlements")
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
