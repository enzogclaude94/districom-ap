"""Application settings loaded from environment variables.

Required variables have no default: the app refuses to start without them.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


class Settings(BaseSettings):
    # hide_input_in_errors: a rejected setting is reported by name, never with its value (secrets, DATABASE_URL).
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore", hide_input_in_errors=True
    )

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str
    secret_key: str = Field(min_length=32)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cookie_secure: bool = True
    session_idle_hours: int = Field(default=8, gt=0)
    session_absolute_days: int = Field(default=7, gt=0)
    # A session's `last_seen_at` is written at most once per this many seconds (R9-5-1): the idle expiry is then up to
    # this much earlier than exact tracking, never later.
    session_touch_seconds: int = Field(default=60, gt=0, le=60)
    login_max_attempts: int = Field(default=5, gt=0)
    login_window_seconds: int = Field(default=300, gt=0)
    # "test" lowers the Argon2 cost so a test suite does not spend minutes hashing; only allowed with APP_ENV=test
    # and set explicitly (tests/conftest.py). Anything else hashes with the standard parameters.
    argon2_profile: Literal["standard", "test"] = "standard"

    upload_max_size_mb: int = Field(default=25, gt=0)
    # Largest request body the API reads, in real bytes (R9-5-2); file uploads (multipart) use upload_max_size_mb.
    request_max_body_bytes: int = Field(default=1024 * 1024, gt=0)
    # Limits of the backend's own database connections only (R9-5-2): migrations, backups, restores and dumps open
    # their own connections and keep the server's defaults.
    db_statement_timeout_ms: int = Field(default=30_000, gt=0)
    db_lock_timeout_ms: int = Field(default=10_000, gt=0)
    db_idle_in_transaction_timeout_ms: int = Field(default=60_000, gt=0)
    business_timezone: str = "America/Argentina/Buenos_Aires"
    log_level: str = "INFO"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _test_hashing_only_in_tests(self) -> "Settings":
        if self.argon2_profile == "test" and self.app_env != "test":
            raise ValueError("ARGON2_PROFILE=test requires APP_ENV=test")
        return self

    @field_validator("database_url")
    @classmethod
    def _use_psycopg_driver(cls, value: str) -> str:
        if value.startswith("postgresql://"):
            return value.replace("postgresql://", "postgresql+psycopg://", 1)
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


# --- production (R9-5-4, O8) --------------------------------------------------------------------------------------

# Values of .env.example, CI, the tests and the docs, and the usual placeholders. Compared case-insensitively.
KNOWN_SECRETS = frozenset(
    {
        "change-me",
        "changeme",
        "ci-password",
        "ci-only-secret-key-not-used-anywhere-else-0123456789",
        "test-secret-key-0123456789abcdef0123456789",
        "districom",
        "postgres",
        "password",
        "secret",
    }
)
PLACEHOLDER_MARKERS = ("change-me", "changeme", "change_me", "example", "placeholder", "replace", "dummy", "ci-only")
MIN_DB_PASSWORD_LENGTH = 16
# A random secret has many distinct characters; "aaaa…" or "0123012301…" do not.
MIN_DISTINCT_CHARACTERS = 10


class InsecureProductionConfigError(RuntimeError):
    """APP_ENV=production with a setting that is not safe to serve with. The message names the variables and why,
    never their values."""


def _weak_secret(value: str) -> str | None:
    lowered = value.lower()
    if lowered in KNOWN_SECRETS:
        return "es un valor de ejemplo conocido"
    if any(marker in lowered for marker in PLACEHOLDER_MARKERS):
        return "parece un placeholder"
    if len(set(value)) < MIN_DISTINCT_CHARACTERS:
        return "no parece aleatorio"
    return None


def production_problems(settings: Settings) -> list[str]:
    """Why `settings` must not start a production server, one line per variable; empty when it may. Only for
    APP_ENV=production: development and test keep their own settings (insecure cookies, CORS, the test Argon2)."""
    if settings.app_env != "production":
        return []
    problems: list[str] = []
    if not settings.cookie_secure:
        problems.append("COOKIE_SECURE debe ser true: la cookie de sesión solo puede viajar por HTTPS.")
    if settings.cors_origins:
        problems.append("CORS_ORIGINS debe estar vacío: en producción el navegador usa el mismo origen (Caddy).")
    if settings.argon2_profile != "standard":
        problems.append("ARGON2_PROFILE debe ser standard.")
    if settings.log_level.strip().upper() in ("DEBUG", "NOTSET"):
        problems.append("LOG_LEVEL no puede ser DEBUG: usá INFO o superior.")
    if (reason := _weak_secret(settings.secret_key)) is not None:
        problems.append(f"SECRET_KEY usa un valor inseguro: {reason}.")
    try:
        password = make_url(settings.database_url).password
    except ArgumentError:  # its message quotes the whole URL, password included
        problems.append("DATABASE_URL no es una URL de base de datos válida.")
        return problems
    if not password:
        problems.append("La contraseña de la base (POSTGRES_PASSWORD, en DATABASE_URL) está vacía.")
    elif (reason := _weak_secret(str(password))) is not None:
        problems.append(
            f"La contraseña de la base (POSTGRES_PASSWORD, en DATABASE_URL) usa un valor inseguro: {reason}."
        )
    elif len(str(password)) < MIN_DB_PASSWORD_LENGTH:
        problems.append(
            f"La contraseña de la base (POSTGRES_PASSWORD, en DATABASE_URL) es demasiado corta: "
            f"mínimo {MIN_DB_PASSWORD_LENGTH} caracteres."
        )
    return problems


def check_production(settings: Settings) -> None:
    if problems := production_problems(settings):
        raise InsecureProductionConfigError(
            "Configuración insegura para APP_ENV=production; el servidor no arranca:\n- " + "\n- ".join(problems)
        )
