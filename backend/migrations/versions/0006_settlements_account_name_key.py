"""settlements: account name key

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-04 05:20:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Two partner accounts that only differ in case, accents or spacing are the same partner (R1-2).
    # Same normalization as `account_name_key` (app/modules/settlements/ledger/models.py).
    op.add_column("accounts", sa.Column("name_key", sa.String(length=120), nullable=True), schema="settlements")
    op.execute(
        "UPDATE settlements.accounts SET name_key = translate("
        "lower(regexp_replace(btrim(name), '\\s+', ' ', 'g')), 'áàäâéèëêíìïîóòöôúùüû', 'aaaaeeeeiiiioooouuuu')"
    )
    op.alter_column("accounts", "name_key", nullable=False, schema="settlements")
    op.create_unique_constraint(op.f("uq_accounts_name_key"), "accounts", ["name_key"], schema="settlements")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("uq_accounts_name_key"), "accounts", schema="settlements", type_="unique")
    op.drop_column("accounts", "name_key", schema="settlements")
