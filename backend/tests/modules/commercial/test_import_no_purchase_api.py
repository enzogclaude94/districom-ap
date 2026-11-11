"""F1.4 over HTTP: the AXUM report through the existing Imports screen's API (upload, preview, confirm, an overlap an
admin decides, the history), the audited customer-code rule, and the F1.3b privacy policy with the report's own
sensitive values: a customer's name and address reach neither a viewer, nor the logs, nor the audit trail."""

import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.commercial.no_purchase.models import NoPurchaseEvent
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from tests.modules.commercial.axum import NO_PURCHASE, axum_file, visit
from tests.modules.commercial.test_import_screen_api import masters, sign_in  # noqa: F401  (fixtures)

XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
NAME, ADDRESS = "ALMACEN PRIVADO AXUM", "PASAJE RESERVADO 4321"
SENSITIVE = (NAME, ADDRESS)
ROW_KEYS = {"id", "sheet", "row_number", "status", "outcome", "business_key", "entity_type", "entity_id", "messages"}
RULE = "/api/v1/commercial/axum-customer-code-rule"


def private_report(day: date = date(2026, 10, 20), *, extra: int = 0) -> bytes:
    rows = [
        visit(77, NAME, "Si", day=day, address=ADDRESS),
        visit(77, NAME, "CERRADO", day=day, address=ADDRESS, start="10:00:00", end="10:00:00", minutes="0:0"),
        visit(1, "CLIENTE 1", "TIENE STOCK", day=day, address=ADDRESS),
        *[visit(2, "CLIENTE 2", "SIN DINERO", day=day, address=ADDRESS) for _ in range(extra)],
    ]
    return axum_file([("01", rows)])


def upload(client: TestClient, data: bytes) -> dict:  # type: ignore[type-arg]
    response = client.post("/api/v1/imports", files={"file": ("Compro no compro.xlsx", data, XLSX)})
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def rows(client: TestClient, job_id: int, **params: object) -> dict:  # type: ignore[type-arg]
    response = client.get(f"/api/v1/imports/{job_id}/rows", params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_upload_preview_confirm_and_history(masters: User, sign_in, db: Session) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    operator = sign_in("operator", "operator")
    job = upload(operator, private_report())
    assert (job["import_type"], job["status"]) == (NO_PURCHASE, "pending")
    assert (job["period_from"], job["period_to"]) == ("2026-10-20", "2026-10-20")
    types = {t["key"]: t for t in operator.get("/api/v1/imports/types").json()}
    assert types[NO_PURCHASE]["source_system"] == "AXUM"
    messages = {m["code"]: m["rows"] for m in operator.get(f"/api/v1/imports/{job['id']}/messages").json()}
    assert messages["purchase_reported"] == 1
    assert messages["customer_unknown_code"] == 1  # 77 is not a STORE SYS customer
    assert messages["salesperson_unmapped"] == 2

    confirmed = operator.post(f"/api/v1/imports/{job['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["counts"]["inserted"] == 2
    assert len(db.scalars(select(NoPurchaseEvent)).all()) == 2

    history = operator.get("/api/v1/imports", params={"module": "commercial"}).json()["items"]
    entry = next(item for item in history if item["id"] == job["id"])
    assert entry["import_type"] == NO_PURCHASE
    # The platform counts what is not skipped: the purchase is skipped, as the header and the group row.
    assert entry["rows_processed"] == 2


def test_an_overlap_is_decided_by_an_admin_over_http(masters: User, sign_in, db: Session) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    operator = sign_in("operator", "operator")
    first = upload(operator, private_report())
    assert operator.post(f"/api/v1/imports/{first['id']}/confirm").status_code == 200
    later = upload(operator, private_report(extra=1))
    assert later["metadata"]["preview"]["replacement"]["action"] == "blocked"

    blocked = operator.post(f"/api/v1/imports/{later['id']}/confirm")
    assert (blocked.status_code, blocked.json()["code"]) == (409, "import_decision_required")
    assert blocked.json()["details"]["reasons"] == ["no_purchase_overlap"]
    refused = operator.post(f"/api/v1/imports/{later['id']}/confirm", json={"decision": "replace", "reason": "x"})
    assert refused.status_code == 403

    admin = sign_in("admin", None)
    empty = admin.post(f"/api/v1/imports/{later['id']}/confirm", json={"decision": "replace", "reason": " "})
    assert empty.status_code == 422
    done = admin.post(
        f"/api/v1/imports/{later['id']}/confirm", json={"decision": "replace", "reason": "Export del día completo"}
    )
    assert done.status_code == 200, done.text
    statuses = sorted(e.status for e in db.scalars(select(NoPurchaseEvent)))
    assert statuses == ["ACTIVE", "ACTIVE", "ACTIVE", "SUPERSEDED", "SUPERSEDED"]


def test_the_customer_code_rule_is_changed_only_by_an_admin_with_a_reason(masters: User, sign_in, db: Session) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    viewer = sign_in("viewer", "viewer")
    assert viewer.get(RULE).json() == {"enabled": True}
    assert viewer.put(RULE, json={"enabled": False, "reason": "x"}).status_code == 403
    operator = sign_in("operator", "operator")
    assert operator.put(RULE, json={"enabled": False, "reason": "x"}).status_code == 403

    admin = sign_in("admin", None)
    empty = admin.put(RULE, json={"enabled": False, "reason": " "})
    assert (empty.status_code, empty.json()["code"]) == (422, "reason_required")
    done = admin.put(RULE, json={"enabled": False, "reason": "Pausa para revisar"})
    assert done.json() == {"enabled": False}
    changes = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "commercial.config.axum_customer_code_rule_set")
        .order_by(AuditEvent.id)
    ).all()
    assert [c.payload["after"] for c in changes] == [
        changes[0].payload["after"],  # the seed's, with Enzo's decision
        {"enabled": False, "reason": "Pausa para revisar"},
    ]
    assert changes[0].payload["after"]["enabled"] is True


