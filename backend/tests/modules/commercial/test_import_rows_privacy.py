"""F1.3b: least privilege on the file's values. A commercial `viewer` reads a job's rows (state, key, messages) to
follow an import, but the raw cells and normalized values, which carry names, CUIT, phone numbers and addresses,
reach only an `operator` or `admin`: the keys are absent from the viewer's response, not empty. Neither the logs nor
the audit trail carry those values for anyone."""

import logging
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from tests.modules.commercial.store_sys import customer, customers_file
from tests.modules.commercial.test_import_screen_api import XLS, masters, sign_in  # noqa: F401  (fixtures)

NAME, CUIT, PHONE, ADDRESS = "ALMACEN PRIVADO SRL", "20-12345678-6", "351 555 0101", "AV SIEMPRE VIVA 742"
SENSITIVE = (NAME, CUIT, PHONE, ADDRESS)
ROW_KEYS = {"id", "sheet", "row_number", "status", "outcome", "business_key", "entity_type", "entity_id", "messages"}


def private_customers() -> bytes:
    rows = [customer(40 + n, f"{NAME} {n}", cuit=CUIT, phone=PHONE, address=ADDRESS) for n in range(5)]
    rows.append(customer(50, f"{NAME} SIN ZONA", zone=None, cuit=CUIT, phone=PHONE, address=ADDRESS))
    return customers_file(rows)


@pytest.fixture
def job(masters: User, sign_in) -> int:  # type: ignore[no-untyped-def]  # noqa: F811
    operator = sign_in("operator", "operator")
    response = operator.post(
        "/api/v1/imports",
        files={"file": ("clientes.xls", private_customers(), XLS)},
        data={"snapshot_date": date(2026, 10, 25).isoformat()},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]  # type: ignore[no-any-return]


def rows(client: TestClient, job_id: int, **params: object) -> dict:  # type: ignore[type-arg]
    response = client.get(f"/api/v1/imports/{job_id}/rows", params=params)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_a_viewer_gets_no_raw_or_normalized_keys(job: int, sign_in) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    viewer = sign_in("viewer", "viewer")
    page = rows(viewer, job)
    assert page["total"] == 7  # header + 6 customers
    for item in page["items"]:
        assert set(item) == ROW_KEYS
    for value in SENSITIVE:
        assert value not in str(page)


@pytest.mark.parametrize("role", ["operator", "admin"])
def test_operators_and_admins_get_the_raw_and_normalized_values(job: int, sign_in, role: str) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    client = sign_in("admin", None) if role == "admin" else sign_in("diagnose", "operator")
    page = rows(client, job, status="pending_resolution")
    assert page["items"]
    for item in page["items"]:
        assert set(item) == ROW_KEYS | {"raw", "normalized"}
    assert any(CUIT in str(item["raw"]) for item in page["items"])
    assert any(item["normalized"] for item in page["items"])


def test_a_viewer_still_filters_pages_and_reads_the_messages(job: int, sign_in) -> None:  # type: ignore[no-untyped-def]  # noqa: F811
    viewer = sign_in("viewer", "viewer")
    messages = viewer.get(f"/api/v1/imports/{job}/messages").json()
    assert messages, "the file has a customer without zone"
    code = messages[0]["code"]
    by_code = rows(viewer, job, code=code)
    assert by_code["total"] == messages[0]["rows"]
    assert all(any(m["code"] == code for m in item["messages"]) for item in by_code["items"])
    assert all(set(item) == ROW_KEYS for item in by_code["items"])

    warned = rows(viewer, job, status="pending_resolution")
    assert warned["total"] == 6
    assert all(item["status"] == "pending_resolution" for item in warned["items"])
    assert rows(viewer, job, status="rejected")["total"] == 0
    assert rows(viewer, job, status="skipped")["total"] == 1  # the header

    first, second = rows(viewer, job, limit=3, offset=0), rows(viewer, job, limit=3, offset=3)
    assert first["total"] == second["total"] == 7
    assert len(first["items"]) == 3
    assert not {i["id"] for i in first["items"]} & {i["id"] for i in second["items"]}

    history = viewer.get("/api/v1/imports", params={"module": "commercial"}).json()["items"]
    entry = next(item for item in history if item["id"] == job)
    assert (entry["rows_detected"], entry["rows_processed"]) == (7, 6)  # the header is detected, not data


def test_logs_and_audit_never_carry_the_files_values(  # type: ignore[no-untyped-def]
    masters: User,  # noqa: F811
    sign_in,  # noqa: F811
    db: Session,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    operator = sign_in("operator", "operator")
    uploaded = operator.post(
        "/api/v1/imports",
        files={"file": ("clientes.xls", private_customers(), XLS)},
        data={"snapshot_date": date(2026, 10, 25).isoformat()},
    ).json()
    rows(operator, uploaded["id"])
    confirmed = operator.post(f"/api/v1/imports/{uploaded['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    rows(sign_in("viewer", "viewer"), uploaded["id"])

    logged = caplog.text + " ".join(str(record.__dict__) for record in caplog.records)
    audited = " ".join(str(event.payload) for event in db.scalars(select(AuditEvent)))
    assert audited, "the import writes audit events"
    for value in SENSITIVE:
        assert value not in logged
        assert value not in audited
