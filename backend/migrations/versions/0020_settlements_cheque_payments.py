"""settlements: cheques of the portfolio delivered as a means of payment (R6-2)

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-10 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0020"
down_revision: str | Sequence[str] | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CYCLE_EVENTS = (
    "'DEPOSITED', 'DEPOSIT_REVERSED', 'CREDITED', 'CREDIT_REVERSED', 'REJECTED', 'REJECTION_REVERSED', "
    "'RETURNED', 'RETURN_REVERSED', 'COLLECTED', 'COLLECTION_REVERSED'"
)
# A cheque delivered by a payment (when the payment is published) and recovered from the third party (when
# that payment is reversed with the cheque physically back): history of the cheque, never its state.
PAYMENT_EVENTS = "'DELIVERED', 'RECOVERED_FROM_PAYMENT'"
OLD_EVENTS = (
    "'RECEIVED', 'CORRECTED', 'VERIFIED', 'VERIFICATION_REJECTED', 'VOIDED', 'PUBLISHED', 'WITHDRAWN', "
    "'DESCRIPTIVE_CORRECTED', " + CYCLE_EVENTS
)
NEW_EVENTS = OLD_EVENTS + ", " + PAYMENT_EVENTS
OLD_REASON_EVENTS = (
    "'VOIDED', 'VERIFICATION_REJECTED', 'DESCRIPTIVE_CORRECTED', 'DEPOSIT_REVERSED', 'CREDIT_REVERSED', "
    "'REJECTED', 'REJECTION_REVERSED', 'RETURNED', 'RETURN_REVERSED', 'COLLECTION_REVERSED'"
)
# The recovery carries the reason of the reversal it belongs to.
NEW_REASON_EVENTS = OLD_REASON_EVENTS + ", 'RECOVERED_FROM_PAYMENT'"

# Items of a payment. Written with their payment, before its operation, and never changed after: only their
# reference (an admin's descriptive correction, while the payment is in effect). CASH leaves an active cash
# box, TRANSFER an active bank account. A CHEQUE item (R6-2) is one live cheque that justifies (paper, or a
# VERIFIED ECHEQ) for exactly its face amount: a cheque is never split. Its row is locked FOR UPDATE, so two
# payments, or a payment and a deposit or a return of the service, serialize on it; where the cheque is (in
# the portfolio on the payment's date and ever since) is checked at commit (`verify_payment`).
PAYMENT_ITEM = """
CREATE OR REPLACE FUNCTION settlements.check_payment_item() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    payment record;
    account_kind text;
    c settlements.cheques%ROWTYPE;
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
        SELECT * INTO c FROM settlements.cheques WHERE id = NEW.cheque_id FOR UPDATE;
        IF NOT FOUND OR c.voided_at IS NOT NULL OR NEW.amount <> c.amount
            OR (c.kind = 'ECHEQ' AND c.verification_status IS DISTINCT FROM 'VERIFIED')
        THEN
            RAISE EXCEPTION 'payment_items: cheque % is voided, unverified or not paid for its whole amount',
                NEW.cheque_id USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    SELECT kind INTO account_kind FROM settlements.accounts WHERE id = NEW.account_id AND active;
    IF account_kind IS DISTINCT FROM (CASE NEW.method WHEN 'CASH' THEN 'CASH_BOX' ELSE 'BANK' END) THEN
        RAISE EXCEPTION 'payment_items: account % is not an active account of method %', NEW.account_id,
            NEW.method USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

