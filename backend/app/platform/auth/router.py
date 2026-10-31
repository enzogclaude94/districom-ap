from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.platform.auth.deps import (
    SESSION_COOKIE,
    AppSettings,
    CurrentUser,
    DbSession,
    SessionUser,
    require_role,
)
from app.platform.auth.models import User
from app.platform.auth.module_roles import ModuleRoleError, grant_module_role, revoke_module_role
from app.platform.auth.network import client_ip
from app.platform.auth.rate_limit import LoginRateLimiter
from app.platform.auth.schemas import (
    AccessGrantIn,
    AccessGrantOut,
    AccessRevokeIn,
    LoginRequest,
    PasswordChangeIn,
    SessionOut,
    SessionsRevokedOut,
    UserOut,
    user_out,
)
from app.platform.auth.service import (
    PasswordError,
    authenticate,
    change_password,
    create_session,
    open_sessions,
    record_login_failure,
    revoke_other_own_sessions,
    revoke_own_session,
    revoke_session,
    session_id_for,
    temporary_password_expired,
)

router = APIRouter(prefix="/auth", tags=["auth"])

_settings = get_settings()
login_limiter = LoginRateLimiter(_settings.login_max_attempts, _settings.login_window_seconds)


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, response: Response, db: DbSession, settings: AppSettings) -> UserOut:
    ip = client_ip(request)
    limiter_key = f"{ip}|{body.email.lower()}"
    if login_limiter.is_blocked(limiter_key):
        record_login_failure(db, body.email, "rate_limited", ip)
        raise AppError(429, "too_many_attempts", "Demasiados intentos. Probá de nuevo en unos minutos.")
    user = authenticate(db, body.email, body.password)
    if user is None:
        login_limiter.register_failure(limiter_key)
        record_login_failure(db, body.email, "invalid_credentials", ip)
        # Same message for unknown email, wrong password or inactive user.
        raise AppError(401, "invalid_credentials", "Email o contraseña incorrectos.")
    login_limiter.reset(limiter_key)
    if temporary_password_expired(user):
        # Only someone who knows the temporary password gets here, so saying it expired reveals nothing.
        record_login_failure(db, body.email, "temporary_password_expired", ip)
        raise AppError(
            401, "temporary_password_expired", "La contraseña temporal venció. Pedile al administrador una nueva."
        )
    token = create_session(db, user, settings, ip, request.headers.get("user-agent"))
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=settings.session_absolute_days * 86400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return user_out(user)


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: DbSession) -> Response:
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        revoke_session(db, token)
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.status_code = 204
    return response


@router.get("/me", response_model=UserOut)
def me(user: SessionUser) -> UserOut:
    """Also for a user with a temporary password, who sees `must_change_password` and nothing else."""
    return user_out(user)


@router.post("/password", response_model=UserOut)
def change_own_password(body: PasswordChangeIn, request: Request, db: DbSession, user: SessionUser) -> UserOut:
    """Change one's own password (also a temporary one). Every other session of the user is closed; this one stays."""
    limiter_key = f"password|{user.id}"
    if login_limiter.is_blocked(limiter_key):
        raise AppError(429, "too_many_attempts", "Demasiados intentos. Probá de nuevo en unos minutos.")
    token = request.cookies.get(SESSION_COOKIE)
    try:
        change_password(
            db,
            user,
            body.current_password,
            body.new_password,
            current_session_id=session_id_for(db, token) if token else None,
        )
    except PasswordError as exc:
        db.rollback()
        if exc.code == "invalid_current_password":
            login_limiter.register_failure(limiter_key)
        status = 401 if exc.code == "temporary_password_expired" else 422
        raise AppError(status, exc.code, str(exc)) from exc
    login_limiter.reset(limiter_key)
    return user_out(user)


@router.get("/sessions", response_model=list[SessionOut])
def my_sessions(request: Request, db: DbSession, settings: AppSettings, user: CurrentUser) -> list[SessionOut]:
    """The user's open sessions, the current one marked."""
    token = request.cookies.get(SESSION_COOKIE)
    current = session_id_for(db, token) if token else None
    return [SessionOut(**vars(s), current=s.id == current) for s in open_sessions(db, user.id, settings)]


@router.post("/sessions/{session_id}/revoke", status_code=204)
def revoke_my_session(session_id: int, db: DbSession, user: CurrentUser) -> Response:
    """Close one of the user's own sessions (also the current one). Someone else's session is not found."""
    if not revoke_own_session(db, user, session_id):
        raise AppError(404, "not_found", "No encontrado.")
    return Response(status_code=204)


@router.post("/sessions/revoke-others", response_model=SessionsRevokedOut)
def revoke_my_other_sessions(request: Request, db: DbSession, user: CurrentUser) -> SessionsRevokedOut:
    token = request.cookies.get(SESSION_COOKIE)
    current = session_id_for(db, token) if token else None
    assert current is not None  # CurrentUser resolved this very token
    return SessionsRevokedOut(revoked=revoke_other_own_sessions(db, user, current))


PlatformAdmin = Annotated[User, Depends(require_role("admin"))]


def _grant_error(exc: ModuleRoleError) -> AppError:
    if str(exc) == "unknown user":
        return AppError(404, "user_not_found", "No existe un usuario con ese email.")
    return AppError(422, "unknown_access_key", "No existe ese módulo o área.")


@router.put("/access-grants", response_model=AccessGrantOut)
def grant_access(body: AccessGrantIn, db: DbSession, admin: PlatformAdmin) -> AccessGrantOut:
    """Give or change a user's role in a module or a protected area (platform admin). Reads no area data."""
    try:
        grant = grant_module_role(db, body.email.strip(), body.key, body.role, granted_by=admin.id)
    except ModuleRoleError as exc:
        raise _grant_error(exc) from exc
    return AccessGrantOut(user_id=grant.user_id, key=grant.module_key, role=grant.role)


@router.post("/access-grants/revoke", response_model=AccessGrantOut)
def revoke_access(body: AccessRevokeIn, db: DbSession, admin: PlatformAdmin) -> AccessGrantOut:
    """Remove a user's role in a module or a protected area (platform admin); idempotent."""
    try:
        revoke_module_role(db, body.email.strip(), body.key, revoked_by=admin.id)
    except ModuleRoleError as exc:
        raise _grant_error(exc) from exc
    user = db.scalar(select(User).where(User.email == body.email.strip()))
    assert user is not None  # revoke_module_role checked it
    return AccessGrantOut(user_id=user.id, key=body.key, role=None)
