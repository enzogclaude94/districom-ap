"""R9-5-4 (O8): with APP_ENV=production the app refuses to start on an unsafe setting, naming the variable and
never its value; development and test keep their own settings."""

import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.core.config import InsecureProductionConfigError, Settings, check_production, production_problems

BACKEND_DIR = Path(__file__).resolve().parents[2]
SECRET = "Zq4pV8tK2mWx9Rb7Lc3Ne6Hy1Ds5Gf0Ja"  # random-looking: 34 characters, many distinct
DB_PASSWORD = "Pw7Kd2Qm9Xr4Tz6Vb1Nc"
ENV = {
    "APP_ENV": "production",
    "DATABASE_URL": f"postgresql+psycopg://districom:{DB_PASSWORD}@db:5432/districom",
    "SECRET_KEY": SECRET,
    "COOKIE_SECURE": "true",
    "CORS_ORIGINS": "",
    "ARGON2_PROFILE": "standard",
    "LOG_LEVEL": "INFO",
}


@pytest.fixture
def env(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """A valid production environment, which each test makes unsafe in one variable."""
    for name, value in ENV.items():
        monkeypatch.setenv(name, value)
    return monkeypatch


def _problems() -> list[str]:
    return production_problems(Settings(_env_file=None))  # type: ignore[call-arg]


def _url(password: str) -> str:
    return f"postgresql+psycopg://districom:{password}@db:5432/districom"


def test_a_valid_production_configuration_starts(env: pytest.MonkeyPatch) -> None:
    assert _problems() == []
    check_production(Settings(_env_file=None))  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("variable", "value", "named", "hidden"),
    [
        ("COOKIE_SECURE", "false", "COOKIE_SECURE", None),
        ("CORS_ORIGINS", "http://localhost:3000", "CORS_ORIGINS", "localhost"),
        ("CORS_ORIGINS", "*", "CORS_ORIGINS", None),
        ("LOG_LEVEL", "DEBUG", "LOG_LEVEL", None),
        ("LOG_LEVEL", "debug", "LOG_LEVEL", None),
        ("SECRET_KEY", "ci-only-secret-key-not-used-anywhere-else-0123456789", "SECRET_KEY", "ci-only"),
        ("SECRET_KEY", "test-secret-key-0123456789abcdef0123456789", "SECRET_KEY", "0123456789"),
        ("SECRET_KEY", "change-me-change-me-change-me-change-me", "SECRET_KEY", "change-me"),
        ("SECRET_KEY", "your-secret-key-placeholder-0123456789", "SECRET_KEY", "your-secret"),
        ("SECRET_KEY", "a" * 40, "SECRET_KEY", "aaaa"),
        ("DATABASE_URL", _url("change-me"), "POSTGRES_PASSWORD", "change-me"),
        ("DATABASE_URL", _url("ci-password"), "POSTGRES_PASSWORD", "ci-password"),
        ("DATABASE_URL", _url("districom"), "POSTGRES_PASSWORD", ":districom@"),
        ("DATABASE_URL", _url("Kd2Qm9Xr4Tz6"), "POSTGRES_PASSWORD", "Kd2Qm9Xr4Tz6"),  # too short
        ("DATABASE_URL", "postgresql+psycopg://districom@db:5432/districom", "POSTGRES_PASSWORD", "db:5432"),
        ("DATABASE_URL", "not a url :Pw7Kd2Qm9", "DATABASE_URL", "Pw7Kd2Qm9"),
    ],
)
def test_each_unsafe_setting_stops_the_start(
    env: pytest.MonkeyPatch, variable: str, value: str, named: str, hidden: str | None
) -> None:
    env.setenv(variable, value)
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    with pytest.raises(InsecureProductionConfigError) as raised:
        check_production(settings)
    message = str(raised.value)
    assert named in message
    assert len(production_problems(settings)) == 1  # only the variable that changed
    if hidden is not None:
        assert hidden not in message  # never the value


def test_the_test_argon2_profile_is_refused(env: pytest.MonkeyPatch) -> None:
    """The settings already refuse it outside APP_ENV=test; production checks it too, by name only."""
    env.setenv("ARGON2_PROFILE", "test")
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "ARGON2_PROFILE=test requires APP_ENV=test" in str(raised.value)
    env.setenv("ARGON2_PROFILE", "standard")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    settings.argon2_profile = "test"
    assert any("ARGON2_PROFILE" in problem for problem in production_problems(settings))


def test_every_problem_is_listed_at_once(env: pytest.MonkeyPatch) -> None:
    env.setenv("COOKIE_SECURE", "false")
    env.setenv("CORS_ORIGINS", "*")
    env.setenv("LOG_LEVEL", "DEBUG")
    env.setenv("SECRET_KEY", "change-me-change-me-change-me-change-me")
    env.setenv("DATABASE_URL", _url("change-me"))
    assert len(_problems()) == 5


@pytest.mark.parametrize("app_env", ["development", "test"])
def test_development_and_test_are_not_bound_by_production_rules(env: pytest.MonkeyPatch, app_env: str) -> None:
    env.setenv("APP_ENV", app_env)
    env.setenv("COOKIE_SECURE", "false")
    env.setenv("CORS_ORIGINS", "http://localhost:3000")
    env.setenv("LOG_LEVEL", "DEBUG")
    env.setenv("SECRET_KEY", "ci-only-secret-key-not-used-anywhere-else-0123456789")
    env.setenv("DATABASE_URL", _url("change-me"))
    if app_env == "test":
        env.setenv("ARGON2_PROFILE", "test")
    check_production(Settings(_env_file=None))  # type: ignore[call-arg]


def test_a_rejected_setting_never_echoes_its_value(env: pytest.MonkeyPatch) -> None:
    """Pydantic would print the input of a failed validation: a short SECRET_KEY, or the DATABASE_URL."""
    env.setenv("SECRET_KEY", "short-secret-value")
    with pytest.raises(ValidationError) as raised:
        Settings(_env_file=None)  # type: ignore[call-arg]
    assert "short-secret-value" not in str(raised.value)
    assert DB_PASSWORD not in str(raised.value)


# --- at start, the real process ---------------------------------------------------------------------------------


def _start(**overrides: str) -> subprocess.CompletedProcess[str]:
    """Imports the app as uvicorn does (`app.main:app`), in a fresh interpreter with only these variables."""
    environment = {"PATH": os.environ["PATH"], **ENV, **overrides}
    return subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND_DIR,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_the_process_refuses_to_start_without_printing_secrets() -> None:
    weak_password = "change-me"
    weak_secret = "ci-only-secret-key-not-used-anywhere-else-0123456789"
    result = _start(
        COOKIE_SECURE="false",
        CORS_ORIGINS="*",
        LOG_LEVEL="DEBUG",
        SECRET_KEY=weak_secret,
        DATABASE_URL=_url(weak_password),
    )
    assert result.returncode != 0
    assert "InsecureProductionConfigError" in result.stderr
    for name in ("COOKIE_SECURE", "CORS_ORIGINS", "LOG_LEVEL", "SECRET_KEY", "POSTGRES_PASSWORD"):
        assert name in result.stderr
    output = result.stdout + result.stderr
    assert weak_secret not in output
    assert f":{weak_password}@" not in output


def test_the_process_starts_with_a_valid_production_configuration() -> None:
    result = _start()
    assert result.returncode == 0, result.stderr
