"""Vehicles of the platform (R5-1, Enzo's decision 2): every signed-in user reads them, only a platform admin
administers them, the plate is normalized and unique, every change is audited and nothing is deleted.
Numbers in brackets are the cases Enzo listed for R5-1."""

import threading
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from app.core.errors import AppError
from app.platform.vehicles import service
from app.platform.vehicles.schemas import VehicleIn

URL = "/api/v1/vehicles"
TRUCK = {"plate": "ab 123 cd", "description": "Camión Mercedes reparto zona norte"}


def _events(db: Session, kind: str) -> list[dict]:  # type: ignore[type-arg]
    rows = db.execute(
        text("SELECT user_id, occurred_at, entity_id, payload FROM audit_events WHERE event_type = :t ORDER BY id"),
        {"t": f"platform.vehicle.{kind}"},
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


def test_an_admin_registers_a_vehicle(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [1][2][18]
    created = login("admin").post(URL, json=TRUCK)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["plate"], body["plate_display"], body["description"], body["active"]) == (
        "AB123CD",
        "AB 123 CD",
        "Camión Mercedes reparto zona norte",
        True,
    )
    assert body["created_by_name"] == body["updated_by_name"] == "Usuario Test"
    stored = db.execute(text("SELECT plate, plate_key FROM vehicles")).one()
    assert tuple(stored) == ("AB123CD", "AB123CD")  # the display format is never stored


def test_the_same_plate_written_differently_is_one_vehicle(login) -> None:  # type: ignore[no-untyped-def]  # [3][5]
    admin = login("admin")
    first = admin.post(URL, json={"plate": "AB 123 CD", "description": "Camión"}).json()
    again = admin.post(URL, json={"plate": "AB-123-CD", "description": "Otro camión"})
    assert (again.status_code, again.json()["code"]) == (409, "vehicle_plate_taken")
    assert again.json()["details"] == {"vehicle_id": first["id"], "active": True}
    assert len(admin.get(URL).json()) == 1


def test_the_previous_plate_format_is_registered(login) -> None:  # type: ignore[no-untyped-def]  # [4]
    body = login("admin").post(URL, json={"plate": "abc.123", "description": "Utilitario"}).json()
    assert (body["plate"], body["plate_display"]) == ("ABC123", "ABC 123")


def test_a_description_is_required(login) -> None:  # type: ignore[no-untyped-def]  # [7]
    admin = login("admin")
    blank = admin.post(URL, json={"plate": "AB123CD", "description": "   "})
    assert (blank.status_code, blank.json()["code"]) == (422, "description_required")
    assert admin.post(URL, json={"plate": "AB123CD"}).status_code == 422
    assert admin.get(URL).json() == []


def test_an_invalid_plate_is_rejected(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [8]
    admin = login("admin")
    for plate in ("AB/123", "AB1", "AB_123_CD"):
        bad = admin.post(URL, json={"plate": plate, "description": "Camión"})
        assert (bad.status_code, bad.json()["code"]) == (422, "invalid_plate"), plate
    message = _refused(
        db,
        "INSERT INTO vehicles (plate, plate_key, description, created_by, updated_by) "
        "SELECT 'ab 123', 'AB123', 'x', id, id FROM users LIMIT 1",
    )
    assert "ck_vehicles_plate_normalized" in message


def test_the_description_is_corrected(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [9][20]
    admin = login("admin")
    vehicle = admin.post(URL, json=TRUCK).json()
    patched = admin.patch(f"{URL}/{vehicle['id']}", json={"description": "  Camión   Iveco "})
    assert patched.status_code == 200, patched.text
    assert patched.json()["description"] == "Camión Iveco"
    [event] = _events(db, "description_changed")
    assert event["payload"]["before"] == {"description": "Camión Mercedes reparto zona norte"}
    assert event["payload"]["after"] == {"description": "Camión Iveco"}
    assert event["user_id"] is not None
    assert event["occurred_at"] is not None
    assert event["entity_id"] == str(vehicle["id"])


def test_a_plate_is_corrected_and_keeps_its_history(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [10][20]
    admin = login("admin")
    vehicle = admin.post(URL, json=TRUCK).json()
    patched = admin.patch(f"{URL}/{vehicle['id']}", json={"plate": "ab-124-cd"})
    assert (patched.json()["id"], patched.json()["plate"]) == (vehicle["id"], "AB124CD")
    [event] = _events(db, "plate_changed")
    assert (event["payload"]["before"], event["payload"]["after"]) == ({"plate": "AB123CD"}, {"plate": "AB124CD"})
    detail = admin.get(f"{URL}/{vehicle['id']}").json()
    assert [e["event_type"] for e in detail["history"]] == ["created", "plate_changed"]
    assert detail["history"][1]["user_name"] == "Usuario Test"
    # the old plate is free again: it no longer names this vehicle
    assert admin.post(URL, json={"plate": "AB123CD", "description": "Otro"}).status_code == 201


def test_correcting_to_a_taken_plate_is_a_conflict(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [11]
    admin = login("admin")
    first = admin.post(URL, json=TRUCK).json()
    second = admin.post(URL, json={"plate": "AC456DE", "description": "Camioneta"}).json()
    clash = admin.patch(f"{URL}/{second['id']}", json={"plate": "AB 123 CD"})
    assert (clash.status_code, clash.json()["code"]) == (409, "vehicle_plate_taken")
    assert clash.json()["details"]["vehicle_id"] == first["id"]
    assert admin.get(f"{URL}/{second['id']}").json()["plate"] == "AC456DE"
    assert _events(db, "plate_changed") == []


def test_deactivate_and_reactivate(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [12][13][14][21]
    admin = login("admin")
    vehicle = admin.post(URL, json=TRUCK).json()
    off = admin.patch(f"{URL}/{vehicle['id']}", json={"active": False})
    assert off.json()["active"] is False
    assert admin.get(URL).json() == []  # not offered by default
    assert [v["id"] for v in admin.get(URL, params={"include_inactive": True}).json()] == [vehicle["id"]]
    assert admin.get(f"{URL}/{vehicle['id']}").json()["active"] is False  # still readable
    with pytest.raises(AppError) as caught:  # what a module calls before naming a vehicle in a new record
        service.active(db, vehicle["id"])
    assert (caught.value.status_code, caught.value.code) == (422, "invalid_vehicle")

    on = admin.patch(f"{URL}/{vehicle['id']}", json={"active": True})
    assert on.json()["active"] is True
    db.expire_all()
    assert service.active(db, vehicle["id"]).id == vehicle["id"]
    [deactivated] = _events(db, "deactivated")
    [activated] = _events(db, "activated")
    assert (deactivated["payload"]["before"], deactivated["payload"]["after"]) == ({"active": True}, {"active": False})
    assert (activated["payload"]["before"], activated["payload"]["after"]) == ({"active": False}, {"active": True})
    assert deactivated["occurred_at"] <= activated["occurred_at"]


def test_a_patch_that_changes_nothing_records_nothing(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    vehicle = admin.post(URL, json=TRUCK).json()
    same = admin.patch(f"{URL}/{vehicle['id']}", json={"plate": "AB-123-CD", "active": True})
    assert same.status_code == 200
    count = db.execute(text("SELECT count(*) FROM audit_events WHERE entity_type = 'vehicle'")).scalar_one()
    assert count == 1  # only the creation


def test_the_database_refuses_deleting_a_vehicle(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [15]
    admin = login("admin")
    vehicle = admin.post(URL, json=TRUCK).json()
    assert admin.delete(f"{URL}/{vehicle['id']}").status_code == 405  # no endpoint
    assert "never deleted" in _refused(db, f"DELETE FROM vehicles WHERE id = {vehicle['id']}")  # noqa: S608
    assert "never change" in _refused(db, "UPDATE vehicles SET created_at = now() - interval '1 day'")
    assert db.execute(text("SELECT count(*) FROM vehicles")).scalar_one() == 1


def test_creation_is_audited(login, db: Session) -> None:  # type: ignore[no-untyped-def]  # [19]
    vehicle = login("admin").post(URL, json=TRUCK).json()
    [event] = _events(db, "created")
    assert event["entity_id"] == str(vehicle["id"])
    assert event["payload"]["before"] is None
    assert event["payload"]["after"] == {
        "plate": "AB123CD",
        "description": "Camión Mercedes reparto zona norte",
        "active": True,
    }
    admin_id = db.execute(text("SELECT id FROM users WHERE email = 'admin@districom.test'")).scalar_one()
    assert event["user_id"] == admin_id


def _vehicle_by_admin(client, make_user) -> dict:  # type: ignore[no-untyped-def,type-arg]
    make_user(email="admin@districom.test", role="admin")
    client.post("/api/v1/auth/login", json={"email": "admin@districom.test", "password": "correct-horse-battery"})
    vehicle = client.post(URL, json=TRUCK).json()
    client.post("/api/v1/auth/logout")
    return vehicle  # type: ignore[no-any-return]


@pytest.mark.parametrize("module_role", ["operator", "admin"])
def test_a_rendiciones_role_does_not_administer_vehicles(  # type: ignore[no-untyped-def]  # [16][17]
    client, make_user, login, grant, module_role: str
) -> None:
    vehicle = _vehicle_by_admin(client, make_user)
    user = login("viewer")
    grant("viewer", "settlements", module_role)
    assert user.get(URL).status_code == 200  # reading is for every signed-in user
    assert user.get(f"{URL}/{vehicle['id']}").json()["plate"] == "AB123CD"
    assert user.post(URL, json={"plate": "AC456DE", "description": "Camioneta"}).status_code == 403
    for change in ({"description": "Otra"}, {"plate": "AC456DE"}, {"active": False}):
        assert user.patch(f"{URL}/{vehicle['id']}", json=change).status_code == 403
    assert user.delete(f"{URL}/{vehicle['id']}").status_code == 405


def test_reading_requires_a_session(client) -> None:  # type: ignore[no-untyped-def]
    assert client.get(URL).status_code == 401


def test_unknown_vehicle(login) -> None:  # type: ignore[no-untyped-def]
    admin = login("admin")
    assert admin.get(f"{URL}/999").json()["code"] == "vehicle_not_found"
    assert admin.patch(f"{URL}/999", json={"active": False}).json()["code"] == "vehicle_not_found"


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


def test_two_admins_registering_the_same_plate_at_once(  # type: ignore[no-untyped-def]  # [6]
    db: Session, make_user, new_session
) -> None:
    """Both requests pass the friendly check before either writes; the unique key decides: one vehicle, and
    the other admin gets the same 409 a sequential duplicate gets, naming the winner."""
    admin = make_user(email="admin@districom.test", role="admin")
    db.commit()
    holder = new_session()  # holds the plate's key so both workers get past the check and wait on the insert
    holder.execute(
        text(
            "INSERT INTO vehicles (plate, plate_key, description, created_by, updated_by) "
            "VALUES ('AB123CD', 'AB123CD', 'reserva', :u, :u)"
        ),
        {"u": admin.id},
    )
    results: list[object] = [None, None]

    def _create(i: int, raw: str) -> None:
        session = new_session()
        try:
            results[i] = service.create(
                session, session.merge(admin, load=False), VehicleIn(plate=raw, description=f"Camión {i}")
            ).id
        except Exception as exc:  # asserted below
            results[i] = exc

    threads = [
        threading.Thread(target=_create, args=(0, "AB 123 CD")),
        threading.Thread(target=_create, args=(1, "ab-123-cd")),
    ]
    for thread in threads:
        thread.start()
    threading.Event().wait(0.5)
    holder.rollback()  # the key is free again: the two creates race for it
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive(), "a worker is still waiting: deadlock?"

    winners = [r for r in results if isinstance(r, int)]
    losers = [r for r in results if isinstance(r, AppError)]
    assert len(winners) == 1, results
    assert len(losers) == 1, results
    assert (losers[0].status_code, losers[0].code) == (409, "vehicle_plate_taken")
    assert losers[0].details == {"vehicle_id": winners[0], "active": True}
    db.expire_all()
    assert db.execute(text("SELECT count(*) FROM vehicles")).scalar_one() == 1
    assert (
        db.execute(text("SELECT count(*) FROM audit_events WHERE event_type = 'platform.vehicle.created'")).scalar_one()
        == 1
    )
