"""settlements: the financial cycle of a cheque (R4-2)

Revision ID: 0013
Revises: 0012
Create Date: 2026-10-07 22:20:28.931483

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0013"
down_revision: str | Sequence[str] | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION'"
)
CYCLE_TYPES = "'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', 'CHEQUE_RETURN'"
NEW_TYPES = OLD_TYPES + ", " + CYCLE_TYPES
OLD_REASON_TYPES = "'ADJUSTMENT', 'REVERSAL', 'PENDING_WRITE_OFF'"
NEW_REASON_TYPES = OLD_REASON_TYPES + ", 'CHEQUE_REJECTION', 'CHEQUE_RETURN'"
OLD_CHEQUE_ORIGINS = "'SETTLEMENT', 'OPENING_BALANCE', 'OWNER_CONTRIBUTION'"
NEW_CHEQUE_ORIGINS = OLD_CHEQUE_ORIGINS + ", 'PENDING_RECEIPT'"
CHEQUE_CYCLE_EVENTS = (
    "'DEPOSITED', 'DEPOSIT_REVERSED', 'CREDITED', 'CREDIT_REVERSED', 'REJECTED', 'REJECTION_REVERSED', "
    "'RETURNED', 'RETURN_REVERSED', 'COLLECTED', 'COLLECTION_REVERSED'"
)
OLD_CHEQUE_EVENTS = (
    "'RECEIVED', 'CORRECTED', 'VERIFIED', 'VERIFICATION_REJECTED', 'VOIDED', 'PUBLISHED', 'WITHDRAWN', "
    "'DESCRIPTIVE_CORRECTED'"
)
NEW_CHEQUE_EVENTS = OLD_CHEQUE_EVENTS + ", " + CHEQUE_CYCLE_EVENTS
OLD_CHEQUE_REASON_EVENTS = "'VOIDED', 'VERIFICATION_REJECTED', 'DESCRIPTIVE_CORRECTED'"
NEW_CHEQUE_REASON_EVENTS = (
    OLD_CHEQUE_REASON_EVENTS + ", 'DEPOSIT_REVERSED', 'CREDIT_REVERSED', 'REJECTED', 'REJECTION_REVERSED', "
    "'RETURNED', 'RETURN_REVERSED', 'COLLECTION_REVERSED'"
)
OLD_PENDING_EVENTS = (
    "'CREATED', 'AMOUNT_CHANGED', 'CUSTOMER_CHANGED', 'RESPONSIBLE_CHANGED', 'REASON_CHANGED', "
    "'EXPECTED_DATE_CHANGED', 'VOIDED', 'PUBLISHED', 'WITHDRAWN', 'RECEIPT_RECORDED', 'RECEIPT_REVERSED', "
    "'RESOLVED', 'RESOLUTION_REVERSED'"
)
NEW_PENDING_EVENTS = OLD_PENDING_EVENTS + ", 'ECHEQ_DECLARED', 'ECHEQ_DECLINED'"
TABLES = ("cheque_deposits", "cheque_credits", "cheque_rejections", "cheque_returns")
ITEM_TABLES = ("cheque_deposit_items", "cheque_credit_items")

# Documents of the cycle are never deleted and their items never change. A document changes only to record
# its reversal: all four fields at once, once, pointing at the REVERSAL of its own operation, and only while
# nothing later stands on it (a deposit whose cheque was credited or rejected, a crediting with a rejection
# after it). Reversing a rejection or a return also voids the pending it created (checked at commit).
APPEND_ONLY = """
CREATE TRIGGER trg_cheque_deposits_no_delete BEFORE DELETE ON settlements.cheque_deposits
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_cheque_credits_no_delete BEFORE DELETE ON settlements.cheque_credits
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_cheque_rejections_no_delete BEFORE DELETE ON settlements.cheque_rejections
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_cheque_returns_no_delete BEFORE DELETE ON settlements.cheque_returns
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_cheque_deposit_items_no_change BEFORE UPDATE OR DELETE ON settlements.cheque_deposit_items
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
CREATE TRIGGER trg_cheque_credit_items_no_change BEFORE UPDATE OR DELETE ON settlements.cheque_credit_items
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

CREATE FUNCTION settlements.cheque_document_type(table_name text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE table_name WHEN 'cheque_deposits' THEN 'CHEQUE_DEPOSIT' WHEN 'cheque_credits' THEN 'CHEQUE_CREDIT'
                           WHEN 'cheque_rejections' THEN 'CHEQUE_REJECTION' WHEN 'cheque_returns' THEN 'CHEQUE_RETURN'
           END
$$;
CREATE FUNCTION settlements.cheque_document_source(doc_type text) RETURNS text LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE doc_type WHEN 'CHEQUE_DEPOSIT' THEN 'cheque_deposit' WHEN 'CHEQUE_CREDIT' THEN 'cheque_credit'
                         WHEN 'CHEQUE_REJECTION' THEN 'cheque_rejection' WHEN 'CHEQUE_RETURN' THEN 'cheque_return'
           END
$$;

CREATE FUNCTION settlements.check_cheque_document_reversal() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    reversal_fields text[] := ARRAY['reversal_operation_id', 'reversed_by', 'reversed_at', 'reversal_reason'];
    doc_type text := settlements.cheque_document_type(TG_TABLE_NAME);
BEGIN
    IF OLD.reversal_operation_id IS NOT NULL OR NEW.reversal_operation_id IS NULL
        OR (to_jsonb(NEW) - reversal_fields) IS DISTINCT FROM (to_jsonb(OLD) - reversal_fields)
    THEN
        RAISE EXCEPTION 'settlements.%: row % only changes to record its reversal, once', TG_TABLE_NAME, OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations r
        JOIN settlements.financial_operations o ON o.id = r.reverses_operation_id
        WHERE r.id = NEW.reversal_operation_id AND o.type = doc_type
            AND o.source_type = settlements.cheque_document_source(doc_type) AND o.source_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'settlements.%: operation % is not the reversal of the operation of row %', TG_TABLE_NAME,
            NEW.reversal_operation_id, NEW.id USING ERRCODE = 'check_violation';
    END IF;
    IF TG_TABLE_NAME = 'cheque_deposits' AND (
        EXISTS (
            SELECT 1 FROM settlements.cheque_deposit_items di
            JOIN settlements.cheque_credit_items ci ON ci.deposit_item_id = di.id
            JOIN settlements.cheque_credits c ON c.id = ci.credit_id
            WHERE di.deposit_id = NEW.id AND c.reversal_operation_id IS NULL
        ) OR EXISTS (
            SELECT 1 FROM settlements.cheque_deposit_items di
            JOIN settlements.cheque_rejections r ON r.deposit_item_id = di.id
            WHERE di.deposit_id = NEW.id AND r.reversal_operation_id IS NULL
        )
    ) OR TG_TABLE_NAME = 'cheque_credits' AND EXISTS (
        SELECT 1 FROM settlements.cheque_credit_items ci
        JOIN settlements.cheque_rejections r ON r.credit_item_id = ci.id
        WHERE ci.credit_id = NEW.id AND r.reversal_operation_id IS NULL
    ) THEN
        RAISE EXCEPTION 'settlements.%: row % has later documents in effect and is not reversed', TG_TABLE_NAME,
            NEW.id USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheque_deposits_reversal BEFORE UPDATE ON settlements.cheque_deposits
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_reversal();
CREATE TRIGGER trg_cheque_credits_reversal BEFORE UPDATE ON settlements.cheque_credits
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_reversal();
CREATE TRIGGER trg_cheque_rejections_reversal BEFORE UPDATE ON settlements.cheque_rejections
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_reversal();
CREATE TRIGGER trg_cheque_returns_reversal BEFORE UPDATE ON settlements.cheque_returns
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_reversal();

CREATE FUNCTION settlements.check_origin_reversal_voids_pending() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.pending_collections p
        WHERE p.voided_at IS NOT NULL AND (
            TG_TABLE_NAME = 'cheque_rejections' AND p.origin_cheque_rejection_id = NEW.id
            OR TG_TABLE_NAME = 'cheque_returns' AND p.origin_cheque_return_id = NEW.id)
    ) THEN
        RAISE EXCEPTION 'settlements.%: reversing row % voids the pending it created', TG_TABLE_NAME, NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_cheque_rejections_voids_pending
    AFTER UPDATE ON settlements.cheque_rejections DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.reversal_operation_id IS NOT NULL)
    EXECUTE FUNCTION settlements.check_origin_reversal_voids_pending();
