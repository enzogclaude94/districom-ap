from typing import Literal

from pydantic import BaseModel, Field

from app.platform.auth.access import accessible_areas, accessible_modules
from app.platform.auth.models import User


class LoginRequest(BaseModel):
    # Plain string: login only looks up an existing account, so format validation adds nothing.
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=256)


class ModuleAccessOut(BaseModel):
    key: str
    name: str
    role: str


class UserOut(BaseModel):
    id: int
    email: str
    full_name: str
    role: str  # platform role
    modules: list[ModuleAccessOut]
    # Protected areas the user was granted (ADR 010); never inherited from the platform role.
    areas: list[ModuleAccessOut] = []


def user_out(user: User) -> UserOut:
    modules = [ModuleAccessOut(key=m.key, name=m.name, role=role) for m, role in accessible_modules(user)]
    areas = [ModuleAccessOut(key=a.key, name=a.name, role=role) for a, role in accessible_areas(user)]
    return UserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role, modules=modules, areas=areas)


class AccessGrantIn(BaseModel):
    """A role in a module or a protected area for the user with this email (platform admin only)."""

    email: str = Field(min_length=3, max_length=254)
    key: str = Field(min_length=1, max_length=40)
    role: Literal["viewer", "operator", "admin"]  # MODULE_ROLES


class AccessRevokeIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    key: str = Field(min_length=1, max_length=40)


class AccessGrantOut(BaseModel):
    user_id: int
    key: str
    role: str | None  # None once revoked
