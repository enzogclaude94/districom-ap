from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.db.session import get_db
from app.platform.auth.models import User
from app.platform.auth.service import resolve_session

SESSION_COOKIE = "districom_session"

DbSession = Annotated[Session, Depends(get_db)]
AppSettings = Annotated[Settings, Depends(get_settings)]


def current_user(request: Request, db: DbSession, settings: AppSettings) -> User:
    token = request.cookies.get(SESSION_COOKIE)
    user = resolve_session(db, token, settings) if token else None
    if user is None:
        raise AppError(401, "unauthorized", "Iniciá sesión para continuar.")
    request.state.user_id = user.id  # for the access log: the id, never the email
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_role(*roles: str) -> Callable[[User], User]:
    def _check(user: CurrentUser) -> User:
        if user.role not in roles:
            raise AppError(403, "forbidden", "No tenés permisos para esta acción.")
        return user

    return _check