CREATE CONSTRAINT TRIGGER trg_cheque_returns_voids_pending
    AFTER UPDATE ON settlements.cheque_returns DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.reversal_operation_id IS NOT NULL)
    EXECUTE FUNCTION settlements.check_origin_reversal_voids_pending();
"""

# What each document may contain, checked when it is written:
# - a deposit takes a live cheque that justifies (physical or verified ECHEQ) for its whole amount, to an
#   account of kind BANK, not before it is payable, not after its presentation deadline (an instrument rule:
#   no role or setting bypasses it) and not before it entered the portfolio;
# - a crediting takes cheques deposited by a deposit in effect, to the same bank, not before the deposit;
# - a rejection names the deposit in effect of its cheque and, after a crediting, the crediting in effect and
#   its bank; never before them;
# - a return gives back a live cheque, never before it entered the portfolio.
# Where a cheque is (in the portfolio, deposited) is checked at commit by its position (0012).
DOCUMENT_RULES = """
CREATE FUNCTION settlements.cheque_portfolio_entry(cheque bigint) RETURNS date LANGUAGE sql STABLE AS $$
    SELECT max(o.business_date) FROM settlements.ledger_entries e
    JOIN settlements.accounts a ON a.id = e.account_id
    JOIN settlements.financial_operations o ON o.id = e.operation_id
    WHERE e.cheque_id = cheque AND a.kind = 'CHEQUE_PORTFOLIO' AND e.amount > 0
$$;

CREATE FUNCTION settlements.check_cheque_bank_account() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.bank_account_id AND kind = 'BANK') THEN
        RAISE EXCEPTION 'settlements.%: account % is not a bank', TG_TABLE_NAME, NEW.bank_account_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheque_deposits_bank BEFORE INSERT ON settlements.cheque_deposits
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_bank_account();
CREATE TRIGGER trg_cheque_credits_bank BEFORE INSERT ON settlements.cheque_credits
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_bank_account();

CREATE FUNCTION settlements.check_cheque_deposit_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    c settlements.cheques%ROWTYPE;
    d settlements.cheque_deposits%ROWTYPE;
    payable date;
BEGIN
    SELECT * INTO c FROM settlements.cheques WHERE id = NEW.cheque_id;
    SELECT * INTO d FROM settlements.cheque_deposits WHERE id = NEW.deposit_id;
    IF d.reversal_operation_id IS NOT NULL OR c.voided_at IS NOT NULL OR NEW.amount <> c.amount
        OR (c.kind = 'ECHEQ' AND c.verification_status <> 'VERIFIED')
    THEN
        RAISE EXCEPTION 'cheque %: voided, unverified, of another amount or deposit % reversed', c.id, d.id
            USING ERRCODE = 'check_violation';
    END IF;
    payable := CASE c.payment_type WHEN 'DEFERRED' THEN c.payment_date ELSE c.issue_date END;
    IF d.business_date < payable THEN
        RAISE EXCEPTION 'cheque %: deposited on % before it is payable (%)', c.id, d.business_date, payable
            USING ERRCODE = 'check_violation';
    END IF;
    IF d.business_date > payable + 30 THEN
        RAISE EXCEPTION 'cheque %: presentation expired on %, deposited on %', c.id, payable + 30, d.business_date
            USING ERRCODE = 'check_violation';
    END IF;
    IF d.business_date < settlements.cheque_portfolio_entry(c.id) THEN
        RAISE EXCEPTION 'cheque %: deposited on % before it entered the portfolio', c.id, d.business_date
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheque_deposit_items_check BEFORE INSERT ON settlements.cheque_deposit_items
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_deposit_item();

CREATE FUNCTION settlements.check_cheque_credit_item() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.cheque_deposit_items di
        JOIN settlements.cheque_deposits d ON d.id = di.deposit_id
        JOIN settlements.cheque_credits c ON c.id = NEW.credit_id
        WHERE di.id = NEW.deposit_item_id AND di.cheque_id = NEW.cheque_id AND di.amount = NEW.amount
            AND d.reversal_operation_id IS NULL AND c.reversal_operation_id IS NULL
            AND d.bank_account_id = c.bank_account_id AND c.business_date >= d.business_date
    ) THEN
        RAISE EXCEPTION 'cheque %: credited without its deposit in effect, to another bank or before it', NEW.cheque_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheque_credit_items_check BEFORE INSERT ON settlements.cheque_credit_items
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_credit_item();

CREATE FUNCTION settlements.check_cheque_rejection() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM settlements.cheque_deposit_items di
        JOIN settlements.cheque_deposits d ON d.id = di.deposit_id
        WHERE di.id = NEW.deposit_item_id AND di.cheque_id = NEW.cheque_id AND d.reversal_operation_id IS NULL
            AND NEW.business_date >= d.business_date
    ) OR NEW.stage = 'AFTER_CREDIT' AND NOT EXISTS (
        SELECT 1 FROM settlements.cheque_credit_items ci
        JOIN settlements.cheque_credits c ON c.id = ci.credit_id
        WHERE ci.id = NEW.credit_item_id AND ci.cheque_id = NEW.cheque_id AND ci.deposit_item_id = NEW.deposit_item_id
            AND c.reversal_operation_id IS NULL AND c.bank_account_id = NEW.bank_account_id
            AND NEW.business_date >= c.business_date
    ) THEN
        RAISE EXCEPTION 'cheque %: rejected without its deposit (or crediting) in effect, or before it', NEW.cheque_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheque_rejections_check BEFORE INSERT ON settlements.cheque_rejections
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_rejection();

