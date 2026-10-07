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

# Snapshot of the official BCRA list (Enzo, R4-1 adjustment 1): API de Cheques v1.0 of the Banco Central de
# la República Argentina, GET https://api.bcra.gob.ar/cheques/v1.0/entidades, fetched on 2026-10-07 and
# checked twice against the response: 59 entities. `codigoEntidad` (an integer: 7, not "007") becomes
# `bcra_code` and `denominacion`, verbatim, `name`. There is no automatic sync: an admin activates,
# deactivates or adds a missing entity (source MANUAL, audited, with a reason) from the API, and an
# official entry never changes (trigger below).
SEED = (
    (5, "The Royal Bank of Scotland N.V."),
    (7, "BANCO DE GALICIA Y BUENOS AIRES S.A."),
    (11, "BANCO DE LA NACION ARGENTINA"),
    (14, "BANCO DE LA PROVINCIA DE BUENOS AIRES"),
    (15, "INDUSTRIAL AND COMMERCIAL BANK OF CHINA"),
    (16, "CITIBANK N.A."),
    (17, "BANCO BBVA ARGENTINA S.A."),
    (18, "MUFG BANK, LTD"),
    (20, "BANCO DE LA PROVINCIA DE CORDOBA S.A."),
    (27, "BANCO SUPERVIELLE S.A."),
    (29, "BANCO DE LA CIUDAD DE BUENOS AIRES"),
    (34, "BANCO PATAGONIA  S.A."),
    (44, "BANCO HIPOTECARIO S.A."),
    (45, "BANCO DE SAN JUAN S.A."),
    (60, "BANCO DEL TUCUMAN S.A."),
    (65, "BANCO MUNICIPAL DE ROSARIO"),
    (72, "BANCO SANTANDER ARGENTINA S.A."),
    (79, "BANCO REGIONAL DE CUYO S.A."),
    (83, "BANCO DEL CHUBUT S.A."),
    (86, "BANCO DE SANTA CRUZ S.A."),
    (93, "LA PAMPA SA"),
    (94, "BANCO DE CORRIENTES S.A."),
    (97, "BANCO PROVINCIA DEL NEUQUEN S.A."),
    (147, "BIBANK S.A."),
    (150, "Banco GGAL SA"),
    (191, "BANCO CREDICOOP COOPERATIVO LIMITADO"),
    (198, "BANCO DE VALORES S.A."),
    (247, "BANCO ROELA S.A."),
    (254, "BANCO MARIVA S.A."),
    (259, "BANCO BMA S.A.U."),
    (266, "BNP PARIBAS"),
    (268, "BANCO PROVINCIA DE TIERRA DEL FUEGO"),
    (277, "BANCO SAENZ S.A."),
    (281, "BANCO MERIDIAN S.A."),
    (285, "BANCO MACRO S.A."),
    (297, "BANCO BANEX S.A."),
    (299, "BANCO COMAFI SOCIEDAD ANONIMA"),
    (301, "BANCO PIANO S.A."),
    (303, "BANCO FINANSUR S.A."),
    (305, "BANCO JULIO SOCIEDAD ANONIMA"),
    (306, "BANCO PRIVADO DE INVERSIONES S.A."),
    (309, "BANCO RIOJA SOCIEDAD ANÓNIMA UNIPERSONAL"),
    (310, "BANCO DEL SOL S.A."),
    (311, "NUEVO BANCO DEL CHACO S. A."),
    (315, "BANCO DE FORMOSA S.A."),
    (319, "BANCO CMF S.A."),
    (321, "BANCO DE SANTIAGO DEL ESTERO S.A."),
    (322, "BANCO INDUSTRIAL"),
    (330, "NUEVO BANCO DE SANTA FE SOCIEDAD ANONIMA"),
    (336, "BANCO BRADESCO ARGENTINA S.A.U."),
    (338, "BANCO DE SERVICIOS Y TRANSACCIONES SAU"),
    (341, "BANCO MASVENTAS S.A."),
    (386, "NUEVO BANCO DE ENTRE RÍOS S.A."),
    (389, "BANCO COLUMBIA S.A."),
    (426, "BANCO BICA S.A."),
    (431, "BANCO COINAG S.A."),
    (432, "BANCO DE COMERCIO S.A."),
    (435, "BANCO SUCREDITO REGIONAL S.A.U."),
    (448, "BANCO DINO S.A."),
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
