from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.core.config import get_settings
from app.core.errors import AppError
from app.platform.auth.deps import SESSION_COOKIE, AppSettings, CurrentUser, DbSession, require_role
from app.platform.auth.models import User
from app.platform.auth.module_roles import ModuleRoleError, grant_module_role, revoke_module_role
from app.platform.auth.rate_limit import LoginRateLimiter
from app.platform.auth.schemas import AccessGrantIn, AccessGrantOut, AccessRevokeIn, LoginRequest, UserOut, user_out
from app.platform.auth.service import authenticate, create_session, revoke_session

router = APIRouter(prefix="/auth", tags=["auth"])

_settings = get_settings()
login_limiter = LoginRateLimiter(_settings.login_max_attempts, _settings.login_window_seconds)


@router.post("/login", response_model=UserOut)
def login(body: LoginRequest, request: Request, response: Response, db: DbSession, settings: AppSettings) -> UserOut:
    ip = request.client.host if request.client else None
    limiter_key = f"{ip}|{body.email.lower()}"
    if login_limiter.is_blocked(limiter_key):
        raise AppError(429, "too_many_attempts", "Demasiados intentos. Probá de nuevo en unos minutos.")
    user = authenticate(db, body.email, body.password)
    if user is None:
        login_limiter.register_failure(limiter_key)
        # Same message for unknown email, wrong password or inactive user.
        raise AppError(401, "invalid_credentials", "Email o contraseña incorrectos.")
    login_limiter.reset(limiter_key)
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
def me(user: CurrentUser) -> UserOut:
    return user_out(user)


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
