"""platform: password and session security

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-30 23:30:00.000000

S2-1 (Enzo's approval of S2, 2026-10-30):

- `users.must_change_password` and `users.temporary_password_expires_at`: a temporary password (given by an
  administrator or the CLI) only lets its holder choose a new one, and only until it expires.
- `users.password_changed_at`: when the password last changed (shown to administrators, audited).
- `user_sessions.access_origin`: where the session was opened from (LAN, Tailscale, the server itself or other),
  derived from the real client IP. Audit information only: it never grants or denies anything.
- `user_sessions.revoked_reason` / `revoked_by`: why and by whom a session was closed.

Existing users keep their password and every existing session stays valid. The downgrade drops the columns.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0032"
down_revision: str | Sequence[str] | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORIGINS = "access_origin IN ('lan', 'tailscale', 'local', 'other')"
REASONS = (
    "revoked_reason IN ('logout', 'revoked_by_user', 'revoked_by_admin', 'password_changed', 'password_reset', "
    "'user_deactivated')"
)


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("users", sa.Column("must_change_password", sa.Boolean(), server_default="false", nullable=False))
    op.add_column("users", sa.Column("temporary_password_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("users", sa.Column("password_changed_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint(
        op.f("ck_users_temporary_password"), "users", "must_change_password OR temporary_password_expires_at IS NULL"
    )
    op.add_column("user_sessions", sa.Column("access_origin", sa.String(length=20), nullable=True))
    op.add_column("user_sessions", sa.Column("revoked_reason", sa.String(length=30), nullable=True))
    op.add_column("user_sessions", sa.Column("revoked_by", sa.BigInteger(), nullable=True))
    op.create_check_constraint(op.f("ck_user_sessions_access_origin"), "user_sessions", ORIGINS)
    op.create_check_constraint(op.f("ck_user_sessions_revoked_reason"), "user_sessions", REASONS)
    op.create_foreign_key(
        op.f("fk_user_sessions_revoked_by_users"), "user_sessions", "users", ["revoked_by"], ["id"], ondelete="SET NULL"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_constraint(op.f("fk_user_sessions_revoked_by_users"), "user_sessions", type_="foreignkey")
    op.drop_constraint(op.f("ck_user_sessions_revoked_reason"), "user_sessions", type_="check")
    op.drop_constraint(op.f("ck_user_sessions_access_origin"), "user_sessions", type_="check")
    op.drop_column("user_sessions", "revoked_by")
    op.drop_column("user_sessions", "revoked_reason")
    op.drop_column("user_sessions", "access_origin")
    op.drop_constraint(op.f("ck_users_temporary_password"), "users", type_="check")
    op.drop_column("users", "password_changed_at")
    op.drop_column("users", "temporary_password_expires_at")
    op.drop_column("users", "must_change_password")