# At commit: a payment and its PAYMENT operation are exactly each other (0019), now with cheques:
# - the legs are each CASH/TRANSFER item's account -X, each CHEQUE item's «Cartera de cheques» -X naming that
#   cheque, and ONE counter-leg +amount in the technical account of the concept;
# - while the payment is in effect, each of its cheques is in no other payment in effect, and its position in
#   «Cartera de cheques» is never negative at the end of any day from the payment's business date on: it was
#   in the portfolio on that date and nothing else took it out since (the position check of 0012 refuses a
#   cheque that leaves twice, whatever the dates);
# - each cheque has exactly one DELIVERED event of this payment, naming its operation; and exactly one
#   RECOVERED_FROM_PAYMENT naming the reversal when the payment is reversed, none while it is in effect: a
#   payment with cheques is reversed only with every cheque declared recovered, in the same transaction.
MATCHES_LEDGER = """
CREATE OR REPLACE FUNCTION settlements.verify_payment(pid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    p settlements.payments%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    counter_account bigint;
    portfolio bigint := (SELECT id FROM settlements.accounts WHERE kind = 'CHEQUE_PORTFOLIO');
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
        SELECT CASE method WHEN 'CHEQUE' THEN portfolio ELSE account_id END AS account_id, cheque_id,
               -amount AS amount
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
    -- The reversal undoes the whole payment: every cash box, bank and cheque back, leg by leg.
    IF reversal IS NOT NULL AND EXISTS (
        (SELECT account_id, cheque_id, -amount FROM settlements.ledger_entries WHERE operation_id = o.id
         EXCEPT ALL
         SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = reversal)
        UNION ALL
        (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = reversal
         EXCEPT ALL
         SELECT account_id, cheque_id, -amount FROM settlements.ledger_entries WHERE operation_id = o.id)
    ) THEN
        RAISE EXCEPTION 'the reversal of payment % is not exactly its operation undone', pid
            USING ERRCODE = 'check_violation';
    END IF;

    -- Cheques (R6-2). Every cheque item is one delivered cheque, with its own history.
    IF EXISTS (
        (SELECT cheque_id FROM settlements.payment_items WHERE payment_id = pid AND cheque_id IS NOT NULL
         EXCEPT ALL
         SELECT cheque_id FROM settlements.cheque_events
            WHERE payment_id = pid AND event_type = 'DELIVERED' AND operation_id = p.operation_id)
        UNION ALL
        (SELECT cheque_id FROM settlements.cheque_events WHERE payment_id = pid AND event_type = 'DELIVERED'
         EXCEPT ALL
         SELECT cheque_id FROM settlements.payment_items WHERE payment_id = pid AND cheque_id IS NOT NULL)
    ) THEN
        RAISE EXCEPTION 'payment %: each cheque has exactly one DELIVERED event of its operation', pid
            USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
        (SELECT cheque_id FROM settlements.payment_items
            WHERE payment_id = pid AND cheque_id IS NOT NULL AND reversal IS NOT NULL
         EXCEPT ALL
         SELECT cheque_id FROM settlements.cheque_events
            WHERE payment_id = pid AND event_type = 'RECOVERED_FROM_PAYMENT' AND operation_id = reversal)
        UNION ALL
        (SELECT cheque_id FROM settlements.cheque_events
            WHERE payment_id = pid AND event_type = 'RECOVERED_FROM_PAYMENT'
         EXCEPT ALL
         SELECT cheque_id FROM settlements.payment_items
            WHERE payment_id = pid AND cheque_id IS NOT NULL AND reversal IS NOT NULL)
    ) THEN
        RAISE EXCEPTION 'payment %: reversed only with every cheque recovered, once', pid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NULL AND EXISTS (
        SELECT 1 FROM settlements.payment_items i
        JOIN settlements.payment_items j ON j.cheque_id = i.cheque_id AND j.payment_id <> i.payment_id
        JOIN settlements.payments q ON q.id = j.payment_id AND q.reversal_operation_id IS NULL
        WHERE i.payment_id = pid
    ) THEN
        RAISE EXCEPTION 'payment %: one of its cheques is in another payment in effect', pid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NULL AND EXISTS (
        SELECT 1 FROM settlements.payment_items i
        WHERE i.payment_id = pid AND i.cheque_id IS NOT NULL AND (
            SELECT min(daily.balance) FROM (
                SELECT f.business_date AS day, sum(sum(e.amount)) OVER (ORDER BY f.business_date) AS balance
                FROM settlements.ledger_entries e
                JOIN settlements.financial_operations f ON f.id = e.operation_id
                WHERE e.cheque_id = i.cheque_id AND e.account_id = portfolio
                GROUP BY f.business_date
            ) daily WHERE daily.day >= p.business_date) < 0
    ) THEN
        RAISE EXCEPTION 'payment %: a cheque was not in the portfolio on % or left it after', pid,
            p.business_date USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE OR REPLACE FUNCTION settlements.check_payment_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    pid bigint;
BEGIN
    IF TG_TABLE_NAME = 'payments' THEN
        pid := NEW.id;
    ELSIF TG_TABLE_NAME IN ('payment_items', 'cheque_events') THEN
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
CREATE CONSTRAINT TRIGGER trg_cheque_events_payment
    AFTER INSERT ON settlements.cheque_events DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.payment_id IS NOT NULL)
    EXECUTE FUNCTION settlements.check_payment_operation();
"""