CREATE FUNCTION settlements.check_cheque_return() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.cheques WHERE id = NEW.cheque_id AND voided_at IS NOT NULL)
        OR NEW.business_date < settlements.cheque_portfolio_entry(NEW.cheque_id)
    THEN
        RAISE EXCEPTION 'cheque %: voided, or returned before it entered the portfolio', NEW.cheque_id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_cheque_returns_check BEFORE INSERT ON settlements.cheque_returns
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_return();
"""

# Checked at commit: every document has exactly its operation, and the operation is exactly the document.
# Its legs, account by account and cheque by cheque:
# - CHEQUE_DEPOSIT: per item, Cartera de cheques -X and Cheques depositados +X, both naming the cheque;
# - CHEQUE_CREDIT: per item, Cheques depositados -X naming the cheque; the bank +the total;
# - CHEQUE_REJECTION in transit: Cheques depositados -X naming the cheque, Pendientes de cobro +X;
#   after crediting: the crediting's bank -X, Pendientes de cobro +X;
# - CHEQUE_RETURN: Cartera de cheques -X naming the cheque, Pendientes de cobro +X;
# and a rejection or a return created exactly one pending for X, with that origin. A REVERSAL of one of
# them exists only with its document marked reversed by it.
DOCUMENT_MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_cheque_document(doc_type text, doc_id bigint) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
    op settlements.financial_operations%ROWTYPE;
    doc_date date;
    portfolio bigint := (SELECT id FROM settlements.accounts WHERE kind = 'CHEQUE_PORTFOLIO');
    transit bigint := (SELECT id FROM settlements.accounts WHERE kind = 'CHEQUES_IN_TRANSIT');
    pendings bigint := (SELECT id FROM settlements.accounts WHERE kind = 'PENDING_COLLECTIONS');
    mismatch boolean;
BEGIN
    SELECT * INTO op FROM settlements.financial_operations
        WHERE type = doc_type AND source_type = settlements.cheque_document_source(doc_type) AND source_id = doc_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'document % % has no operation', doc_type, doc_id USING ERRCODE = 'check_violation';
    END IF;
    IF doc_type = 'CHEQUE_DEPOSIT' THEN
        SELECT business_date INTO doc_date FROM settlements.cheque_deposits WHERE id = doc_id;
        SELECT NOT EXISTS (SELECT 1 FROM settlements.cheque_deposit_items WHERE deposit_id = doc_id) OR EXISTS (
            (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id
             EXCEPT ALL
             (SELECT portfolio, cheque_id, -amount FROM settlements.cheque_deposit_items WHERE deposit_id = doc_id
              UNION ALL
              SELECT transit, cheque_id, amount FROM settlements.cheque_deposit_items WHERE deposit_id = doc_id))
            UNION ALL
            ((SELECT portfolio, cheque_id, -amount FROM settlements.cheque_deposit_items WHERE deposit_id = doc_id
              UNION ALL
              SELECT transit, cheque_id, amount FROM settlements.cheque_deposit_items WHERE deposit_id = doc_id)
             EXCEPT ALL
             SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id)
        ) INTO mismatch;
    ELSIF doc_type = 'CHEQUE_CREDIT' THEN
        SELECT business_date INTO doc_date FROM settlements.cheque_credits WHERE id = doc_id;
        SELECT NOT EXISTS (SELECT 1 FROM settlements.cheque_credit_items WHERE credit_id = doc_id) OR EXISTS (
            (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id
             EXCEPT ALL
             (SELECT transit, cheque_id, -amount FROM settlements.cheque_credit_items WHERE credit_id = doc_id
              UNION ALL
              SELECT c.bank_account_id, NULL::bigint, sum(i.amount) FROM settlements.cheque_credits c
                JOIN settlements.cheque_credit_items i ON i.credit_id = c.id WHERE c.id = doc_id
                GROUP BY c.bank_account_id))
            UNION ALL
            ((SELECT transit, cheque_id, -amount FROM settlements.cheque_credit_items WHERE credit_id = doc_id
              UNION ALL
              SELECT c.bank_account_id, NULL::bigint, sum(i.amount) FROM settlements.cheque_credits c
                JOIN settlements.cheque_credit_items i ON i.credit_id = c.id WHERE c.id = doc_id
                GROUP BY c.bank_account_id)
             EXCEPT ALL
             SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id)
        ) INTO mismatch;
    ELSIF doc_type = 'CHEQUE_REJECTION' THEN
        SELECT business_date INTO doc_date FROM settlements.cheque_rejections WHERE id = doc_id;
        SELECT NOT EXISTS (
            SELECT 1 FROM settlements.pending_collections p JOIN settlements.cheque_rejections r ON r.id = doc_id
            JOIN settlements.cheques c ON c.id = r.cheque_id
            WHERE p.origin_cheque_rejection_id = doc_id AND p.amount = c.amount
        ) OR EXISTS (
            (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id
             EXCEPT ALL
             SELECT x.account_id, x.cheque_id, x.amount FROM settlements.cheque_rejections r
                JOIN settlements.cheques c ON c.id = r.cheque_id
                CROSS JOIN LATERAL (VALUES
                    (CASE r.stage WHEN 'IN_TRANSIT' THEN transit ELSE r.bank_account_id END,
                     CASE r.stage WHEN 'IN_TRANSIT' THEN c.id END, -c.amount),
                    (pendings, NULL::bigint, c.amount)) x(account_id, cheque_id, amount)
                WHERE r.id = doc_id)
            UNION ALL
            (SELECT x.account_id, x.cheque_id, x.amount FROM settlements.cheque_rejections r
                JOIN settlements.cheques c ON c.id = r.cheque_id
                CROSS JOIN LATERAL (VALUES
                    (CASE r.stage WHEN 'IN_TRANSIT' THEN transit ELSE r.bank_account_id END,
                     CASE r.stage WHEN 'IN_TRANSIT' THEN c.id END, -c.amount),
                    (pendings, NULL::bigint, c.amount)) x(account_id, cheque_id, amount)
                WHERE r.id = doc_id
             EXCEPT ALL
             SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id)
        ) INTO mismatch;
    ELSE
        SELECT business_date INTO doc_date FROM settlements.cheque_returns WHERE id = doc_id;
        SELECT NOT EXISTS (
            SELECT 1 FROM settlements.pending_collections p JOIN settlements.cheque_returns r ON r.id = doc_id
            JOIN settlements.cheques c ON c.id = r.cheque_id
            WHERE p.origin_cheque_return_id = doc_id AND p.amount = c.amount
        ) OR EXISTS (
            (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id
             EXCEPT ALL
             SELECT x.account_id, x.cheque_id, x.amount FROM settlements.cheque_returns r
                JOIN settlements.cheques c ON c.id = r.cheque_id
                CROSS JOIN LATERAL (VALUES (portfolio, c.id, -c.amount), (pendings, NULL::bigint, c.amount))
                    x(account_id, cheque_id, amount)
                WHERE r.id = doc_id)
            UNION ALL
            (SELECT x.account_id, x.cheque_id, x.amount FROM settlements.cheque_returns r
                JOIN settlements.cheques c ON c.id = r.cheque_id
                CROSS JOIN LATERAL (VALUES (portfolio, c.id, -c.amount), (pendings, NULL::bigint, c.amount))
                    x(account_id, cheque_id, amount)
                WHERE r.id = doc_id
             EXCEPT ALL
             SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = op.id)
        ) INTO mismatch;
    END IF;
    IF mismatch OR doc_date IS DISTINCT FROM op.business_date THEN
        RAISE EXCEPTION 'operation % does not match % %', op.id, doc_type, doc_id USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_cheque_document_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    doc_type text;
    doc_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'financial_operations' THEN
        IF NEW.source_type IS DISTINCT FROM settlements.cheque_document_source(NEW.type) THEN
            RAISE EXCEPTION 'operation % of type % has no document of its type', NEW.id, NEW.type
                USING ERRCODE = 'check_violation';
        END IF;
        doc_type := NEW.type;
        doc_id := NEW.source_id;
    ELSIF TG_TABLE_NAME = 'cheque_deposit_items' THEN
        doc_type := 'CHEQUE_DEPOSIT';
        doc_id := NEW.deposit_id;
    ELSIF TG_TABLE_NAME = 'cheque_credit_items' THEN
        doc_type := 'CHEQUE_CREDIT';
        doc_id := NEW.credit_id;
    ELSIF TG_TABLE_NAME = 'pending_collections' THEN
        doc_type := CASE NEW.origin_type WHEN 'CHEQUE_REJECTION' THEN 'CHEQUE_REJECTION' ELSE 'CHEQUE_RETURN' END;
        doc_id := coalesce(NEW.origin_cheque_rejection_id, NEW.origin_cheque_return_id);
    ELSE
        doc_type := settlements.cheque_document_type(TG_TABLE_NAME);
        doc_id := NEW.id;
    END IF;
    PERFORM settlements.verify_cheque_document(doc_type, doc_id);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_cheque_document
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', 'CHEQUE_RETURN'))
    EXECUTE FUNCTION settlements.check_cheque_document_operation();
CREATE CONSTRAINT TRIGGER trg_cheque_deposits_operation
    AFTER INSERT ON settlements.cheque_deposits DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_operation();
CREATE CONSTRAINT TRIGGER trg_cheque_deposit_items_operation
    AFTER INSERT ON settlements.cheque_deposit_items DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_operation();
CREATE CONSTRAINT TRIGGER trg_cheque_credits_operation
    AFTER INSERT ON settlements.cheque_credits DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_operation();
CREATE CONSTRAINT TRIGGER trg_cheque_credit_items_operation
    AFTER INSERT ON settlements.cheque_credit_items DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_operation();
CREATE CONSTRAINT TRIGGER trg_cheque_rejections_operation
    AFTER INSERT ON settlements.cheque_rejections DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_operation();
CREATE CONSTRAINT TRIGGER trg_cheque_returns_operation
    AFTER INSERT ON settlements.cheque_returns DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_cheque_document_operation();
CREATE CONSTRAINT TRIGGER trg_pending_collections_cheque_origin
    AFTER INSERT ON settlements.pending_collections DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.origin_type <> 'SETTLEMENT')
    EXECUTE FUNCTION settlements.check_cheque_document_operation();

CREATE FUNCTION settlements.check_cheque_document_reversal_row() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    original settlements.financial_operations%ROWTYPE;
BEGIN
    SELECT * INTO original FROM settlements.financial_operations WHERE id = NEW.reverses_operation_id;
    IF original.type = 'CHEQUE_DEPOSIT' AND NOT EXISTS (
            SELECT 1 FROM settlements.cheque_deposits WHERE id = original.source_id AND reversal_operation_id = NEW.id)
        OR original.type = 'CHEQUE_CREDIT' AND NOT EXISTS (
            SELECT 1 FROM settlements.cheque_credits WHERE id = original.source_id AND reversal_operation_id = NEW.id)
        OR original.type = 'CHEQUE_REJECTION' AND NOT EXISTS (
            SELECT 1 FROM settlements.cheque_rejections WHERE id = original.source_id AND reversal_operation_id = NEW.id)
        OR original.type = 'CHEQUE_RETURN' AND NOT EXISTS (
            SELECT 1 FROM settlements.cheque_returns WHERE id = original.source_id AND reversal_operation_id = NEW.id)
    THEN
        RAISE EXCEPTION 'reversal % of a % without its document marked reversed', NEW.id, original.type
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_financial_operations_cheque_document_reversal
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type = 'REVERSAL')
    EXECUTE FUNCTION settlements.check_cheque_document_reversal_row();
"""

