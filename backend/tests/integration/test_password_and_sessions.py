"""S2-1: own password change, temporary passwords with a forced change, own sessions, real IP and origin, Origin check.

Every refusal is tried through the API, as a client would: a temporary password reaching a module, someone else's
session, a cross-site change, a wrong current password repeated."""

import logging
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.platform.audit.models import AuditEvent
from app.platform.auth.deps import SESSION_COOKIE
from app.platform.auth.models import User, UserSession
from app.platform.auth.service import set_temporary_password
from tests.conftest import PASSWORD

LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
PASSWORD_URL = "/api/v1/auth/password"
SESSIONS = "/api/v1/auth/sessions"
NEW_PASSWORD = "otra-frase-bien-larga"


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, headers={"X-Requested-With": "test"})


def _login(email: str, password: str = PASSWORD, **headers: str) -> TestClient:
    client = _client()
    response = client.post(LOGIN, json={"email": email, "password": password}, headers=headers)
    assert response.status_code == 200, response.text
    return client


def _user(db: Session, email: str = "admin@districom.test") -> User:
    db.rollback()  # a fresh snapshot of what the API committed
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


def _events(db: Session, event_type: str) -> list[AuditEvent]:
    db.rollback()
    return list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == event_type).order_by(AuditEvent.id)))


# --- Own password change -------------------------------------------------------------------------------------------


