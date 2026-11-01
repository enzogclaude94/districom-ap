import hashlib
import secrets
from functools import lru_cache

from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import Settings, get_settings

# Argon2id with the library defaults (RFC 9106 low-memory profile).
STANDARD_HASHER = PasswordHasher()
# Tests only (ARGON2_PROFILE=test, which requires APP_ENV=test): the same Argon2id hash format, salt and
# length, at the lowest time and memory cost. A hash carries its own parameters, so verification is the same code.
TEST_HASHER = PasswordHasher(time_cost=1, memory_cost=8, parallelism=1, hash_len=32, salt_len=16, type=Type.ID)


def hasher_for(settings: Settings) -> PasswordHasher:
    return TEST_HASHER if settings.argon2_profile == "test" else STANDARD_HASHER


@lru_cache
def _hasher() -> PasswordHasher:
    return hasher_for(get_settings())


@lru_cache
def _dummy_hash() -> str:
    """Verified when the email is unknown so response time does not reveal which accounts exist."""
    return _hasher().hash(secrets.token_urlsafe(16))


MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 256
# Temporary passwords: no characters that are easy to misread when dictated or copied by hand (0/O, 1/l/I).
_TEMPORARY_ALPHABET = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_temporary_password() -> str:
    """Four groups of four characters (about 92 bits): shown once to whoever gives it, stored only as its hash."""
    groups = ("".join(secrets.choice(_TEMPORARY_ALPHABET) for _ in range(4)) for _ in range(4))
    return "-".join(groups)


def password_problem(password: str, *, email: str) -> str | None:
    """Why a new password is not acceptable, as a message for the user, or None."""
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"La contraseña debe tener al menos {MIN_PASSWORD_LENGTH} caracteres."
    if len(password) > MAX_PASSWORD_LENGTH:
        return f"La contraseña puede tener como mucho {MAX_PASSWORD_LENGTH} caracteres."
    if not password.strip():
        return "La contraseña no puede ser solo espacios."
    if password.strip().lower() == email.strip().lower():
        return "La contraseña no puede ser tu email."
    return None


def hash_password(password: str) -> str:
    return _hasher().hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher().verify(password_hash or _dummy_hash(), password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def new_session_token() -> str:
    return secrets.token_urlsafe(32)  # 256 bits


def hash_token(token: str) -> bytes:
    return hashlib.sha256(token.encode()).digest()
