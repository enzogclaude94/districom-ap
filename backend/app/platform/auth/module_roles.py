"""Granting and revoking per-module roles (stage 1): CLI and `PUT/DELETE /auth/access-grants` (platform admin).

The same rows hold the grants on protected areas (ADR 010), under the area key (`settlements.employee_finance`).
Granting an area never reads its data: it is how a platform admin, who does not inherit such an area,
gives it to themselves or to someone else.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.audit.service import record_change
from app.platform.auth.models import MODULE_ROLES, User, UserModuleRole
from app.platform.modules import installed_area, installed_modules


class ModuleRoleError(ValueError):
    pass


def _user(db: Session, email: str) -> User:
    user = db.scalar(select(User).where(User.email == email))
    if user is None:
        raise ModuleRoleError("unknown user")
    return user


def _check_module(module_key: str) -> None:
    """A module or a protected area that is installed."""
    if module_key not in {module.key for module in installed_modules()} and installed_area(module_key) is None:
        raise ModuleRoleError(f"unknown module {module_key}")


def grant_module_role(
    db: Session, email: str, module_key: str, role: str, *, granted_by: int | None = None
) -> UserModuleRole:
    """Create or change the user's role in a module (idempotent for the same role)."""
    _check_module(module_key)
    if role not in MODULE_ROLES:
        raise ModuleRoleError(f"unknown role {role}")
    user = _user(db, email)
    grant = db.scalar(
        select(UserModuleRole)
        .where(UserModuleRole.user_id == user.id, UserModuleRole.module_key == module_key)
        .with_for_update()
    )
    before = {"module": module_key, "role": grant.role} if grant else None
    if grant is None:
        grant = UserModuleRole(user_id=user.id, module_key=module_key, role=role, granted_by=granted_by)
        db.add(grant)
    elif grant.role == role:
        return grant
    else:
        grant.role = role
        grant.granted_by = granted_by
    db.flush()
    record_change(
        db,
        "platform.module_role.granted",
        action="grant" if before is None else "change",
        user_id=granted_by,
        entity_type="user",
        entity_id=user.id,
        before=before,
        after={"module": module_key, "role": role},
    )
    db.commit()
    return grant


def revoke_module_role(db: Session, email: str, module_key: str, *, revoked_by: int | None = None) -> bool:
    """Remove the user's access to a module. Returns False if there was nothing to revoke."""
    _check_module(module_key)
    user = _user(db, email)
    grant = db.scalar(
        select(UserModuleRole).where(UserModuleRole.user_id == user.id, UserModuleRole.module_key == module_key)
    )
    if grant is None:
        return False
    before = {"module": module_key, "role": grant.role}
    db.delete(grant)
    record_change(
        db,
        "platform.module_role.revoked",
        action="revoke",
        user_id=revoked_by,
        entity_type="user",
        entity_id=user.id,
        before=before,
        after=None,
    )
    db.commit()
    return True
