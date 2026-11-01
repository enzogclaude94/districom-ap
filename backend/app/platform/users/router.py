"""User administration API (S2-2, ADR 015): platform admins only, with a session and no temporary password.

Every guard lives in `service.py`; this module maps requests and errors."""

from collections.abc import Callable
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.core.errors import AppError
from app.platform.auth.deps import AppSettings, DbSession, require_role
from app.platform.auth.models import User
from app.platform.auth.service import open_sessions
from app.platform.modules import installed_area, installed_modules
from app.platform.users import service
from app.platform.users.schemas import (
    AccessCatalogOut,
    AccessIn,
    ActivityOut,
    AdminSessionOut,
    CatalogEntryOut,
    GrantOut,
    SessionsRevokedOut,
    TemporaryPasswordOut,
    UserCreateIn,
    UserDetailOut,
    UserPage,
    UserSummaryOut,
    UserUpdateIn,
)

router = APIRouter(prefix="/users", tags=["users"])

PlatformAdmin = Annotated[User, Depends(require_role("admin"))]


def _call[T](action: Callable[[], T]) -> T:
    try:
        return action()
    except service.UserAdminError as exc:
        raise AppError(exc.status, exc.code, str(exc)) from exc


def _grants(user: User) -> tuple[list[GrantOut], list[GrantOut]]:
    names = {module.key: module.name for module in installed_modules()}
    modules, areas = [], []
    for grant in sorted(user.module_roles, key=lambda g: g.module_key):
        if grant.module_key in names:
            modules.append(GrantOut(key=grant.module_key, name=names[grant.module_key], role=grant.role))
        elif (found := installed_area(grant.module_key)) is not None:
            areas.append(GrantOut(key=grant.module_key, name=f"{found[0].name} > {found[1].name}", role=grant.role))
    return modules, areas


def _summary(user: User, sessions: int) -> UserSummaryOut:
    modules, areas = _grants(user)
    return UserSummaryOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        role=user.role,
        active=user.active,
        must_change_password=user.must_change_password,
        last_login_at=user.last_login_at,
        password_changed_at=user.password_changed_at,
        created_at=user.created_at,
        modules=modules,
        areas=areas,
        open_sessions=sessions,
    )


def _detail(db: DbSession, settings: AppSettings, user: User) -> UserDetailOut:
    db.refresh(user)
    sessions = open_sessions(db, user.id, settings)
    events = service.activity(db, user)
    actors = {
        row.id: row.full_name
        for row in db.execute(
            select(User.id, User.full_name).where(User.id.in_({e.user_id for e in events if e.user_id}))
        )
    }
    return UserDetailOut(
        **_summary(user, len(sessions)).model_dump(),
        sessions=[AdminSessionOut(**vars(s)) for s in sessions],
        activity=[
            ActivityOut(
                occurred_at=e.occurred_at,
                event_type=e.event_type,
                actor_id=e.user_id,
                actor_name=actors.get(e.user_id) if e.user_id else None,
                payload=e.payload,
            )
            for e in events
        ],
    )


@router.get("/access-catalog", response_model=AccessCatalogOut)
def access_catalog(_: PlatformAdmin) -> AccessCatalogOut:
    """Modules and protected areas a user can be given."""
    return AccessCatalogOut(
        modules=[CatalogEntryOut(key=m.key, name=m.name) for m in installed_modules()],
        areas=[
            CatalogEntryOut(key=a.key, name=a.name, module_key=m.key)
            for m in installed_modules()
            for a in m.protected_areas
        ],
    )


