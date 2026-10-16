"""Rendiciones > Empleados is a protected area (R8-1, ADR 010, Enzo's D1, D2 and D12).

Who reaches `settlements.employee_finance` (an explicit grant, never inherited by the platform admin, 404
without it), how the platform admin grants it without reading it first, and what Cajas stops showing to
whoever lacks it. The redaction of R8 operations, which do not exist yet, is covered in
`tests/unit/test_employee_finance_privacy.py`.
"""

from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.modules.settlements.ledger.models import Account
from app.platform.auth.access import area_role
from app.platform.auth.module_roles import ModuleRoleError, grant_module_role, revoke_module_role
from tests.modules.settlements.conftest import Roles
from tests.modules.settlements.employee_finance_support import AREA, BASE, EF, error
from tests.modules.settlements.outlet_support import close, count, create, employee_part

ENDPOINTS = (
    f"{EF}/employees",
    f"{EF}/employees/1",
    f"{EF}/employees/1/movements",
    f"{EF}/employees/1/compensation-terms",
)
TERM = {"valid_from": "2026-01-01", "base_salary": "800000"}


def _no_area(client: TestClient) -> None:
    """Every endpoint of the area answers 404 with nothing about the data, reads and writes alike."""
    for path in ENDPOINTS:
        response = client.get(path)
        assert response.status_code == 404, (path, response.text)
        assert response.json()["code"] == "not_found"
        assert response.json()["message"] == "No encontrado."
        assert response.json()["details"] in (None, {})
    assert client.post(f"{EF}/employees/1/compensation-terms", json=TERM).status_code == 404
    assert (
        client.post(f"{EF}/compensation-terms/1/end", json={"valid_to": "2026-02-01", "reason": "x"}).status_code == 404
    )
    assert client.get("/api/v1/auth/me").json()["areas"] == []


def test_a_settlements_viewer_without_the_area_sees_nothing(user_as: Callable[..., TestClient]) -> None:
    _no_area(user_as("lector", settlements="viewer"))


def test_a_settlements_admin_without_the_area_sees_nothing(user_as: Callable[..., TestClient]) -> None:
    _no_area(user_as("jefe", settlements="admin"))


def test_the_platform_admin_does_not_inherit_the_area(user_as: Callable[..., TestClient]) -> None:
    client = user_as("plataforma", platform="admin")
    me = client.get("/api/v1/auth/me").json()
    assert {m["key"]: m["role"] for m in me["modules"]}["settlements"] == "admin"
    _no_area(client)


def test_the_area_needs_access_to_rendiciones_too(user_as: Callable[..., TestClient]) -> None:
    """A grant on the area alone, without a role in Rendiciones, reaches nothing."""
    _no_area(user_as("suelto", area="admin"))


def test_platform_admin_grants_the_area_then_reads_it_and_revoking_removes_it(
    user_as: Callable[..., TestClient], db: Session
) -> None:
    client = user_as("plataforma", platform="admin")
    _no_area(client)
    # Granting reads nothing of the area: it only answers who has which role.
    granted = client.put(
        "/api/v1/auth/access-grants", json={"email": "plataforma@districom.test", "key": AREA, "role": "viewer"}
    )
    assert granted.status_code == 200, granted.text
    assert granted.json() == {"user_id": granted.json()["user_id"], "key": AREA, "role": "viewer"}
    assert client.get("/api/v1/auth/me").json()["areas"] == [{"key": AREA, "name": "Empleados", "role": "viewer"}]
    assert client.get(f"{EF}/employees").status_code == 200
    # Viewer of the area: salary terms are admin's.
    assert error(client.post(f"{EF}/employees/1/compensation-terms", json=TERM)) == (403, "forbidden")
    # Another user too, and the change is audited with who granted it.
    user_as("lector", settlements="viewer")
    client = user_as("plataforma", platform="admin")
    other = client.put(
        "/api/v1/auth/access-grants", json={"email": "lector@districom.test", "key": AREA, "role": "operator"}
    )
    assert other.status_code == 200, other.text
    revoked = client.post("/api/v1/auth/access-grants/revoke", json={"email": "plataforma@districom.test", "key": AREA})
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["role"] is None
    _no_area(client)
    assert user_as("lector").get(f"{EF}/employees").status_code == 200
    audited = db.execute(
        text(
            "SELECT event_type, payload->'after' FROM audit_events"
            " WHERE event_type LIKE 'platform.module_role.%' ORDER BY id"
        )
    ).all()
    assert [(e, (a or {}).get("module")) for e, a in audited] == [
        ("platform.module_role.granted", AREA),
        ("platform.module_role.granted", "settlements"),  # the reader's role in Rendiciones (fixture)
        ("platform.module_role.granted", AREA),
        ("platform.module_role.revoked", None),
    ]


def test_only_the_platform_admin_manages_grants(user_as: Callable[..., TestClient]) -> None:
    client = user_as("jefe", settlements="admin", area="admin")
    body = {"email": "jefe@districom.test", "key": AREA, "role": "admin"}
    assert error(client.put("/api/v1/auth/access-grants", json=body)) == (403, "forbidden")
    assert error(client.post("/api/v1/auth/access-grants/revoke", json={"email": body["email"], "key": AREA})) == (
        403,
        "forbidden",
    )
    admin = user_as("plataforma", platform="admin")
    assert error(admin.put("/api/v1/auth/access-grants", json=body | {"key": "settlements.otra"})) == (
        422,
        "unknown_access_key",
    )
    assert error(admin.put("/api/v1/auth/access-grants", json=body | {"email": "nadie@districom.test"})) == (
        404,
        "user_not_found",
    )


