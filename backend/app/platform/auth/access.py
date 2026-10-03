"""Access to business modules (ARCHITECTURE.md §8, ADR 006).

Stage 0 (today): there are no per-module roles yet, so a user's role in every module is derived
from the platform role. Stage 1 replaces `module_role` with a lookup in `user_module_roles`;
endpoints keep using `require_module_access` unchanged.
"""

from collections.abc import Callable

from app.core.errors import AppError
from app.platform.auth.deps import CurrentUser
from app.platform.auth.models import User
from app.platform.modules import ModuleSpec, installed_modules

MODULE_ROLES = ("viewer", "admin")  # ordered from least to most privileged


def module_role(user: User, module_key: str) -> str | None:
    """Role of `user` in the module, or None without access."""
    return "admin" if user.role == "admin" else "viewer"


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
