"""Responsables of the bocas de expendio (R7-1, Enzo's decisions D3 and Ajustes 1 and 5; ADR 009).

Any active employee may answer for an active boca from a day on; several at once; the same employee in
several bocas; never the same employee twice in the same boca over overlapping days. Ending is once, with a
reason, and history is never rewritten. Ranges are inclusive at both ends. The database repeats each rule.
"""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.platform.parties.models import Employee
from tests.modules.settlements.days import days_ago, today

URL = "/api/v1/settlements"


class Roles:
    """The test client is one browser: each property logs it in as that user and returns it."""

    def __init__(self, as_role) -> None:  # type: ignore[no-untyped-def]
        self._as = as_role

    @property
    def admin(self) -> TestClient:
        return self._as("admin")  # type: ignore[no-any-return]

    @property
    def operator(self) -> TestClient:
        return self._as("operator")  # type: ignore[no-any-return]

    @property
    def viewer(self) -> TestClient:
        return self._as("viewer")  # type: ignore[no-any-return]


@pytest.fixture
def r(as_role) -> Roles:  # type: ignore[no-untyped-def]
    return Roles(as_role)


@pytest.fixture
def employee(db: Session) -> Callable[[str], int]:
    def _employee(name: str) -> int:
        row = Employee(full_name=name)
        db.add(row)
        db.commit()
        return row.id

    return _employee


@pytest.fixture
def boca(r: Roles) -> Callable[[str], int]:
    def _boca(code: str) -> int:
        created = r.admin.post("/api/v1/outlets", json={"code": code, "name": f"Boca {code}"})
        assert created.status_code == 201, created.text
        return created.json()["id"]  # type: ignore[no-any-return]

    return _boca


def _assign(client: TestClient, outlet_id: int, employee_id: int, valid_from: str):  # type: ignore[no-untyped-def]
    return client.post(
        f"{URL}/outlets/{outlet_id}/responsibilities", json={"employee_id": employee_id, "valid_from": valid_from}
    )


def _end(client: TestClient, rid: int, valid_to: str, reason: str = "Cambio de turno de la semana"):  # type: ignore[no-untyped-def]
    return client.post(f"{URL}/outlet-responsibilities/{rid}/end", json={"valid_to": valid_to, "reason": reason})


def _refused(db: Session, sql: str, **params: object) -> str:
    def _write() -> None:
        db.execute(text(sql), params)
        db.commit()

    with pytest.raises(DBAPIError) as caught:
        _write()
    db.rollback()
    return str(caught.value.orig)


def test_a_valid_responsibility(r: Roles, boca, employee, db: Session) -> None:  # type: ignore[no-untyped-def]
    outlet, ana = boca("B1"), employee("Ana Gómez")
    created = _assign(r.admin, outlet, ana, days_ago(3).isoformat())
    assert created.status_code == 201, created.text
    body = created.json()
    assert (body["outlet_code"], body["employee_name"], body["valid_from"], body["valid_to"]) == (
        "B1",
        "Ana Gómez",
        days_ago(3).isoformat(),
        None,
    )
    assert (body["ended_at"], body["ended_by_name"], body["end_reason"]) == (None, None, None)
    assert body["created_by_name"] == "Usuario Test"
    [event] = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = 'settlements.outlet_responsibility.created'")
    ).scalars()
    assert event["after"] == {"outlet_id": outlet, "employee_id": ana, "valid_from": days_ago(3).isoformat()}


def test_two_different_responsables_at_once(r: Roles, boca, employee) -> None:  # type: ignore[no-untyped-def]
    outlet, ana, beto = boca("B1"), employee("Ana"), employee("Beto")
    assert _assign(r.admin, outlet, ana, days_ago(5).isoformat()).status_code == 201
    assert _assign(r.admin, outlet, beto, days_ago(2).isoformat()).status_code == 201
    now = r.viewer.get(f"{URL}/outlets/{outlet}/responsibilities", params={"on": today().isoformat()}).json()
    assert sorted(i["employee_name"] for i in now["items"]) == ["Ana", "Beto"]
    assert now["total"] == 2


