from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.audit.service import record_event
from app.auth.models import ROLES, User, UserSession
from app.auth.security import MIN_PASSWORD_LENGTH, hash_password, hash_token, new_session_token, verify_password
from app.core.config import Settings


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


def resolve_session(db: Session, token: str, settings: Settings) -> User | None:
    """Return the active user for a token, sliding the idle expiry. None if invalid."""
    now = datetime.now(UTC)
    row = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
    ).first()
    if row is None:
        return None
    session, user = row
    idle_limit = session.last_seen_at + timedelta(hours=settings.session_idle_hours)
    if not user.active or now >= session.expires_at or now >= idle_limit:
        return None
    session.last_seen_at = now
    db.commit()
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
