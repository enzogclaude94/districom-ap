from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.platform.audit.models import AuditEvent
from app.platform.parties.models import Employee, EmployeeFunction
from app.platform.parties.service import has_function

SALESPERSON = "commercial.salesperson"


def _create(client: TestClient, **body: object) -> dict:  # type: ignore[type-arg]
    payload = {"full_name": "Pedro Gómez", **body}
    response = client.post("/api/v1/employees", json=payload)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _assign(client: TestClient, employee_id: int, valid_from: str, code: str = SALESPERSON):  # type: ignore[no-untyped-def]
    return client.post(
        f"/api/v1/employees/{employee_id}/functions", json={"function_code": code, "valid_from": valid_from}
    )


def _events(db: Session, event_type: str) -> list[AuditEvent]:
    return list(db.scalars(select(AuditEvent).where(AuditEvent.event_type == event_type).order_by(AuditEvent.id)))


def test_create_get_and_list(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    created = _create(client, hire_date="2025-03-01", phone=" 351 555 0000 ")
    assert created["full_name"] == "Pedro Gómez"
    assert created["active"] is True
    assert created["phone"] == "351 555 0000"
    assert created["functions"] == []
    assert created["external_axum_id"] is None

    assert client.get(f"/api/v1/employees/{created['id']}").json() == created
    page = client.get("/api/v1/employees").json()
    assert page["total"] == 1
    assert page["items"][0]["id"] == created["id"]


def test_list_filters_search_and_paginates(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    ana = _create(client, full_name="Ana Ruiz")
    _create(client, full_name="Bruno Díaz")
    zoe = _create(client, full_name="Zoe 100%")
    assert _assign(client, ana["id"], "2026-01-01").status_code == 201
    client.post(f"/api/v1/employees/{zoe['id']}/deactivate", json={})

    assert [e["full_name"] for e in client.get("/api/v1/employees?q=ruiz").json()["items"]] == ["Ana Ruiz"]
    # Wildcards in the search are literal.
    assert client.get("/api/v1/employees", params={"q": "%"}).json()["total"] == 1
    assert [e["id"] for e in client.get(f"/api/v1/employees?function_code={SALESPERSON}").json()["items"]] == [
        ana["id"]
    ]
    assert client.get("/api/v1/employees?active=true").json()["total"] == 2
    page = client.get("/api/v1/employees?limit=1&offset=1").json()
    assert page["total"] == 3
    assert [e["full_name"] for e in page["items"]] == ["Bruno Díaz"]


def test_update_audits_before_and_after_without_pii(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    created = _create(client, phone="351 555 0000")
    response = client.put(
        f"/api/v1/employees/{created['id']}", json={"full_name": "Pedro A. Gómez", "phone": "351 555 1111"}
    )
    assert response.status_code == 200, response.text
    assert response.json()["full_name"] == "Pedro A. Gómez"

    (event,) = _events(db, "platform.employee.updated")
    assert event.entity_type == "employee"
    assert event.entity_id == str(created["id"])
    assert event.payload["action"] == "update"
    assert event.payload["before"]["full_name"] == "Pedro Gómez"
    assert event.payload["after"]["full_name"] == "Pedro A. Gómez"
    assert event.payload["after"]["phone_changed"] is True
    assert "351" not in str(event.payload)
    assert _events(db, "platform.employee.created")[0].payload["before"] is None


def test_update_without_changes_is_not_audited(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    created = _create(client)
    client.put(f"/api/v1/employees/{created['id']}", json={"full_name": "Pedro Gómez"})
    assert _events(db, "platform.employee.updated") == []


def test_validation_rejects_blank_name_and_unknown_fields(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    assert client.post("/api/v1/employees", json={"full_name": "   "}).status_code == 422
    assert client.post("/api/v1/employees", json={"full_name": "X", "external_axum_id": "7"}).status_code == 422


def test_assign_and_end_function_keeps_history(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    employee = _create(client)
    response = _assign(client, employee["id"], "2026-01-01")
    assert response.status_code == 201, response.text
    (function,) = response.json()["functions"]
    assert function["label"] == "Preventista"
    assert function["valid_to"] is None
    assert function["current"] is True

    ended = client.post(
        f"/api/v1/employees/{employee['id']}/functions/{function['id']}/end", json={"valid_to": "2026-03-31"}
    )
    assert ended.status_code == 200, ended.text
    # A new period of the same function starts the next day: history has two rows.
    assert _assign(client, employee["id"], "2026-04-01").status_code == 201
    rows = db.scalars(select(EmployeeFunction).order_by(EmployeeFunction.valid_from)).all()
    assert [(r.valid_from, r.valid_to) for r in rows] == [
        (date(2026, 1, 1), date(2026, 3, 31)),
        (date(2026, 4, 1), None),
    ]
    assert has_function(db, employee["id"], SALESPERSON, date(2026, 2, 15))
    assert has_function(db, employee["id"], SALESPERSON, date(2026, 5, 1))
    assert not has_function(db, employee["id"], SALESPERSON, date(2025, 12, 31))
    assert len(_events(db, "platform.employee_function.assigned")) == 2
    (end_event,) = _events(db, "platform.employee_function.ended")
    assert end_event.payload["before"]["valid_to"] is None
    assert end_event.payload["after"]["valid_to"] == "2026-03-31"


def test_function_rules(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    employee = _create(client)
    function_id = _assign(client, employee["id"], "2026-01-01").json()["functions"][0]["id"]

    overlap = _assign(client, employee["id"], "2026-06-01")
    assert overlap.status_code == 409
    assert overlap.json()["code"] == "function_overlap"
    assert _assign(client, employee["id"], "2026-01-01", code="commercial.unknown").json()["code"] == "unknown_function"

    end_url = f"/api/v1/employees/{employee['id']}/functions/{function_id}/end"
    assert client.post(end_url, json={"valid_to": "2025-12-31"}).json()["code"] == "invalid_end_date"
    assert client.post(end_url, json={"valid_to": "2026-01-01"}).status_code == 200  # one-day period
    assert client.post(end_url, json={"valid_to": "2026-02-01"}).json()["code"] == "function_already_ended"
    other = _create(client, full_name="Otro")
    assert (
        client.post(
            f"/api/v1/employees/{other['id']}/functions/{function_id}/end", json={"valid_to": "2026-02-01"}
        ).status_code
        == 404
    )


def test_deactivate_closes_open_functions_and_reactivate(login, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    employee = _create(client)
    _assign(client, employee["id"], "2026-01-01")
    response = client.post(f"/api/v1/employees/{employee['id']}/deactivate", json={"end_date": "2026-09-30"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active"] is False
    assert body["functions"][0]["valid_to"] == "2026-09-30"
    assert _assign(client, employee["id"], "2026-10-01").json()["code"] == "employee_inactive"
    assert client.post(f"/api/v1/employees/{employee['id']}/deactivate", json={}).status_code == 409
    # The row is never deleted.
    assert db.get(Employee, employee["id"]) is not None

    reactivated = client.post(f"/api/v1/employees/{employee['id']}/reactivate")
    assert reactivated.json()["active"] is True
    assert reactivated.json()["functions"][0]["valid_to"] == "2026-09-30"  # not reopened
    assert client.post(f"/api/v1/employees/{employee['id']}/reactivate").status_code == 409
    (event,) = _events(db, "platform.employee.deactivated")
    assert event.payload["after"]["end_date"] == "2026-09-30"


def test_deactivate_rejects_end_date_before_an_open_function(login) -> None:  # type: ignore[no-untyped-def]
    client = login("admin")
    employee = _create(client)
    _assign(client, employee["id"], "2026-10-01")
    response = client.post(f"/api/v1/employees/{employee['id']}/deactivate", json={"end_date": "2026-09-30"})
    assert response.json()["code"] == "function_starts_after_end"
    assert client.get(f"/api/v1/employees/{employee['id']}").json()["active"] is True


def test_function_codes_lists_registered_functions(login) -> None:  # type: ignore[no-untyped-def]
    codes = login("admin").get("/api/v1/employees/function-codes").json()
    assert {"code": SALESPERSON, "label": "Preventista"} in codes


def test_only_platform_admins_manage_employees(client: TestClient, login) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/employees").status_code == 401
    viewer = login("viewer")
    assert viewer.get("/api/v1/employees").status_code == 403
    assert viewer.post("/api/v1/employees", json={"full_name": "X"}).status_code == 403


def test_not_found(login) -> None:  # type: ignore[no-untyped-def]
    response = login("admin").get("/api/v1/employees/999")
    assert response.status_code == 404
    assert response.json()["code"] == "employee_not_found"


def test_database_rejects_overlapping_periods_and_inverted_ranges(db: Session) -> None:
    employee = Employee(full_name="Directo")
    db.add(employee)
    db.flush()
    db.add(EmployeeFunction(employee_id=employee.id, function_code=SALESPERSON, valid_from=date(2026, 1, 1)))
    db.flush()
    db.add(
        EmployeeFunction(
            employee_id=employee.id, function_code=SALESPERSON, valid_from=date(2025, 6, 1), valid_to=date(2026, 1, 1)
        )
    )
    with pytest.raises(IntegrityError, match="ex_employee_functions_no_overlap"):
        db.flush()
    db.rollback()

    with pytest.raises(IntegrityError, match="valid_range"):
        db.execute(
            text(
                "INSERT INTO employees (full_name) VALUES ('X');"
                "INSERT INTO employee_functions (employee_id, function_code, valid_from, valid_to) "
                "VALUES (currval('employees_id_seq'), 'x.y', '2026-02-01', '2026-01-01')"
            )
        )
    db.rollback()


def test_external_ids_are_unique_when_present(db: Session) -> None:
    db.add_all([Employee(full_name="A"), Employee(full_name="B")])  # both without external ids
    db.flush()
    db.add_all([Employee(full_name="C", external_axum_id="7"), Employee(full_name="D", external_axum_id="7")])
    with pytest.raises(IntegrityError, match="uq_employees_external_axum_id"):
        db.flush()
    db.rollback()