def test_change_own_password_closes_the_other_sessions_and_keeps_this_one(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    other = _login("admin@districom.test")
    response = client.post(PASSWORD_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 200, response.text
    assert client.get(ME).status_code == 200  # this session stays
    assert other.get(ME).status_code == 401  # the other one is closed
    assert _client().post(LOGIN, json={"email": "admin@districom.test", "password": PASSWORD}).status_code == 401
    _login("admin@districom.test", NEW_PASSWORD)
    user = _user(db)
    assert user.password_changed_at is not None
    assert not user.must_change_password
    revoked = db.scalar(select(UserSession).where(UserSession.revoked_reason == "password_changed"))
    assert revoked is not None
    assert revoked.revoked_by == user.id
    (event,) = _events(db, "platform.password.changed")
    assert event.user_id == user.id
    assert event.payload == {"was_temporary": False, "sessions_revoked": 1}


@pytest.mark.parametrize(
    ("current", "new", "code"),
    [
        ("wrong-password-123", NEW_PASSWORD, "invalid_current_password"),
        (PASSWORD, "corta", "weak_password"),
        (PASSWORD, PASSWORD, "same_password"),
        (PASSWORD, "admin@districom.test", "weak_password"),
    ],
)
def test_change_own_password_refusals(login, db: Session, current: str, new: str, code: str) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    before = _user(db).password_hash
    response = client.post(PASSWORD_URL, json={"current_password": current, "new_password": new})
    assert response.status_code == 422
    assert response.json()["code"] == code
    assert _user(db).password_hash == before
    assert _events(db, "platform.password.changed") == []


def test_wrong_current_password_is_rate_limited(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    for _ in range(get_settings().login_max_attempts):
        client.post(PASSWORD_URL, json={"current_password": "wrong-password-123", "new_password": NEW_PASSWORD})
    response = client.post(PASSWORD_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 429


def test_password_change_requires_a_session(client: TestClient) -> None:
    response = client.post(PASSWORD_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
    assert response.status_code == 401


def test_passwords_never_appear_in_logs_or_audit(login, db: Session, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    with caplog.at_level(logging.DEBUG):
        client.post(PASSWORD_URL, json={"current_password": PASSWORD, "new_password": NEW_PASSWORD})
        temporary = set_temporary_password(db, _user(db), get_settings(), by=None)
    logged = "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    audited = db.execute(text("SELECT string_agg(payload::text, ' ') FROM audit_events")).scalar_one()
    stored = db.execute(text("SELECT password_hash FROM users")).scalar_one()
    for secret in (PASSWORD, NEW_PASSWORD, temporary):
        assert secret not in logged
        assert secret not in audited
        assert secret not in stored
    assert stored.startswith("$argon2id$")


# --- Temporary password and forced change -------------------------------------------------------------------------


def test_temporary_password_only_lets_its_holder_choose_a_new_one(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    before = _login("admin@districom.test") if make_user() else None
    temporary = set_temporary_password(db, _user(db), get_settings(), by=None)
    assert before is not None
    assert before.get(ME).status_code == 401

    client = _login("admin@districom.test", temporary)
    me = client.get(ME).json()
    assert me["must_change_password"] is True
    assert me["modules"] == []
    assert me["areas"] == []
    # Platform admin with a temporary password: nothing but the password change, also for admin endpoints.
    for method, url in (
        ("GET", "/api/v1/settings"),
        ("GET", "/api/v1/settlements/accounts"),
        ("GET", SESSIONS),
        ("PUT", "/api/v1/auth/access-grants"),
    ):
        response = client.request(
            method, url, json={"email": "admin@districom.test", "key": "commercial", "role": "admin"}
        )
        assert response.status_code == 403, url
        assert response.json()["code"] == "password_change_required"

    response = client.post(PASSWORD_URL, json={"current_password": temporary, "new_password": NEW_PASSWORD})
    assert response.status_code == 200
    assert response.json()["must_change_password"] is False
    assert client.get("/api/v1/settings").status_code == 200
    user = _user(db)
    assert user.temporary_password_expires_at is None
    (event,) = _events(db, "platform.password.changed")
    assert event.payload["was_temporary"] is True


def test_expired_temporary_password_neither_logs_in_nor_changes(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user()
    temporary = set_temporary_password(db, _user(db), get_settings(), by=None)
    client = _login("admin@districom.test", temporary)
    db.execute(update(User).values(temporary_password_expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    response = client.post(PASSWORD_URL, json={"current_password": temporary, "new_password": NEW_PASSWORD})
    assert response.status_code == 401
    assert response.json()["code"] == "temporary_password_expired"
    response = _client().post(LOGIN, json={"email": "admin@districom.test", "password": temporary})
    assert response.status_code == 401
    assert response.json()["code"] == "temporary_password_expired"
    assert [e.payload["reason"] for e in _events(db, "platform.login.failed")] == ["temporary_password_expired"]


def test_a_wrong_password_never_says_the_temporary_one_expired(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user()
    set_temporary_password(db, _user(db), get_settings(), by=None)
    db.execute(update(User).values(temporary_password_expires_at=datetime.now(UTC) - timedelta(minutes=1)))
    db.commit()
    response = _client().post(LOGIN, json={"email": "admin@districom.test", "password": "wrong-password-123"})
    assert response.json()["code"] == "invalid_credentials"


def test_reset_audit_names_the_user_not_the_password(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = make_user()
    target = make_user(email="viewer@districom.test", role="viewer")
    set_temporary_password(db, target, get_settings(), by=admin.id)
    (event,) = _events(db, "platform.password.reset")
    assert event.user_id == admin.id
    assert event.entity_id == str(target.id)
    assert set(event.payload) == {"sessions_revoked", "expires_at"}


def test_cli_reset_password_prints_the_temporary_password_once(make_user, db: Session, capsys, caplog) -> None:  # type: ignore[no-untyped-def]
    from app import cli

    make_user()
    with caplog.at_level(logging.DEBUG):
        assert cli.main(["reset-password", "--email", "admin@districom.test"]) == 0
    printed = capsys.readouterr().out
    temporary = printed.strip().rsplit(" ", 1)[-1]
    assert len(temporary) == 19
    assert temporary not in "\n".join(f"{r.getMessage()} {r.__dict__}" for r in caplog.records)
    assert _user(db).must_change_password
    _login("admin@districom.test", temporary)
    assert cli.main(["reset-password", "--email", "nobody@districom.test"]) == 1


# --- Own sessions -------------------------------------------------------------------------------------------------


def test_list_and_revoke_own_sessions(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    other = _login("admin@districom.test", **{"User-Agent": "Tablet del deposito"})
    sessions = client.get(SESSIONS).json()
    assert len(sessions) == 2
    assert [s["current"] for s in sessions].count(True) == 1
    other_id = next(s["id"] for s in sessions if s["user_agent"] == "Tablet del deposito")
    assert client.post(f"{SESSIONS}/{other_id}/revoke").status_code == 204
    assert other.get(ME).status_code == 401
    assert client.post(f"{SESSIONS}/{other_id}/revoke").status_code == 404  # already closed
    assert len(client.get(SESSIONS).json()) == 1
    (event,) = _events(db, "platform.session.revoked")
    assert event.entity_id == str(other_id)


def test_revoke_other_sessions(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    others = [_login("admin@districom.test") for _ in range(2)]
    assert client.post(f"{SESSIONS}/revoke-others").json() == {"revoked": 2}
    assert all(o.get(ME).status_code == 401 for o in others)
    assert client.get(ME).status_code == 200


def test_cannot_see_or_revoke_someone_elses_session(login, make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user(email="viewer@districom.test", role="viewer")
    victim = _login("viewer@districom.test")
    attacker = login("admin")  # even a platform admin: own sessions only on these endpoints
    victim_ids = [s["id"] for s in victim.get(SESSIONS).json()]
    assert all(s["id"] not in victim_ids for s in attacker.get(SESSIONS).json())
    assert attacker.post(f"{SESSIONS}/{victim_ids[0]}/revoke").status_code == 404
    assert victim.get(ME).status_code == 200


def test_closed_and_expired_sessions_are_not_listed(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    _login("admin@districom.test").post("/api/v1/auth/logout")
    _login("admin@districom.test")
    db.execute(text("UPDATE user_sessions SET expires_at = now() - interval '1 minute' WHERE id = 3"))
    db.commit()
    assert len(client.get(SESSIONS).json()) == 1
    logout = db.scalar(select(UserSession).where(UserSession.id == 2))
    assert logout is not None
    assert logout.revoked_reason == "logout"


# --- Real IP and origin (audit only) --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ip", "origin"),
    [("192.168.1.25", "lan"), ("100.101.102.103", "tailscale"), ("127.0.0.1", "local"), ("200.45.12.3", "other")],
)
def test_login_records_ip_and_origin(make_user, db: Session, ip: str, origin: str) -> None:  # type: ignore[no-untyped-def]
    from app.main import app

    make_user()
    with TestClient(app, headers={"X-Requested-With": "test"}, client=(ip, 50000)) as client:
        assert client.post(LOGIN, json={"email": "admin@districom.test", "password": PASSWORD}).status_code == 200
        (listed,) = client.get(SESSIONS).json()
    assert (listed["ip"], listed["access_origin"]) == (ip, origin)
    (event,) = _events(db, "LOGIN")
    assert event.payload == {"ip": ip, "access_origin": origin}


def test_failed_logins_are_audited_without_the_email(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    from app.main import app

    user = make_user()
    with TestClient(app, headers={"X-Requested-With": "test"}, client=("100.101.102.103", 50000)) as client:
        client.post(LOGIN, json={"email": "admin@districom.test", "password": "wrong-password-123"})
        client.post(LOGIN, json={"email": "nadie@districom.test", "password": "wrong-password-123"})
    known, unknown = _events(db, "platform.login.failed")
    assert known.user_id == user.id
    assert unknown.user_id is None
    for event in (known, unknown):
        assert event.payload == {"reason": "invalid_credentials", "ip": "100.101.102.103", "access_origin": "tailscale"}
        assert "districom.test" not in str(event.payload)


def test_origin_never_grants_access(make_user) -> None:  # type: ignore[no-untyped-def]
    """The server itself or the LAN do not replace a session: without one, 401 from anywhere."""
    from app.main import app

    make_user()
    for ip in ("127.0.0.1", "192.168.1.25", "100.101.102.103"):
        with TestClient(app, headers={"X-Requested-With": "test"}, client=(ip, 50000)) as client:
            assert client.get(ME).status_code == 401
            assert client.get("/api/v1/settings").status_code == 401


# --- Origin check on changes ----------------------------------------------------------------------------------------


def test_cross_origin_change_is_refused_even_with_a_valid_session(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    response = client.post(
        PASSWORD_URL,
        json={"current_password": PASSWORD, "new_password": NEW_PASSWORD},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "origin_not_allowed"
    assert _events(db, "platform.password.changed") == []
    response = client.post(f"{SESSIONS}/revoke-others", headers={"Sec-Fetch-Site": "cross-site"})
    assert response.status_code == 403


def test_same_origin_and_trusted_origin_changes_go_through(login, monkeypatch: pytest.MonkeyPatch) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    assert client.post(f"{SESSIONS}/revoke-others", headers={"Origin": "http://testserver"}).status_code == 200
    assert client.post(f"{SESSIONS}/revoke-others", headers={"Origin": "http://localhost:3000"}).status_code == 403
    monkeypatch.setattr(get_settings(), "trusted_origins", ["http://localhost:3000"])
    assert client.post(f"{SESSIONS}/revoke-others", headers={"Origin": "http://localhost:3000"}).status_code == 200


def test_cross_origin_login_is_refused(make_user) -> None:  # type: ignore[no-untyped-def]
    make_user()
    response = _client().post(
        LOGIN,
        json={"email": "admin@districom.test", "password": PASSWORD},
        headers={"Origin": "https://evil.example.com"},
    )
    assert response.status_code == 403
    assert SESSION_COOKIE not in response.cookies


def test_reads_are_not_subject_to_the_origin_check(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    assert client.get(ME, headers={"Origin": "https://evil.example.com"}).status_code == 200
