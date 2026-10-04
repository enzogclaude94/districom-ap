"""platform: user module roles

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-04 01:18:37.544991

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # Per-module roles, stage 1 of ARCHITECTURE.md §8 (ADR 007).
    op.create_table(
        "user_module_roles",
        sa.Column("user_id", sa.BigInteger(), nullable=False),
        sa.Column("module_key", sa.String(length=40), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("granted_by", sa.BigInteger(), nullable=True),
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("role IN ('viewer', 'operator', 'admin')", name=op.f("ck_user_module_roles_role")),
        sa.ForeignKeyConstraint(
            ["granted_by"], ["users.id"], name=op.f("fk_user_module_roles_granted_by_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_user_module_roles_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_module_roles")),
        sa.UniqueConstraint("user_id", "module_key", name=op.f("uq_user_module_roles_user_id_module_key")),
    )
    # Stage 0 gave every platform viewer read access to Inteligencia Comercial; keep it explicitly.
    # Platform admins need no rows: they are admin in every module.
    op.execute(
        "INSERT INTO user_module_roles (user_id, module_key, role) "
        "SELECT id, 'commercial', 'viewer' FROM users WHERE role = 'viewer'"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_table("user_module_roles")
