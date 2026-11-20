"""F1.6: Comercial > Datos > Calidad over the API (/api/v1/quality…): the list (filters, order, pagination), the
summary (issues apart from occurrences), the detail with its safe references, ignore and resolve with their
permissions, a stale decision, the audit, and privacy (details, external_ref, logs)."""

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401
from app.modules.commercial.config.seed import seed_config
from app.platform.audit.models import AuditEvent
from app.platform.quality.models import DataQualityIssue
from app.platform.quality.service import record_issue
from tests.conftest import PASSWORD
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    SALES,
    customer,
    customers_file,
    run,
    sale,
    sales_file,
)

API = "/api/v1/quality"
PHONE = "11-5555-0101"
CUIT = "20-12345678-9"
ADDRESS = "CALLE PRIVADA 742"
SECRET_SELLER = "VENDEDOR SECRETO"
NOTE_WITH_A_NAME = "Lo revisó Juana Pérez con el cliente"


@pytest.fixture
def sign_in(client: TestClient, make_user, grant):  # type: ignore[no-untyped-def]
    def _sign_in(name: str, module_role: str | None = "viewer", role: str = "viewer") -> TestClient:
        client.post("/api/v1/auth/logout")
        email = f"{name}@districom.test"
        make_user(email=email, role=role)
        if module_role:
            grant(name, "commercial", module_role)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _sign_in


@pytest.fixture
def data(db: Session, make_user, grant) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """A master with a customer without zone (with its contact data), a seller without mapping, an unclassified
    zone; sales with an unknown product and an orphan customer code."""
    owner = make_user(email="owner@districom.test", role="viewer")
    grant("owner", "commercial", "operator")
    seed_config(db)
    db.refresh(owner)
    job = run(
        db,
        owner,
        customers_file(
            [
                customer(1, "ALMACEN UNO", zone=None, phone=PHONE, cuit=CUIT, address=ADDRESS, seller=SECRET_SELLER),
                customer(2, "KIOSCO DOS", zone="RUTA 03 - MISA"),
            ]
        ),
        CUSTOMERS,
        snapshot_date=date(2026, 10, 20),
    )
    sales = run(db, owner, sales_file([sale(9001, 998, "OTRO NOMBRE")]), SALES, scope="FULL")

    def one(code: str) -> DataQualityIssue:
        """The oldest issue of the code (the master opens one seller issue per unmapped seller)."""
        issue = db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == code).order_by(DataQualityIssue.id))
        return issue.first()  # type: ignore[return-value]

    return {"job": job.id, "sales_job": sales.id, "one": one}


def page(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get(f"{API}/issues", params={"module": "commercial", **params})
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_list_filters_order_and_pagination(data: dict[str, Any], sign_in) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer")
    everything = page(client, status="open", limit=100)
    codes = [i["code"] for i in everything["items"]]
    assert {"commercial.customer.without_zone", "commercial.seller.unmapped", "commercial.zone.unconfigured"} <= set(
        codes
    )
    assert {"commercial.product.unknown", "commercial.customer.unresolved_code"} <= set(codes)
    ranks = [{"error": 0, "warning": 1, "info": 2}[i["severity"]] for i in everything["items"]]
    assert ranks == sorted(ranks)  # errors, then warnings, then info

    assert [i["code"] for i in page(client, code="commercial.zone.unconfigured")["items"]] == [
        "commercial.zone.unconfigured"
    ]
    assert {i["group"]["key"] for i in page(client, group="customers")["items"]} == {"customers"}
    assert {i["source"]["job_id"] for i in page(client, job_id=data["sales_job"])["items"]} == {data["sales_job"]}
    assert all(i["severity"] == "info" for i in page(client, severity="info")["items"])
    assert {i["code"] for i in page(client, q="zona")["items"]} >= {"commercial.zone.unconfigured"}
    first, second = page(client, limit=2, offset=0), page(client, limit=2, offset=2)
    assert first["total"] == everything["total"]
    assert len(first["items"]) == 2
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}
    for bad in ({"status": "closed"}, {"severity": "fatal"}, {"code": "x.y"}, {"group": "nada"}, {"sort": "random"}):
        assert client.get(f"{API}/issues", params={"module": "commercial", **bad}).status_code == 422, bad


