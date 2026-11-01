"""S2-2: user administration by platform admins (ADR 015).

Every refusal is tried through the API the way an attacker or a mistaken admin would: other roles, a temporary
password, extra fields, one's own account, the last active admin, an area without its module."""

import threading

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User, UserModuleRole
from tests.conftest import PASSWORD

USERS = "/api/v1/users"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"
AREA = "settlements.employee_finance"


def _client() -> TestClient:
    from app.main import app

    return TestClient(app, headers={"X-Requested-With": "test"})


def _login(email: str, password: str = PASSWORD) -> TestClient:
    client = _client()
    response = client.post(LOGIN, json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return client


def _user(db: Session, email: str) -> User:
    db.rollback()
    user = db.scalar(select(User).where(User.email == email))
    assert user is not None
    return user


def _events(db: Session, prefix: str) -> list[AuditEvent]:
    db.rollback()
    return list(db.scalars(select(AuditEvent).where(AuditEvent.event_type.startswith(prefix)).order_by(AuditEvent.id)))


@pytest.fixture
def admin(login) -> TestClient:  # type: ignore[no-untyped-def]
    return login("admin")  # type: ignore[no-any-return]


def _create(admin: TestClient, email: str = "ana@districom.test", role: str = "viewer") -> dict:  # type: ignore[type-arg]
    response = admin.post(USERS, json={"email": email, "full_name": "Ana Pérez", "role": role})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


# --- Who can administer ---------------------------------------------------------------------------------------------


def _every_endpoint(user_id: int) -> list[tuple[str, str, dict | None]]:  # type: ignore[type-arg]
    return [
        ("GET", USERS, None),
        ("GET", f"{USERS}/access-catalog", None),
        ("GET", f"{USERS}/{user_id}", None),
        ("POST", USERS, {"email": "x@districom.test", "full_name": "X", "role": "admin"}),
        ("PATCH", f"{USERS}/{user_id}", {"role": "admin"}),
        ("POST", f"{USERS}/{user_id}/deactivate", None),
        ("POST", f"{USERS}/{user_id}/activate", None),
        ("POST", f"{USERS}/{user_id}/reset-password", None),
        ("PUT", f"{USERS}/{user_id}/access", {"key": AREA, "role": "admin"}),
        ("POST", f"{USERS}/{user_id}/sessions/revoke-all", None),
        ("POST", f"{USERS}/{user_id}/sessions/1/revoke", None),
    ]


@pytest.mark.parametrize(
    "grants",
    [
        [],
        [("commercial", "admin")],
        [("settlements", "admin")],
        [("settlements", "admin"), (AREA, "admin"), ("settlements.store_sys_import", "admin")],
    ],
    ids=["no-module", "commercial-admin", "settlements-admin", "settlements-and-areas-admin"],
)
def test_only_platform_admins_reach_user_administration(login, grant, db: Session, grants) -> None:  # type: ignore[no-untyped-def]
    viewer = login("viewer")
    for key, role in grants:
        grant("viewer", key, role)
    me = _user(db, "viewer@districom.test")
    for method, url, body in _every_endpoint(me.id):
        response = viewer.request(method, url, json=body)
        assert response.status_code == 403, (method, url)
    assert _user(db, "viewer@districom.test").role == "viewer"
    assert _events(db, "platform.user") == []


def test_no_session_no_administration(client: TestClient, make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    user = make_user()
    for method, url, body in _every_endpoint(user.id):
        assert client.request(method, url, json=body).status_code == 401, (method, url)


def test_an_admin_with_a_temporary_password_administers_nothing(admin: TestClient, make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    created = _create(admin, "otro-admin@districom.test", role="admin")
    other = _login("otro-admin@districom.test", created["temporary_password"])
    for method, url, body in _every_endpoint(created["user"]["id"]):
        response = other.request(method, url, json=body)
        assert response.status_code == 403, (method, url)
        assert response.json()["code"] == "password_change_required"


# --- Create, edit, activate -----------------------------------------------------------------------------------------


def test_create_user_with_a_temporary_password_shown_once(admin: TestClient, db: Session) -> None:
    created = _create(admin)
    temporary = created["temporary_password"]
    assert created["user"]["must_change_password"] is True
    assert created["user"]["modules"] == []
    assert created["user"]["areas"] == []
    detail = admin.get(f"{USERS}/{created['user']['id']}").json()
    assert "temporary_password" not in detail
    assert temporary not in str(admin.get(USERS, params={"status": "all"}).json())
    stored = _user(db, "ana@districom.test").password_hash
    assert stored is not None
    assert stored.startswith("$argon2id$")
    assert temporary not in stored

    ana = _login("ana@districom.test", temporary)
    assert ana.get(ME).json()["must_change_password"] is True
    assert ana.get("/api/v1/settings").status_code == 403
    events = [e.event_type for e in _events(db, "platform.")]
    assert events[:2] == ["platform.user.created", "platform.password.reset"]
    audited = db.execute(text("SELECT string_agg(payload::text, ' ') FROM audit_events")).scalar_one()
    assert temporary not in audited
    assert "ana@districom.test" not in audited


@pytest.mark.parametrize(
    "body",
    [
        {"email": "b@districom.test", "full_name": "B", "role": "superadmin"},
        {"email": "b@districom.test", "full_name": "B", "role": "viewer", "active": False},
        {"email": "b@districom.test", "full_name": "B", "role": "viewer", "password": "una-que-elijo-yo"},
        {"email": "b@districom.test", "full_name": "B", "role": "viewer", "must_change_password": False},
        {"email": "b@districom.test", "full_name": "B", "role": "viewer", "modules": [{"key": "settlements"}]},
        {"email": "sin-arroba", "full_name": "B", "role": "viewer"},
        {"email": "b@districom.test", "full_name": "  ", "role": "viewer"},
    ],
)
def test_create_rejects_anything_beyond_its_fields(admin: TestClient, db: Session, body: dict) -> None:  # type: ignore[type-arg]
    assert admin.post(USERS, json=body).status_code == 422
    db.rollback()
    assert db.scalar(select(User).where(User.email == "b@districom.test")) is None


def test_create_refuses_a_taken_email_in_any_case(admin: TestClient) -> None:
    _create(admin)
    response = admin.post(USERS, json={"email": "ANA@districom.test", "full_name": "Otra", "role": "viewer"})
    assert response.status_code == 409
    assert response.json()["code"] == "email_taken"


@pytest.mark.parametrize(
    "body", [{"active": False}, {"must_change_password": False}, {"password_hash": "x"}, {"role": "root"}]
)
def test_update_rejects_fields_it_does_not_own(admin: TestClient, body: dict) -> None:  # type: ignore[type-arg]
    created = _create(admin)
    assert admin.patch(f"{USERS}/{created['user']['id']}", json=body).status_code == 422


def test_edit_name_email_and_role_is_audited_without_personal_data(admin: TestClient, db: Session) -> None:
    user_id = _create(admin)["user"]["id"]
    response = admin.patch(
        f"{USERS}/{user_id}",
        json={"full_name": "Ana María Pérez", "email": "ana.perez@districom.test", "role": "admin"},
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["full_name"], body["email"], body["role"]) == ("Ana María Pérez", "ana.perez@districom.test", "admin")
    (event,) = _events(db, "platform.user.updated")
    assert event.payload["before"] == {"role": "viewer"}
    assert event.payload["after"] == {"role": "admin", "email_changed": True, "full_name_changed": True}
    assert "Ana" not in str(event.payload)
    assert "districom.test" not in str(event.payload)


def test_deactivate_closes_every_session_and_activate_does_not_reopen_them(admin: TestClient, db: Session) -> None:
    created = _create(admin)
    ana = _login("ana@districom.test", created["temporary_password"])
    ana.post(
        "/api/v1/auth/password",
        json={"current_password": created["temporary_password"], "new_password": "ana-frase-larga-1"},
    )
    tablet = _login("ana@districom.test", "ana-frase-larga-1")
    user_id = created["user"]["id"]
    assert admin.get(f"{USERS}/{user_id}").json()["open_sessions"] == 2

    response = admin.post(f"{USERS}/{user_id}/deactivate")
    assert response.status_code == 200
    assert response.json()["active"] is False
    assert response.json()["sessions"] == []
    assert ana.get(ME).status_code == 401
    assert tablet.get(ME).status_code == 401
    assert (
        _client().post(LOGIN, json={"email": "ana@districom.test", "password": "ana-frase-larga-1"}).status_code == 401
    )
    (event,) = _events(db, "platform.user.deactivated")
    assert event.payload["after"] == {"active": False, "sessions_revoked": 2}
    reasons = db.scalars(
        text("SELECT DISTINCT revoked_reason FROM user_sessions WHERE user_id = :id"), {"id": user_id}
    ).all()
    assert reasons == ["user_deactivated"]

    assert admin.post(f"{USERS}/{user_id}/activate").json()["active"] is True
    assert ana.get(ME).status_code == 401  # the old sessions stay closed
    _login("ana@districom.test", "ana-frase-larga-1")


def test_reset_password_closes_every_session_and_forces_a_change(admin: TestClient, db: Session) -> None:
    created = _create(admin)
    ana = _login("ana@districom.test", created["temporary_password"])
    response = admin.post(f"{USERS}/{created['user']['id']}/reset-password")
    assert response.status_code == 200
    new_temporary = response.json()["temporary_password"]
    assert new_temporary != created["temporary_password"]
    assert ana.get(ME).status_code == 401
    assert (
        _client()
        .post(LOGIN, json={"email": "ana@districom.test", "password": created["temporary_password"]})
        .status_code
        == 401
    )
    assert _login("ana@districom.test", new_temporary).get(ME).json()["must_change_password"] is True
    resets = _events(db, "platform.password.reset")
    assert resets[-1].payload["sessions_revoked"] == 1
    assert resets[-1].user_id == _user(db, "admin@districom.test").id


def test_reset_of_an_inactive_user_is_refused(admin: TestClient) -> None:
    user_id = _create(admin)["user"]["id"]
    admin.post(f"{USERS}/{user_id}/deactivate")
    response = admin.post(f"{USERS}/{user_id}/reset-password")
    assert response.status_code == 409
    assert response.json()["code"] == "user_inactive"


# --- One's own account and the last admin ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "deactivate", None),
        ("PATCH", "", {"role": "viewer"}),
        ("POST", "reset-password", None),
    ],
)
def test_nobody_changes_their_own_role_state_or_password_here(
    admin: TestClient, db: Session, method, path, body
) -> None:  # type: ignore[no-untyped-def]
    _create(admin, "otro@districom.test", role="admin")  # another active admin: the refusal is about oneself
    me = _user(db, "admin@districom.test")
    response = admin.request(method, f"{USERS}/{me.id}/{path}".rstrip("/"), json=body)
    assert response.status_code == 409
    assert response.json()["code"] == "own_account"
    assert admin.get(ME).status_code == 200
    me = _user(db, "admin@districom.test")
    assert (me.role, me.active, me.must_change_password) == ("admin", True, False)


def test_editing_ones_own_name_is_allowed(admin: TestClient, db: Session) -> None:
    me = _user(db, "admin@districom.test")
    assert admin.patch(f"{USERS}/{me.id}", json={"full_name": "Enzo"}).json()["full_name"] == "Enzo"


def test_admins_administer_each_other_but_never_themselves(admin: TestClient, db: Session) -> None:
    other = _create(admin, "otro@districom.test", role="admin")
    other_client = _login("otro@districom.test", other["temporary_password"])
    other_client.post(
        "/api/v1/auth/password",
        json={"current_password": other["temporary_password"], "new_password": "otra-frase-larga-1"},
    )
    me = _user(db, "admin@districom.test")
    # Two active admins: one can deactivate the other...
    assert other_client.post(f"{USERS}/{me.id}/deactivate").status_code == 200
    # ...and the remaining one cannot demote themselves (nor could anyone else: there is nobody else).
    response = other_client.patch(f"{USERS}/{other['user']['id']}", json={"role": "viewer"})
    assert response.status_code == 409
    assert response.json()["code"] == "own_account"
    assert _user(db, "otro@districom.test").role == "admin"
    # Inactive admins do not count: reactivating one makes two again.
    assert other_client.post(f"{USERS}/{me.id}/activate").status_code == 200
    assert other_client.patch(f"{USERS}/{me.id}", json={"role": "viewer"}).status_code == 200
    response = _login("admin@districom.test").get(USERS)
    assert response.status_code == 403  # demoted: no longer administers


def test_the_service_never_leaves_no_active_admin(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    """Through the API the actor is always another active admin, so the guard shows only under a race (below); it
    holds on its own too, whoever calls the service (an inactive admin does not count)."""
    from app.platform.users import service

    lone = make_user()
    gone = make_user(email="viejo@districom.test", role="admin")
    gone.active = False
    db.commit()
    for action in (
        lambda: service.set_active(db, gone, lone, False),
        lambda: service.update_user(db, gone, lone, email=None, full_name=None, role="viewer"),
    ):
        with pytest.raises(service.UserAdminError) as refused:
            action()
        assert refused.value.code == "last_admin"
        db.rollback()
    assert _user(db, "admin@districom.test").role == "admin"
    assert _user(db, "admin@districom.test").active


def test_two_admins_demoting_each_other_at_once_leave_one_admin(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    """The last-admin check locks every active admin row: of two crossed demotions, exactly one succeeds."""
    a = make_user(email="a@districom.test", role="admin")
    b = make_user(email="b@districom.test", role="admin")
    clients = {"a": _login("a@districom.test"), "b": _login("b@districom.test")}
    results: dict[str, int] = {}
    barrier = threading.Barrier(2)

    def demote(who: str, target: int) -> None:
        barrier.wait()
        results[who] = clients[who].patch(f"{USERS}/{target}", json={"role": "viewer"}).status_code

    threads = [threading.Thread(target=demote, args=("a", b.id)), threading.Thread(target=demote, args=("b", a.id))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results.values()) == [200, 409]
    db.rollback()
    assert db.scalar(text("SELECT count(*) FROM users WHERE role = 'admin' AND active")) == 1


# --- Access: modules and protected areas ----------------------------------------------------------------------------


def test_grant_module_and_area_roles(admin: TestClient, db: Session) -> None:
    created = _create(admin)
    user_id = created["user"]["id"]
    response = admin.put(f"{USERS}/{user_id}/access", json={"key": "settlements", "role": "operator"})
    assert response.status_code == 200
    assert response.json()["modules"] == [{"key": "settlements", "name": "Rendiciones", "role": "operator"}]
    assert response.json()["areas"] == []

    ana = _login("ana@districom.test", created["temporary_password"])
    ana.post(
        "/api/v1/auth/password",
        json={"current_password": created["temporary_password"], "new_password": "ana-frase-larga-1"},
    )
    me = ana.get(ME).json()
    assert [m["key"] for m in me["modules"]] == ["settlements"]
    assert me["areas"] == []  # a module role never implies its protected areas
    assert ana.get("/api/v1/settlements/employee-finance/accounts").status_code == 404

    response = admin.put(f"{USERS}/{user_id}/access", json={"key": AREA, "role": "viewer"})
    assert response.status_code == 200
    assert [a["key"] for a in response.json()["areas"]] == [AREA]
    assert [a["key"] for a in ana.get(ME).json()["areas"]] == [AREA]  # same session: read on every request
    grants = _events(db, "platform.module_role.granted")
    assert [e.payload["after"] for e in grants] == [
        {"module": "settlements", "role": "operator"},
        {"module": AREA, "role": "viewer"},
    ]


def test_an_area_needs_a_role_in_its_module(admin: TestClient, db: Session) -> None:
    user_id = _create(admin)["user"]["id"]
    response = admin.put(f"{USERS}/{user_id}/access", json={"key": AREA, "role": "admin"})
    assert response.status_code == 409
    assert response.json()["code"] == "area_requires_module"
    db.rollback()
    assert db.scalar(select(UserModuleRole).where(UserModuleRole.user_id == user_id)) is None


def test_removing_a_module_removes_its_areas(admin: TestClient, db: Session) -> None:
    user_id = _create(admin)["user"]["id"]
    admin.put(f"{USERS}/{user_id}/access", json={"key": "settlements", "role": "operator"})
    admin.put(f"{USERS}/{user_id}/access", json={"key": AREA, "role": "admin"})
    admin.put(f"{USERS}/{user_id}/access", json={"key": "commercial", "role": "viewer"})
    response = admin.put(f"{USERS}/{user_id}/access", json={"key": "settlements", "role": None})
    assert response.status_code == 200
    assert [m["key"] for m in response.json()["modules"]] == ["commercial"]
    assert response.json()["areas"] == []
    revoked = [e.payload["before"]["module"] for e in _events(db, "platform.module_role.revoked")]
    assert sorted(revoked) == sorted(["settlements", AREA])


def test_platform_admins_get_areas_but_no_module_rows(admin: TestClient, db: Session) -> None:
    other_id = _create(admin, "otro@districom.test", role="admin")["user"]["id"]
    response = admin.put(f"{USERS}/{other_id}/access", json={"key": "settlements", "role": "viewer"})
    assert response.status_code == 409
    assert response.json()["code"] == "platform_admin_module"
    # The area is never inherited, so it can be granted, also to oneself (ADR 010).
    me = _user(db, "admin@districom.test")
    assert admin.put(f"{USERS}/{me.id}/access", json={"key": AREA, "role": "admin"}).status_code == 200
    assert [a["key"] for a in admin.get(ME).json()["areas"]] == [AREA]


@pytest.mark.parametrize(
    "body",
    [
        {"key": "settlements.inexistente", "role": "admin"},
        {"key": "superadmin", "role": "admin"},
        {"key": "settlements", "role": "owner"},
        {"key": "settlements", "role": "admin", "user_id": 1},
    ],
)
def test_access_rejects_unknown_keys_roles_and_fields(admin: TestClient, body: dict) -> None:  # type: ignore[type-arg]
    user_id = _create(admin)["user"]["id"]
    assert admin.put(f"{USERS}/{user_id}/access", json=body).status_code == 422


# --- Sessions and activity ------------------------------------------------------------------------------------------


def test_admin_sees_and_closes_a_users_sessions(admin: TestClient, db: Session) -> None:
    created = _create(admin)
    user_id = created["user"]["id"]
    first = _login("ana@districom.test", created["temporary_password"])
    second = _login("ana@districom.test", created["temporary_password"])
    sessions = admin.get(f"{USERS}/{user_id}").json()["sessions"]
    assert len(sessions) == 2
    assert {"ip", "access_origin", "user_agent", "last_seen_at"} <= set(sessions[0])
    assert admin.post(f"{USERS}/{user_id}/sessions/{sessions[0]['id']}/revoke").json() == {"revoked": 1}
    assert sorted([first.get(ME).status_code, second.get(ME).status_code]) == [200, 401]
    assert admin.post(f"{USERS}/{user_id}/sessions/{sessions[0]['id']}/revoke").status_code == 404
    assert admin.post(f"{USERS}/{user_id}/sessions/revoke-all").json() == {"revoked": 1}
    assert first.get(ME).status_code == 401
    assert second.get(ME).status_code == 401
    reasons = db.scalars(
        text("SELECT DISTINCT revoked_reason FROM user_sessions WHERE user_id = :id"), {"id": user_id}
    ).all()
    assert reasons == ["revoked_by_admin"]


def test_a_session_of_another_user_cannot_be_closed_through_this_one(admin: TestClient, db: Session) -> None:
    ana_id = _create(admin)["user"]["id"]
    _create(admin, "beto@districom.test")
    admin_session = db.scalar(text("SELECT id FROM user_sessions ORDER BY id LIMIT 1"))
    response = admin.post(f"{USERS}/{ana_id}/sessions/{admin_session}/revoke")
    assert response.status_code == 404
    assert admin.get(ME).status_code == 200


def test_activity_lists_what_was_done_and_by_whom(admin: TestClient) -> None:
    user_id = _create(admin)["user"]["id"]
    admin.put(f"{USERS}/{user_id}/access", json={"key": "commercial", "role": "viewer"})
    admin.post(f"{USERS}/{user_id}/deactivate")
    activity = admin.get(f"{USERS}/{user_id}").json()["activity"]
    assert [a["event_type"] for a in activity] == [
        "platform.user.deactivated",
        "platform.module_role.granted",
        "platform.password.reset",
        "platform.user.created",
    ]
    assert {a["actor_name"] for a in activity} == {"Usuario Test"}


def test_list_filters_and_counts(admin: TestClient) -> None:
    _create(admin)
    beto = _create(admin, "beto@districom.test")
    admin.post(f"{USERS}/{beto['user']['id']}/deactivate")
    active = admin.get(USERS).json()
    assert active["total"] == 2
    assert {u["email"] for u in active["items"]} == {"admin@districom.test", "ana@districom.test"}
    assert admin.get(USERS, params={"status": "inactive"}).json()["items"][0]["email"] == "beto@districom.test"
    assert admin.get(USERS, params={"status": "all", "q": "BETO"}).json()["total"] == 1
    me = next(u for u in active["items"] if u["email"] == "admin@districom.test")
    assert me["open_sessions"] == 1
    catalog = admin.get(f"{USERS}/access-catalog").json()
    assert {m["key"] for m in catalog["modules"]} == {"commercial", "settlements"}
    assert {a["key"]: a["module_key"] for a in catalog["areas"]} == {
        AREA: "settlements",
        "settlements.store_sys_import": "settlements",
    }


def test_unknown_user_is_404(admin: TestClient) -> None:
    assert admin.get(f"{USERS}/999999").status_code == 404
    assert admin.post(f"{USERS}/999999/deactivate").status_code == 404