@router.get("", response_model=UserPage)
def list_users(
    db: DbSession,
    settings: AppSettings,
    _: PlatformAdmin,
    status: Annotated[str, Query(pattern="^(active|inactive|all)$")] = "active",
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> UserPage:
    active = {"active": True, "inactive": False, "all": None}[status]
    users, total = service.list_users(db, active=active, search=q, limit=limit, offset=offset)
    counts = service.open_session_counts(db, [u.id for u in users], settings)
    return UserPage(items=[_summary(u, counts.get(u.id, 0)) for u in users], total=total)


@router.post("", response_model=TemporaryPasswordOut, status_code=201)
def create_user(body: UserCreateIn, db: DbSession, settings: AppSettings, admin: PlatformAdmin) -> TemporaryPasswordOut:
    """A new account with a temporary password, shown only in this response. No module or area until granted."""
    created = _call(
        lambda: service.create_user(db, settings, admin, email=body.email, full_name=body.full_name, role=body.role)
    )
    user = created.user
    assert user.temporary_password_expires_at is not None
    return TemporaryPasswordOut(
        user=_summary(user, 0),
        temporary_password=created.temporary_password,
        expires_at=user.temporary_password_expires_at,
    )


@router.get("/{user_id}", response_model=UserDetailOut)
def get_user(user_id: int, db: DbSession, settings: AppSettings, _: PlatformAdmin) -> UserDetailOut:
    return _detail(db, settings, _call(lambda: service.get_user(db, user_id)))


@router.patch("/{user_id}", response_model=UserDetailOut)
def update_user(
    user_id: int, body: UserUpdateIn, db: DbSession, settings: AppSettings, admin: PlatformAdmin
) -> UserDetailOut:
    user = _call(lambda: service.get_user(db, user_id))
    _call(lambda: service.update_user(db, admin, user, email=body.email, full_name=body.full_name, role=body.role))
    return _detail(db, settings, user)


@router.post("/{user_id}/deactivate", response_model=UserDetailOut)
def deactivate_user(user_id: int, db: DbSession, settings: AppSettings, admin: PlatformAdmin) -> UserDetailOut:
    """Closes every session of the user. Never one's own account, never the last active platform admin."""
    user = _call(lambda: service.get_user(db, user_id))
    _call(lambda: service.set_active(db, admin, user, False))
    return _detail(db, settings, user)


@router.post("/{user_id}/activate", response_model=UserDetailOut)
def activate_user(user_id: int, db: DbSession, settings: AppSettings, admin: PlatformAdmin) -> UserDetailOut:
    user = _call(lambda: service.get_user(db, user_id))
    _call(lambda: service.set_active(db, admin, user, True))
    return _detail(db, settings, user)


@router.post("/{user_id}/reset-password", response_model=TemporaryPasswordOut)
def reset_password(user_id: int, db: DbSession, settings: AppSettings, admin: PlatformAdmin) -> TemporaryPasswordOut:
    """A new temporary password, shown only in this response; every session of the user is closed."""
    user = _call(lambda: service.get_user(db, user_id))
    password = _call(lambda: service.reset_password(db, settings, admin, user))
    assert user.temporary_password_expires_at is not None
    return TemporaryPasswordOut(
        user=_summary(user, 0), temporary_password=password, expires_at=user.temporary_password_expires_at
    )


@router.put("/{user_id}/access", response_model=UserDetailOut)
def set_access(
    user_id: int, body: AccessIn, db: DbSession, settings: AppSettings, admin: PlatformAdmin
) -> UserDetailOut:
    """Give, change or remove (`role: null`) a role in a module or a protected area."""
    user = _call(lambda: service.get_user(db, user_id))
    _call(lambda: service.set_access(db, admin, user, body.key, body.role))
    db.expire(user, ["module_roles"])
    return _detail(db, settings, user)


@router.post("/{user_id}/sessions/{session_id}/revoke", response_model=SessionsRevokedOut)
def revoke_session(user_id: int, session_id: int, db: DbSession, admin: PlatformAdmin) -> SessionsRevokedOut:
    user = _call(lambda: service.get_user(db, user_id))
    return SessionsRevokedOut(revoked=_call(lambda: service.revoke_sessions(db, admin, user, session_id)))


@router.post("/{user_id}/sessions/revoke-all", response_model=SessionsRevokedOut)
def revoke_all_sessions(user_id: int, db: DbSession, admin: PlatformAdmin) -> SessionsRevokedOut:
    user = _call(lambda: service.get_user(db, user_id))
    return SessionsRevokedOut(revoked=_call(lambda: service.revoke_sessions(db, admin, user, None)))
