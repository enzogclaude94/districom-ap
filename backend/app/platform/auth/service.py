from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.platform.audit.service import record_event
from app.platform.auth.models import ROLES, User, UserSession
from app.platform.auth.network import access_origin
from app.platform.auth.security import (
    hash_password,
    hash_token,
    new_session_token,
    new_temporary_password,
    password_problem,
    verify_password,
)


class PasswordError(ValueError):
    """A password change refused; `code` is the API error code and the message is for the user."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, password):
        return None
    if user is None or not user.active:
        return None
    return user


def temporary_password_expired(user: User, now: datetime | None = None) -> bool:
    """A temporary password past its expiry: it no longer opens a session nor lets its holder choose a new one."""
    expires = user.temporary_password_expires_at
    return user.must_change_password and expires is not None and (now or _now()) >= expires


def create_session(db: Session, user: User, settings: Settings, ip: str | None, user_agent: str | None) -> str:
    token = new_session_token()
    now = _now()
    origin = access_origin(ip)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now + timedelta(days=settings.session_absolute_days),
            ip=ip,
            user_agent=(user_agent or "")[:300] or None,
            access_origin=origin,
        )
    )
    user.last_login_at = now
    record_event(db, "LOGIN", user_id=user.id, entity_type="user", entity_id=str(user.id), payload=_where(ip, origin))
    db.commit()
    return token


def _where(ip: str | None, origin: str) -> dict[str, str | None]:
    """Where a request came from, for an audit payload (S2: information, never authorization)."""
    return {"ip": ip, "access_origin": origin}


def record_login_failure(db: Session, email: str, reason: str, ip: str | None) -> None:
    """Audit a refused login: the account's id when it exists, never the email typed (PII, or someone else's)."""
    user_id = db.scalar(select(User.id).where(User.email == email))
    record_event(
        db,
        "platform.login.failed",
        user_id=user_id,
        entity_type="user" if user_id else None,
        entity_id=str(user_id) if user_id else None,
        payload={"reason": reason, **_where(ip, access_origin(ip))},
    )
    db.commit()


def _now() -> datetime:
    return datetime.now(UTC)


def resolve_session(db: Session, token: str, settings: Settings) -> User | None:
    """Return the active user for a token, sliding the idle expiry. None if invalid.

    Every check uses the persisted `last_seen_at`, and only a session that passed all of them is touched, at most
    once per `session_touch_seconds`: the conditional UPDATE repeats every check, so it writes for one of any number
    of concurrent requests (the others no longer match once it commits) and never for a session that became invalid
    in between. When it matches nothing, a read-only recheck with the same `now` tells the two apart: another
    request touched it first (still valid) or it was revoked, expired, went idle or its user was deactivated (this
    request is rejected too)."""
    now = _now()
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
    ).first()
    if row is None:
        return None
    session, user = row
    idle = timedelta(hours=settings.session_idle_hours)
    if not user.active or now >= session.expires_at or now >= session.last_seen_at + idle:
        return None
    touch = timedelta(seconds=settings.session_touch_seconds)
    if now - session.last_seen_at >= touch:
        valid = (
            UserSession.id == session.id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            UserSession.last_seen_at > now - idle,
        )
        user_active = select(User.id).where(User.id == UserSession.user_id, User.active.is_(True)).exists()
        touched = db.execute(
            update(UserSession)
            .where(*valid, user_active, UserSession.last_seen_at <= now - touch)
            .values(last_seen_at=now)
            .execution_options(synchronize_session=False)
        )
        if touched.rowcount:  # type: ignore[attr-defined]
            db.commit()
        elif db.execute(select(UserSession.id).where(*valid, user_active)).first() is None:
            return None
    return user


def revoke_session(db: Session, token: str) -> None:
    """Logout: closes the session of this token."""
    db.execute(
        update(UserSession)
        .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason="logout", revoked_by=UserSession.user_id)
    )
    db.commit()


def session_id_for(db: Session, token: str) -> int | None:
    return db.scalar(select(UserSession.id).where(UserSession.token_hash == hash_token(token)))


def revoke_user_sessions(
    db: Session, user_id: int, reason: str, *, by: int | None, keep_session_id: int | None = None
) -> int:
    """Close every open session of the user, but `keep_session_id`, in the caller's transaction. How many closed."""
    keep = () if keep_session_id is None else (UserSession.id != keep_session_id,)
    result = db.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None), *keep)
        .values(revoked_at=_now(), revoked_reason=reason, revoked_by=by)
        .execution_options(synchronize_session=False)
    )
    return int(result.rowcount)  # type: ignore[attr-defined]


