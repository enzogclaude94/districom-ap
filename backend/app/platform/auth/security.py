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
