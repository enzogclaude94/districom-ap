"""Bocas de expendio of the platform (R7-1, ADR 009): every signed-in user reads them, only a platform admin
administers them; the code is normalized, unique and immutable; the name is corrected but two bocas never
share an operationally identical name; every change is audited and nothing is deleted."""

import threading
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.platform.outlets import service
from app.platform.outlets.schemas import OutletIn

URL = "/api/v1/outlets"
CENTRO = {"code": " boca-01 ", "name": "Boca  Centro", "description": "Depósito de la avenida"}


def _events(db: Session, kind: str) -> list[dict]:  # type: ignore[type-arg]
    rows = db.execute(
        text("SELECT user_id, occurred_at, entity_id, payload FROM audit_events WHERE event_type = :t ORDER BY id"),
        {"t": f"platform.outlet.{kind}"},
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


def test_an_admin_registers_a_boca(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    created = login("admin").post(URL, json=CENTRO)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["code"], body["name"], body["description"], body["active"]) == (
        "BOCA-01",
        "Boca Centro",
        "Depósito de la avenida",
        True,
    )
    assert body["created_by_name"] == body["updated_by_name"] == "Usuario Test"
    stored = db.execute(text("SELECT code, name, name_key FROM outlets")).one()
    assert tuple(stored) == ("BOCA-01", "Boca Centro", "boca centro")


def test_the_code_is_normalized_and_validated(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    assert admin.post(URL, json={"code": "b2", "name": "Boca Dos"}).json()["code"] == "B2"
    for code in ("B", "BOCA 01", "BOCA_01", "BOCA.01", "Ñ1", "A" * 21, "   "):
        bad = admin.post(URL, json={"code": code, "name": f"Otra {code}"})
        assert (bad.status_code, bad.json()["code"]) == (422, "invalid_outlet_code"), code
    message = _refused(
        db,
        "INSERT INTO outlets (code, name, name_key, created_by, updated_by) "
        "SELECT 'boca-9', 'Nueve', 'nueve', id, id FROM users LIMIT 1",
    )
    assert "ck_outlets_code_format" in message


def test_a_duplicate_code_is_a_conflict(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    first = admin.post(URL, json=CENTRO).json()
    again = admin.post(URL, json={"code": "BOCA-01", "name": "Boca Norte"})
    assert (again.status_code, again.json()["code"]) == (409, "outlet_code_taken")
    assert again.json()["details"] == {"outlet_id": first["id"], "active": True}
    message = _refused(
        db,
        "INSERT INTO outlets (code, name, name_key, created_by, updated_by) "
        "SELECT 'BOCA-01', 'Norte', 'norte', id, id FROM users LIMIT 1",
    )
    assert "uq_outlets_code" in message
    assert len(admin.get(URL).json()) == 1


def test_the_code_never_changes(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    boca = admin.post(URL, json=CENTRO).json()
    patched = admin.patch(f"{URL}/{boca['id']}", json={"code": "BOCA-02"})
    assert patched.status_code == 422  # not a field of the correction
    assert admin.get(f"{URL}/{boca['id']}").json()["code"] == "BOCA-01"
    assert "the code never changes" in _refused(db, f"UPDATE outlets SET code = 'BOCA-02' WHERE id = {boca['id']}")  # noqa: S608


def test_a_name_identical_after_normalizing_is_a_conflict(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    first = admin.post(URL, json={"code": "B1", "name": "Boca Pérez S.A."}).json()
    clash = admin.post(URL, json={"code": "B2", "name": "  boca perez sa "})
    assert (clash.status_code, clash.json()["code"]) == (409, "outlet_name_taken")
    assert clash.json()["details"]["outlet_id"] == first["id"]
    second = admin.post(URL, json={"code": "B2", "name": "Boca Norte"}).json()
    renamed = admin.patch(f"{URL}/{second['id']}", json={"name": "BOCA PEREZ S.A."})
    assert (renamed.status_code, renamed.json()["code"]) == (409, "outlet_name_taken")
    message = _refused(
        db,
        "INSERT INTO outlets (code, name, name_key, created_by, updated_by) "
        "SELECT 'B3', 'Boca Perez SA', 'boca perez sa', id, id FROM users LIMIT 1",
    )
    assert "uq_outlets_name_key" in message


def test_the_name_is_corrected_and_audited(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    boca = admin.post(URL, json=CENTRO).json()
    patched = admin.patch(f"{URL}/{boca['id']}", json={"name": "Boca  Centro Sur"})
    assert (patched.json()["id"], patched.json()["code"], patched.json()["name"]) == (
        boca["id"],
        "BOCA-01",
        "Boca Centro Sur",
    )
    [event] = _events(db, "name_changed")
    assert (event["payload"]["before"], event["payload"]["after"]) == (
        {"name": "Boca Centro"},
        {"name": "Boca Centro Sur"},
    )
    assert event["entity_id"] == str(boca["id"])
    # a correction in capitals only is still a correction of the same boca, not a conflict with itself
    assert admin.patch(f"{URL}/{boca['id']}", json={"name": "BOCA CENTRO SUR"}).status_code == 200
    # the old name is free again
    assert admin.post(URL, json={"code": "B9", "name": "Boca Centro"}).status_code == 201


def test_a_blank_name_is_refused(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    for name in ("   ", "..."):
        blank = admin.post(URL, json={"code": "B1", "name": name})
        assert (blank.status_code, blank.json()["code"]) == (422, "outlet_name_required"), name
    assert admin.post(URL, json={"code": "B1"}).status_code == 422
    assert admin.get(URL).json() == []


def test_the_description_is_optional_corrected_and_cleared(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    boca = admin.post(URL, json={"code": "B1", "name": "Boca Uno", "description": "   "}).json()
    assert boca["description"] is None
    set_ = admin.patch(f"{URL}/{boca['id']}", json={"description": " Junto al  depósito "}).json()
    assert set_["description"] == "Junto al depósito"
    cleared = admin.patch(f"{URL}/{boca['id']}", json={"description": ""}).json()
    assert cleared["description"] is None
    events = _events(db, "description_changed")
    assert [(e["payload"]["before"], e["payload"]["after"]) for e in events] == [
        ({"description": None}, {"description": "Junto al depósito"}),
        ({"description": "Junto al depósito"}, {"description": None}),
    ]


def test_deactivate_and_reactivate(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    boca = admin.post(URL, json=CENTRO).json()
    off = admin.patch(f"{URL}/{boca['id']}", json={"active": False})
    assert off.json()["active"] is False
    assert admin.get(URL).json() == []  # not offered by default
    assert [o["id"] for o in admin.get(URL, params={"include_inactive": True}).json()] == [boca["id"]]
    assert admin.get(f"{URL}/{boca['id']}").json()["active"] is False  # still readable
    with pytest.raises(AppError) as caught:  # what Rendiciones calls before naming a boca in a new record
        service.active(db, boca["id"])
    assert (caught.value.status_code, caught.value.code) == (422, "inactive_outlet")

    on = admin.patch(f"{URL}/{boca['id']}", json={"active": True})
    assert on.json()["active"] is True
    db.expire_all()
    assert service.active(db, boca["id"]).id == boca["id"]
    [deactivated] = _events(db, "deactivated")
    [reactivated] = _events(db, "reactivated")
    assert (deactivated["payload"]["before"], deactivated["payload"]["after"]) == ({"active": True}, {"active": False})
    assert (reactivated["payload"]["before"], reactivated["payload"]["after"]) == ({"active": False}, {"active": True})
    detail = admin.get(f"{URL}/{boca['id']}").json()
    assert [e["event_type"] for e in detail["history"]] == ["created", "deactivated", "reactivated"]
    assert detail["history"][1]["user_name"] == "Usuario Test"


def test_a_patch_that_changes_nothing_records_nothing(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    boca = admin.post(URL, json=CENTRO).json()
    same = admin.patch(f"{URL}/{boca['id']}", json={"name": " Boca Centro ", "active": True})
    assert same.status_code == 200
    count = db.execute(text("SELECT count(*) FROM audit_events WHERE entity_type = 'outlet'")).scalar_one()
    assert count == 1  # only the creation


def test_creation_is_audited(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    boca = login("admin").post(URL, json=CENTRO).json()
    [event] = _events(db, "created")
    assert event["entity_id"] == str(boca["id"])
    assert event["payload"]["before"] is None
    assert event["payload"]["after"] == {
        "code": "BOCA-01",
        "name": "Boca Centro",
        "description": "Depósito de la avenida",
        "active": True,
    }
    admin_id = db.execute(text("SELECT id FROM users WHERE email = 'admin@districom.test'")).scalar_one()
    assert event["user_id"] == admin_id


def test_the_database_refuses_deleting_a_boca(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    boca = admin.post(URL, json=CENTRO).json()
    assert admin.delete(f"{URL}/{boca['id']}").status_code == 405  # no endpoint
    assert "never deleted" in _refused(db, f"DELETE FROM outlets WHERE id = {boca['id']}")  # noqa: S608
    assert "never change" in _refused(db, "UPDATE outlets SET created_at = now() - interval '1 day'")
    assert db.execute(text("SELECT count(*) FROM outlets")).scalar_one() == 1


def _boca_by_admin(client, make_user) -> dict:  # type: ignore[no-untyped-def,type-arg]
    make_user(email="admin@districom.test", role="admin")
    client.post("/api/v1/auth/login", json={"email": "admin@districom.test", "password": "correct-horse-battery"})
    boca = client.post(URL, json=CENTRO).json()
    client.post("/api/v1/auth/logout")
    return boca  # type: ignore[no-any-return]


@pytest.mark.parametrize("module_role", [None, "operator", "admin"])
def test_only_a_platform_admin_administers_bocas(  # type: ignore[no-untyped-def]
    client, make_user, login, grant, module_role: str | None
) -> None:
    boca = _boca_by_admin(client, make_user)
    user = login("viewer")
    if module_role is not None:
        grant("viewer", "settlements", module_role)
    assert user.get(URL).status_code == 200  # reading is for every signed-in user
    assert user.get(f"{URL}/{boca['id']}").json()["code"] == "BOCA-01"
    assert user.post(URL, json={"code": "B2", "name": "Boca Norte"}).status_code == 403
    for change in ({"name": "Otra"}, {"description": "x"}, {"active": False}):
        assert user.patch(f"{URL}/{boca['id']}", json=change).status_code == 403
    assert user.delete(f"{URL}/{boca['id']}").status_code == 405


def test_reading_requires_a_session(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get(URL).status_code == 401


def test_unknown_boca(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    assert admin.get(f"{URL}/999").json()["code"] == "outlet_not_found"
    assert admin.patch(f"{URL}/999", json={"active": False}).json()["code"] == "outlet_not_found"


@pytest.fixture
def new_session(db: Session) -> Iterator[Callable[[], Session]]:
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    sessions: list[Session] = []

    def _new() -> Session:
        session = factory()
        sessions.append(session)
        return session

    yield _new
    for session in sessions:
        session.close()


def test_two_admins_registering_the_same_code_at_once(  # type: ignore[no-untyped-def]
    db: Session, make_user, new_session
) -> None:
    """Both pass the friendly check before either writes; the unique key decides: one boca, and the other admin
    gets the same 409 a sequential duplicate gets."""
    admin = make_user(email="admin@districom.test", role="admin")
    db.commit()
    holder = new_session()
    holder.execute(
        text(
            "INSERT INTO outlets (code, name, name_key, created_by, updated_by) "
            "VALUES ('BOCA-01', 'reserva', 'reserva', :u, :u)"
        ),
        {"u": admin.id},
    )
    results: list[object] = [None, None]

    def _create(i: int, raw: str) -> None:
        session = new_session()
        try:
            results[i] = service.create(
                session, session.merge(admin, load=False), OutletIn(code=raw, name=f"Boca {i}")
            ).id
        except Exception as exc:  # asserted below
            results[i] = exc

    threads = [
        threading.Thread(target=_create, args=(0, "BOCA-01")),
        threading.Thread(target=_create, args=(1, "boca-01")),
    ]
    for thread in threads:
        thread.start()
    threading.Event().wait(0.5)
    holder.rollback()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "a worker is still waiting: deadlock?"

    winners = [r for r in results if isinstance(r, int)]
    losers = [r for r in results if isinstance(r, AppError)]
    assert (len(winners), len(losers)) == (1, 1), results
    assert (losers[0].status_code, losers[0].code) == (409, "outlet_code_taken")
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM outlets")).scalar_one() == 1
