"""Access to business modules (ARCHITECTURE.md §8, ADR 006 and 007).

Stage 1: a platform admin (`users.role = admin`) is `admin` in every installed module; any other
user has the role stored in `user_module_roles`, or no access. Endpoints only use
`require_module_access`, so a future per-action stage changes this file alone.
"""

from collections.abc import Callable

from app.core.errors import AppError
from app.platform.auth.deps import CurrentUser
from app.platform.auth.models import MODULE_ROLES, User
from app.platform.modules import ModuleSpec, installed_modules


def module_role(user: User, module_key: str) -> str | None:
    """Role of `user` in the module, or None without access."""
    if module_key not in {module.key for module in installed_modules()}:
        return None
    if user.role == "admin":
        return "admin"
    for grant in user.module_roles:
        if grant.module_key == module_key:
            return grant.role
    return None


def has_module_access(user: User, module_key: str, min_role: str = "viewer") -> bool:
    role = module_role(user, module_key)
    return role is not None and MODULE_ROLES.index(role) >= MODULE_ROLES.index(min_role)


def require_module_access(module_key: str, min_role: str = "viewer") -> Callable[[User], User]:
    if min_role not in MODULE_ROLES:
        raise ValueError(f"Unknown module role: {min_role}")

    def _check(user: CurrentUser) -> User:
        if not has_module_access(user, module_key, min_role):
            raise AppError(403, "forbidden", "No tenés permisos para esta acción.")
        return user

    return _check


def accessible_modules(user: User) -> list[tuple[ModuleSpec, str]]:
    """Installed modules the user can open, with the user's role in each."""
    result = []
    for module in installed_modules():
        role = module_role(user, module.key)
        if role is not None:
            result.append((module, role))
    return result
