"""settlements: expected amount events

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-26 15:20:09.399982

R9-1 (ADR 011, Enzo's R9-1 approval with adjustments): the Store Sys -> DistriCom contract of the expected amount.

- `expected_amount_events`: every change of the expected amount of a settlement, manual or from Store Sys, with its
  outcome. Store Sys records are unique by (source, external_reference, revision). Nothing is deleted; only a
  PENDING_CONFIRMATION event is decided, once (CONFIRMED, KEPT or SUPERSEDED by a later Store Sys event).
- `settlements.expected_event_id`: the event that set the amount in effect. Existing settlements keep NULL («Manual ·
  anterior a integración Store Sys»); nothing is backfilled. From now on the amount and its origin change only with
  a new event, which the database checks.
- `settlements.expected_source` admits STORE_SYS.
- A DELIVERY settlement may open without its expected amount (adjustment 1): NULL or > 0; closing still requires it.
- `settlement_closings` freezes the expected amount it was made against: amount, origin, event and Store Sys key.
- A settlement with an undecided Store Sys update does not close.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0030"
down_revision: str | Sequence[str] | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_EXPECTED_BY_TYPE = (
    "(type = 'DELIVERY' AND expected_amount IS NOT NULL AND expected_amount > 0) OR "
    "(type = 'OUTLET' AND (expected_amount IS NULL OR expected_amount >= 0))"
)
NEW_EXPECTED_BY_TYPE = (
    "(type = 'DELIVERY' AND (expected_amount IS NULL OR expected_amount > 0)) OR "
    "(type = 'OUTLET' AND (expected_amount IS NULL OR expected_amount >= 0))"
)

# 0023's body plus `expected_event_id`, which a CLOSED settlement never changes either.
FROZEN = """
CREATE OR REPLACE FUNCTION settlements.check_closed_settlement_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'CLOSED' AND NEW.status = 'CLOSED'
        AND (NEW.type, NEW.business_date, NEW.employee_id, NEW.outlet_id, NEW.sequence_number,
             NEW.expected_amount, NEW.expected_source, NEW.expected_event_id, NEW.external_reference, NEW.notes,
             NEW.version)
            IS DISTINCT FROM
            (OLD.type, OLD.business_date, OLD.employee_id, OLD.outlet_id, OLD.sequence_number,
             OLD.expected_amount, OLD.expected_source, OLD.expected_event_id, OLD.external_reference, OLD.notes,
             OLD.version)
    THEN
        RAISE EXCEPTION 'settlements.settlements: settlement % is CLOSED and cannot change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.type, NEW.business_date, NEW.employee_id, NEW.outlet_id, NEW.sequence_number, NEW.client_request_id)
        IS DISTINCT FROM
        (OLD.type, OLD.business_date, OLD.employee_id, OLD.outlet_id, OLD.sequence_number, OLD.client_request_id)
    THEN
        RAISE EXCEPTION 'settlements.settlements: driver, boca, date and number of a settlement never change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
"""
# 0023's body, restored by the downgrade.
OLD_FROZEN = """
CREATE OR REPLACE FUNCTION settlements.check_closed_settlement_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.status = 'CLOSED' AND NEW.status = 'CLOSED'
        AND (NEW.type, NEW.business_date, NEW.employee_id, NEW.outlet_id, NEW.sequence_number,
             NEW.expected_amount, NEW.expected_source, NEW.external_reference, NEW.notes, NEW.version)
            IS DISTINCT FROM
            (OLD.type, OLD.business_date, OLD.employee_id, OLD.outlet_id, OLD.sequence_number,
             OLD.expected_amount, OLD.expected_source, OLD.external_reference, OLD.notes, OLD.version)
    THEN
        RAISE EXCEPTION 'settlements.settlements: settlement % is CLOSED and cannot change', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.type, NEW.business_date, NEW.employee_id, NEW.outlet_id, NEW.sequence_number, NEW.client_request_id)
        IS DISTINCT FROM
        (OLD.type, OLD.business_date, OLD.employee_id, OLD.outlet_id, OLD.sequence_number, OLD.client_request_id)
    THEN
        RAISE EXCEPTION 'settlements.settlements: driver, boca, date and number of a settlement never change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