# «Banco en descubierto» (Enzo's R4-2 policy). A bank account still has `allow_negative = false`: no
# ordinary operation may leave any end-of-day balance of the bank negative, from its business date on,
# exactly as in 0005 (a retroactive debit is checked against every later day). The one exception is the
# leg on a BANK account of a CHEQUE_REJECTION: the bank debited a cheque it had credited, an external
# event the company does not decide, so it is booked even if it creates or deepens an overdraft. Incomes
# are never checked (positive legs), so they reduce or cancel an overdraft while it lasts; any other debit
# (a withdrawal, a transfer out, a reversal of a crediting...) needs every end-of-day balance from its date
# on to stay >= 0, so it can neither create an overdraft nor deepen one. A CHEQUE_REJECTION exists only
# with its document (DOCUMENT_MATCHES_LEDGER): the crediting in effect of that cheque, on that bank, for
# the cheque's amount. The account row is still locked, so a rejection serializes with the other debits.
OVERDRAFT = """
CREATE OR REPLACE FUNCTION settlements.check_account_not_negative() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    negative_allowed boolean;
    account_kind text;
    operation_type text;
    since date;
    lowest numeric;
    lowest_day date;
BEGIN
    IF NEW.amount > 0 THEN
        RETURN NULL;
    END IF;
    SELECT allow_negative, kind INTO negative_allowed, account_kind
        FROM settlements.accounts WHERE id = NEW.account_id FOR NO KEY UPDATE;
    IF negative_allowed THEN
        RETURN NULL;
    END IF;
    SELECT business_date, type INTO since, operation_type
        FROM settlements.financial_operations WHERE id = NEW.operation_id;
    IF account_kind = 'BANK' AND operation_type = 'CHEQUE_REJECTION' THEN
        RETURN NULL;
    END IF;
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
"""