@dataclass(frozen=True)
class OpenSession:
    id: int
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    ip: str | None
    access_origin: str | None
    user_agent: str | None


def open_sessions(db: Session, user_id: int, settings: Settings) -> list[OpenSession]:
    """The user's sessions that still work: not revoked, not expired, not idle. Newest activity first."""
    now = _now()
    rows = db.scalars(
        select(UserSession)
        .where(
            UserSession.user_id == user_id,
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
            UserSession.last_seen_at > now - timedelta(hours=settings.session_idle_hours),
        )
        .order_by(UserSession.last_seen_at.desc(), UserSession.id.desc())
    )
    return [
        OpenSession(s.id, s.created_at, s.last_seen_at, s.expires_at, s.ip, s.access_origin, s.user_agent) for s in rows
    ]


def revoke_own_session(db: Session, user: User, session_id: int) -> bool:
    """The user closes one of their own sessions; False when it is not theirs or already closed (same answer)."""
    result = db.execute(
        update(UserSession)
        .where(UserSession.id == session_id, UserSession.user_id == user.id, UserSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoked_reason="revoked_by_user", revoked_by=user.id)
        .execution_options(synchronize_session=False)
    )
    if not result.rowcount:  # type: ignore[attr-defined]
        db.rollback()
        return False
    record_event(
        db,
        "platform.session.revoked",
        user_id=user.id,
        entity_type="user_session",
        entity_id=str(session_id),
        payload={"reason": "revoked_by_user"},
    )
    db.commit()
    return True


def revoke_other_own_sessions(db: Session, user: User, current_session_id: int) -> int:
    closed = revoke_user_sessions(db, user.id, "revoked_by_user", by=user.id, keep_session_id=current_session_id)
    if closed:
        record_event(
            db,
            "platform.sessions.revoked",
            user_id=user.id,
            entity_type="user",
            entity_id=str(user.id),
            payload={"reason": "revoked_by_user", "count": closed},
        )
    db.commit()
    return closed


def change_password(
    db: Session, user: User, current_password: str, new_password: str, *, current_session_id: int | None
) -> None:
    """The user's own change: proves the current (or temporary) password, then closes every other session.

    Raises PasswordError. The caller rate-limits failures of `current_password` (it is a password guess)."""
    if temporary_password_expired(user):
        raise PasswordError(
            "temporary_password_expired", "La contraseña temporal venció. Pedile al administrador una nueva."
        )
    if not verify_password(user.password_hash, current_password):
        raise PasswordError("invalid_current_password", "La contraseña actual no es correcta.")
    problem = password_problem(new_password, email=user.email)
    if problem:
        raise PasswordError("weak_password", problem)
    if verify_password(user.password_hash, new_password):
        raise PasswordError("same_password", "La contraseña nueva tiene que ser distinta de la actual.")
    was_temporary = user.must_change_password
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    user.temporary_password_expires_at = None
    user.password_changed_at = _now()
    closed = revoke_user_sessions(db, user.id, "password_changed", by=user.id, keep_session_id=current_session_id)
    record_event(
        db,
        "platform.password.changed",
        user_id=user.id,
        entity_type="user",
        entity_id=str(user.id),
        payload={"was_temporary": was_temporary, "sessions_revoked": closed},
    )
    db.commit()


def set_temporary_password(db: Session, user: User, settings: Settings, *, by: int | None) -> str:
    """Give the user a new temporary password and close all their sessions (S2 policy for a reset).

    Returns the password in clear exactly once, for whoever hands it over; only its hash is stored, and neither the
    audit event nor any log carries it. `by` is the administrator, None for the CLI."""
    password = new_temporary_password()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.temporary_password_expires_at = _now() + timedelta(hours=settings.temporary_password_hours)
    user.password_changed_at = _now()
    closed = revoke_user_sessions(db, user.id, "password_reset", by=by)
    record_event(
        db,
        "platform.password.reset",
        user_id=by,
        entity_type="user",
        entity_id=str(user.id),
        payload={"sessions_revoked": closed, "expires_at": user.temporary_password_expires_at.isoformat()},
    )
    db.commit()
    return password


def create_user(db: Session, email: str, full_name: str, password: str, role: str) -> User:
    if role not in ROLES:
        raise ValueError(f"Rol inválido: {role}")
    problem = password_problem(password, email=email)
    if problem:
        raise ValueError(problem)
    user = User(email=email, full_name=full_name, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    record_event(db, "USER_CREATED", user_id=None, entity_type="user", entity_id=str(user.id), payload={"role": role})
    db.commit()
    return user