"""

# Invariants of an event: written against the settlement as it is (its version and the amount in effect), never a
# setting outcome on a settlement that is not OPEN, REJECTED_CLOSED only on a CLOSED one; a pending is decided once,
# only while its settlement is OPEN, and nothing else of an event ever changes. No event is deleted.
EVENT_GUARDS = """
CREATE FUNCTION settlements.check_expected_amount_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st settlements.settlements%ROWTYPE;
BEGIN
    SELECT * INTO st FROM settlements.settlements WHERE id = NEW.settlement_id;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.outcome <> 'PENDING_CONFIRMATION' OR NEW.outcome NOT IN ('CONFIRMED', 'KEPT', 'SUPERSEDED')
            OR NEW.decided_by IS NULL
            OR (NEW.settlement_id, NEW.settlement_version, NEW.source, NEW.external_reference, NEW.revision,
                NEW.request_fingerprint, NEW.reported_at, NEW.client_request_id, NEW.expected_amount,
                NEW.previous_amount, NEW.previous_source, NEW.previous_event_id, NEW.reason, NEW.received_by,
                NEW.received_at, NEW.supersedes_event_id)
               IS DISTINCT FROM
               (OLD.settlement_id, OLD.settlement_version, OLD.source, OLD.external_reference, OLD.revision,
                OLD.request_fingerprint, OLD.reported_at, OLD.client_request_id, OLD.expected_amount,
                OLD.previous_amount, OLD.previous_source, OLD.previous_event_id, OLD.reason, OLD.received_by,
                OLD.received_at, OLD.supersedes_event_id)
        THEN
            RAISE EXCEPTION 'expected_amount_events: event % is decided once, and only a pending one', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        IF NEW.outcome IN ('CONFIRMED', 'KEPT') AND st.status <> 'OPEN' THEN
            RAISE EXCEPTION 'expected_amount_events: settlement % is CLOSED', st.id USING ERRCODE = 'check_violation';
        END IF;
        RETURN NEW;
    END IF;
    IF NEW.settlement_version <> st.version THEN
        RAISE EXCEPTION 'expected_amount_events: event of version % on settlement % at version %',
            NEW.settlement_version, st.id, st.version USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.previous_amount, NEW.previous_source, NEW.previous_event_id)
        IS DISTINCT FROM (st.expected_amount, st.expected_source, st.expected_event_id) THEN
        RAISE EXCEPTION 'expected_amount_events: the previous amount is not the one in effect on settlement %', st.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.outcome = 'REJECTED_CLOSED' AND st.status <> 'CLOSED' THEN
        RAISE EXCEPTION 'expected_amount_events: settlement % is not CLOSED', st.id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.outcome NOT IN ('REJECTED_CLOSED', 'SUPERSEDED') AND st.status <> 'OPEN' THEN
        RAISE EXCEPTION 'expected_amount_events: settlement % is CLOSED', st.id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.outcome IN ('CONFIRMED', 'KEPT') OR (NEW.outcome = 'SUPERSEDED' AND NEW.decided_by IS NOT NULL) THEN
        RAISE EXCEPTION 'expected_amount_events: an event is born undecided' USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.supersedes_event_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM settlements.expected_amount_events
        WHERE id = NEW.supersedes_event_id AND settlement_id = NEW.settlement_id AND outcome = 'SUPERSEDED'
          AND decided_by IS NOT NULL) THEN
        RAISE EXCEPTION 'expected_amount_events: event % supersedes no pending of its settlement', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_expected_amount_events_check BEFORE INSERT OR UPDATE ON settlements.expected_amount_events
    FOR EACH ROW EXECUTE FUNCTION settlements.check_expected_amount_event();
CREATE TRIGGER trg_expected_amount_events_no_delete BEFORE DELETE ON settlements.expected_amount_events
    FOR EACH ROW EXECUTE FUNCTION settlements.forbid_delete();

-- A pending decided SUPERSEDED is named by the later event that replaced it, in the same transaction.
CREATE FUNCTION settlements.check_expected_amount_event_superseded() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM settlements.expected_amount_events WHERE supersedes_event_id = NEW.id) THEN
        RAISE EXCEPTION 'expected_amount_events: pending % was superseded by no event', NEW.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_expected_amount_events_superseded
    AFTER UPDATE ON settlements.expected_amount_events DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW WHEN (NEW.outcome = 'SUPERSEDED') EXECUTE FUNCTION settlements.check_expected_amount_event_superseded();