def test_roles_of_the_area(user_as: Callable[..., TestClient], employee: Callable[[str], int]) -> None:
    ana = employee("Ana Pérez")
    for name, role in (("v", "viewer"), ("o", "operator")):
        client = user_as(name, settlements="viewer", area=role)
        assert client.get(f"{EF}/employees").status_code == 200
        assert client.get(f"{EF}/employees/{ana}").status_code == 200
        assert client.get(f"{EF}/employees/{ana}/movements").status_code == 200
        assert client.get(f"{EF}/employees/{ana}/compensation-terms").status_code == 200
        assert error(client.post(f"{EF}/employees/{ana}/compensation-terms", json=TERM)) == (403, "forbidden")
    admin = user_as("a", settlements="viewer", area="admin")
    created = admin.post(f"{EF}/employees/{ana}/compensation-terms", json=TERM)
    assert created.status_code == 201, created.text
    assert error(
        user_as("o").post(
            f"{EF}/compensation-terms/{created.json()['id']}/end", json={"valid_to": "2026-03-31", "reason": "x"}
        )
    ) == (403, "forbidden")


def test_area_role_in_the_authorization_layer(db: Session, make_user) -> None:  # type: ignore[no-untyped-def]
    """The rule lives in the central authorization, not in the endpoints: a platform admin has every module
    and no area until granted; the grant and the revocation take effect at once; unknown keys are refused."""
    admin = make_user(email="root@districom.test", role="admin")
    assert area_role(admin, AREA) is None
    grant_module_role(db, "root@districom.test", AREA, "operator")
    db.refresh(admin)
    assert area_role(admin, AREA) == "operator"
    revoke_module_role(db, "root@districom.test", AREA)
    db.refresh(admin)
    assert area_role(admin, AREA) is None
    try:
        grant_module_role(db, "root@districom.test", "settlements.inexistente", "admin")
    except ModuleRoleError:
        pass
    else:
        raise AssertionError("an unknown area must be refused")


# --- Cajas without the area (Enzo's D2) ------------------------------------------------------------------


def _shortage(
    r: Roles, boca: Callable[[str], int], employee: Callable[[str], int], ids: dict[str, int]
) -> tuple[int, int]:
    """A boca settlement closed with a $1.000 shortage assigned to an employee."""
    ana = employee("Ana Cajera")
    sid = create(r.operator, boca("SUR"), "100000")
    count(r.operator, sid, ("ARS_20000", 4, ids["main_cash"]), ("ARS_1000", 19, ids["main_cash"]))
    closed = close(r.operator, sid, employee_part(ana, "1000"))
    assert closed.status_code == 201, closed.text
    return ana, sid


def test_cajas_hides_internal_accounts_without_the_area(
    r: Roles,
    boca: Callable[[str], int],
    employee: Callable[[str], int],
    ids: dict[str, int],
    db: Session,
    user_as: Callable[..., TestClient],
) -> None:
    ana, _sid = _shortage(r, boca, employee, ids)
    account = db.scalar(select(Account.id).where(Account.employee_id == ana))
    assert account is not None
    for client in (user_as("lector", settlements="viewer"), user_as("plataforma", platform="admin")):
        listed = client.get(f"{BASE}/accounts", params={"include_inactive": True}).json()
        assert all(a["kind"] != "EMPLOYEE" for a in listed)
        assert any(a["kind"] == "CASH_BOX" for a in listed)
        assert error(client.get(f"{BASE}/accounts/{account}")) == (404, "account_not_found")
        assert error(client.get(f"{BASE}/accounts/{account}/movements")) == (404, "account_not_found")
        # Same answer as an account that does not exist: nothing to discover.
        assert (
            client.get(f"{BASE}/accounts/{account}").json()["message"]
            == client.get(f"{BASE}/accounts/999999").json()["message"]
        )
    # With the area, the same user reads it.
    reader = user_as("socio", settlements="viewer", area="viewer")
    listed = reader.get(f"{BASE}/accounts").json()
    assert [a["balance"] for a in listed if a["kind"] == "EMPLOYEE"] == ["1000.00"]
    assert reader.get(f"{BASE}/accounts/{account}").json()["balance"] == "1000.00"
    assert reader.get(f"{BASE}/accounts/{account}/movements").json()["total"] == 1


def test_the_settlement_keeps_showing_its_allocation_without_the_area(
    r: Roles,
    boca: Callable[[str], int],
    employee: Callable[[str], int],
    ids: dict[str, int],
    user_as: Callable[..., TestClient],
) -> None:
    """R2/R7 unchanged: the allocation stays where it is decided (the settlement and its close), without the
    balance of the internal account or any other of its movements."""
    ana, sid = _shortage(r, boca, employee, ids)
    client = user_as("lector", settlements="viewer")
    detail: dict[str, Any] = client.get(f"{BASE}/outlet-settlements/{sid}").json()
    allocations = detail["current_difference"]["allocations"]
    assert [(a["target"], a["employee_id"], a["amount"]) for a in allocations] == [("EMPLOYEE", ana, "1000.00")]
    operation = client.get(f"{BASE}/operations/{detail['close_in_effect']['operation_id']}").json()
    assert operation["type"] == "SETTLEMENT_CLOSE"
    assert [e["amount"] for e in operation["entries"] if e["account_kind"] == "EMPLOYEE"] == ["1000.00"]
    # The cash box still reconciles: its movement is the close, unredacted (not an R8 operation).
    movements = client.get(f"{BASE}/accounts/{ids['main_cash']}/movements").json()["items"]
    assert movements[0]["operation_type"] == "SETTLEMENT_CLOSE"