# 0019's versions, restored by the downgrade.
PREVIOUS = """
DROP TRIGGER trg_cheque_events_payment ON settlements.cheque_events;

CREATE OR REPLACE FUNCTION settlements.check_payment_item() RETURNS trigger LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION settlements.verify_payment(pid bigint) RETURNS void LANGUAGE plpgsql AS $$
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

CREATE OR REPLACE FUNCTION settlements.check_payment_operation() RETURNS trigger LANGUAGE plpgsql AS $$
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
"""

# The downgrade would lose who received each delivered cheque: refused while any payment used a cheque.
REFUSE_DOWNGRADE = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.payment_items WHERE method = 'CHEQUE')
        OR EXISTS (SELECT 1 FROM settlements.cheque_events WHERE payment_id IS NOT NULL) THEN
        RAISE EXCEPTION 'cheques were delivered in payments: downgrading would lose their history';
    END IF;
END
$$;
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def _event_checks(*, upgrade: bool) -> None:
    events, reasons = (NEW_EVENTS, NEW_REASON_EVENTS) if upgrade else (OLD_EVENTS, OLD_REASON_EVENTS)
    operation_events = CYCLE_EVENTS + (", " + PAYMENT_EVENTS if upgrade else "")
    _replace_check("cheque_events", "ck_cheque_events_event_type", f"event_type IN ({events})")
    _replace_check(
        "cheque_events",
        "ck_cheque_events_reason_required",
        f"event_type NOT IN ({reasons}) OR length(btrim(coalesce(reason, ''))) > 0",
    )
    _replace_check(
        "cheque_events",
        "ck_cheque_events_cycle_operation",
        f"event_type NOT IN ({operation_events}) OR operation_id IS NOT NULL",
    )


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("cheque_events", sa.Column("payment_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_foreign_key(
        op.f("fk_cheque_events_payment_id_payments"),
        "cheque_events",
        "payments",
        ["payment_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_index(
        op.f("ix_settlements_cheque_events_payment_id"), "cheque_events", ["payment_id"], schema="settlements"
    )
    _event_checks(upgrade=True)
    op.create_check_constraint(
        op.f("ck_cheque_events_payment_events"),
        "cheque_events",
        f"(event_type IN ({PAYMENT_EVENTS})) = (payment_id IS NOT NULL)",
        schema="settlements",
    )
    op.execute(PAYMENT_ITEM)
    op.execute(MATCHES_LEDGER)


def downgrade() -> None:
    """Downgrade schema. Refused once a cheque was delivered in a payment."""
    op.execute(REFUSE_DOWNGRADE)
    op.execute(PREVIOUS)
    op.drop_constraint(op.f("ck_cheque_events_payment_events"), "cheque_events", schema="settlements", type_="check")
    _event_checks(upgrade=False)
    op.drop_index(op.f("ix_settlements_cheque_events_payment_id"), table_name="cheque_events", schema="settlements")
    op.drop_constraint(
        op.f("fk_cheque_events_payment_id_payments"), "cheque_events", schema="settlements", type_="foreignkey"
    )
    op.drop_column("cheque_events", "payment_id", schema="settlements")