def test_summary_counts_issues_apart_from_occurrences(
    data: dict[str, Any],
    db: Session,
    sign_in,  # type: ignore[no-untyped-def]
) -> None:
    issue = data["one"]("commercial.customer.without_zone")
    db.execute(update(DataQualityIssue).where(DataQualityIssue.id == issue.id).values(occurrences=400))
    db.commit()
    client = sign_in("viewer")
    summary = client.get(f"{API}/issues/summary", params={"module": "commercial"}).json()
    rows = db.scalars(select(DataQualityIssue)).all()
    open_warnings = [r for r in rows if r.status == "open" and r.severity == "warning"]
    assert summary["open"]["warning"] == {
        "issues": len(open_warnings),
        "occurrences": sum(r.occurrences for r in open_warnings),
    }
    assert summary["open"]["warning"]["occurrences"] >= summary["open"]["warning"]["issues"] + 399
    assert summary["open_total"]["issues"] == sum(1 for r in rows if r.status == "open")
    assert summary["resolved"] == {"issues": 0, "occurrences": 0}
    grouped = client.get(f"{API}/issues/summary", params={"module": "commercial", "group": "zones"}).json()
    assert grouped["open_total"]["issues"] == 1


def test_detail_has_safe_links_and_no_pii(
    data: dict[str, Any],
    db: Session,
    sign_in,  # type: ignore[no-untyped-def]
) -> None:
    client = sign_in("viewer")
    seller = data["one"]("commercial.seller.unmapped")
    detail = client.get(f"{API}/issues/{seller.id}").json()
    assert detail["fix"] == "mapping"
    assert isinstance(detail["mapping_id"], int)
    assert detail["source"]["job_id"] == data["job"]
    assert detail["source"]["import_label"] == "STORE SYS · Clientes"
    assert detail["description"]
    assert detail["action"]
    assert detail["closes_when"]
    assert detail["dedupe_key"] is None  # technical reference only for whoever decides

    orphan = data["one"]("commercial.customer.unresolved_code")
    detail = client.get(f"{API}/issues/{orphan.id}").json()
    assert detail["subject"] == "Código STORE SYS 998"
    assert detail["mapping_id"] is None

    zone = client.get(f"{API}/issues/{data['one']('commercial.customer.without_zone').id}").json()
    assert zone["subject"] == "Cliente ALMACEN UNO"
    assert zone["fix"] is None
    text = str(client.get(f"{API}/issues", params={"module": "commercial", "limit": 100}).json()) + str(zone)
    for secret in (PHONE, CUIT, ADDRESS, "OTRO NOMBRE"):
        assert secret not in text, secret

    unknown = data["one"]("commercial.product.unknown")
    detail = client.get(f"{API}/issues/{unknown.id}").json()
    assert set(detail["facts"]) <= {"lines", "row_number", "codes", "mapping_status", "match"}


def test_details_never_leave_as_stored(data: dict[str, Any], db: Session, sign_in) -> None:  # type: ignore[no-untyped-def]
    """Even if a producer one day wrote something sensitive in `details` or `external_ref`, it does not reach the
    screen: only the allowed keys with simple values do."""
    issue_id = record_issue(
        db,
        "commercial.document.changed",
        "document:999",
        details={"phone": PHONE, "address": ADDRESS, "lines": 3, "codes": ["x"], "row_number": {"nested": CUIT}},
        external_ref={"source_system": "STORE_SYS", "namespace": "customer", "address": ADDRESS},
    )
    db.commit()
    detail = sign_in("viewer").get(f"{API}/issues/{issue_id}").json()
    assert detail["facts"] == {"lines": 3, "codes": ["x"]}
    for secret in (PHONE, ADDRESS, CUIT):
        assert secret not in str(detail)


def test_ignore_needs_a_note_and_an_operator(
    data: dict[str, Any],
    db: Session,
    sign_in,  # type: ignore[no-untyped-def]
) -> None:
    issue = data["one"]("commercial.customer.without_zone")
    viewer = sign_in("viewer")
    assert viewer.post(f"{API}/issues/{issue.id}/ignore", json={"note": "x"}).status_code == 403
    operator = sign_in("op", "operator")
    response = operator.post(f"{API}/issues/{issue.id}/ignore", json={"note": "   "})
    assert response.status_code == 422
    assert response.json()["code"] == "note_required"
    response = operator.post(f"{API}/issues/{issue.id}/ignore", json={"note": NOTE_WITH_A_NAME})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["status"], body["resolution_note"], body["resolved_by_name"]) == (
        "ignored",
        NOTE_WITH_A_NAME,
        "Usuario Test",
    )
    assert body["resolved_automatically"] is False
    [event] = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "platform.quality.ignored")).all()
    assert event.payload == {
        "issue_id": issue.id,
        "code": "commercial.customer.without_zone",
        "severity": "warning",
        "from_status": "open",
        "to_status": "ignored",
    }
    assert page(operator, status="ignored")["total"] == 1
    assert issue.id not in [i["id"] for i in page(operator, status="open", limit=100)["items"]]


