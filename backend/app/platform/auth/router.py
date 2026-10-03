from fastapi import APIRouter, Request, Response

from app.core.config import get_settings
from app.core.errors import AppError
from app.platform.auth.deps import SESSION_COOKIE, AppSettings, CurrentUser, DbSession
from app.platform.auth.rate_limit import LoginRateLimiter
from app.platform.auth.schemas import LoginRequest, UserOut
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
    return UserOut.model_validate(user)


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
    return UserOut.model_validate(user)
