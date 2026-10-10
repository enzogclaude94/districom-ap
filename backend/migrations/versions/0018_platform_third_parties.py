"""platform: third parties master

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-09 23:40:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0018"
down_revision: str | Sequence[str] | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The CUIT check digit (AFIP modulo 11), the same rule as `third_parties/tax_ids.py`: the database refuses
# a CUIT whatever wrote it.
CUIT = """
CREATE FUNCTION public.cuit_is_valid(cuit text) RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    weights int[] := ARRAY[5, 4, 3, 2, 7, 6, 5, 4, 3, 2];
    total int := 0;
    digit int;
BEGIN
    IF cuit IS NULL OR cuit !~ '^[0-9]{11}$' THEN
        RETURN false;
    END IF;
    FOR i IN 1..10 LOOP
        total := total + substr(cuit, i, 1)::int * weights[i];
    END LOOP;
    digit := 11 - total % 11;
    IF digit = 11 THEN
        digit := 0;
    ELSIF digit = 10 THEN
        RETURN false;
    END IF;
    RETURN substr(cuit, 11, 1)::int = digit;
END
$$;
"""

# A third party is never deleted (payments point at it) and keeps its author and creation instant; name,
# CUIT, notes and `active` change through the platform API, which audits each change.
GUARDS = """
CREATE FUNCTION check_third_party_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.created_by, NEW.created_at) IS DISTINCT FROM (OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'third_parties: created_by and created_at never change (third party %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_third_parties_update BEFORE UPDATE ON third_parties
    FOR EACH ROW EXECUTE FUNCTION check_third_party_update();
CREATE FUNCTION forbid_third_party_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'third_parties rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_third_parties_no_delete BEFORE DELETE ON third_parties
    FOR EACH ROW EXECUTE FUNCTION forbid_third_party_delete();
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.execute(CUIT)
    op.create_table(
        "third_parties",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("name_key", sa.String(length=200), nullable=False),
        sa.Column("tax_id", sa.String(length=11), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_third_parties_name_not_blank")),
        sa.CheckConstraint("length(name_key) > 0", name=op.f("ck_third_parties_name_key_not_blank")),
        sa.CheckConstraint(
            "tax_id IS NULL OR public.cuit_is_valid(tax_id)", name=op.f("ck_third_parties_tax_id_valid")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_third_parties_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_third_parties_updated_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_third_parties")),
    )
    op.create_index(op.f("ix_third_parties_name_key"), "third_parties", ["name_key"], unique=False)
    op.create_index(
        "uq_third_parties_tax_id",
        "third_parties",
        ["tax_id"],
        unique=True,
        postgresql_where=sa.text("tax_id IS NOT NULL"),
    )
    op.execute(GUARDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_third_parties_no_delete ON third_parties")
    op.execute("DROP FUNCTION forbid_third_party_delete()")
    op.execute("DROP TRIGGER trg_third_parties_update ON third_parties")
    op.execute("DROP FUNCTION check_third_party_update()")
    op.drop_index("uq_third_parties_tax_id", table_name="third_parties", postgresql_where=sa.text("tax_id IS NOT NULL"))
    op.drop_index(op.f("ix_third_parties_name_key"), table_name="third_parties")
    op.drop_table("third_parties")
    op.execute("DROP FUNCTION public.cuit_is_valid(text)")