def test_manual_resolve_is_for_admins(data: dict[str, Any], db: Session, sign_in) -> None:  # type: ignore[no-untyped-def]
    issue = data["one"]("commercial.zone.unconfigured")
    assert sign_in("viewer").post(f"{API}/issues/{issue.id}/resolve", json={"note": "ok"}).status_code == 403
    assert sign_in("op", "operator").post(f"{API}/issues/{issue.id}/resolve", json={"note": "ok"}).status_code == 403
    admin = sign_in("boss", None, role="admin")
    assert admin.post(f"{API}/issues/{issue.id}/resolve", json={"note": ""}).status_code == 422
    response = admin.post(f"{API}/issues/{issue.id}/resolve", json={"note": NOTE_WITH_A_NAME})
    assert response.status_code == 200
    assert response.json()["status"] == "resolved"
    assert response.json()["resolved_automatically"] is False
    [event] = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "platform.quality.resolved")).all()
    assert "Juana" not in str(event.payload)
    assert "dedupe" not in str(event.payload)


def test_a_stale_decision_is_refused_with_the_current_status(
    data: dict[str, Any],
    db: Session,
    sign_in,  # type: ignore[no-untyped-def]
) -> None:
    issue = data["one"]("commercial.customer.without_zone")
    admin = sign_in("boss", None, role="admin")
    assert admin.post(f"{API}/issues/{issue.id}/ignore", json={"note": "Primero"}).status_code == 200
    second = admin.post(f"{API}/issues/{issue.id}/resolve", json={"note": "Segundo"})
    assert second.status_code == 409
    assert second.json()["details"] == {"issue_id": issue.id, "status": "ignored"}
    db.expire_all()
    stored = db.get_one(DataQualityIssue, issue.id)
    assert (stored.status, stored.resolution_note) == ("ignored", "Primero")  # nothing overwritten


def test_module_access(data: dict[str, Any], sign_in) -> None:  # type: ignore[no-untyped-def]
    issue = data["one"]("commercial.customer.without_zone")
    outsider = sign_in("outsider", None)
    assert outsider.get(f"{API}/issues", params={"module": "commercial"}).status_code == 403
    assert outsider.get(f"{API}/issues/{issue.id}").status_code == 404
    assert outsider.get(f"{API}/issues", params={"module": "nada"}).status_code == 422


def test_codes_carry_their_presentation(sign_in) -> None:  # type: ignore[no-untyped-def]
    codes = sign_in("viewer").get(f"{API}/codes", params={"module": "commercial"}).json()
    by_code = {c["code"]: c for c in codes}
    assert by_code["commercial.seller.unmapped"]["fix"] == "mapping"
    assert by_code["commercial.zone.unconfigured"]["fix"] is None
    assert "Maestros" in by_code["commercial.zone.unconfigured"]["action"]
    assert "platform.import.invalid_row" in by_code
    assert "settlements" not in str(codes)


def test_logs_carry_no_pii(data: dict[str, Any], sign_in, caplog: pytest.LogCaptureFixture) -> None:  # type: ignore[no-untyped-def]
    caplog.set_level("DEBUG")
    client = sign_in("boss", None, role="admin")
    for item in page(client, limit=100)["items"]:
        client.get(f"{API}/issues/{item['id']}")
    issue = data["one"]("commercial.customer.without_zone")
    client.post(f"{API}/issues/{issue.id}/ignore", json={"note": NOTE_WITH_A_NAME})
    logged = " ".join(r.getMessage() + " " + str(r.__dict__) for r in caplog.records)
    for secret in (PHONE, CUIT, ADDRESS, SECRET_SELLER, "Juana", "customer_code:998"):
        assert secret not in logged, secret
