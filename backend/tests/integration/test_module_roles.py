import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.cli import main as cli
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import UserModuleRole
from app.platform.auth.module_roles import ModuleRoleError, grant_module_role, revoke_module_role

EMAIL = "viewer@districom.test"


def _roles(db: Session) -> list[tuple[str, str]]:
    db.expire_all()
    return [(r.module_key, r.role) for r in db.scalars(select(UserModuleRole).order_by(UserModuleRole.module_key))]


def test_grant_change_and_revoke_are_audited(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user(email=EMAIL, role="viewer")
    grant_module_role(db, EMAIL, "settlements", "viewer")
    grant_module_role(db, EMAIL, "settlements", "viewer")  # same role: no-op
    grant_module_role(db, EMAIL, "settlements", "operator")
    assert _roles(db) == [("settlements", "operator")]
    assert revoke_module_role(db, EMAIL, "settlements") is True
    assert revoke_module_role(db, EMAIL, "settlements") is False
    assert _roles(db) == []

    events = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type.like("platform.module_role.%")).order_by(AuditEvent.id)
    ).all()
    assert [e.payload["action"] for e in events] == ["grant", "change", "revoke"]
    assert events[1].payload["before"] == {"module": "settlements", "role": "viewer"}
    assert events[1].payload["after"] == {"module": "settlements", "role": "operator"}


@pytest.mark.parametrize(
    ("email", "module", "role", "reason"),
    [
        ("nobody@districom.test", "settlements", "viewer", "unknown user"),
        (EMAIL, "inventory", "viewer", "unknown module"),
        (EMAIL, "settlements", "owner", "unknown role"),
    ],
)
def test_grant_validates_inputs(make_user, db: Session, email: str, module: str, role: str, reason: str) -> None:  # type: ignore[no-untyped-def]
    make_user(email=EMAIL, role="viewer")
    with pytest.raises(ModuleRoleError, match=reason):
        grant_module_role(db, email, module, role)


def test_database_rejects_unknown_roles_and_duplicates(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy.exc import IntegrityError

    user = make_user(email=EMAIL, role="viewer")
    db.add(UserModuleRole(user_id=user.id, module_key="settlements", role="owner"))
    with pytest.raises(IntegrityError, match="ck_user_module_roles_role"):
        db.flush()
    db.rollback()
    db.add_all(
        [
            UserModuleRole(user_id=user.id, module_key="settlements", role="viewer"),
            UserModuleRole(user_id=user.id, module_key="settlements", role="admin"),
        ]
    )
    with pytest.raises(IntegrityError, match="uq_user_module_roles_user_id_module_key"):
        db.flush()
    db.rollback()


def test_cli_grants_and_revokes(make_user, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user(email=EMAIL, role="viewer")
    assert cli(["grant-module-role", "--email", EMAIL, "--module", "settlements", "--role", "operator"]) == 0
    assert _roles(db) == [("settlements", "operator")]
    assert (
        cli(["grant-module-role", "--email", "nobody@districom.test", "--module", "settlements", "--role", "viewer"])
        == 1
    )
    assert cli(["revoke-module-role", "--email", EMAIL, "--module", "settlements"]) == 0
    assert _roles(db) == []
    with pytest.raises(SystemExit):
        cli(["grant-module-role", "--email", EMAIL, "--module", "inventory", "--role", "viewer"])


def test_settlements_endpoints_require_module_access(client: TestClient, login, grant) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/settlements/status").status_code == 401
    viewer = login("viewer")
    grant("viewer", "commercial", "admin")  # admin elsewhere does not open Rendiciones
    response = viewer.get("/api/v1/settlements/status")
    assert response.status_code == 403
    assert response.json()["code"] == "forbidden"

    grant("viewer", "settlements", "operator")
    assert viewer.get("/api/v1/settlements/status").json() == {"module": "settlements", "role": "operator"}


def test_platform_admin_opens_settlements(login) -> None:  # type: ignore[no-untyped-def]
    assert login("admin").get("/api/v1/settlements/status").json() == {"module": "settlements", "role": "admin"}


def test_settings_show_each_module_to_its_admins(login, grant) -> None:  # type: ignore[no-untyped-def]
    admin_scopes = [s["scope"] for s in login("admin").get("/api/v1/settings").json()]
    assert admin_scopes == ["platform", "commercial", "settlements"]

    client = login("viewer")
    grant("viewer", "settlements", "admin")
    grant("viewer", "commercial", "operator")
    scopes = client.get("/api/v1/settings").json()
    assert [s["scope"] for s in scopes] == ["settlements"]
    by_key = {setting["key"]: setting for setting in scopes[0]["settings"]}
    assert set(by_key) == {
        "settlements.pending_alert_hours",
        "settlements.cash_denominations",
        "settlements.cheque_expiring_soon_days",
    }
    assert by_key["settlements.pending_alert_hours"]["value"] == 24
    assert by_key["settlements.cash_denominations"]["is_default"] is True
    assert by_key["settlements.cheque_expiring_soon_days"]["value"] == 7


def test_settlements_registers_the_delivery_driver_function(login) -> None:  # type: ignore[no-untyped-def]
    codes = login("admin").get("/api/v1/employees/function-codes").json()
    assert {"code": "settlements.delivery_driver", "label": "Repartidor"} in codes