def test_the_same_employee_in_the_same_boca_overlapping_is_refused(  # type: ignore[no-untyped-def]
    r: Roles, boca, employee, db: Session
) -> None:
    outlet, ana = boca("B1"), employee("Ana")
    first = _assign(r.admin, outlet, ana, days_ago(10).isoformat()).json()
    again = _assign(r.admin, outlet, ana, days_ago(2).isoformat())
    assert (again.status_code, again.json()["code"]) == (409, "outlet_responsibility_overlap")
    assert again.json()["details"] == {"responsibility_id": first["id"]}
    # ended the day before yesterday: from yesterday on it may start again; on the last day it may not
    assert _end(r.admin, first["id"], days_ago(2).isoformat()).status_code == 200
    on_last_day = _assign(r.admin, outlet, ana, days_ago(2).isoformat())
    assert (on_last_day.status_code, on_last_day.json()["code"]) == (409, "outlet_responsibility_overlap")
    before_start = _assign(r.admin, outlet, ana, days_ago(20).isoformat())  # open-ended, so it overlaps
    assert before_start.status_code == 409
    assert _assign(r.admin, outlet, ana, days_ago(1).isoformat()).status_code == 201
    message = _refused(
        db,
        "INSERT INTO settlements.outlet_responsibilities (outlet_id, employee_id, valid_from, created_by) "
        "SELECT :o, :e, :d, id FROM users LIMIT 1",
        o=outlet,
        e=ana,
        d=today(),
    )
    assert "ex_outlet_responsibilities_no_overlap" in message


def test_the_same_employee_in_two_bocas(r: Roles, boca, employee) -> None:  # type: ignore[no-untyped-def]
    north, south, ana = boca("NORTE"), boca("SUR"), employee("Ana")
    assert _assign(r.admin, north, ana, days_ago(3).isoformat()).status_code == 201
    assert _assign(r.admin, south, ana, days_ago(3).isoformat()).status_code == 201
    mine = r.viewer.get(f"{URL}/outlet-responsibilities", params={"employee_id": ana, "on": today().isoformat()})
    assert sorted(i["outlet_code"] for i in mine.json()["items"]) == ["NORTE", "SUR"]


def test_history_by_date_is_inclusive_at_both_ends(r: Roles, boca, employee) -> None:  # type: ignore[no-untyped-def]
    outlet, ana, beto = boca("B1"), employee("Ana"), employee("Beto")
    first = _assign(r.admin, outlet, ana, days_ago(10).isoformat()).json()
    assert _end(r.admin, first["id"], days_ago(6).isoformat()).status_code == 200
    _assign(r.admin, outlet, beto, days_ago(5).isoformat())

    def on(n: int) -> list[str]:
        page = r.viewer.get(f"{URL}/outlets/{outlet}/responsibilities", params={"on": days_ago(n).isoformat()})
        assert page.status_code == 200
        return [i["employee_name"] for i in page.json()["items"]]

    assert on(11) == []
    assert on(10) == ["Ana"]  # first day
    assert on(6) == ["Ana"]  # last day
    assert on(5) == ["Beto"]
    assert on(0) == ["Beto"]  # no upper bound while open
    full = r.viewer.get(f"{URL}/outlets/{outlet}/responsibilities").json()
    assert [i["employee_name"] for i in full["items"]] == ["Beto", "Ana"]  # whole history, latest start first
    assert r.viewer.get(f"{URL}/outlets/{outlet}/responsibilities", params={"on": "2026-13-01"}).status_code == 422


def test_ending_a_responsibility(r: Roles, boca, employee, db: Session) -> None:  # type: ignore[no-untyped-def]
    outlet, ana = boca("B1"), employee("Ana")
    rid = _assign(r.admin, outlet, ana, days_ago(4).isoformat()).json()["id"]
    before_start = _end(r.admin, rid, days_ago(5).isoformat())
    assert (before_start.status_code, before_start.json()["code"]) == (422, "invalid_valid_to")
    blank = _end(r.admin, rid, days_ago(1).isoformat(), reason="   ")
    assert (blank.status_code, blank.json()["code"]) == (422, "end_reason_required")
    assert _end(r.admin, rid, days_ago(1).isoformat(), reason="").status_code == 422
    ended = _end(r.admin, rid, days_ago(4).isoformat(), reason="  Pasó a  reparto ")  # the same day it started
    assert ended.status_code == 200, ended.text
    body = ended.json()
    assert (body["valid_to"], body["end_reason"], body["ended_by_name"]) == (
        days_ago(4).isoformat(),
        "Pasó a reparto",
        "Usuario Test",
    )
    assert body["ended_at"] is not None
    [event] = db.execute(
        text("SELECT payload FROM audit_events WHERE event_type = 'settlements.outlet_responsibility.ended'")
    ).scalars()
    assert event["after"] == {"valid_to": days_ago(4).isoformat(), "reason": "Pasó a reparto"}


