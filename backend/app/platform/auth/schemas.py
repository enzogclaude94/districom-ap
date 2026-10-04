from pydantic import BaseModel, Field

from app.platform.auth.access import accessible_modules
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


def user_out(user: User) -> UserOut:
    modules = [ModuleAccessOut(key=m.key, name=m.name, role=role) for m, role in accessible_modules(user)]
    return UserOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role, modules=modules)
