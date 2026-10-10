"""Third parties of the platform (R6-1, Enzo's decisions D2, D7 and D8): every signed-in user reads them, a
platform admin administers them, an operator of Rendiciones may only create one quickly; the CUIT is
optional, validated and unique; a homonym needs an explicit, audited confirmation; nothing is deleted."""

import threading
from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.platform.auth.models import User
from app.platform.third_parties import service
from app.platform.third_parties.schemas import ThirdPartyIn

URL = "/api/v1/third-parties"
QUICK = "/api/v1/settlements/third-parties"
PEREZ = {"name": "Distribuidora Pérez S.A.", "tax_id": "30-71234567-1", "notes": "Proveedor de gaseosas"}


def _events(db: Session, kind: str) -> list[dict]:  # type: ignore[type-arg]
    rows = db.execute(
        text("SELECT user_id, entity_id, payload FROM audit_events WHERE event_type = :t ORDER BY id"),
        {"t": f"platform.third_party.{kind}"},
    ).mappings()
    return [dict(row) for row in rows]


def _refused(db: Session, sql: str) -> str:
    def _write() -> None:
        db.execute(text(sql))
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def _code(response) -> tuple[int, str]:  # type: ignore[no-untyped-def]
    return response.status_code, response.json().get("code")


@pytest.fixture
def operator(client: TestClient, make_user, grant) -> Callable[[], TestClient]:  # type: ignore[no-untyped-def]
    """Log in as a user who is operator of Rendiciones and nothing else on the platform."""

    def _login() -> TestClient:
        make_user(email="operator@districom.test", role="viewer")
        grant("operator", "settlements", "operator")
        response = client.post(
            "/api/v1/auth/login", json={"email": "operator@districom.test", "password": "correct-horse-battery"}
        )
        assert response.status_code == 200, response.text
        return client

    return _login


# --- the master -------------------------------------------------------------------------------------


def test_an_admin_registers_a_third_party_with_cuit(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    created = login("admin").post(URL, json=PEREZ)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["name"], body["tax_id"], body["tax_id_display"], body["notes"], body["active"]) == (
        "Distribuidora Pérez S.A.",
        "30712345671",
        "30-71234567-1",
        "Proveedor de gaseosas",
        True,
    )
    stored = db.execute(text("SELECT tax_id, name_key FROM third_parties")).one()
    assert tuple(stored) == ("30712345671", "distribuidora perez sa")  # hyphens are never stored
    [event] = _events(db, "created")
    assert event["payload"]["after"] == {
        "name": "Distribuidora Pérez S.A.",
        "tax_id": "30712345671",
        "notes": "Proveedor de gaseosas",
        "active": True,
        "origin": "platform",
    }


