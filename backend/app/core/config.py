"""Application settings loaded from environment variables.

Required variables have no default: the app refuses to start without them.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

DEV_SERVER_ORIGIN = "http://localhost:3000"  # `pnpm dev` and the development Docker Compose stack


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=("../.env", ".env"), env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["development", "test", "production"] = "development"
    database_url: str
    secret_key: str = Field(min_length=32)

    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    # Origins (scheme://host[:port]) besides the request's own that may send changes (S2). The Next.js server in
    # front of the backend (development, E2E) forwards with the backend's Host, so its origin goes here; unset in
    # development it is the default dev server. Production is same-origin through Caddy and leaves it empty.
    trusted_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cookie_secure: bool = True
    session_idle_hours: int = Field(default=8, gt=0)
    session_absolute_days: int = Field(default=7, gt=0)
    # A session's `last_seen_at` is written at most once per this many seconds (R9-5-1): the idle expiry is then up to
    # this much earlier than exact tracking, never later.
    session_touch_seconds: int = Field(default=60, gt=0, le=60)
    login_max_attempts: int = Field(default=5, gt=0)
    login_window_seconds: int = Field(default=300, gt=0)
    # How long a temporary password (given by an administrator or the CLI) can be used to choose a new one (S2).
    temporary_password_hours: int = Field(default=72, gt=0, le=720)
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

    @field_validator("cors_origins", "trusted_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _development_trusts_the_dev_server(self) -> "Settings":
        if self.app_env == "development" and "trusted_origins" not in self.model_fields_set:
            self.trusted_origins = [DEV_SERVER_ORIGIN]
        return self

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
