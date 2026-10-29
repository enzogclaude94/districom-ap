"""settlements: faster ledger guards

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-29 00:40:00.000000

R9-4 (Enzo's approval of 0031): the same rules, measured faster. Nothing about data changes.

- `assert_outlet_advances` compares a settlement's consolidated remittances with `=` instead of
  `IS NOT DISTINCT FROM`, so the index on `settlement_closing_remittances(close_operation_id)` is used. Same result:
  `close_operation_id` is NOT NULL, and a CLOSED settlement always has its `current_close_operation_id`; with a NULL
  neither form matches a row.
- `check_account_not_negative` keeps 0013's rule (no end-of-day balance from the debit's date on below zero, the
  bank's CHEQUE_REJECTION aside) but reads only the days after the debit: the balance at the end of the debit's day
  is the account's total minus what is dated later (an entry's date is its operation's, which never changes), and
  each later day adds its own sum. The lowest balance, its day and the message are the same as before.
- `ix_ledger_entries_account_id_include_amount`: an account's total is an index-only scan.

The downgrade drops the index and restores 0013's and 0024's functions exactly.
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0031"
down_revision: str | Sequence[str] | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

NOT_NEGATIVE = """
CREATE OR REPLACE FUNCTION settlements.check_account_not_negative() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    negative_allowed boolean;
    account_kind text;
    operation_type text;
    since date;
    lowest numeric;
    lowest_day date;
    total numeric;
    later numeric;
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
    SELECT coalesce(sum(amount), 0) INTO total FROM settlements.ledger_entries WHERE account_id = NEW.account_id;
    SELECT coalesce(sum(e.amount), 0) INTO later FROM settlements.ledger_entries e
        JOIN settlements.financial_operations o ON o.id = e.operation_id
        WHERE e.account_id = NEW.account_id AND o.business_date > since;
    SELECT day, balance INTO lowest_day, lowest FROM (
        SELECT since AS day, total - later AS balance
        UNION ALL
        SELECT o.business_date AS day, total - later + sum(sum(e.amount)) OVER (ORDER BY o.business_date) AS balance
        FROM settlements.ledger_entries e
        JOIN settlements.financial_operations o ON o.id = e.operation_id
        WHERE e.account_id = NEW.account_id AND o.business_date > since
        GROUP BY o.business_date
    ) daily
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

OUTLET_ADVANCES = """
CREATE OR REPLACE FUNCTION settlements.assert_outlet_advances(boca bigint) RETURNS void LANGUAGE plpgsql AS $$
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
                    WHERE close_operation_id = s.current_close_operation_id)
                UNION ALL
                (SELECT remittance_id, amount FROM settlements.settlement_closing_remittances
                    WHERE close_operation_id = s.current_close_operation_id
                 EXCEPT
                 SELECT id, amount FROM settlements.outlet_remittances
                    WHERE settlement_id = s.id AND reversal_operation_id IS NULL))
    ) THEN
        RAISE EXCEPTION 'outlet_advances_inconsistent: a closed settlement of boca % does not consolidate exactly its '
            'remittances in effect', boca USING ERRCODE = 'check_violation';
    END IF;
END
$$;
"""

# 0013's OVERDRAFT, verbatim.
OLD_NOT_NEGATIVE = """
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

# 0024's function, verbatim.
OLD_OUTLET_ADVANCES = """
CREATE OR REPLACE FUNCTION settlements.assert_outlet_advances(boca bigint) RETURNS void LANGUAGE plpgsql AS $$
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
"""


def upgrade() -> None:
    op.create_index(
        "ix_ledger_entries_account_id_include_amount",
        "ledger_entries",
        ["account_id"],
        unique=False,
        schema="settlements",
        postgresql_include=["amount"],
    )
    op.execute(NOT_NEGATIVE)
    op.execute(OUTLET_ADVANCES)


def downgrade() -> None:
    op.execute(OLD_OUTLET_ADVANCES)
    op.execute(OLD_NOT_NEGATIVE)
    op.drop_index("ix_ledger_entries_account_id_include_amount", table_name="ledger_entries", schema="settlements")