def test_a_viewer_gets_no_name_or_address_of_the_report(masters: User, sign_in) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    job = upload(sign_in("operator", "operator"), private_report())
    viewer = sign_in("viewer", "viewer")
    page = rows(viewer, job["id"])
    assert page["total"] == 5  # header, group row and 3 data rows
    for item in page["items"]:
        assert set(item) == ROW_KEYS
    detail = viewer.get(f"/api/v1/imports/{job['id']}").json()
    messages = viewer.get(f"/api/v1/imports/{job['id']}/messages").json()
    for value in SENSITIVE:
        assert value not in str(page)
        assert value not in str(detail)
        assert value not in str(messages)


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operators_and_admins_get_the_rows_values(masters: User, sign_in, role: str) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    job = upload(sign_in("operator", "operator"), private_report())
    client = sign_in("admin", None) if role == "admin" else sign_in("diagnose", "operator")
    page = rows(client, job["id"], status="pending_resolution")
    assert page["items"]
    for item in page["items"]:
        assert set(item) == ROW_KEYS | {"raw", "normalized"}
    assert any(NAME in str(item["raw"]) for item in page["items"])
    assert any(ADDRESS in str(item["normalized"]) for item in page["items"])


def test_logs_and_audit_never_carry_a_name_or_address(  # type: ignore[no-untyped-def]
    masters: User,  # noqa: F811
    sign_in,  # noqa: F811
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    operator = sign_in("operator", "operator")
    first = upload(operator, private_report())
    rows(operator, first["id"])
    assert operator.post(f"/api/v1/imports/{first['id']}/confirm").status_code == 200
    later = upload(operator, private_report(extra=1))
    admin = sign_in("admin", None)
    decided = admin.post(
        f"/api/v1/imports/{later['id']}/confirm", json={"decision": "replace", "reason": "Export del día completo"}
    )
    assert decided.status_code == 200, decided.text
    rows(sign_in("viewer", "viewer"), later["id"])

    logged = caplog.text + " ".join(str(record.__dict__) for record in caplog.records)
    audited = " ".join(str(event.payload) for event in db.scalars(select(AuditEvent)))
    types = {event.event_type for event in db.scalars(select(AuditEvent))}
    assert {"commercial.no_purchase.events_superseded", "commercial.axum.customers_linked_by_rule"} <= types
    for value in (*SENSITIVE, "CLIENTE 1"):
        assert value not in logged
        assert value not in audited
