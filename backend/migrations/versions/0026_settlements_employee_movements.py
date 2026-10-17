"""settlements: employee movements

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-16 22:00:00.000000

R8-2 (ADR 010, Enzo's R8-2 decisions): consumptions, advances and adjustments of an employee's internal account.
Each movement is the document of its own operation, which moves the employee's `EMPLOYEE` account and one
counterpart:

    CONSUMPTION        EMPLOYEE +X   «Consumos de empleados» (EMPLOYEE_CONSUMPTIONS) -X
    ADVANCE            EMPLOYEE +X   the cash box or the bank that paid it -X
    ADJUSTMENT CHARGE  EMPLOYEE +X   «Ajustes a empleados» (EMPLOYEE_ADJUSTMENTS) -X
    ADJUSTMENT CREDIT  EMPLOYEE -X   «Ajustes a empleados» +X

Both technical accounts are closed: only their own document and its reversal move them. The `EMPLOYEE` account
itself is closed too, now in the database: only a settlement close (R2/R7), a pending write-off (R3), these three
documents and their reversals move it. Each movement keeps the account and the signed amount of its `EMPLOYEE`
leg, so the internal payroll (R8-4) can point at exactly that leg.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0026"
down_revision: str | Sequence[str] | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_KINDS = (
    "'CASH_BOX', 'BANK', 'OWNER_EQUITY', 'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', "
    "'SETTLEMENT_DIFFERENCE', 'EMPLOYEE', 'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', "
    "'CHEQUES_IN_TRANSIT', 'MERCHANDISE_PAYMENTS', 'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS', 'OUTLET_COLLECTIONS', "
    "'OUTLET_ADVANCES'"
)
NEW_KINDS = OLD_KINDS + ", 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS'"
OLD_TECHNICAL = (
    "'OPENING_BALANCE', 'ADJUSTMENT', 'DELIVERY_COLLECTIONS', 'EXPENSE', 'SETTLEMENT_DIFFERENCE', "
    "'PENDING_COLLECTIONS', 'UNCOLLECTIBLE', 'CHEQUE_PORTFOLIO', 'CHEQUES_IN_TRANSIT', 'MERCHANDISE_PAYMENTS', "
    "'THIRD_PARTY_REFUNDS', 'OTHER_PAYMENTS', 'OUTLET_COLLECTIONS'"
)
NEW_TECHNICAL = OLD_TECHNICAL + ", 'EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS'"
OLD_TYPES = (
    "'OPENING_BALANCE', 'CASH_TRANSFER', 'BANK_DEPOSIT', 'BANK_WITHDRAWAL', 'OWNER_CONTRIBUTION', "
    "'OWNER_WITHDRAWAL', 'ADJUSTMENT', 'SETTLEMENT_CLOSE', 'REVERSAL', 'PENDING_COLLECTION', 'PENDING_WRITE_OFF', "
    "'CHEQUE_OPENING_BALANCE', 'CHEQUE_OWNER_CONTRIBUTION', 'CHEQUE_DEPOSIT', 'CHEQUE_CREDIT', 'CHEQUE_REJECTION', "
    "'CHEQUE_RETURN', 'DIRECT_EXPENSE', 'PAYMENT', 'OUTLET_REMITTANCE'"
)
MOVEMENT_TYPES = "'EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT'"
NEW_TYPES = f"{OLD_TYPES}, {MOVEMENT_TYPES}"

# Technical, never available money, outside the cash board (Enzo's R8-2 points 2 and 4). Each one goes negative
# (consumptions) or either way (adjustments), so both may be negative.
SEED_ACCOUNTS = """
INSERT INTO settlements.accounts (code, name, name_key, kind, allow_negative) VALUES
    ('employee_consumptions', 'Consumos de empleados', 'consumos de empleados', 'EMPLOYEE_CONSUMPTIONS', true),
    ('employee_adjustments', 'Ajustes a empleados', 'ajustes a empleados', 'EMPLOYEE_ADJUSTMENTS', true)
