"""Access to business modules (ARCHITECTURE.md §8, ADR 006 and 007).

Stage 1: a platform admin (`users.role = admin`) is `admin` in every installed module; any other
user has the role stored in `user_module_roles`, or no access. Endpoints only use
`require_module_access`, so a future per-action stage changes this file alone.

Protected areas (ADR 010): a part of a module with its own grant, stored in the same table under the area
key (`settlements.employee_finance`). Reaching it takes a role in the module and a grant on the area; an
area with `explicit_grant_required` is not inherited by the platform admin, who grants it (CLI or
`PUT /auth/access-grants`) but cannot read it without a grant of their own. Endpoints use
`require_area_access`, which answers 404 to anyone without the area: the area's resources are not
discoverable.
"""

from collections.abc import Callable

from app.core.errors import AppError
from app.platform.auth.deps import CurrentUser
from app.platform.auth.models import MODULE_ROLES, User
from app.platform.modules import ModuleSpec, ProtectedAreaSpec, installed_area, installed_areas, installed_modules


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


def area_role(user: User, area_key: str) -> str | None:
    """Role of `user` in a protected area, or None without access (also without access to its module)."""
    found = installed_area(area_key)
    if found is None:
        return None
    module, area = found
    if module_role(user, module.key) is None:
        return None
    if user.role == "admin" and not area.explicit_grant_required:
        return "admin"
    for grant in user.module_roles:
        if grant.module_key == area_key:
            return grant.role
    return None


def has_area_access(user: User, area_key: str, min_role: str = "viewer") -> bool:
    role = area_role(user, area_key)
    return role is not None and MODULE_ROLES.index(role) >= MODULE_ROLES.index(min_role)


def require_area_access(area_key: str, min_role: str = "viewer") -> Callable[[User], User]:
    """Without the area: 404, nothing to discover. With it but below `min_role`: 403."""
    if min_role not in MODULE_ROLES:
        raise ValueError(f"Unknown module role: {min_role}")

    def _check(user: CurrentUser) -> User:
        if area_role(user, area_key) is None:
            raise AppError(404, "not_found", "No encontrado.")
        if not has_area_access(user, area_key, min_role):
            raise AppError(403, "forbidden", "No tenés permisos para esta acción.")
        return user

    return _check


def accessible_areas(user: User) -> list[tuple[ProtectedAreaSpec, str]]:
    """Protected areas the user can open, with the user's role in each."""
    result = []
    for _, area in installed_areas():
        role = area_role(user, area.key)
        if role is not None:
            result.append((area, role))
    return result


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