# Pending collections with an origin other than a settlement (R4-2): exactly one origin, which never
# changes; in effect for their amount from the operation of their origin until it is reversed (then the
# pending is voided with it). Such a pending only changes its responsible, or is voided once its origin
# is reversed. Receipts and resolutions are dated on or after their pending's origin, and a receipt with
# a cheque takes exactly that cheque (born from this pending) into "Cartera de cheques".
PENDINGS = """
CREATE OR REPLACE FUNCTION settlements.check_pending_collection_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.voided_at IS NOT NULL THEN
        RAISE EXCEPTION 'settlements.pending_collections: pending % is voided and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.origin_type, NEW.settlement_id, NEW.origin_cheque_rejection_id, NEW.origin_cheque_return_id,
        NEW.client_request_id, NEW.request_fingerprint, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM (OLD.origin_type, OLD.settlement_id, OLD.origin_cheque_rejection_id,
                          OLD.origin_cheque_return_id, OLD.client_request_id, OLD.request_fingerprint,
                          OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'settlements.pending_collections: the identity of a pending never changes'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION settlements.check_pending_collection_open() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    current_status text;
    responsible_fields text[] := ARRAY['responsible_employee_id', 'updated_by', 'updated_at'];
    void_fields text[] := ARRAY['voided_by', 'voided_at', 'void_reason'];
BEGIN
    IF TG_OP = 'UPDATE' AND NEW.settlement_id IS DISTINCT FROM OLD.settlement_id THEN
        RAISE EXCEPTION 'settlements.pending_collections: a row cannot move to another settlement'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF TG_OP = 'UPDATE' AND (to_jsonb(NEW) - responsible_fields) = (to_jsonb(OLD) - responsible_fields) THEN
        RETURN NEW;
    END IF;
    IF NEW.origin_type <> 'SETTLEMENT' THEN
        IF TG_OP = 'INSERT' THEN
            RETURN NEW;
        END IF;
        IF NEW.voided_at IS NOT NULL
            AND (to_jsonb(NEW) - responsible_fields - void_fields) = (to_jsonb(OLD) - responsible_fields - void_fields)
            AND (EXISTS (SELECT 1 FROM settlements.cheque_rejections
                         WHERE id = NEW.origin_cheque_rejection_id AND reversal_operation_id IS NOT NULL)
                 OR EXISTS (SELECT 1 FROM settlements.cheque_returns
                            WHERE id = NEW.origin_cheque_return_id AND reversal_operation_id IS NOT NULL))
        THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION 'settlements.pending_collections: pending % only changes its responsible, or is voided '
            'with the reversal of its origin', OLD.id USING ERRCODE = 'restrict_violation';
    END IF;
    SELECT status INTO current_status FROM settlements.settlements WHERE id = NEW.settlement_id;
    IF current_status <> 'OPEN' THEN
        RAISE EXCEPTION 'settlements.pending_collections: the settlement is % and cannot change', current_status
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;

CREATE OR REPLACE FUNCTION settlements.check_pending_applied() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    sid bigint;
    pid bigint;
    bad record;
BEGIN
    IF TG_TABLE_NAME = 'settlements' THEN
        sid := NEW.id;
    ELSIF TG_TABLE_NAME = 'pending_collections' THEN
        pid := NEW.id;
    ELSE
        pid := NEW.pending_id;
    END IF;
    SELECT p.id, effect.amount AS in_effect, applied.amount AS applied INTO bad
    FROM settlements.pending_collections p
    LEFT JOIN settlements.settlements s ON s.id = p.settlement_id
    LEFT JOIN settlements.settlement_closing_pendings cp
        ON cp.pending_id = p.id AND cp.close_operation_id = s.current_close_operation_id
    CROSS JOIN LATERAL (
        SELECT CASE WHEN p.origin_type = 'SETTLEMENT' THEN cp.amount
                    WHEN p.voided_at IS NULL THEN p.amount END AS amount
    ) effect
    CROSS JOIN LATERAL (
        SELECT coalesce((SELECT sum(amount) FROM settlements.pending_collection_receipts
                         WHERE pending_id = p.id AND reversal_operation_id IS NULL), 0)
             + coalesce((SELECT sum(amount) FROM settlements.pending_collection_resolutions
                         WHERE pending_id = p.id AND reversal_operation_id IS NULL), 0) AS amount
    ) applied
    WHERE (p.settlement_id = sid OR p.id = pid)
        AND applied.amount > 0 AND (effect.amount IS NULL OR applied.amount > effect.amount)
    LIMIT 1;
    IF FOUND THEN
        RAISE EXCEPTION 'pending %: applied % exceeds its amount in effect %', bad.id, bad.applied, bad.in_effect
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_pending_collections_applied
    AFTER UPDATE OF voided_at ON settlements.pending_collections DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.origin_type <> 'SETTLEMENT')
    EXECUTE FUNCTION settlements.check_pending_applied();

CREATE OR REPLACE FUNCTION settlements.check_pending_application() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    expected_type text := CASE TG_TABLE_NAME WHEN 'pending_collection_receipts' THEN 'PENDING_COLLECTION'
                                             ELSE 'PENDING_WRITE_OFF' END;
    origin_date date;
BEGIN
    SELECT coalesce(s.business_date, r.business_date, rt.business_date) INTO origin_date
        FROM settlements.pending_collections p
        LEFT JOIN settlements.settlements s ON s.id = p.settlement_id
        LEFT JOIN settlements.cheque_rejections r ON r.id = p.origin_cheque_rejection_id
        LEFT JOIN settlements.cheque_returns rt ON rt.id = p.origin_cheque_return_id
        WHERE p.id = NEW.pending_id;
    IF NEW.business_date < origin_date THEN
        RAISE EXCEPTION 'settlements.%: date % is before the origin of the pending (%)', TG_TABLE_NAME,
            NEW.business_date, origin_date USING ERRCODE = 'check_violation';
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
                    AND e.amount = NEW.amount AND e.cheque_id IS NOT DISTINCT FROM NEW.cheque_id
                    AND a.kind = CASE NEW.method WHEN 'CASH' THEN 'CASH_BOX' WHEN 'TRANSFER' THEN 'BANK'
                                                 ELSE 'CHEQUE_PORTFOLIO' END
            )
            OR NEW.method = 'CHEQUE' AND NOT EXISTS (
                SELECT 1 FROM settlements.cheques c
                WHERE c.id = NEW.cheque_id AND c.origin_pending_id = NEW.pending_id AND c.amount = NEW.amount
            )
        THEN
            RAISE EXCEPTION 'settlements.pending_collection_receipts: operation % does not take % to its destination',
                NEW.operation_id, NEW.method USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NEW;
END
$$;
"""

