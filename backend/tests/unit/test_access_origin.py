"""S2: where a request comes from (audit only) and which origins may send changes."""

import pytest
from starlette.requests import Request

from app.core.middleware import _normalized_origin, origin_rejection
from app.platform.auth.network import access_origin
from app.platform.auth.security import new_temporary_password, password_problem


@pytest.mark.parametrize(
    ("ip", "origin"),
    [
        ("127.0.0.1", "local"),
        ("::1", "local"),
        ("100.101.102.103", "tailscale"),
        ("100.64.0.1", "tailscale"),
        ("fd7a:115c:a1e0::1234", "tailscale"),
        ("192.168.1.25", "lan"),
        ("10.0.0.8", "lan"),
        ("172.16.4.2", "lan"),
        ("::ffff:192.168.1.25", "lan"),
        ("fe80::1", "lan"),
        ("8.8.8.8", "other"),
        ("100.128.0.1", "other"),  # just outside the Tailscale range
        (None, "other"),
    ],
)
def test_access_origin(ip: str | None, origin: str) -> None:
    assert access_origin(ip) == origin


@pytest.mark.parametrize(
    ("value", "normalized"),
    [
        ("https://DistriCom.Example.com", "https://districom.example.com"),
        ("https://districom.example.com:443", "https://districom.example.com"),
        ("http://localhost:3000/", "http://localhost:3000"),
        ("http://[::1]:3000", "http://[::1]:3000"),
        ("null", None),
        ("file://", None),
        ("https://user@districom.example.com", None),
        ("https://districom.example.com/path", None),
        ("https://districom.example.com:99999", None),
    ],
)
def test_normalized_origin(value: str, normalized: str | None) -> None:
    assert _normalized_origin(value) == normalized


def _request(headers: dict[str, str], host: str = "districom.example.com", scheme: str = "https") -> Request:
    raw = [(b"host", host.encode())] + [(k.lower().encode(), v.encode()) for k, v in headers.items()]
    return Request({"type": "http", "method": "POST", "path": "/api/v1/x", "headers": raw, "scheme": scheme})


@pytest.mark.parametrize(
    ("headers", "rejection"),
    [
        ({"Origin": "https://districom.example.com"}, None),
        ({"Origin": "https://districom.example.com:443"}, None),
        ({}, None),  # a script or the CLI: no browser, no Origin
        ({"Sec-Fetch-Site": "same-origin"}, None),
        ({"Origin": "https://evil.example.com"}, "origin_not_allowed"),
        ({"Origin": "http://districom.example.com"}, "origin_not_allowed"),  # other scheme
        ({"Origin": "https://districom.example.com.evil.net"}, "origin_not_allowed"),
        ({"Origin": "null"}, "origin_not_allowed"),
        ({"Sec-Fetch-Site": "cross-site"}, "cross_site"),
        ({"Sec-Fetch-Site": "same-site"}, "cross_site"),
    ],
)
def test_origin_rejection(headers: dict[str, str], rejection: str | None) -> None:
    assert origin_rejection(_request(headers)) == rejection


def test_temporary_passwords_are_long_random_and_unambiguous() -> None:
    passwords = {new_temporary_password() for _ in range(200)}
    assert len(passwords) == 200
    for password in passwords:
        assert len(password) == 19
        assert password.count("-") == 3
        assert not set(password) & set("0O1lI")
        assert password_problem(password, email="x@districom.test") is None


@pytest.mark.parametrize(
    ("password", "problem"),
    [
        ("corta", "al menos 12"),
        (" " * 14, "solo espacios"),
        ("Ana@Districom.test", "tu email"),
        ("x" * 257, "como mucho 256"),
        ("una-frase-larga-y-propia", None),
    ],
)
def test_password_policy(password: str, problem: str | None) -> None:
    result = password_problem(password, email="ana@districom.test")
    assert (result is None) if problem is None else (problem in (result or ""))


def test_development_trusts_the_dev_server_unless_configured() -> None:
    from app.core.config import Settings

    base = {"database_url": "postgresql://x/y", "secret_key": "k" * 32, "_env_file": None, "argon2_profile": "standard"}
    assert Settings(app_env="development", **base).trusted_origins == ["http://localhost:3000"]  # type: ignore[arg-type]
    assert Settings(app_env="development", trusted_origins="", **base).trusted_origins == []  # type: ignore[arg-type]
    assert Settings(app_env="production", **base).trusted_origins == []  # type: ignore[arg-type]
    production = Settings(app_env="production", trusted_origins="https://a.example, https://b.example", **base)  # type: ignore[arg-type]
    assert production.trusted_origins == ["https://a.example", "https://b.example"]
