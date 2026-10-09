"""platform: vehicles master

Revision ID: 0014
Revises: 0013
Create Date: 2026-10-09 01:10:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0014"
down_revision: str | Sequence[str] | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# A vehicle is never deleted (records of every module will point at it) and keeps its author and creation
# instant; plate, description and `active` change through the platform API, which audits each change.
GUARDS = """
CREATE FUNCTION check_vehicle_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.created_by, NEW.created_at) IS DISTINCT FROM (OLD.created_by, OLD.created_at) THEN
        RAISE EXCEPTION 'vehicles: created_by and created_at never change (vehicle %)', OLD.id
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_vehicles_update BEFORE UPDATE ON vehicles
    FOR EACH ROW EXECUTE FUNCTION check_vehicle_update();
CREATE FUNCTION forbid_vehicle_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'vehicles rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_vehicles_no_delete BEFORE DELETE ON vehicles
    FOR EACH ROW EXECUTE FUNCTION forbid_vehicle_delete();
"""


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "vehicles",
        sa.Column("plate", sa.String(length=10), nullable=False),
        sa.Column("plate_key", sa.String(length=10), nullable=False),
        sa.Column("description", sa.String(length=200), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("updated_by", sa.BigInteger(), nullable=False),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("plate ~ '^[A-Z0-9]{5,10}$'", name=op.f("ck_vehicles_plate_normalized")),
        sa.CheckConstraint("plate_key ~ '^[A-Z0-9]{5,10}$'", name=op.f("ck_vehicles_plate_key_normalized")),
        sa.CheckConstraint("length(btrim(description)) > 0", name=op.f("ck_vehicles_description_not_blank")),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_vehicles_created_by_users"), ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["updated_by"], ["users.id"], name=op.f("fk_vehicles_updated_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vehicles")),
        sa.UniqueConstraint("plate_key", name=op.f("uq_vehicles_plate_key")),
    )
    op.execute(GUARDS)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_vehicles_no_delete ON vehicles")
    op.execute("DROP FUNCTION forbid_vehicle_delete()")
    op.execute("DROP TRIGGER trg_vehicles_update ON vehicles")
    op.execute("DROP FUNCTION check_vehicle_update()")
    op.drop_table("vehicles")
