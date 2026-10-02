import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_missing_required_variables_fail_with_clear_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("SECRET_KEY", raising=False)
    with pytest.raises(ValidationError) as exc_info:
        Settings(_env_file=None)  # type: ignore[call-arg]
    missing = {err["loc"][0] for err in exc_info.value.errors()}
    assert missing == {"database_url", "secret_key"}


def test_short_secret_key_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SECRET_KEY", "short")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_cors_origins_parsed_from_comma_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CORS_ORIGINS", "http://a.test, http://b.test")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.cors_origins == ["http://a.test", "http://b.test"]


def test_plain_postgres_url_uses_psycopg_driver(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@h/db")
    assert Settings(_env_file=None).database_url == "postgresql+psycopg://u:p@h/db"  # type: ignore[call-arg]