"""

# The two technical accounts never change: not their kind, code, state nor whether they may be negative.
ACCOUNT_GUARD = """
CREATE FUNCTION settlements.check_employee_technical_account() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE TRIGGER trg_accounts_employee_technical BEFORE UPDATE ON settlements.accounts
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_technical_account();
"""

# Every leg on an internal account, «Consumos de empleados» or «Ajustes a empleados» is checked on insert against
# the operation it belongs to (a reversal counts as the operation it reverses, with the opposite sign):
# - EMPLOYEE: a settlement close, a pending write-off or one of the three movements (Enzo's R8-2 point 13);
# - EMPLOYEE_CONSUMPTIONS: only an EMPLOYEE_CONSUMPTION, always -X (its reversal +X);
# - EMPLOYEE_ADJUSTMENTS: only an EMPLOYEE_ADJUSTMENT, either way.
LEG_DISCIPLINE = """
CREATE FUNCTION settlements.check_employee_leg() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE TRIGGER trg_ledger_entries_employee BEFORE INSERT ON settlements.ledger_entries
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_leg();
"""

# A movement is born for the employee's own internal account, on a business date that is not in the future (in
# Buenos Aires), with an ADVANCE paid by an active cash box or bank (locked FOR SHARE, so a deactivation serializes
# with it), and in effect, before its operation. Afterwards only two things happen to it, once each: its operation
# is recorded (in the creating transaction) and it is reversed (who, when, the reason). A reversed movement never
# changes; none is deleted.
MOVEMENT_GUARDS = """
CREATE FUNCTION settlements.check_employee_movement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'INSERT' THEN
        IF NOT EXISTS (SELECT 1 FROM settlements.accounts WHERE id = NEW.employee_account_id
                       AND kind = 'EMPLOYEE' AND employee_id = NEW.employee_id) THEN
            RAISE EXCEPTION 'employee_movements: account % is not the internal account of employee %',
                NEW.employee_account_id, NEW.employee_id USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.business_date > (clock_timestamp() AT TIME ZONE 'America/Argentina/Buenos_Aires')::date THEN
            RAISE EXCEPTION 'employee_movements: business date % is in the future', NEW.business_date
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.kind = 'ADVANCE' AND NOT EXISTS (
            SELECT 1 FROM settlements.accounts WHERE id = NEW.funding_account_id
                AND kind IN ('CASH_BOX', 'BANK') AND active FOR SHARE) THEN
            RAISE EXCEPTION 'employee_movements: account % is not an active cash box or bank', NEW.funding_account_id
                USING ERRCODE = 'check_violation';
        END IF;
        IF NEW.operation_id IS NOT NULL OR NEW.reversal_operation_id IS NOT NULL THEN
            RAISE EXCEPTION 'employee_movements: a movement is born in effect, before its operation'
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF OLD.reversal_operation_id IS NOT NULL THEN
        RAISE EXCEPTION 'employee_movements: movement % is reversed and does not change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.kind, NEW.direction, NEW.employee_id, NEW.employee_account_id, NEW.business_date, NEW.amount,
        NEW.employee_amount, NEW.funding_account_id, NEW.description, NEW.reference, NEW.client_request_id,
        NEW.request_fingerprint, NEW.created_by, NEW.created_at)
        IS DISTINCT FROM
       (OLD.kind, OLD.direction, OLD.employee_id, OLD.employee_account_id, OLD.business_date, OLD.amount,
        OLD.employee_amount, OLD.funding_account_id, OLD.description, OLD.reference, OLD.client_request_id,
        OLD.request_fingerprint, OLD.created_by, OLD.created_at)
    THEN
        RAISE EXCEPTION 'employee_movements: movement % is published and never changes; reverse it', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.operation_id IS NOT NULL AND NEW.operation_id IS DISTINCT FROM OLD.operation_id THEN
        RAISE EXCEPTION 'employee_movements: the operation of movement % never changes', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_employee_movements_check BEFORE INSERT OR UPDATE ON settlements.employee_movements
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_movement();
CREATE TRIGGER trg_employee_movements_no_delete BEFORE DELETE ON settlements.employee_movements
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();
"""

# At commit: a movement and its operation are exactly each other. The operation has the type of the movement's
# kind, the movement as source and its business date, and exactly two legs: the employee's internal account with
# `employee_amount` and the counterpart with its opposite (no cheque). The movement is reversed exactly when a
# REVERSAL of that operation exists, on the same business date. An operation of one of the three types without its
# movement, or a reversal of one without the movement marked reversed by it, is refused.
MOVEMENT_MATCHES_LEDGER = """
CREATE FUNCTION settlements.verify_employee_movement(mid bigint) RETURNS void LANGUAGE plpgsql AS $$
DECLARE
    m settlements.employee_movements%ROWTYPE;
    o settlements.financial_operations%ROWTYPE;
    counterpart bigint;
    reversal bigint;
    mismatch boolean;
