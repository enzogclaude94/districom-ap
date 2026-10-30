from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.platform.audit.service import record_event
from app.platform.auth.models import ROLES, User, UserSession
from app.platform.auth.security import (
    MIN_PASSWORD_LENGTH,
    hash_password,
    hash_token,
    new_session_token,
    verify_password,
)


def authenticate(db: Session, email: str, password: str) -> User | None:
    user = db.scalar(select(User).where(User.email == email))
    if not verify_password(user.password_hash if user else None, password):
        return None
    if user is None or not user.active:
        return None
    return user


def create_session(db: Session, user: User, settings: Settings, ip: str | None, user_agent: str | None) -> str:
    token = new_session_token()
    now = datetime.now(UTC)
    db.add(
        UserSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=now + timedelta(days=settings.session_absolute_days),
            ip=ip,
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    user.last_login_at = now
    record_event(db, "LOGIN", user_id=user.id, entity_type="user", entity_id=str(user.id))
    db.commit()
    return token


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
    db.execute(
        update(UserSession)
        .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    db.commit()


def create_user(db: Session, email: str, full_name: str, password: str, role: str) -> User:
    if role not in ROLES:
        raise ValueError(f"Rol inválido: {role}")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres.")
    user = User(email=email, full_name=full_name, password_hash=hash_password(password), role=role)
    db.add(user)
    db.flush()
    record_event(db, "USER_CREATED", user_id=None, entity_type="user", entity_id=str(user.id), payload={"role": role})
    db.commit()
    return user
