"""settlements: create schema

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-04 02:00:00

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Rendiciones owns the `settlements` schema (ADR 007). Its tables arrive from R1 on.
    op.execute("CREATE SCHEMA settlements")


def downgrade() -> None:
    """Downgrade schema."""
    # RESTRICT: refuses to drop the schema if a later migration left tables in it.
    op.execute("DROP SCHEMA settlements RESTRICT")