# The pending a cheque was received for is part of its origin, and never changes. A published cheque out
# of any settlement is voided only once the receipt that brought it in was reversed.
CHEQUE_UPDATE = """
CREATE OR REPLACE FUNCTION settlements.check_cheque_update() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    fixed text[] := ARRAY['kind', 'origin_type', 'settlement_id', 'origin_pending_id', 'received_business_date',
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
    IF published AND OLD.settlement_id IS NULL AND changed && voiding AND NOT EXISTS (
        SELECT 1 FROM settlements.pending_collection_receipts
        WHERE cheque_id = OLD.id AND reversal_operation_id IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'settlements.cheques: cheque % had a financial effect and is not voided', OLD.id
            USING ERRCODE = 'restrict_violation';
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
"""

PREVIOUS_FUNCTIONS = """
CREATE OR REPLACE FUNCTION settlements.check_account_not_negative() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE OR REPLACE FUNCTION settlements.check_pending_collection_update() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE OR REPLACE FUNCTION settlements.check_pending_collection_open() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE OR REPLACE FUNCTION settlements.check_pending_applied() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE OR REPLACE FUNCTION settlements.check_pending_application() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE OR REPLACE FUNCTION settlements.check_cheque_update() RETURNS trigger LANGUAGE plpgsql AS $$
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
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_pending_collections_applied ON settlements.pending_collections;
DROP TRIGGER trg_financial_operations_cheque_document_reversal ON settlements.financial_operations;
DROP FUNCTION settlements.check_cheque_document_reversal_row();
DROP TRIGGER trg_pending_collections_cheque_origin ON settlements.pending_collections;
DROP TRIGGER trg_cheque_returns_operation ON settlements.cheque_returns;
DROP TRIGGER trg_cheque_rejections_operation ON settlements.cheque_rejections;
DROP TRIGGER trg_cheque_credit_items_operation ON settlements.cheque_credit_items;
DROP TRIGGER trg_cheque_credits_operation ON settlements.cheque_credits;
DROP TRIGGER trg_cheque_deposit_items_operation ON settlements.cheque_deposit_items;
DROP TRIGGER trg_cheque_deposits_operation ON settlements.cheque_deposits;
DROP TRIGGER trg_financial_operations_cheque_document ON settlements.financial_operations;
DROP FUNCTION settlements.check_cheque_document_operation();
DROP FUNCTION settlements.verify_cheque_document(text, bigint);
DROP TRIGGER trg_cheque_returns_check ON settlements.cheque_returns;
DROP FUNCTION settlements.check_cheque_return();
DROP TRIGGER trg_cheque_rejections_check ON settlements.cheque_rejections;
DROP FUNCTION settlements.check_cheque_rejection();
DROP TRIGGER trg_cheque_credit_items_check ON settlements.cheque_credit_items;
DROP FUNCTION settlements.check_cheque_credit_item();
DROP TRIGGER trg_cheque_deposit_items_check ON settlements.cheque_deposit_items;
DROP FUNCTION settlements.check_cheque_deposit_item();
DROP TRIGGER trg_cheque_credits_bank ON settlements.cheque_credits;
DROP TRIGGER trg_cheque_deposits_bank ON settlements.cheque_deposits;
DROP FUNCTION settlements.check_cheque_bank_account();
DROP FUNCTION settlements.cheque_portfolio_entry(bigint);
DROP TRIGGER trg_cheque_returns_voids_pending ON settlements.cheque_returns;
DROP TRIGGER trg_cheque_rejections_voids_pending ON settlements.cheque_rejections;
DROP FUNCTION settlements.check_origin_reversal_voids_pending();
DROP TRIGGER trg_cheque_returns_reversal ON settlements.cheque_returns;
DROP TRIGGER trg_cheque_rejections_reversal ON settlements.cheque_rejections;
DROP TRIGGER trg_cheque_credits_reversal ON settlements.cheque_credits;
DROP TRIGGER trg_cheque_deposits_reversal ON settlements.cheque_deposits;
DROP FUNCTION settlements.check_cheque_document_reversal();
DROP FUNCTION settlements.cheque_document_source(text);
DROP FUNCTION settlements.cheque_document_type(text);
DROP TRIGGER trg_cheque_credit_items_no_change ON settlements.cheque_credit_items;
DROP TRIGGER trg_cheque_deposit_items_no_change ON settlements.cheque_deposit_items;
DROP TRIGGER trg_cheque_returns_no_delete ON settlements.cheque_returns;
DROP TRIGGER trg_cheque_rejections_no_delete ON settlements.cheque_rejections;
DROP TRIGGER trg_cheque_credits_no_delete ON settlements.cheque_credits;
DROP TRIGGER trg_cheque_deposits_no_delete ON settlements.cheque_deposits;
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def _checks(*, upgrade: bool) -> None:
    types, reasons = (NEW_TYPES, NEW_REASON_TYPES) if upgrade else (OLD_TYPES, OLD_REASON_TYPES)
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({types})")
    _replace_check(
        "financial_operations",
        "ck_financial_operations_reason_required",
        f"type NOT IN ({reasons}) OR length(btrim(coalesce(description, ''))) > 0",
    )
    origins = NEW_CHEQUE_ORIGINS if upgrade else OLD_CHEQUE_ORIGINS
    _replace_check("cheques", "ck_cheques_origin_type", f"origin_type IN ({origins})")
    events = NEW_CHEQUE_EVENTS if upgrade else OLD_CHEQUE_EVENTS
    _replace_check("cheque_events", "ck_cheque_events_event_type", f"event_type IN ({events})")
    reason_events = NEW_CHEQUE_REASON_EVENTS if upgrade else OLD_CHEQUE_REASON_EVENTS
    _replace_check(
        "cheque_events",
        "ck_cheque_events_reason_required",
        f"event_type NOT IN ({reason_events}) OR length(btrim(coalesce(reason, ''))) > 0",
    )
    pending_events = NEW_PENDING_EVENTS if upgrade else OLD_PENDING_EVENTS
    _replace_check(
        "pending_collection_events", "ck_pending_collection_events_event_type", f"event_type IN ({pending_events})"
    )
    methods = "'CASH', 'TRANSFER', 'CHEQUE'" if upgrade else "'CASH', 'TRANSFER'"
    _replace_check("pending_collection_receipts", "ck_pending_collection_receipts_method", f"method IN ({methods})")


NEW_CHECKS = (
    (
        "cheques",
        "ck_cheques_pending_receipt_origin",
        "(origin_type = 'PENDING_RECEIPT') = (origin_pending_id IS NOT NULL)",
    ),
    (
        "cheque_events",
        "ck_cheque_events_cycle_operation",
        f"event_type NOT IN ({CHEQUE_CYCLE_EVENTS}) OR operation_id IS NOT NULL",
    ),
    (
        "pending_collections",
        "ck_pending_collections_origin_type",
        "origin_type IN ('SETTLEMENT', 'CHEQUE_REJECTION', 'CHEQUE_RETURN')",
    ),
    (
        "pending_collections",
        "ck_pending_collections_one_origin",
        "(origin_type = 'SETTLEMENT') = (settlement_id IS NOT NULL) AND "
        "(origin_type = 'CHEQUE_REJECTION') = (origin_cheque_rejection_id IS NOT NULL) AND "
        "(origin_type = 'CHEQUE_RETURN') = (origin_cheque_return_id IS NOT NULL)",
    ),
    (
        "pending_collection_events",
        "ck_pending_collection_events_settlement_pair",
        "(settlement_id IS NULL) = (settlement_version IS NULL)",
    ),
    (
        "pending_collection_events",
        "ck_pending_collection_events_cheque_events",
        "(event_type IN ('ECHEQ_DECLARED', 'ECHEQ_DECLINED')) = (cheque_id IS NOT NULL)",
    ),
    (
        "pending_collection_receipts",
        "ck_pending_collection_receipts_cheque_method",
        "(method = 'CHEQUE') = (cheque_id IS NOT NULL)",
    ),
)


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "cheque_deposits",
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("slip_number", sa.String(length=40), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
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
            name=op.f("ck_cheque_deposits_reversal_complete"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_cheque_deposits_fingerprint_format")
        ),
        sa.ForeignKeyConstraint(
            ["bank_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_cheque_deposits_bank_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cheque_deposits_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_cheque_deposits_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_cheque_deposits_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheque_deposits")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_cheque_deposits_client_request_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_cheque_deposits_reversal_operation_id")),
        schema="settlements",
    )
    op.create_table(
        "cheque_deposit_items",
        sa.Column("deposit_id", sa.BigInteger(), nullable=False),
        sa.Column("cheque_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_cheque_deposit_items_amount_positive")),
        sa.ForeignKeyConstraint(
            ["cheque_id"],
            ["settlements.cheques.id"],
            name=op.f("fk_cheque_deposit_items_cheque_id_cheques"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deposit_id"],
            ["settlements.cheque_deposits.id"],
            name=op.f("fk_cheque_deposit_items_deposit_id_cheque_deposits"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheque_deposit_items")),
        sa.UniqueConstraint("deposit_id", "cheque_id", name="uq_cheque_deposit_items_deposit_cheque"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cheque_deposit_items_cheque_id"),
        "cheque_deposit_items",
        ["cheque_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cheque_deposit_items_deposit_id"),
        "cheque_deposit_items",
        ["deposit_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "cheque_credits",
        sa.Column("bank_account_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
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
            name=op.f("ck_cheque_credits_reversal_complete"),
        ),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_cheque_credits_fingerprint_format")),
        sa.ForeignKeyConstraint(
            ["bank_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_cheque_credits_bank_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cheque_credits_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_cheque_credits_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_cheque_credits_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheque_credits")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_cheque_credits_client_request_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_cheque_credits_reversal_operation_id")),
        schema="settlements",
    )
    op.create_table(
        "cheque_credit_items",
        sa.Column("credit_id", sa.BigInteger(), nullable=False),
        sa.Column("cheque_id", sa.BigInteger(), nullable=False),
        sa.Column("deposit_item_id", sa.BigInteger(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint("amount > 0", name=op.f("ck_cheque_credit_items_amount_positive")),
        sa.ForeignKeyConstraint(
            ["cheque_id"],
            ["settlements.cheques.id"],
            name=op.f("fk_cheque_credit_items_cheque_id_cheques"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["credit_id"],
            ["settlements.cheque_credits.id"],
            name=op.f("fk_cheque_credit_items_credit_id_cheque_credits"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deposit_item_id"],
            ["settlements.cheque_deposit_items.id"],
            name=op.f("fk_cheque_credit_items_deposit_item_id_cheque_deposit_items"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheque_credit_items")),
        sa.UniqueConstraint("credit_id", "cheque_id", name="uq_cheque_credit_items_credit_cheque"),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cheque_credit_items_cheque_id"),
        "cheque_credit_items",
        ["cheque_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cheque_credit_items_credit_id"),
        "cheque_credit_items",
        ["credit_id"],
        unique=False,
        schema="settlements",
    )
    op.create_table(
        "cheque_rejections",
        sa.Column("cheque_id", sa.BigInteger(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("deposit_item_id", sa.BigInteger(), nullable=False),
        sa.Column("credit_item_id", sa.BigInteger(), nullable=True),
        sa.Column("bank_account_id", sa.BigInteger(), nullable=True),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
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
            name=op.f("ck_cheque_rejections_reversal_complete"),
        ),
        sa.CheckConstraint(
            "(stage = 'AFTER_CREDIT') = (credit_item_id IS NOT NULL) AND (stage = 'AFTER_CREDIT') = (bank_account_id IS NOT NULL)",
            name=op.f("ck_cheque_rejections_stage_links"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_cheque_rejections_fingerprint_format")
        ),
        sa.CheckConstraint("stage IN ('IN_TRANSIT', 'AFTER_CREDIT')", name=op.f("ck_cheque_rejections_stage")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_cheque_rejections_reason_not_blank")),
        sa.ForeignKeyConstraint(
            ["bank_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_cheque_rejections_bank_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["cheque_id"],
            ["settlements.cheques.id"],
            name=op.f("fk_cheque_rejections_cheque_id_cheques"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cheque_rejections_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["credit_item_id"],
            ["settlements.cheque_credit_items.id"],
            name=op.f("fk_cheque_rejections_credit_item_id_cheque_credit_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["deposit_item_id"],
            ["settlements.cheque_deposit_items.id"],
            name=op.f("fk_cheque_rejections_deposit_item_id_cheque_deposit_items"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_cheque_rejections_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_cheque_rejections_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheque_rejections")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_cheque_rejections_client_request_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_cheque_rejections_reversal_operation_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cheque_rejections_cheque_id"),
        "cheque_rejections",
        ["cheque_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_cheque_rejections_live_cheque",
        "cheque_rejections",
        ["cheque_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("reversal_operation_id IS NULL"),
    )
    op.create_table(
        "cheque_returns",
        sa.Column("cheque_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
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
            name=op.f("ck_cheque_returns_reversal_complete"),
        ),
        sa.CheckConstraint("request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_cheque_returns_fingerprint_format")),
        sa.CheckConstraint("length(btrim(reason)) > 0", name=op.f("ck_cheque_returns_reason_not_blank")),
        sa.ForeignKeyConstraint(
            ["cheque_id"],
            ["settlements.cheques.id"],
            name=op.f("fk_cheque_returns_cheque_id_cheques"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_cheque_returns_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_cheque_returns_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_cheque_returns_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cheque_returns")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_cheque_returns_client_request_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_cheque_returns_reversal_operation_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_cheque_returns_cheque_id"),
        "cheque_returns",
        ["cheque_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_cheque_returns_live_cheque",
        "cheque_returns",
        ["cheque_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("reversal_operation_id IS NULL"),
    )
    op.add_column("cheques", sa.Column("origin_pending_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_index(
        op.f("ix_settlements_cheques_origin_pending_id"),
        "cheques",
        ["origin_pending_id"],
        unique=False,
        schema="settlements",
    )
    op.create_foreign_key(
        op.f("fk_cheques_origin_pending_id_pending_collections"),
        "cheques",
        "pending_collections",
        ["origin_pending_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_index(
        "uq_financial_operations_cheque_document",
        "financial_operations",
        ["source_type", "source_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("type IN ('CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', 'CHEQUE_RETURN')"),
    )
    op.add_column(
        "pending_collection_events", sa.Column("cheque_id", sa.BigInteger(), nullable=True), schema="settlements"
    )
    op.alter_column(
        "pending_collection_events", "settlement_id", existing_type=sa.BIGINT(), nullable=True, schema="settlements"
    )
    op.alter_column(
        "pending_collection_events",
        "settlement_version",
        existing_type=sa.INTEGER(),
        nullable=True,
        schema="settlements",
    )
    op.create_foreign_key(
        op.f("fk_pending_collection_events_cheque_id_cheques"),
        "pending_collection_events",
        "cheques",
        ["cheque_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.add_column(
        "pending_collection_receipts", sa.Column("cheque_id", sa.BigInteger(), nullable=True), schema="settlements"
    )
    op.create_unique_constraint(
        op.f("uq_pending_collection_receipts_cheque_id"),
        "pending_collection_receipts",
        ["cheque_id"],
        schema="settlements",
    )
    op.create_foreign_key(
        op.f("fk_pending_collection_receipts_cheque_id_cheques"),
        "pending_collection_receipts",
        "cheques",
        ["cheque_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.add_column(
        "pending_collections",
        sa.Column("origin_type", sa.String(length=20), server_default="SETTLEMENT", nullable=False),
        schema="settlements",
    )
    op.add_column(
        "pending_collections",
        sa.Column("origin_cheque_rejection_id", sa.BigInteger(), nullable=True),
        schema="settlements",
    )
    op.add_column(
        "pending_collections",
        sa.Column("origin_cheque_return_id", sa.BigInteger(), nullable=True),
        schema="settlements",
    )
    op.alter_column(
        "pending_collections", "settlement_id", existing_type=sa.BIGINT(), nullable=True, schema="settlements"
    )
    op.create_unique_constraint(
        op.f("uq_pending_collections_origin_cheque_rejection_id"),
        "pending_collections",
        ["origin_cheque_rejection_id"],
        schema="settlements",
    )
    op.create_unique_constraint(
        op.f("uq_pending_collections_origin_cheque_return_id"),
        "pending_collections",
        ["origin_cheque_return_id"],
        schema="settlements",
    )
    op.create_foreign_key(
        op.f("fk_pending_collections_origin_cheque_return_id_cheque_returns"),
        "pending_collections",
        "cheque_returns",
        ["origin_cheque_return_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_pending_collections_origin_cheque_rejection_id_cheque_rejections"),
        "pending_collections",
        "cheque_rejections",
        ["origin_cheque_rejection_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )

    _checks(upgrade=True)
    for table, name, condition in NEW_CHECKS:
        op.create_check_constraint(op.f(name), table, condition, schema="settlements")
    op.execute(APPEND_ONLY)
    op.execute(DOCUMENT_RULES)
    op.execute(DOCUMENT_MATCHES_LEDGER)
    op.execute(OVERDRAFT)
    op.execute(PENDINGS)
    op.execute(CHEQUE_UPDATE)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute(DROP_TRIGGERS)
    op.execute(PREVIOUS_FUNCTIONS)
    for table, name, _ in NEW_CHECKS:
        op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    _checks(upgrade=False)
    op.drop_constraint(
        op.f("fk_pending_collections_origin_cheque_rejection_id_cheque_rejections"),
        "pending_collections",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_pending_collections_origin_cheque_return_id_cheque_returns"),
        "pending_collections",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_pending_collections_origin_cheque_return_id"),
        "pending_collections",
        schema="settlements",
        type_="unique",
    )
    op.drop_constraint(
        op.f("uq_pending_collections_origin_cheque_rejection_id"),
        "pending_collections",
        schema="settlements",
        type_="unique",
    )
    op.alter_column(
        "pending_collections", "settlement_id", existing_type=sa.BIGINT(), nullable=False, schema="settlements"
    )
    op.drop_column("pending_collections", "origin_cheque_return_id", schema="settlements")
    op.drop_column("pending_collections", "origin_cheque_rejection_id", schema="settlements")
    op.drop_column("pending_collections", "origin_type", schema="settlements")
    op.drop_constraint(
        op.f("fk_pending_collection_receipts_cheque_id_cheques"),
        "pending_collection_receipts",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("uq_pending_collection_receipts_cheque_id"),
        "pending_collection_receipts",
        schema="settlements",
        type_="unique",
    )
    op.drop_column("pending_collection_receipts", "cheque_id", schema="settlements")
    op.drop_constraint(
        op.f("fk_pending_collection_events_cheque_id_cheques"),
        "pending_collection_events",
        schema="settlements",
        type_="foreignkey",
    )
    op.alter_column(
        "pending_collection_events",
        "settlement_version",
        existing_type=sa.INTEGER(),
        nullable=False,
        schema="settlements",
    )
    op.alter_column(
        "pending_collection_events", "settlement_id", existing_type=sa.BIGINT(), nullable=False, schema="settlements"
    )
    op.drop_column("pending_collection_events", "cheque_id", schema="settlements")
    op.drop_index(
        "uq_financial_operations_cheque_document",
        table_name="financial_operations",
        schema="settlements",
        postgresql_where=sa.text("type IN ('CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', 'CHEQUE_RETURN')"),
    )
    op.drop_constraint(
        op.f("fk_cheques_origin_pending_id_pending_collections"), "cheques", schema="settlements", type_="foreignkey"
    )
    op.drop_index(op.f("ix_settlements_cheques_origin_pending_id"), table_name="cheques", schema="settlements")
    op.drop_column("cheques", "origin_pending_id", schema="settlements")
    for table in (
        "cheque_rejections",
        "cheque_returns",
        "cheque_credit_items",
        "cheque_credits",
        "cheque_deposit_items",
        "cheque_deposits",
    ):
        op.drop_table(table, schema="settlements")
