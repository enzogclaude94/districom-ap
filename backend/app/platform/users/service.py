"""User administration by platform admins (S2-2, ADR 015).

Guards that never depend on the screen:
- only a platform admin (`users.role = admin`, with a session and no temporary password) reaches these functions,
  through `require_role("admin")`; nobody can change their own platform role, active state or password here;
- there is always at least one active platform admin: deactivating or demoting one locks every active admin row
  first, so two admins doing it to each other at the same time cannot both succeed;
- a deactivation and a password reset close every session of the user; role and access changes close none,
  because access is read from the database on every request;
- access to a module never implies a protected area: they are separate grants (ADR 010), and removing a module
  also removes the user's grants on its areas, so no area is left pointing at a module the user cannot open.
No user is ever deleted: their history (sessions, audit, what they recorded) stays.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.platform.audit.models import AuditEvent
from app.platform.audit.service import record_change
from app.platform.auth.models import MODULE_ROLES, ROLES, User, UserModuleRole, UserSession
from app.platform.auth.module_roles import grant_module_role, revoke_module_role
from app.platform.auth.service import revoke_session_of, revoke_user_sessions, set_temporary_password
from app.platform.modules import installed_area, installed_modules


class UserAdminError(Exception):
    """A refused administrative action; `code` and `status` go to the API error."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def _not_yourself(admin: User, user: User, what: str) -> None:
    if admin.id == user.id:
        raise UserAdminError(409, "own_account", f"No podés {what} tu propia cuenta: pedíselo a otro administrador.")


def _keep_an_active_admin(db: Session, user: User) -> None:
    """Refuse when `user` is the only active platform admin. Locks every active admin row (in id order, so two
    concurrent requests wait for each other instead of deadlocking) until the caller commits."""
    admins = db.scalars(
        select(User.id).where(User.role == "admin", User.active.is_(True)).order_by(User.id).with_for_update()
    ).all()
    if user.id in admins and len(admins) == 1:
        raise UserAdminError(
            409, "last_admin", "Es el último administrador activo: antes dale el rol de administrador a otra persona."
        )


def get_user(db: Session, user_id: int) -> User:
    user = db.get(User, user_id)
    if user is None:
        raise UserAdminError(404, "user_not_found", "No existe ese usuario.")
    return user