BEGIN
    SELECT * INTO m FROM settlements.employee_movements WHERE id = mid;
    IF m.operation_id IS NULL THEN
        RAISE EXCEPTION 'employee movement % has no operation', mid USING ERRCODE = 'check_violation';
    END IF;
    SELECT * INTO o FROM settlements.financial_operations WHERE id = m.operation_id;
    IF o.type <> ('EMPLOYEE_' || m.kind) OR o.source_type IS DISTINCT FROM 'employee_movement'
        OR o.source_id IS DISTINCT FROM mid OR o.business_date <> m.business_date THEN
        RAISE EXCEPTION 'operation % is not the operation of employee movement %', o.id, mid
            USING ERRCODE = 'check_violation';
    END IF;
    counterpart := CASE m.kind
        WHEN 'ADVANCE' THEN m.funding_account_id
        WHEN 'CONSUMPTION' THEN (SELECT id FROM settlements.accounts WHERE kind = 'EMPLOYEE_CONSUMPTIONS')
        ELSE (SELECT id FROM settlements.accounts WHERE kind = 'EMPLOYEE_ADJUSTMENTS')
    END;
    SELECT EXISTS (
        (SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id
         EXCEPT ALL
         SELECT * FROM (VALUES (m.employee_account_id, NULL::bigint, m.employee_amount),
                               (counterpart, NULL::bigint, -m.employee_amount)) x(account_id, cheque_id, amount))
        UNION ALL
        (SELECT * FROM (VALUES (m.employee_account_id, NULL::bigint, m.employee_amount),
                               (counterpart, NULL::bigint, -m.employee_amount)) x(account_id, cheque_id, amount)
         EXCEPT ALL
         SELECT account_id, cheque_id, amount FROM settlements.ledger_entries WHERE operation_id = o.id)
    ) INTO mismatch;
    IF counterpart IS NULL OR mismatch THEN
        RAISE EXCEPTION 'operation % does not match employee movement %', o.id, mid USING ERRCODE = 'check_violation';
    END IF;
    SELECT id INTO reversal FROM settlements.financial_operations WHERE reverses_operation_id = o.id;
    IF reversal IS DISTINCT FROM m.reversal_operation_id THEN
        RAISE EXCEPTION 'employee movement % is reversed exactly by the reversal of its operation', mid
            USING ERRCODE = 'check_violation';
    END IF;
    IF reversal IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.financial_operations
        WHERE id = reversal AND type = 'REVERSAL' AND business_date = m.business_date) THEN
        RAISE EXCEPTION 'the reversal of employee movement % keeps its business date', mid
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;

CREATE FUNCTION settlements.check_employee_movement_operation() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    mid bigint;
BEGIN
    IF TG_TABLE_NAME = 'employee_movements' THEN
        mid := NEW.id;
    ELSIF NEW.type IN ('EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT') THEN
        SELECT id INTO mid FROM settlements.employee_movements WHERE operation_id = NEW.id;
        IF mid IS NULL THEN
            RAISE EXCEPTION 'operation % of type % has no employee movement', NEW.id, NEW.type
                USING ERRCODE = 'check_violation';
        END IF;
    ELSE
        SELECT m.id INTO mid FROM settlements.employee_movements m WHERE m.operation_id = NEW.reverses_operation_id;
        IF mid IS NULL THEN
            IF EXISTS (SELECT 1 FROM settlements.financial_operations
                       WHERE id = NEW.reverses_operation_id AND type IN ('EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT')) THEN
                RAISE EXCEPTION 'reversal % of an employee movement without its movement', NEW.id
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NULL;
        END IF;
    END IF;
    PERFORM settlements.verify_employee_movement(mid);
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_employee_movements_operation
    AFTER INSERT OR UPDATE ON settlements.employee_movements DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_employee_movement_operation();
