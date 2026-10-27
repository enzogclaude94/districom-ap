"""Rendiciones > Integración Store Sys (`settlements.store_sys_import`, Enzo's R9-1 adjustment 3): who imports, who
reads and who changes an employee's Store Sys code, with its history. The platform admin does not inherit the area."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.modules.settlements.store_sys_support import CODES, IMPORT, as_user, record, send

__all__ = ["as_user"]


@pytest.fixture
def ana(employee: Callable[[str], int]) -> int:
    return employee("Ana Pérez")


def _put(client: TestClient, employee_id: int, code: str | None, reason: str | None = None) -> object:
    return client.put(f"{CODES}/{employee_id}", json={"store_code": code, "reason": reason})


@pytest.mark.parametrize(
    ("grants", "status"),
    [
        ({"platform": "admin"}, 404),  # the platform admin does not inherit the area
        ({"settlements": "admin"}, 404),
        ({"settlements": "operator", "store_sys": "viewer"}, 403),
        ({"settlements": "viewer", "store_sys": "operator"}, 403),
        ({"store_sys": "admin"}, 404),  # the area without Rendiciones is nothing
        ({"settlements": "operator", "store_sys": "operator"}, 200),
        ({"platform": "admin", "store_sys": "operator"}, 200),
    ],
)
def test_who_imports(as_user: Callable[..., TestClient], grants: dict[str, str], status: int) -> None:
    client = as_user("someone", **grants)
    assert send(client, record("A", "1")).status_code == status


def test_reading_codes_takes_the_area(as_user: Callable[..., TestClient], ana: int) -> None:
    assert as_user("root", platform="admin").get(CODES).status_code == 404
    assert as_user("cajero", settlements="admin").get(f"{CODES}/{ana}").status_code == 404
    reader = as_user("lector", settlements="viewer", store_sys="viewer")
    listed = reader.get(CODES, params={"q": "Ana"})
    assert listed.status_code == 200
    assert listed.json()["items"] == [
        {"employee_id": ana, "full_name": "Ana Pérez", "active": True, "store_code": None}
    ]
    assert reader.get(f"{CODES}/{ana}/history").json() == []


def test_the_platform_api_no_longer_shows_the_code(as_user: Callable[..., TestClient], ana: int) -> None:
    body = as_user("root", platform="admin").get(f"/api/v1/employees/{ana}").json()
    assert "external_store_id" not in body


def test_only_the_area_admin_changes_a_code_with_history(
    as_user: Callable[..., TestClient], ana: int, db: Session
) -> None:
    for grants in ({"platform": "admin"}, {"settlements": "admin", "store_sys": "operator"}):
        client = as_user("x" + str(len(grants)) + next(iter(grants)), **grants)
        assert _put(client, ana, "R-01").status_code in (403, 404)  # type: ignore[attr-defined]
    admin = as_user("integra", settlements="viewer", store_sys="admin")
    loaded = admin.put(f"{CODES}/{ana}", json={"store_code": " R-01 "})
    assert loaded.status_code == 200, loaded.text
    assert loaded.json()["store_code"] == "R-01"
    # Correcting or removing an existing code takes a reason; nothing is deleted silently.
    assert admin.put(f"{CODES}/{ana}", json={"store_code": "R-02"}).status_code == 422
    assert admin.put(f"{CODES}/{ana}", json={"store_code": None}).status_code == 422
    assert (
        admin.put(f"{CODES}/{ana}", json={"store_code": "R-02", "reason": "Store Sys lo renumeró"}).status_code == 200
    )
    assert admin.put(f"{CODES}/{ana}", json={"store_code": "", "reason": "Ya no reparte"}).status_code == 200
    changes = admin.get(f"{CODES}/{ana}/history").json()
    assert [(c["before"], c["after"], c["reason"], c["changed_by_name"]) for c in changes] == [
        (None, "R-01", None, "Usuario Test"),
        ("R-01", "R-02", "Store Sys lo renumeró", "Usuario Test"),
        ("R-02", None, "Ya no reparte", "Usuario Test"),
    ]
    assert db.execute(text("SELECT external_store_id FROM employees WHERE id = :e"), {"e": ana}).scalar() is None


def test_a_code_belongs_to_one_employee(as_user: Callable[..., TestClient], employee: Callable[[str], int]) -> None:
    admin = as_user("integra", settlements="viewer", store_sys="admin")
    ana, juan = employee("Ana"), employee("Juan")
    assert admin.put(f"{CODES}/{ana}", json={"store_code": "R-01"}).status_code == 200
    taken = admin.put(f"{CODES}/{juan}", json={"store_code": "R-01"})
    assert (taken.status_code, taken.json()["code"]) == (409, "external_code_taken")
    assert admin.put(f"{CODES}/999999", json={"store_code": "R-09"}).status_code == 404


def test_the_area_is_listed_for_who_holds_it(as_user: Callable[..., TestClient]) -> None:
    me = as_user("integra", settlements="viewer", store_sys="admin").get("/api/v1/auth/me").json()
    areas = {a["key"]: a for a in me.get("areas", [])}
    assert "settlements.store_sys_import" in areas
    assert IMPORT.startswith("/api/v1/settlements/")
