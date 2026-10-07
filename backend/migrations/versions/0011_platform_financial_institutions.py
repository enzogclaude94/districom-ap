"""platform: financial institutions catalog (BCRA codes)

Revision ID: 0011
Revises: 0010
Create Date: 2026-10-07 16:05:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0011"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Entities of the BCRA list most used by a distributor's customers. The list is reference data, not
# business data: an admin activates, deactivates or adds a missing entity (source MANUAL) from the API.
SEED = (
    (7, "Banco de Galicia y Buenos Aires S.A."),
    (11, "Banco de la Nación Argentina"),
    (14, "Banco de la Provincia de Buenos Aires"),
    (15, "Industrial and Commercial Bank of China (Argentina) S.A.U."),
    (17, "Banco BBVA Argentina S.A."),
    (20, "Banco de la Provincia de Córdoba S.A."),
    (27, "Banco Supervielle S.A."),
    (29, "Banco de la Ciudad de Buenos Aires"),
    (34, "Banco Patagonia S.A."),
    (44, "Banco Hipotecario S.A."),
    (45, "Banco de San Juan S.A."),
    (65, "Banco Municipal de Rosario"),
    (72, "Banco Santander Argentina S.A."),
    (83, "Banco del Chubut S.A."),
    (86, "Banco de Santa Cruz S.A."),
    (93, "Banco de La Pampa S.E.M."),
    (94, "Banco de Corrientes S.A."),
    (97, "Banco Provincia del Neuquén S.A."),
    (143, "Brubank S.A.U."),
    (150, "HSBC Bank Argentina S.A."),
    (191, "Banco Credicoop Cooperativo Limitado"),
    (268, "Banco Provincia de Tierra del Fuego"),
    (285, "Banco Macro S.A."),
    (299, "Banco Comafi S.A."),
    (300, "Banco de Inversión y Comercio Exterior S.A."),
    (301, "Banco Piano S.A."),
    (309, "Banco Rioja S.A.U."),
    (311, "Nuevo Banco del Chaco S.A."),
    (315, "Banco de Formosa S.A."),
    (321, "Banco de Santiago del Estero S.A."),
    (322, "Banco Industrial S.A."),
    (330, "Nuevo Banco de Santa Fe S.A."),
    (386, "Nuevo Banco de Entre Ríos S.A."),
    (389, "Banco Columbia S.A."),
)

# Never deleted; an official entry's code, name and source never change, only `active`. A MANUAL one
# keeps its author and reason.
IMMUTABLE = """
CREATE FUNCTION check_financial_institution_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (NEW.bcra_code, NEW.name, NEW.source, NEW.created_by, NEW.manual_reason, NEW.created_at)
        IS DISTINCT FROM (OLD.bcra_code, OLD.name, OLD.source, OLD.created_by, OLD.manual_reason, OLD.created_at)
    THEN
        RAISE EXCEPTION 'financial_institutions: only active changes (entity %)', OLD.bcra_code
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END
$$;
CREATE TRIGGER trg_financial_institutions_update BEFORE UPDATE ON financial_institutions
    FOR EACH ROW EXECUTE FUNCTION check_financial_institution_update();
CREATE FUNCTION forbid_financial_institution_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'financial_institutions rows are never deleted' USING ERRCODE = 'restrict_violation';
END
$$;
CREATE TRIGGER trg_financial_institutions_no_delete BEFORE DELETE ON financial_institutions
    FOR EACH ROW EXECUTE FUNCTION forbid_financial_institution_delete();
"""


def upgrade() -> None:
    """Upgrade schema."""
    table = op.create_table(
        "financial_institutions",
        sa.Column("bcra_code", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=True),
        sa.Column("manual_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("bcra_code BETWEEN 1 AND 99999", name=op.f("ck_financial_institutions_bcra_code_range")),
        sa.CheckConstraint("length(btrim(name)) > 0", name=op.f("ck_financial_institutions_name_not_blank")),
        sa.CheckConstraint("source IN ('BCRA', 'MANUAL')", name=op.f("ck_financial_institutions_source")),
        sa.CheckConstraint(
            "(source = 'MANUAL') = (created_by IS NOT NULL AND length(btrim(coalesce(manual_reason, ''))) > 0)",
            name=op.f("ck_financial_institutions_manual_has_author"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by"], ["users.id"], name=op.f("fk_financial_institutions_created_by_users"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_financial_institutions")),
        sa.UniqueConstraint("bcra_code", name=op.f("uq_financial_institutions_bcra_code")),
    )
    op.bulk_insert(table, [{"bcra_code": code, "name": name, "source": "BCRA"} for code, name in SEED])
    op.execute(IMMUTABLE)


def downgrade() -> None:
    """Downgrade schema."""
    op.execute("DROP TRIGGER trg_financial_institutions_no_delete ON financial_institutions")
    op.execute("DROP FUNCTION forbid_financial_institution_delete()")
    op.execute("DROP TRIGGER trg_financial_institutions_update ON financial_institutions")
    op.execute("DROP FUNCTION check_financial_institution_update()")
    op.drop_table("financial_institutions")
