from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.platform.auth.schemas import AccessOrigin

PlatformRole = Literal["admin", "viewer"]  # ROLES
ModuleRole = Literal["viewer", "operator", "admin"]  # MODULE_ROLES


def _strip(value: object) -> object:
    return value.strip() if isinstance(value, str) else value


class _Email(BaseModel):
    @field_validator("email", mode="before", check_fields=False)
    @classmethod
    def _normalize_email(cls, value: object) -> object:
        value = _strip(value)
        if isinstance(value, str) and ("@" not in value or value.startswith("@") or value.endswith("@")):
            raise ValueError("Email inválido.")
        return value


class UserCreateIn(_Email):
    """Every field the admin may set: nothing else is accepted (no active flag, no password, no grants)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    full_name: str = Field(min_length=1, max_length=200)
    role: PlatformRole = "viewer"

    _strip_name = field_validator("full_name", mode="before")(_strip)


class UserUpdateIn(_Email):
    model_config = ConfigDict(extra="forbid")

    email: str | None = Field(default=None, min_length=3, max_length=254)
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: PlatformRole | None = None

    _strip_name = field_validator("full_name", mode="before")(_strip)


class AccessIn(BaseModel):
    """A role in a module or a protected area; `role: null` removes it."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=40)
    role: ModuleRole | None


class GrantOut(BaseModel):
    key: str
    name: str
    role: ModuleRole


class UserSummaryOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: PlatformRole
    active: bool
    must_change_password: bool
    last_login_at: datetime | None
    password_changed_at: datetime | None
    created_at: datetime
    # Explicit grants. A platform admin is admin in every module without rows; areas are never implicit.
    modules: list[GrantOut]
    areas: list[GrantOut]
    open_sessions: int


class UserPage(BaseModel):
    items: list[UserSummaryOut]
    total: int


class AdminSessionOut(BaseModel):
    id: int
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    access_origin: AccessOrigin | None
    user_agent: str | None


class ActivityOut(BaseModel):
    occurred_at: datetime
    event_type: str
    actor_id: int | None
    actor_name: str | None
    payload: dict[str, Any]


class UserDetailOut(UserSummaryOut):
    sessions: list[AdminSessionOut]
    activity: list[ActivityOut]


class TemporaryPasswordOut(BaseModel):
    """Shown once: the server keeps only its hash and never shows it again."""

    user: UserSummaryOut
    temporary_password: str
    expires_at: datetime


class CatalogEntryOut(BaseModel):
    key: str
    name: str
    module_key: str | None = None  # the module of a protected area


class AccessCatalogOut(BaseModel):
    modules: list[CatalogEntryOut]
    areas: list[CatalogEntryOut]


class SessionsRevokedOut(BaseModel):
    revoked: int
