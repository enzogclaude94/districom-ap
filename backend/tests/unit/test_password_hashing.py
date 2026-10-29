import pytest
from argon2 import extract_parameters, profiles
from pydantic import ValidationError

from app.core.config import Settings
from app.platform.auth import security


def test_the_test_suite_hashes_at_the_reduced_cost() -> None:
    """tests/conftest.py sets ARGON2_PROFILE=test explicitly: same Argon2id format, lowest cost."""
    hashed = security.hash_password("correct-horse-battery")
    params = extract_parameters(hashed)
    assert hashed.startswith("$argon2id$v=19$m=8,t=1,p=1$")
    assert (params.time_cost, params.memory_cost, params.parallelism) == (1, 8, 1)
    assert (params.salt_len, params.hash_len) == (16, 32)
    assert security.verify_password(hashed, "correct-horse-battery")
    assert not security.verify_password(hashed, "wrong-horse-battery")


@pytest.mark.parametrize("app_env", ["development", "production"])
def test_a_normal_configuration_keeps_the_standard_parameters(monkeypatch: pytest.MonkeyPatch, app_env: str) -> None:
    monkeypatch.setenv("APP_ENV", app_env)
    monkeypatch.delenv("ARGON2_PROFILE")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.argon2_profile == "standard"
    hashed = security.hasher_for(settings).hash("correct-horse-battery")
    assert extract_parameters(hashed) == profiles.RFC_9106_LOW_MEMORY
    assert hashed.startswith("$argon2id$v=19$m=65536,t=3,p=4$")


def test_the_test_profile_is_refused_outside_tests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("APP_ENV", "production")
    monkeypatch.setenv("ARGON2_PROFILE", "test")
    with pytest.raises(ValidationError, match="requires APP_ENV=test"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_verification_reads_the_parameters_from_the_hash() -> None:
    """Hashes of either profile verify with the same code: nothing about the format or the check changed."""
    standard = security.STANDARD_HASHER.hash("correct-horse-battery")
    cheap = security.TEST_HASHER.hash("correct-horse-battery")
    assert security.TEST_HASHER.verify(standard, "correct-horse-battery")
    assert security.STANDARD_HASHER.verify(cheap, "correct-horse-battery")
    assert security.verify_password(standard, "correct-horse-battery")