def test_a_second_end_is_refused_and_history_is_never_rewritten(  # type: ignore[no-untyped-def]
    r: Roles, boca, employee, db: Session
) -> None:
    outlet, ana = boca("B1"), employee("Ana")
    rid = _assign(r.admin, outlet, ana, days_ago(4).isoformat()).json()["id"]
    assert _end(r.admin, rid, days_ago(2).isoformat()).status_code == 200
    again = _end(r.admin, rid, days_ago(1).isoformat())
    assert (again.status_code, again.json()["code"]) == (409, "outlet_responsibility_already_ended")
    sql = "UPDATE settlements.outlet_responsibilities SET {} WHERE id = :rid"
    assert "already ended" in _refused(db, sql.format("valid_to = :d"), d=days_ago(1), rid=rid)
    assert "already ended" in _refused(db, sql.format("end_reason = 'otro'"), rid=rid)
    assert (
        r.viewer.get(f"{URL}/outlet-responsibilities", params={"outlet_id": outlet}).json()["items"][0]["valid_to"]
        == days_ago(2).isoformat()
    )


def test_an_open_responsibility_only_changes_by_ending(  # type: ignore[no-untyped-def]
    r: Roles, boca, employee, db: Session
) -> None:
    outlet, ana, beto = boca("B1"), employee("Ana"), employee("Beto")
    rid = _assign(r.admin, outlet, ana, days_ago(4).isoformat()).json()["id"]
    sql = "UPDATE settlements.outlet_responsibilities SET {} WHERE id = :rid"
    assert "only change is ending" in _refused(db, sql.format("valid_from = :d"), d=days_ago(9), rid=rid)
    message = _refused(
        db,
        sql.format("employee_id = :e, valid_to = :d, end_reason = 'x', ended_by = created_by, ended_at = now()"),
        e=beto,
        d=today(),
        rid=rid,
    )
    assert "never change" in message
    incomplete = _refused(db, sql.format("valid_to = :d"), d=today(), rid=rid)  # an end without reason or who
    assert "ck_outlet_responsibilities_end_complete" in incomplete
    born_ended = _refused(
        db,
        "INSERT INTO settlements.outlet_responsibilities "
        "(outlet_id, employee_id, valid_from, valid_to, created_by, ended_by, ended_at, end_reason) "
        "SELECT :o, :e, :f, :t, id, id, now(), 'x' FROM users LIMIT 1",
        o=outlet,
        e=beto,
        f=days_ago(9),
        t=days_ago(8),
    )
    assert "born open" in born_ended


def test_nothing_is_deleted(r: Roles, boca, employee, db: Session) -> None:  # type: ignore[no-untyped-def]
    outlet, ana = boca("B1"), employee("Ana")
    rid = _assign(r.admin, outlet, ana, days_ago(4).isoformat()).json()["id"]
    assert r.admin.delete(f"{URL}/outlet-responsibilities/{rid}").status_code in (404, 405)  # no endpoint
    message = _refused(db, "DELETE FROM settlements.outlet_responsibilities WHERE id = :rid", rid=rid)
    assert "outlet_responsibilities rows are never deleted" in message
    assert db.execute(text("SELECT count(*) FROM settlements.outlet_responsibilities")).scalar_one() == 1


def test_an_inactive_boca_takes_no_new_responsable(r: Roles, boca, employee, db: Session) -> None:  # type: ignore[no-untyped-def]
    outlet, ana, beto = boca("B1"), employee("Ana"), employee("Beto")
    rid = _assign(r.admin, outlet, ana, days_ago(4).isoformat()).json()["id"]
    assert r.admin.patch(f"/api/v1/outlets/{outlet}", json={"active": False}).json()["active"] is False
    refused = _assign(r.admin, outlet, beto, today().isoformat())
    assert (refused.status_code, refused.json()["code"]) == (422, "inactive_outlet")
    message = _refused(
        db,
        "INSERT INTO settlements.outlet_responsibilities (outlet_id, employee_id, valid_from, created_by) "
        "SELECT :o, :e, :d, id FROM users LIMIT 1",
        o=outlet,
        e=beto,
        d=today(),
    )
    assert "is not active" in message
    # the existing one stays readable and can be ended
    page = r.viewer.get(f"{URL}/outlets/{outlet}/responsibilities", params={"on": today().isoformat()}).json()
    assert [(i["employee_name"], i["outlet_active"]) for i in page["items"]] == [("Ana", False)]
    assert _end(r.admin, rid, today().isoformat(), reason="Boca cerrada").status_code == 200