"""

# Invariants of the settlement: the amount and its origin change only with a new event (never back to none); an
# undecided pending stops the close; and, at commit, the event in effect is one of its own that set exactly that
# amount and origin. An amount inserted without its event is refused at commit.
SETTLEMENT_GUARDS = """
CREATE FUNCTION settlements.check_settlement_expected_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.expected_event_id IS DISTINCT FROM OLD.expected_event_id AND NEW.expected_event_id IS NULL THEN
        RAISE EXCEPTION 'settlements.settlements: the expected amount of settlement % never loses its event', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    IF (NEW.expected_amount, NEW.expected_source) IS DISTINCT FROM (OLD.expected_amount, OLD.expected_source)
        AND NEW.expected_event_id IS NOT DISTINCT FROM OLD.expected_event_id THEN
        RAISE EXCEPTION 'expected_event_required: the expected amount of settlement % changes only with an event',
            OLD.id USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'CLOSED' AND OLD.status <> 'CLOSED' AND EXISTS (
        SELECT 1 FROM settlements.expected_amount_events
        WHERE settlement_id = OLD.id AND outcome = 'PENDING_CONFIRMATION') THEN
        RAISE EXCEPTION 'expected_update_pending: settlement % has a Store Sys update to decide', OLD.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_settlements_expected_change BEFORE UPDATE ON settlements.settlements
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_expected_change();

CREATE FUNCTION settlements.check_settlement_expected_event() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st settlements.settlements%ROWTYPE;
    ev settlements.expected_amount_events%ROWTYPE;
BEGIN
    SELECT * INTO st FROM settlements.settlements WHERE id = NEW.id;
    IF st.expected_event_id IS NULL THEN
        IF TG_OP = 'INSERT' AND st.expected_amount IS NOT NULL THEN
            RAISE EXCEPTION 'expected_event_required: settlement % has an expected amount without its event', st.id
                USING ERRCODE = 'check_violation';
        END IF;
        RETURN NULL;
    END IF;
    SELECT * INTO ev FROM settlements.expected_amount_events WHERE id = st.expected_event_id;
    IF ev.settlement_id <> st.id OR ev.outcome NOT IN ('MANUAL', 'APPLIED', 'MATCHED', 'CONFIRMED')
        OR ev.expected_amount IS DISTINCT FROM st.expected_amount OR ev.source <> st.expected_source THEN
        RAISE EXCEPTION 'expected_event_mismatch: settlement % is not at the amount of its event %', st.id, ev.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER trg_settlements_expected_event
    AFTER INSERT OR UPDATE ON settlements.settlements DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION settlements.check_settlement_expected_event();
"""

# A close freezes the expected amount it was made against, whoever writes it: what is not given is taken from the
# settlement, and what is given must be the settlement's.
CLOSING_GUARD = """
CREATE FUNCTION settlements.freeze_closing_expected() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    st settlements.settlements%ROWTYPE;
    ev settlements.expected_amount_events%ROWTYPE;
BEGIN
    SELECT * INTO st FROM settlements.settlements WHERE id = NEW.settlement_id;
    IF st.expected_event_id IS NOT NULL THEN
        SELECT * INTO ev FROM settlements.expected_amount_events WHERE id = st.expected_event_id;
    END IF;
    IF NEW.expected_source IS NULL THEN
        NEW.expected_amount := st.expected_amount;
        NEW.expected_source := st.expected_source;
        NEW.expected_event_id := st.expected_event_id;
        NEW.expected_reference := ev.external_reference;
        NEW.expected_revision := ev.revision;
    ELSIF (NEW.expected_amount, NEW.expected_source, NEW.expected_event_id, NEW.expected_reference,
           NEW.expected_revision)
        IS DISTINCT FROM
        (st.expected_amount, st.expected_source, st.expected_event_id, ev.external_reference, ev.revision) THEN
        RAISE EXCEPTION 'settlement_closings: the expected amount frozen is not the one of settlement %', st.id
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_settlement_closings_expected BEFORE INSERT ON settlements.settlement_closings
    FOR EACH ROW EXECUTE FUNCTION settlements.freeze_closing_expected();
"""

DROP_TRIGGERS = """
DROP TRIGGER trg_settlement_closings_expected ON settlements.settlement_closings;
DROP FUNCTION settlements.freeze_closing_expected();
DROP TRIGGER trg_settlements_expected_event ON settlements.settlements;
DROP FUNCTION settlements.check_settlement_expected_event();
DROP TRIGGER trg_settlements_expected_change ON settlements.settlements;
DROP FUNCTION settlements.check_settlement_expected_change();
DROP TRIGGER trg_expected_amount_events_superseded ON settlements.expected_amount_events;
DROP FUNCTION settlements.check_expected_amount_event_superseded();
DROP TRIGGER trg_expected_amount_events_no_delete ON settlements.expected_amount_events;
DROP TRIGGER trg_expected_amount_events_check ON settlements.expected_amount_events;
DROP FUNCTION settlements.check_expected_amount_event();
"""

# Downgrade only while nothing of R9-1 exists: no event, no Store Sys origin, no delivery without its expected amount.
DOWNGRADE_GUARD = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM settlements.expected_amount_events)
        OR EXISTS (SELECT 1 FROM settlements.settlements WHERE expected_source <> 'MANUAL')
        OR EXISTS (SELECT 1 FROM settlements.settlements WHERE type = 'DELIVERY' AND expected_amount IS NULL)
    THEN
        RAISE EXCEPTION 'downgrade refused: expected amount events, Store Sys amounts or deliveries without an '
            'expected amount exist' USING ERRCODE = 'restrict_violation';
    END IF;
END
$$;
"""


