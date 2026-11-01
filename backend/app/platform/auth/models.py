from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import CITEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base, IdMixin, TimestampMixin

ROLES = ("admin", "viewer")  # platform roles: `admin` administers the platform and every module
MODULE_ROLES = ("viewer", "operator", "admin")  # per-module roles, from least to most privileged
# Where a session was opened from, by the real client IP (app/platform/auth/network.py). Audit only, never access.
ACCESS_ORIGINS = ("lan", "tailscale", "local", "other")
SESSION_REVOKE_REASONS = (
    "logout",
    "revoked_by_user",
    "revoked_by_admin",
    "password_changed",
    "password_reset",
    "user_deactivated",
)


class User(IdMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'viewer')", name="role"),
        CheckConstraint("must_change_password OR temporary_password_expires_at IS NULL", name="temporary_password"),
    )

    email: Mapped[str] = mapped_column(CITEXT, unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20))
    active: Mapped[bool] = mapped_column(Boolean, server_default="true")
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # A temporary password only lets its holder choose a new one (S2), until it expires.
    must_change_password: Mapped[bool] = mapped_column(Boolean, server_default="false")
    temporary_password_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # Loaded with the user (few rows) so access checks need no extra query per endpoint.
    module_roles: Mapped[list["UserModuleRole"]] = relationship(lazy="selectin", foreign_keys="UserModuleRole.user_id")


class UserModuleRole(IdMixin, TimestampMixin, Base):
    """Role of a user in one module (ARCHITECTURE.md §8, stage 1). No row means no access."""

    __tablename__ = "user_module_roles"
    __table_args__ = (
        UniqueConstraint("user_id", "module_key"),
        CheckConstraint("role IN ('viewer', 'operator', 'admin')", name="role"),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"))
    module_key: Mapped[str] = mapped_column(String(40))
    role: Mapped[str] = mapped_column(String(20))
    granted_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))


class UserSession(IdMixin, Base):
    """Server-side session. Only a SHA-256 of the cookie token is stored."""

    __tablename__ = "user_sessions"
    __table_args__ = (
        CheckConstraint("access_origin IN ('lan', 'tailscale', 'local', 'other')", name="access_origin"),
        CheckConstraint(
            "revoked_reason IN ('logout', 'revoked_by_user', 'revoked_by_admin', 'password_changed', "
            "'password_reset', 'user_deactivated')",
            name="revoked_reason",
        ),
    )

    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[bytes] = mapped_column(LargeBinary, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    access_origin: Mapped[str | None] = mapped_column(String(20))
    revoked_reason: Mapped[str | None] = mapped_column(String(30))
    revoked_by: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
