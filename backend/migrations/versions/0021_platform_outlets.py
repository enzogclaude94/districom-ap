"""platform: outlets master

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-11 03:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0021"
down_revision: str | Sequence[str] | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A boca is never deleted (rendiciones and responsibilities point at it). Its code is its identity next to
# the id and never changes, nor do its author and creation instant; name, description and `active` change
# through the platform API, which audits each change.
GUARDS = """
CREATE FUNCTION check_outlet_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.code IS DISTINCT FROM OLD.code THEN
        RAISE EXCEPTION 'outlets: the code never changes (outlet %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF (NEW.created_by, NEW.created_at) IS DISTINCT FROM (OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'outlets: created_by and created_at never change (outlet %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_outlets_update BEFORE UPDATE ON outlets
    FOR EACH ROW EXECUTE FUNCTION check_outlet_update();
CREATE FUNCTION forbid_outlet_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'outlets rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_outlets_no_delete BEFORE DELETE ON outlets
    FOR EACH ROW EXECUTE FUNCTION forbid_outlet_delete();
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "outlets",
        sa.Column("code", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("name_key", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("code ~ '^[A-Z0-9-]{2,20}$'", name=op.f("ck_outlets_code_format")),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_outlets_name_not_blank")),
        sa.CheckConstraint("length(name_key) > 0", name=op.f("ck_outlets_name_key_not_blank")),
        sa.CheckConstraint(
            "description IS NULL OR length(btrim(description)) > 0", name=op.f("ck_outlets_description_not_blank")
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_outlets_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_outlets_updated_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outlets")),
        sa.UniqueConstraint("code", name=op.f("uq_outlets_code")),
        sa.UniqueConstraint("name_key", name=op.f("uq_outlets_name_key")),
    )
    op.execute(GUARDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_outlets_no_delete ON outlets")
    op.execute("DROP FUNCTION forbid_outlet_delete()")
    op.execute("DROP TRIGGER trg_outlets_update ON outlets")
    op.execute("DROP FUNCTION check_outlet_update()")
    op.drop_table("outlets")