def _replace_check(table: str, name: str, condition: str) -> None:
    op.drop_constraint(op.f(name), table, schema="settlements", type_="check")
    op.create_check_constraint(op.f(name), table, condition, schema="settlements")


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "expected_amount_events",
        sa.Column("settlement_id", sa.BigInteger(), nullable=False),
        sa.Column("settlement_version", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("outcome", sa.String(length=30), nullable=False),
        sa.Column("external_reference", sa.String(length=100), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=True),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_request_id", sa.Uuid(), nullable=True),
        sa.Column("expected_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("previous_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        sa.Column("previous_source", sa.String(length=20), nullable=True),
        sa.Column("previous_event_id", sa.BigInteger(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("received_by", sa.BigInteger(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("decided_by", sa.BigInteger(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("supersedes_event_id", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.CheckConstraint(
            "(decided_by IS NULL) = (decided_at IS NULL) AND (outcome NOT IN ('CONFIRMED', 'KEPT') OR decided_by IS "
            "NOT NULL) AND (outcome <> 'KEPT' OR length(btrim(coalesce(decision_reason, ''))) > 0) AND "
            "(decision_reason IS NULL OR outcome = 'KEPT') AND (decided_by IS NULL OR outcome IN ('CONFIRMED', "
            "'KEPT', 'SUPERSEDED'))",
            name=op.f("ck_expected_amount_events_decision_fields"),
        ),
        sa.CheckConstraint(
            "(source = 'MANUAL') = (outcome = 'MANUAL')", name=op.f("ck_expected_amount_events_outcome_by_source")
        ),
        sa.CheckConstraint(
            "(source = 'STORE_SYS' AND external_reference IS NOT NULL AND revision IS NOT NULL AND "
            "request_fingerprint IS NOT NULL AND expected_amount IS NOT NULL) OR (source = 'MANUAL' AND "
            "external_reference IS NULL AND revision IS NULL AND request_fingerprint IS NULL AND reported_at IS NULL "
            "AND client_request_id IS NULL)",
            name=op.f("ck_expected_amount_events_fields_by_source"),
        ),
        sa.CheckConstraint(
            "outcome IN ('APPLIED', 'MATCHED', 'PENDING_CONFIRMATION', 'CONFIRMED', 'KEPT', 'REJECTED_CLOSED', "
            "'SUPERSEDED', 'MANUAL')",
            name=op.f("ck_expected_amount_events_outcome"),
        ),
        sa.CheckConstraint(
            "request_fingerprint ~ '^[0-9a-f]{64}$'", name=op.f("ck_expected_amount_events_fingerprint_format")
        ),
        sa.CheckConstraint("source IN ('MANUAL', 'STORE_SYS')", name=op.f("ck_expected_amount_events_source")),
        sa.CheckConstraint("expected_amount >= 0", name=op.f("ck_expected_amount_events_expected_amount_not_negative")),
        sa.CheckConstraint(
            "length(btrim(external_reference)) > 0",
            name=op.f("ck_expected_amount_events_external_reference_not_blank"),
        ),
        sa.CheckConstraint(
            "reason IS NULL OR length(btrim(reason)) > 0", name=op.f("ck_expected_amount_events_reason_not_blank")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_expected_amount_events_revision_positive")),
        sa.ForeignKeyConstraint(
            ["decided_by"], ["users.id"], name=op.f("fk_expected_amount_events_decided_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["previous_event_id"],
            ["settlements.expected_amount_events.id"],
            name=op.f("fk_expected_amount_events_previous_event_id_expected_amount_events"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["received_by"], ["users.id"], name=op.f("fk_expected_amount_events_received_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["settlement_id"],
            ["settlements.settlements.id"],
            name=op.f("fk_expected_amount_events_settlement_id_settlements"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_event_id"],
            ["settlements.expected_amount_events.id"],
            name=op.f("fk_expected_amount_events_supersedes_event_id_expected_amount_events"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_expected_amount_events")),
        sa.UniqueConstraint("supersedes_event_id", name=op.f("uq_expected_amount_events_supersedes_event_id")),
        schema="settlements",
    )
    op.create_index(
        op.f("ix_settlements_expected_amount_events_settlement_id"),
        "expected_amount_events",
        ["settlement_id"],
        unique=False,
        schema="settlements",
    )
    op.create_index(
        "uq_expected_amount_events_one_pending",
        "expected_amount_events",
        ["settlement_id"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("outcome = 'PENDING_CONFIRMATION'"),
    )
    op.create_index(
        "uq_expected_amount_events_store_key",
        "expected_amount_events",
        ["source", "external_reference", "revision"],
        unique=True,
        schema="settlements",
        postgresql_where=sa.text("source = 'STORE_SYS'"),
    )
    op.add_column(
        "settlement_closings",
        sa.Column("expected_amount", sa.Numeric(precision=14, scale=2), nullable=True),
        schema="settlements",
    )
    op.add_column(
        "settlement_closings", sa.Column("expected_source", sa.String(length=20), nullable=True), schema="settlements"
    )
    op.add_column(
        "settlement_closings", sa.Column("expected_event_id", sa.BigInteger(), nullable=True), schema="settlements"
    )
    op.add_column(
        "settlement_closings",
        sa.Column("expected_reference", sa.String(length=100), nullable=True),
        schema="settlements",
    )
    op.add_column(
        "settlement_closings", sa.Column("expected_revision", sa.Integer(), nullable=True), schema="settlements"
    )
    op.create_foreign_key(
        op.f("fk_settlement_closings_expected_event_id_expected_amount_events"),
        "settlement_closings",
        "expected_amount_events",
        ["expected_event_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        op.f("ck_settlement_closings_expected_source"),
        "settlement_closings",
        "expected_source IN ('MANUAL', 'STORE_SYS')",
        schema="settlements",
    )
    op.add_column("settlements", sa.Column("expected_event_id", sa.BigInteger(), nullable=True), schema="settlements")
    op.create_foreign_key(
        "fk_settlements_expected_event_id_expected_amount_events",
        "settlements",
        "expected_amount_events",
        ["expected_event_id"],
        ["id"],
        source_schema="settlements",
        referent_schema="settlements",
        ondelete="RESTRICT",
    )
    _replace_check("settlements", "ck_settlements_expected_source", "expected_source IN ('MANUAL', 'STORE_SYS')")
    _replace_check("settlements", "ck_settlements_expected_amount_by_type", NEW_EXPECTED_BY_TYPE)
    op.execute(FROZEN)
    op.execute(EVENT_GUARDS)
    op.execute(SETTLEMENT_GUARDS)
    op.execute(CLOSING_GUARD)


def downgrade() -> None:
    """Downgrade schema. Refused while any event, Store Sys amount or delivery without its expected amount exists."""
    op.execute(DOWNGRADE_GUARD)
    op.execute(DROP_TRIGGERS)
    op.execute(OLD_FROZEN)
    _replace_check("settlements", "ck_settlements_expected_amount_by_type", OLD_EXPECTED_BY_TYPE)
    _replace_check("settlements", "ck_settlements_expected_source", "expected_source IN ('MANUAL')")
    op.drop_constraint(
        "fk_settlements_expected_event_id_expected_amount_events",
        "settlements",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_column("settlements", "expected_event_id", schema="settlements")
    op.drop_constraint(
        op.f("ck_settlement_closings_expected_source"), "settlement_closings", schema="settlements", type_="check"
    )
    op.drop_constraint(
        op.f("fk_settlement_closings_expected_event_id_expected_amount_events"),
        "settlement_closings",
        schema="settlements",
        type_="foreignkey",
    )
    op.drop_column("settlement_closings", "expected_revision", schema="settlements")
    op.drop_column("settlement_closings", "expected_reference", schema="settlements")
    op.drop_column("settlement_closings", "expected_event_id", schema="settlements")
    op.drop_column("settlement_closings", "expected_source", schema="settlements")
    op.drop_column("settlement_closings", "expected_amount", schema="settlements")
    op.drop_index(
        "uq_expected_amount_events_store_key",
        table_name="expected_amount_events",
        schema="settlements",
        postgresql_where=sa.text("source = 'STORE_SYS'"),
    )
    op.drop_index(
        "uq_expected_amount_events_one_pending",
        table_name="expected_amount_events",
        schema="settlements",
        postgresql_where=sa.text("outcome = 'PENDING_CONFIRMATION'"),
    )
    op.drop_index(
        op.f("ix_settlements_expected_amount_events_settlement_id"),
        table_name="expected_amount_events",
        schema="settlements",
    )
    op.drop_table("expected_amount_events", schema="settlements")