CREATE CONSTRAINT TRIGGER trg_financial_operations_employee_movement
    AFTER INSERT ON settlements.financial_operations DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.type IN ('EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT', 'REVERSAL'))
    EXECUTE FUNCTION settlements.check_employee_movement_operation();
"""

# Before 0026 only a settlement close and a pending write-off (and their reversals) moved an internal account; the
# new discipline is checked against what already exists before it starts.
EXISTING_EMPLOYEE_LEGS = """
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM settlements.ledger_entries e
            JOIN settlements.accounts a ON a.id = e.account_id AND a.kind = 'EMPLOYEE'
            JOIN settlements.financial_operations o ON o.id = e.operation_id
            LEFT JOIN settlements.financial_operations r ON r.id = o.reverses_operation_id
        WHERE coalesce(r.type, o.type) NOT IN ('SETTLEMENT_CLOSE', 'PENDING_WRITE_OFF')
    ) THEN
        RAISE EXCEPTION 'an internal account was moved by another operation type: 0026 refused'
            USING ERRCODE = 'check_violation';
    END IF;
END
$$;
"""

# Downgrade only while nothing of R8-2 exists: a movement, its operation or a leg on the technical accounts would not
# fit the previous schema.
DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.employee_movements)
        OR EXISTS (SELECT 1 FROM settlements.financial_operations
                   WHERE type IN ('EMPLOYEE_CONSUMPTION', 'EMPLOYEE_ADVANCE', 'EMPLOYEE_ADJUSTMENT'))
        OR EXISTS (SELECT 1 FROM settlements.ledger_entries e JOIN settlements.accounts a ON a.id = e.account_id
                   WHERE a.kind IN ('EMPLOYEE_CONSUMPTIONS', 'EMPLOYEE_ADJUSTMENTS'))
    THEN
        RAISE EXCEPTION 'employee movements exist: downgrade of 0026 refused' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_financial_operations_employee_movement ON settlements.financial_operations;
DROP TRIGGER trg_employee_movements_operation ON settlements.employee_movements;
DROP FUNCTION settlements.check_employee_movement_operation();
DROP FUNCTION settlements.verify_employee_movement(bigint);
DROP TRIGGER trg_employee_movements_no_delete ON settlements.employee_movements;
DROP TRIGGER trg_employee_movements_check ON settlements.employee_movements;
DROP FUNCTION settlements.check_employee_movement();
DROP TRIGGER trg_ledger_entries_employee ON settlements.ledger_entries;
DROP FUNCTION settlements.check_employee_leg();
DROP TRIGGER trg_accounts_employee_technical ON settlements.accounts;
DROP FUNCTION settlements.check_employee_technical_account();
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
    op.execute(EXISTING_EMPLOYEE_LEGS)
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({NEW_KINDS})")
    _technical_index(NEW_TECHNICAL)
    op.execute(SEED_ACCOUNTS)

    # One operation per movement; reversals point at it instead.
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({NEW_TYPES})")
    op.create_index(
        "uq_financial_operations_employee_movement",
        "financial_operations",
        ["source_type", "source_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text(f"type IN ({MOVEMENT_TYPES})"),
    )

    op.create_table(
        "employee_movements",
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("direction", sa.String(length=10), nullable=True),
        sa.Column("employee_id", sa.BigInteger(), nullable=False),
        sa.Column("employee_account_id", sa.BigInteger(), nullable=False),
        sa.Column("business_date", sa.Date(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("employee_amount", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("funding_account_id", sa.BigInteger(), nullable=True),
        sa.Column("description", sa.Text(), nullable=False),
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
        sa.CheckConstraint("kind IN ('CONSUMPTION', 'ADVANCE', 'ADJUSTMENT')", name=op.f("ck_employee_movements_kind")),
        sa.CheckConstraint(
            "(kind = 'ADJUSTMENT') = (direction IS NOT NULL) AND (direction IS NULL OR direction IN ('CHARGE', 'CREDIT'))",
            name=op.f("ck_employee_movements_direction"),
        ),
        sa.CheckConstraint(
            "(kind = 'ADVANCE') = (funding_account_id IS NOT NULL)", name=op.f("ck_employee_movements_funding")
        ),
        sa.CheckConstraint("amount > 0", name=op.f("ck_employee_movements_amount_positive")),
        sa.CheckConstraint(
            "employee_amount = CASE WHEN direction = 'CREDIT' THEN -amount ELSE amount END",
            name=op.f("ck_employee_movements_employee_amount"),
        ),
        sa.CheckConstraint(
            "length(btrim(description)) > 0 AND length(description) <= 500",
            name=op.f("ck_employee_movements_description"),
        ),
        sa.CheckConstraint(
            "reference IS NULL OR length(btrim(reference)) > 0", name=op.f("ck_employee_movements_reference_not_blank")
        ),
        sa.CheckConstraint(
            "(reversal_operation_id IS NULL) = (reversed_by IS NULL) AND (reversed_by IS NULL) = (reversed_at IS NULL) "
            "AND (reversed_at IS NULL) = (reversal_reason IS NULL)",
            name=op.f("ck_employee_movements_reversal_fields"),
        ),
        sa.CheckConstraint(
            "reversal_reason IS NULL OR length(btrim(reversal_reason)) > 0",
            name=op.f("ck_employee_movements_reversal_reason_not_blank"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_employee_movements_fingerprint_format")
        ),
        sa.ForeignKeyConstraint(
            ["employee_id"],
            ["employees.id"],
            name=op.f("fk_employee_movements_employee_id_employees"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["employee_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_movements_employee_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["funding_account_id"],
            ["settlements.accounts.id"],
            name=op.f("fk_employee_movements_funding_account_id_accounts"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_movements_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversal_operation_id"],
            ["settlements.financial_operations.id"],
            name=op.f("fk_employee_movements_reversal_operation_id_financial_operations"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_by"], ["users.id"], name=op.f("fk_employee_movements_reversed_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_employee_movements_created_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_employee_movements")),
        sa.UniqueConstraint("client_request_id", name=op.f("uq_employee_movements_client_request_id")),
        sa.UniqueConstraint("operation_id", name=op.f("uq_employee_movements_operation_id")),
        sa.UniqueConstraint("reversal_operation_id", name=op.f("uq_employee_movements_reversal_operation_id")),
        schema="settlements",
    )
    for column in ("employee_id", "funding_account_id"):
        op.create_index(
            op.f(f"ix_settlements_employee_movements_{column}"), "employee_movements", [column], schema="settlements"
        )

    op.execute(ACCOUNT_GUARD)
    op.execute(LEG_DISCIPLINE)
    op.execute(MOVEMENT_GUARDS)
    op.execute(MOVEMENT_MATCHES_LEDGER)


def downgrade() -> None:
    """Downgrade schema. Refused while any movement, its operation or a leg on the technical accounts exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute(DROP_TRIGGERS)
    op.drop_table("employee_movements", schema="settlements")
    op.drop_index("uq_financial_operations_employee_movement", table_name="financial_operations", schema="settlements")
    _replace_check("financial_operations", "ck_financial_operations_type", f"type IN ({OLD_TYPES})")
    op.execute("DELETE FROM settlements.accounts WHERE code IN ('employee_consumptions', 'employee_adjustments')")
    _technical_index(OLD_TECHNICAL)
    _replace_check("accounts", "ck_accounts_kind", f"kind IN ({OLD_KINDS})")