def test_a_third_party_without_cuit(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    body = admin.post(URL, json={"name": "  Gomería   El Tano ", "tax_id": "  "}).json()
    assert (body["name"], body["tax_id"], body["tax_id_display"], body["notes"]) == (
        "Gomería El Tano",
        None,
        None,
        None,
    )
    # Several third parties without CUIT may exist: the partial unique index only covers given CUITs.
    assert admin.post(URL, json={"name": "Fletes Rodríguez"}).status_code == 201


@pytest.mark.parametrize("tax_id", ["30-71234567-2", "3071234567", "30A71234567", "20999999990"])
def test_an_invalid_cuit_is_refused(login, tax_id: str) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    assert _code(admin.post(URL, json={"name": "Distribuidora Norte", "tax_id": tax_id})) == (422, "invalid_tax_id")
    assert admin.get(URL).json() == []


def test_the_database_refuses_an_invalid_cuit(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    login("admin")
    message = _refused(
        db,
        "INSERT INTO third_parties (name, name_key, tax_id, created_by, updated_by) "
        "SELECT 'X', 'x', '30712345672', id, id FROM users LIMIT 1",
    )
    assert "ck_third_parties_tax_id_valid" in message


def test_a_duplicate_cuit_is_blocked_however_it_is_written(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    first = admin.post(URL, json=PEREZ).json()
    again = admin.post(URL, json={"name": "Otra razón social", "tax_id": "30.71234567.1"})
    assert _code(again) == (409, "third_party_tax_id_taken")
    assert again.json()["details"] == {"third_party_id": first["id"], "active": True}
    # Not even confirming a homonym gets past it: the same CUIT is the same third party.
    confirmed = admin.post(URL, json={**PEREZ, "confirm_not_duplicate": True, "duplicate_reason": "Es otra empresa"})
    assert _code(confirmed) == (409, "third_party_tax_id_taken")
    assert db.execute(text("SELECT count(*) FROM third_parties")).scalar_one() == 1


def test_a_homonym_needs_an_explicit_audited_confirmation(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    first = admin.post(URL, json={"name": "Distribuidora Pérez"}).json()
    warned = admin.post(URL, json={"name": "DISTRIBUIDORA  PEREZ", "tax_id": "20-12345678-6"})
    assert _code(warned) == (409, "third_party_possible_duplicate")
    assert warned.json()["details"]["candidates"] == [
        {"third_party_id": first["id"], "name": "Distribuidora Pérez", "tax_id_display": None, "active": True}
    ]
    no_reason = admin.post(URL, json={"name": "Distribuidora Pérez", "confirm_not_duplicate": True})
    assert _code(no_reason) == (422, "reason_required")
    assert db.execute(text("SELECT count(*) FROM third_parties")).scalar_one() == 1  # never merged, never created

    confirmed = admin.post(
        URL,
        json={"name": "Distribuidora Pérez", "confirm_not_duplicate": True, "duplicate_reason": "Es la de Rosario"},
    )
    assert confirmed.status_code == 201, confirmed.text
    assert confirmed.json()["id"] != first["id"]
    event = _events(db, "created")[-1]
    assert event["payload"]["after"]["duplicate_confirmation"] == {
        "candidates": [first["id"]],
        "reason": "Es la de Rosario",
    }


def test_two_homonyms_with_different_cuits_are_different_third_parties(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    assert admin.post(URL, json={"name": "Distribuidora Pérez", "tax_id": "30712345671"}).status_code == 201
    other = admin.post(URL, json={"name": "Distribuidora Perez", "tax_id": "20123456786"})
    assert other.status_code == 201, other.text  # the CUITs tell them apart: no warning


def test_a_name_is_required(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    assert _code(admin.post(URL, json={"name": " .. "})) == (422, "name_required")
    assert admin.post(URL, json={"tax_id": "30712345671"}).status_code == 422


def test_correct_deactivate_and_reactivate(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    row = admin.post(URL, json=PEREZ).json()
    patched = admin.patch(f"{URL}/{row['id']}", json={"name": "Distribuidora Pérez SRL", "tax_id": "20123456786"})
    assert patched.status_code == 200, patched.text
    assert (patched.json()["name"], patched.json()["tax_id"]) == ("Distribuidora Pérez SRL", "20123456786")
    removed = admin.patch(f"{URL}/{row['id']}", json={"tax_id": ""}).json()
    assert removed["tax_id"] is None
    off = admin.patch(f"{URL}/{row['id']}", json={"active": False}).json()
    assert off["active"] is False
    assert admin.get(URL).json() == []  # not offered by default
    assert [t["id"] for t in admin.get(URL, params={"include_inactive": True}).json()] == [row["id"]]
    with pytest.raises(AppError) as caught:  # what a module calls before naming a third party in a new record
        service.lock_active(db, row["id"])
    assert (caught.value.status_code, caught.value.code) == (422, "third_party_inactive")
    db.rollback()
    assert admin.patch(f"{URL}/{row['id']}", json={"active": True}).json()["active"] is True
    detail = admin.get(f"{URL}/{row['id']}").json()
    assert [e["event_type"] for e in detail["history"]] == [
        "created",
        "name_changed",
        "tax_id_changed",
        "tax_id_changed",
        "deactivated",
        "activated",
    ]
    assert detail["history"][2]["before"] == {"tax_id": "30712345671"}
    assert detail["history"][2]["after"] == {"tax_id": "20123456786"}


def test_correcting_to_a_taken_cuit_is_blocked(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    first = admin.post(URL, json=PEREZ).json()
    second = admin.post(URL, json={"name": "Fletes Rodríguez"}).json()
    clash = admin.patch(f"{URL}/{second['id']}", json={"tax_id": "30-71234567-1"})
    assert _code(clash) == (409, "third_party_tax_id_taken")
    assert clash.json()["details"]["third_party_id"] == first["id"]
    assert admin.get(f"{URL}/{second['id']}").json()["tax_id"] is None


def test_search_by_name_or_cuit(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    perez = admin.post(URL, json=PEREZ).json()
    admin.post(URL, json={"name": "Fletes Rodríguez"})
    assert [t["id"] for t in admin.get(URL, params={"q": "perez"}).json()] == [perez["id"]]
    assert [t["id"] for t in admin.get(URL, params={"q": "71234"}).json()] == [perez["id"]]
    assert len(admin.get(URL, params={"q": "  "}).json()) == 2


def test_the_database_refuses_deleting_a_third_party(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    row = admin.post(URL, json=PEREZ).json()
    assert admin.delete(f"{URL}/{row['id']}").status_code == 405  # no endpoint
    assert "never deleted" in _refused(db, f"DELETE FROM third_parties WHERE id = {row['id']}")  # noqa: S608
    assert "never change" in _refused(db, "UPDATE third_parties SET created_at = now() - interval '1 day'")


# --- who may do what ----------------------------------------------------------------------------------


def test_an_operator_creates_a_third_party_quickly(operator, db: Session) -> None:  # type: ignore[no-untyped-def]
    user = operator()
    created = user.post(QUICK, json={"name": "Gomería El Tano", "tax_id": "27-30111222-5", "notes": "Paga en efectivo"})
    assert created.status_code == 201, created.text
    assert created.json()["tax_id_display"] == "27-30111222-5"
    [event] = _events(db, "created")
    assert event["payload"]["after"]["origin"] == "settlements.payment"
    operator_id = db.execute(text("SELECT id FROM users WHERE email = 'operator@districom.test'")).scalar_one()
    assert event["user_id"] == operator_id
    # The same rules as the master: duplicates and homonyms.
    assert _code(user.post(QUICK, json={"name": "Otro", "tax_id": "27301112225"})) == (409, "third_party_tax_id_taken")
    assert _code(user.post(QUICK, json={"name": "gomeria el tano"})) == (409, "third_party_possible_duplicate")
    homonym = user.post(
        QUICK, json={"name": "gomeria el tano", "confirm_not_duplicate": True, "duplicate_reason": "Otra sucursal"}
    )
    assert homonym.status_code == 201


@pytest.mark.parametrize("module_role", ["operator", "admin"])
def test_a_rendiciones_role_does_not_administer_the_master(  # type: ignore[no-untyped-def]
    client, make_user, login, grant, module_role: str
) -> None:
    make_user(email="admin@districom.test", role="admin")
    client.post("/api/v1/auth/login", json={"email": "admin@districom.test", "password": "correct-horse-battery"})
    row = client.post(URL, json=PEREZ).json()
    client.post("/api/v1/auth/logout")
    user = login("viewer")
    grant("viewer", "settlements", module_role)
    assert user.get(URL).status_code == 200  # reading is for every signed-in user
    assert user.get(f"{URL}/{row['id']}").json()["name"] == "Distribuidora Pérez S.A."
    assert _code(user.post(URL, json={"name": "Otro"}))[0] == 403
    assert _code(user.patch(f"{URL}/{row['id']}", json={"name": "Otro nombre"}))[0] == 403
    assert _code(user.patch(f"{URL}/{row['id']}", json={"active": False}))[0] == 403
    assert user.get(f"{URL}/{row['id']}").json()["active"] is True


def test_a_viewer_of_rendiciones_cannot_create_quickly(client, login, grant) -> None:  # type: ignore[no-untyped-def]
    user = login("viewer")
    grant("viewer", "settlements", "viewer")
    assert _code(user.post(QUICK, json={"name": "Gomería"}))[0] == 403


# --- concurrency ----------------------------------------------------------------------------------


def test_the_same_cuit_registered_at_the_same_time_is_one_third_party(db: Session, make_user) -> None:  # type: ignore[no-untyped-def]
    admin: User = make_user(email="admin@districom.test", role="admin")
    factory = sessionmaker(bind=db.get_bind())
    barrier = threading.Barrier(2)
    results: list[str] = []

    def _register(name: str) -> None:
        with factory() as session:
            actor = session.get(User, admin.id)
            assert actor is not None
            barrier.wait()
            try:
                service.create(session, actor, ThirdPartyIn(name=name, tax_id="30712345671"))
                results.append("created")
            except AppError as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=_register, args=(n,)) for n in ("Pérez A", "Pérez B")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["created", "third_party_tax_id_taken"]
    assert db.execute(text("SELECT count(*) FROM third_parties")).scalar_one() == 1


def test_the_same_name_registered_at_the_same_time_warns_the_second(db: Session, make_user) -> None:  # type: ignore[no-untyped-def]
    admin: User = make_user(email="admin@districom.test", role="admin")
    factory = sessionmaker(bind=db.get_bind())
    barrier = threading.Barrier(2)
    results: list[str] = []

    def _register() -> None:
        with factory() as session:
            actor = session.get(User, admin.id)
            assert actor is not None
            barrier.wait()
            try:
                service.create(session, actor, ThirdPartyIn(name="Gomería El Tano"))
                results.append("created")
            except AppError as exc:
                results.append(exc.code)

    threads = [threading.Thread(target=_register) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["created", "third_party_possible_duplicate"]
