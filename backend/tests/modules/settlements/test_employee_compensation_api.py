"""Salary terms of an employee through the API (R8-1, ADR 010): monthly internal salary, history by validity.

The database repeats each rule (`test_employee_compensation_db_invariants.py`).
"""

from collections.abc import Callable
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.parties.models import Employee
from tests.modules.settlements.days import days_ago, today
from tests.modules.settlements.employee_finance_support import EF, error


def _admin(user_as: Callable[..., TestClient]) -> TestClient:
    return user_as("rrhh", settlements="viewer", area="admin")


def _term(client: TestClient, employee_id: int, valid_from: str, salary: str, notes: str | None = None) -> dict:  # type: ignore[type-arg]
    response = client.post(
        f"{EF}/employees/{employee_id}/compensation-terms",
        json={"valid_from": valid_from, "base_salary": salary, "notes": notes},
    )
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def _end(client: TestClient, term_id: int, valid_to: str, reason: str = "Aumento de sueldo") -> object:
    return client.post(f"{EF}/compensation-terms/{term_id}/end", json={"valid_to": valid_to, "reason": reason})


def test_create_a_term_with_its_author_and_audit(
    user_as: Callable[..., TestClient], employee: Callable[[str], int], db: Session
) -> None:
    ana = employee("Ana Pérez")
    client = _admin(user_as)
    created = _term(client, ana, days_ago(10).isoformat(), "800000", "  Acordado   en la entrevista ")
    assert created["base_salary"] == "800000.00"
    assert created["valid_to"] is None
    assert created["status"] == "CURRENT"
    assert created["notes"] == "Acordado en la entrevista"
    assert created["created_by_name"] == "Usuario Test"
    audited = db.execute(
        text("SELECT event_type, payload->'after' FROM audit_events WHERE entity_type = 'compensation_term'")
    ).all()
    assert audited == [
        (
            "settlements.employee_finance.compensation_term.created",
            {
                "employee_id": ana,
                "valid_from": days_ago(10).isoformat(),
                "base_salary": "800000.00",
                "notes": "Acordado en la entrevista",
            },
        )
    ]


def test_salary_zero_is_valid_and_negative_is_refused(
    user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    client = _admin(user_as)
    assert _term(client, employee("Pasante"), "2026-01-01", "0")["base_salary"] == "0.00"
    other = employee("Otro")
    for salary in ("-1", "1.234", "123456789012345"):
        response = client.post(
            f"{EF}/employees/{other}/compensation-terms", json={"valid_from": "2026-01-01", "base_salary": salary}
        )
        assert error(response) == (422, "validation_error"), salary


def test_overlap_is_refused_and_a_salary_change_ends_then_creates(
    user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    ana = employee("Ana Pérez")
    client = _admin(user_as)
    first = _term(client, ana, "2026-01-01", "800000")
    # A new open term overlaps the open one, whatever its start.
    for start in ("2026-01-01", "2026-06-01", "2025-12-01"):
        response = client.post(
            f"{EF}/employees/{ana}/compensation-terms", json={"valid_from": start, "base_salary": "900000"}
        )
        assert error(response) == (409, "compensation_term_overlap"), start
        assert response.json()["details"] == {"compensation_term_id": first["id"]}
    # The salary change: end the term in effect (inclusive), then the new one from the next day.
    ended = _end(client, first["id"], "2026-10-31")
    assert ended.status_code == 200, ended.text  # type: ignore[attr-defined]
    assert error(
        client.post(f"{EF}/employees/{ana}/compensation-terms", json={"valid_from": "2026-10-31", "base_salary": "1"})
    ) == (
        409,
        "compensation_term_overlap",
    )
    second = _term(client, ana, "2026-11-01", "950000")
    history = client.get(f"{EF}/employees/{ana}/compensation-terms").json()
    assert [(t["id"], t["valid_from"], t["valid_to"], t["base_salary"]) for t in history] == [
        (second["id"], "2026-11-01", None, "950000.00"),
        (first["id"], "2026-01-01", "2026-10-31", "800000.00"),
    ]
    assert history[1]["end_reason"] == "Aumento de sueldo"
    assert history[1]["ended_by_name"] == "Usuario Test"


def test_a_future_term_is_valid_and_reads_as_future(
    user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    ana = employee("Ana Pérez")
    client = _admin(user_as)
    current = _term(client, ana, days_ago(30).isoformat(), "800000")
    assert _end(client, current["id"], (today() + timedelta(days=5)).isoformat()).status_code == 200  # type: ignore[attr-defined]
    future = _term(client, ana, (today() + timedelta(days=6)).isoformat(), "900000")
    assert future["status"] == "FUTURE"
    row = client.get(f"{EF}/employees/{ana}").json()
    assert row["current_term"]["id"] == current["id"]
    assert row["current_term"]["status"] == "CURRENT"
    assert row["next_term"]["id"] == future["id"]


def test_end_once_with_reason_and_not_before_start(
    user_as: Callable[..., TestClient], employee: Callable[[str], int]
) -> None:
    client = _admin(user_as)
    term = _term(client, employee("Ana Pérez"), "2026-03-01", "800000")
    assert error(_end(client, term["id"], "2026-02-28")) == (422, "invalid_valid_to")
    assert error(_end(client, term["id"], "2026-03-31", "   ")) == (422, "end_reason_required")
    assert error(client.post(f"{EF}/compensation-terms/{term['id']}/end", json={"valid_to": "2026-03-31"})) == (
        422,
        "validation_error",
    )
    # Ending on its first day is valid (inclusive).
    assert _end(client, term["id"], "2026-03-01").status_code == 200  # type: ignore[attr-defined]
    second = _end(client, term["id"], "2026-04-30")
    assert error(second) == (409, "compensation_term_already_ended")
    assert error(_end(client, 999999, "2026-04-30")) == (404, "compensation_term_not_found")


def test_employee_must_exist_and_be_active_to_get_a_new_term_and_history_stays(
    user_as: Callable[..., TestClient], employee: Callable[[str], int], db: Session
) -> None:
    client = _admin(user_as)
    assert error(
        client.post(f"{EF}/employees/999999/compensation-terms", json={"valid_from": "2026-01-01", "base_salary": "1"})
    ) == (
        404,
        "employee_not_found",
    )
    ana = employee("Ana Pérez")
    term = _term(client, ana, "2026-01-01", "800000")
    row = db.get(Employee, ana)
    assert row is not None
    row.active = False
    db.commit()
    assert error(
        client.post(f"{EF}/employees/{ana}/compensation-terms", json={"valid_from": "2027-01-01", "base_salary": "1"})
    ) == (
        422,
        "inactive_employee",
    )
    # Still listed and readable, and its term can still be ended.
    listed = {e["employee_id"]: e for e in client.get(f"{EF}/employees").json()["items"]}
    assert listed[ana]["active"] is False
    assert _end(client, term["id"], "2026-09-30", "Dejó la empresa").status_code == 200  # type: ignore[attr-defined]
    assert client.get(f"{EF}/employees/{ana}/compensation-terms").json()[0]["status"] == "PAST"


def test_terms_are_never_deleted_nor_edited(user_as: Callable[..., TestClient]) -> None:
    """The API offers no edit nor delete: a change is ending and creating."""
    client = _admin(user_as)
    assert client.delete(f"{EF}/compensation-terms/1").status_code in (404, 405)
    assert client.patch(f"{EF}/compensation-terms/1", json={"base_salary": "1"}).status_code in (404, 405)