def list_users(
    db: Session, *, active: bool | None, search: str | None, limit: int, offset: int
) -> tuple[list[User], int]:
    query = select(User)
    if active is not None:
        query = query.where(User.active.is_(active))
    if search:
        pattern = f"%{search.strip()}%"
        query = query.where(or_(User.full_name.ilike(pattern), User.email.ilike(pattern)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(User.active.desc(), User.full_name, User.id).limit(limit).offset(offset)).all()
    return list(rows), total


def open_session_counts(db: Session, user_ids: list[int], settings: Settings) -> dict[int, int]:
    """Open sessions (not revoked, expired or idle) per user, for a list."""
    now = datetime.now(UTC)
    rows = db.execute(
        select(UserSession.user_id, func.count())
        .where(
            UserSession.user_id.in_(user_ids),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            UserSession.last_seen_at > now - timedelta(hours=settings.session_idle_hours),
        )
        .group_by(UserSession.user_id)
    )
    return dict(rows.tuples().all())


def _email_taken(db: Session, email: str, except_id: int | None = None) -> bool:
    query = select(User.id).where(User.email == email)
    if except_id is not None:
        query = query.where(User.id != except_id)
    return db.scalar(query) is not None


@dataclass(frozen=True)
class NewUser:
    user: User
    temporary_password: str


def create_user(db: Session, settings: Settings, admin: User, *, email: str, full_name: str, role: str) -> NewUser:
    """A new account with a temporary password (shown once); no module or area until granted."""
    if role not in ROLES:
        raise UserAdminError(422, "invalid_role", "Rol de plataforma inválido.")
    if _email_taken(db, email):
        raise UserAdminError(409, "email_taken", "Ya existe un usuario con ese email.")
    user = User(email=email, full_name=full_name, role=role, password_hash=None)
    db.add(user)
    db.flush()
    record_change(
        db,
        "platform.user.created",
        action="create",
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        before=None,
        after={"role": role, "active": True},
    )
    password = set_temporary_password(db, user, settings, by=admin.id)  # commits
    return NewUser(user, password)


def update_user(
    db: Session, admin: User, user: User, *, email: str | None, full_name: str | None, role: str | None
) -> User:
    """Name, email and platform role. Changing one's own platform role is refused; demoting the last active admin
    too. Personal data never goes to the audit: only which fields changed."""
    changed: dict[str, object] = {}
    before: dict[str, object] = {}
    if role is not None and role != user.role:
        if role not in ROLES:
            raise UserAdminError(422, "invalid_role", "Rol de plataforma inválido.")
        _not_yourself(admin, user, "cambiar el rol de plataforma de")
        if user.role == "admin":
            _keep_an_active_admin(db, user)
        before["role"], changed["role"] = user.role, role
        user.role = role
    if email is not None and email.lower() != user.email.lower():
        if _email_taken(db, email, except_id=user.id):
            raise UserAdminError(409, "email_taken", "Ya existe un usuario con ese email.")
        user.email = email
        changed["email_changed"] = True
    if full_name is not None and full_name != user.full_name:
        user.full_name = full_name
        changed["full_name_changed"] = True
    if not changed:
        db.rollback()
        return user
    record_change(
        db,
        "platform.user.updated",
        action="update",
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        before=before or None,
        after=changed,
    )
    db.commit()
    return user


def set_active(db: Session, admin: User, user: User, active: bool) -> int:
    """Activate or deactivate. A deactivation closes every session of the user; returns how many."""
    if user.active == active:
        db.rollback()
        return 0
    closed = 0
    if not active:
        _not_yourself(admin, user, "desactivar")
        if user.role == "admin":
            _keep_an_active_admin(db, user)
        closed = revoke_user_sessions(db, user.id, "user_deactivated", by=admin.id)
    user.active = active
    record_change(
        db,
        "platform.user.activated" if active else "platform.user.deactivated",
        action="activate" if active else "deactivate",
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        before={"active": not active},
        after={"active": active, "sessions_revoked": closed},
    )
    db.commit()
    return closed


def reset_password(db: Session, settings: Settings, admin: User, user: User) -> str:
    """A new temporary password (shown once) and every session of the user closed. Not for one's own account:
    that is «Mi cuenta», which asks for the current password."""
    _not_yourself(admin, user, "resetear la contraseña de")
    if not user.active:
        raise UserAdminError(
            409, "user_inactive", "El usuario está desactivado: activalo antes de darle una contraseña."
        )
    return set_temporary_password(db, user, settings, by=admin.id)


def access_keys() -> tuple[set[str], dict[str, str]]:
    """Installed module keys, and each protected area's key with its module's."""
    modules = {module.key for module in installed_modules()}
    areas = {}
    for module in installed_modules():
        for area in module.protected_areas:
            areas[area.key] = module.key
    return modules, areas


def set_access(db: Session, admin: User, user: User, key: str, role: str | None) -> None:
    """Give, change or (with None) remove a role in a module or a protected area.

    An area needs its module: granting one without a role in the module is refused (it would grant nothing and
    look like it did), and removing a module also removes the areas of that module. The platform admin's own
    module roles are implicit (admin everywhere), so a module row for a platform admin is refused as meaningless;
    areas with an explicit grant are not inherited and can be granted to anyone, also to oneself (ADR 010)."""
    modules, areas = access_keys()
    if key not in modules and key not in areas:
        raise UserAdminError(422, "unknown_access_key", "No existe ese módulo o área.")
    if role is not None and role not in MODULE_ROLES:
        raise UserAdminError(422, "invalid_role", "Rol inválido.")
    if key in modules and user.role == "admin":
        raise UserAdminError(
            409, "platform_admin_module", "Un administrador de plataforma ya es administrador en todos los módulos."
        )
    if role is None:
        revoke_module_role(db, user.email, key, revoked_by=admin.id)
        if key in modules:
            for area_key, module_key in areas.items():
                if module_key == key:
                    revoke_module_role(db, user.email, area_key, revoked_by=admin.id)
        return
    if key in areas and user.role != "admin":
        has_module = db.scalar(
            select(UserModuleRole.id).where(UserModuleRole.user_id == user.id, UserModuleRole.module_key == areas[key])
        )
        if has_module is None:
            module = installed_area(key)
            name = module[0].name if module else areas[key]
            raise UserAdminError(
                409, "area_requires_module", f"Primero dale un rol en {name}: el área no da acceso al módulo."
            )
    grant_module_role(db, user.email, key, role, granted_by=admin.id)


def revoke_sessions(db: Session, admin: User, user: User, session_id: int | None) -> int:
    """Close one session of the user (`session_id`) or all of them; returns how many."""
    if session_id is None:
        closed = revoke_user_sessions(db, user.id, "revoked_by_admin", by=admin.id)
    elif revoke_session_of(db, user.id, session_id, "revoked_by_admin", by=admin.id):
        closed = 1
    else:
        db.rollback()
        raise UserAdminError(404, "not_found", "No encontrado.")
    record_change(
        db,
        "platform.user.sessions_revoked",
        action="revoke_sessions",
        user_id=admin.id,
        entity_type="user",
        entity_id=user.id,
        before=None,
        after={"session_id": session_id, "sessions_revoked": closed},
    )
    db.commit()
    return closed


def activity(db: Session, user: User, limit: int = 50) -> list[AuditEvent]:
    """Audit events about the user's account (what was done to it) and their logins, newest first."""
    return list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.entity_type == "user",
                AuditEvent.entity_id == str(user.id),
            )
            .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
            .limit(limit)
        )
    )