def test_an_inactive_employee_takes_no_new_responsibility(  # type: ignore[no-untyped-def]
    r: Roles, boca, employee, db: Session
) -> None:
    north, south, ana = boca("NORTE"), boca("SUR"), employee("Ana")
    rid = _assign(r.admin, north, ana, days_ago(4).isoformat()).json()["id"]
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": ana})
    db.commit()
    refused = _assign(r.admin, south, ana, today().isoformat())
    assert (refused.status_code, refused.json()["code"]) == (422, "inactive_employee")
    assert _assign(r.admin, south, 999999, today().isoformat()).json()["code"] == "inactive_employee"
    assert ana not in [e["id"] for e in r.admin.get(f"{URL}/outlet-responsibilities/employees").json()]
    message = _refused(
        db,
        "INSERT INTO settlements.outlet_responsibilities (outlet_id, employee_id, valid_from, created_by) "
        "SELECT :o, :e, :d, id FROM users LIMIT 1",
        o=south,
        e=ana,
        d=today(),
    )
    assert "is not active" in message
    page = r.viewer.get(f"{URL}/outlet-responsibilities", params={"employee_id": ana}).json()
    assert [(i["outlet_code"], i["employee_active"]) for i in page["items"]] == [("NORTE", False)]
    assert _end(r.admin, rid, today().isoformat(), reason="Dejó la empresa").status_code == 200


def test_selectable_employees_are_the_active_ones(r: Roles, employee, db: Session) -> None:  # type: ignore[no-untyped-def]
    ana, beto = employee("Ana"), employee("Beto")
    db.execute(text("UPDATE employees SET active = false WHERE id = :e"), {"e": beto})
    db.commit()
    names = [(e["id"], e["full_name"]) for e in r.admin.get(f"{URL}/outlet-responsibilities/employees").json()]
    assert names == [(ana, "Ana")]


def test_permissions(r: Roles, boca, employee, client: TestClient) -> None:  # type: ignore[no-untyped-def]
    outlet, ana = boca("B1"), employee("Ana")
    rid = _assign(r.admin, outlet, ana, days_ago(4).isoformat()).json()["id"]
    for role in ("viewer", "operator"):
        user = getattr(r, role)
        assert user.get(f"{URL}/outlet-responsibilities").status_code == 200
        assert user.get(f"{URL}/outlets/{outlet}/responsibilities").status_code == 200
        assert _assign(getattr(r, role), outlet, employee(f"Otro {role}"), today().isoformat()).status_code == 403
        assert _end(getattr(r, role), rid, today().isoformat()).status_code == 403
        assert getattr(r, role).get(f"{URL}/outlet-responsibilities/employees").status_code == 403
    client.post("/api/v1/auth/logout")
    assert client.get(f"{URL}/outlet-responsibilities").status_code == 401


def test_a_user_without_rendiciones_reads_bocas_but_not_responsables(  # type: ignore[no-untyped-def]
    r: Roles, boca, login
) -> None:
    outlet = boca("B1")
    user = login("viewer")  # a platform user with no role in Rendiciones
    assert user.get("/api/v1/outlets").status_code == 200
    assert user.get(f"{URL}/outlets/{outlet}/responsibilities").status_code == 403


def test_unknown_boca_or_responsibility(r: Roles, employee) -> None:  # type: ignore[no-untyped-def]
    ana = employee("Ana")
    assert r.viewer.get(f"{URL}/outlets/999/responsibilities").json()["code"] == "outlet_not_found"
    assert _assign(r.admin, 999, ana, today().isoformat()).json()["code"] == "outlet_not_found"
    assert _end(r.admin, 999, today().isoformat()).json()["code"] == "outlet_responsibility_not_found"
