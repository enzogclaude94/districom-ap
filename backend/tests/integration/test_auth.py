import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.audit.models import AuditEvent
from app.platform.auth.deps import SESSION_COOKIE
from app.platform.auth.models import User, UserSession
from tests.conftest import PASSWORD

LOGIN = "/api/v1/auth/login"


def test_login_success_sets_httponly_session_cookie(client: TestClient, make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user()
    response = client.post(LOGIN, json={"email": "admin@districom.test", "password": PASSWORD})
    assert response.status_code == 200
    assert response.json()["role"] == "admin"
    cookie_header = response.headers["set-cookie"]
    assert f"{SESSION_COOKIE}=" in cookie_header
    assert "HttpOnly" in cookie_header
    assert "samesite=lax" in cookie_header.lower()
    assert client.get("/api/v1/auth/me").json()["email"] == "admin@districom.test"
    assert db.scalar(select(AuditEvent.event_type).where(AuditEvent.event_type == "LOGIN")) == "LOGIN"


def test_session_token_is_stored_hashed(client: TestClient, make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user()
    client.post(LOGIN, json={"email": "admin@districom.test", "password": PASSWORD})
    token = client.cookies[SESSION_COOKIE]
    stored = db.scalar(select(UserSession.token_hash))
    assert stored is not None
    assert token.encode() != stored
    assert len(stored) == 32


@pytest.mark.parametrize(
    ("email", "password"),
    [("admin@districom.test", "wrong-password-123"), ("nobody@districom.test", PASSWORD)],
)
def test_login_failure_does_not_reveal_account_existence(
    client: TestClient, make_user, email: str, password: str
) -> None:  # type: ignore[no-untyped-def]
    make_user()
    response = client.post(LOGIN, json={"email": email, "password": password})
    assert response.status_code == 401
    body = response.json()
    assert body["code"] == "invalid_credentials"
    assert body["message"] == "Email o contraseña incorrectos."
    assert SESSION_COOKIE not in client.cookies


def test_inactive_user_cannot_login(client: TestClient, make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    user = make_user()
    user.active = False
    db.commit()
    response = client.post(LOGIN, json={"email": "admin@districom.test", "password": PASSWORD})
    assert response.status_code == 401


def test_protected_endpoint_requires_session(client: TestClient) -> None:
    response = client.get("/api/v1/auth/me")
    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def test_viewer_is_forbidden_on_admin_endpoint(login) -> None:  # type: ignore[no-untyped-def]
    client = login("viewer")
    response = client.get("/api/v1/settings")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"


def test_admin_can_read_settings_with_defaults(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    settings = {s["key"]: s for s in response.json()}
    assert settings["currency"] == {"key": "currency", "value": "ARS", "is_default": True}


def test_logout_revokes_session(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    token = client.cookies[SESSION_COOKIE]
    assert client.post("/api/v1/auth/logout").status_code == 204
    client.cookies.set(SESSION_COOKIE, token)  # replaying the old cookie must not work
    assert client.get("/api/v1/auth/me").status_code == 401


def test_password_is_hashed_with_argon2id(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user()
    stored = db.scalar(select(User.password_hash))
    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert PASSWORD not in stored


def test_login_is_rate_limited(client: TestClient, make_user) -> None:  # type: ignore[no-untyped-def]
    make_user()
    for _ in range(5):
        client.post(LOGIN, json={"email": "admin@districom.test", "password": "wrong-password-123"})
    response = client.post(LOGIN, json={"email": "admin@districom.test", "password": PASSWORD})
    assert response.status_code == 429


def test_mutating_request_without_csrf_header_is_rejected(client: TestClient, make_user) -> None:  # type: ignore[no-untyped-def]
    make_user()
    response = client.post(
        LOGIN,
        json={"email": "admin@districom.test", "password": PASSWORD},
        headers={"X-Requested-With": ""},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "csrf_header_missing"


def test_password_never_appears_in_logs(client: TestClient, make_user, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    make_user()
    secret = "super-secret-wrong-pass"
    with caplog.at_level(logging.DEBUG):
        client.post(LOGIN, json={"email": "admin@districom.test", "password": secret})
        client.post(LOGIN, json={"email": "x", "password": secret})
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert "request" in logged
    assert secret not in logged
