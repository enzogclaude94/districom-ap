"""F1.1b: the generic import API and CLI (docs/IMPORTS.md §8) on the synthetic type."""

import json
from datetime import date
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (composition root: installs the modules)
from app.cli import main as cli
from app.core.config import get_settings
from tests.conftest import PASSWORD
from tests.imports.adapter import OTHER_TYPE_KEY, TYPE_KEY
from tests.imports.files import biff7_xls

pytestmark = pytest.mark.usefixtures("synthetic_types")

REPORT = biff7_xls(
    [
        ["codigo", "nombre", "cantidad", "fecha"],
        [1001, "Almacén", 6, date(2026, 10, 23)],
        [None, "sin código", 1, None],
    ]
)
XLS = "application/vnd.ms-excel"


def post_file(client: TestClient, data: bytes, name: str = "items.xls", import_type: str | None = None):  # type: ignore[no-untyped-def]
    form = {"import_type": import_type} if import_type else {}
    return client.post("/api/v1/imports", files={"file": (name, data, XLS)}, data=form)


@pytest.fixture
def sign_in(client: TestClient, make_user):  # type: ignore[no-untyped-def]
    """Log in as `<name>@districom.test`, a platform viewer (or admin) whose module roles the test grants."""

    def _sign_in(name: str, role: str = "viewer") -> TestClient:
        client.post("/api/v1/auth/logout")
        email = f"{name}@districom.test"
        make_user(email=email, role=role)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _sign_in


@pytest.fixture
def operator(sign_in, grant) -> TestClient:  # type: ignore[no-untyped-def]
    client = sign_in("operator")
    grant("operator", "commercial", "operator")
    return client  # type: ignore[no-any-return]


def test_upload_preview_rows_issues_and_confirm(operator: TestClient, db: Session) -> None:
    types = operator.get("/api/v1/imports/types").json()
    assert {(t["key"], t["can_import"]) for t in types} >= {(TYPE_KEY, True), (OTHER_TYPE_KEY, True)}

    response = post_file(operator, REPORT)
    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["status"], job["import_type"], job["source_system"]) == ("pending", TYPE_KEY, "STORE_SYS")
    assert job["file"]["detected_format"] == "XLS"
    assert job["counts"]["rejected"] == 1
    assert job["can_confirm"]
    assert not job["can_reprocess"]

    rows = operator.get(f"/api/v1/imports/{job['id']}/rows", params={"status": "rejected"}).json()
    assert rows["total"] == 1
    assert rows["items"][0]["messages"][0]["code"] == "missing_code"
    assert operator.get(f"/api/v1/imports/{job['id']}/rows", params={"status": "nope"}).status_code == 422

    confirmed = operator.post(f"/api/v1/imports/{job['id']}/confirm")
    assert confirmed.status_code == 200, confirmed.text
    assert (confirmed.json()["status"], confirmed.json()["counts"]["inserted"]) == ("completed_with_warnings", 1)
    issues = operator.get(f"/api/v1/imports/{job['id']}/issues").json()
    assert [i["code"] for i in issues] == ["platform.import.invalid_row"]
    assert db.scalar(text("SELECT count(*) FROM test_import_items")) == 1

    again = post_file(operator, REPORT, "copia.xls").json()
    assert (again["status"], again["duplicate_of_job_id"]) == ("duplicate", job["id"])
    assert operator.post(f"/api/v1/imports/{again['id']}/confirm").json()["code"] == "import_job_not_pending"

    listed = operator.get("/api/v1/imports", params={"status": "duplicate"}).json()
    assert [i["id"] for i in listed["items"]] == [again["id"]]


def test_cancel_and_reprocess_permissions(operator: TestClient, sign_in) -> None:  # type: ignore[no-untyped-def]
    client = operator
    job = post_file(client, REPORT).json()
    assert client.post(f"/api/v1/imports/{job['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/v1/imports/{job['id']}/reprocess").status_code == 403

    sign_in("admin", role="admin")
    response = client.post(f"/api/v1/imports/{job['id']}/reprocess")
    assert response.status_code == 201, response.text
    assert (response.json()["status"], response.json()["reprocess_of_job_id"]) == ("pending", job["id"])


def test_modules_without_access_see_nothing(operator: TestClient, sign_in, grant) -> None:  # type: ignore[no-untyped-def]
    job = post_file(operator, REPORT).json()
    client = sign_in("viewer")
    grant("viewer", "settlements", "admin")
    assert client.get("/api/v1/imports/types").json() == []
    assert client.get("/api/v1/imports").json() == {"items": [], "total": 0}
    assert client.get(f"/api/v1/imports/{job['id']}").status_code == 404
    assert post_file(client, REPORT).status_code == 403
    assert post_file(client, REPORT, import_type=TYPE_KEY).status_code == 404

    grant("viewer", "commercial", "viewer")
    assert client.get(f"/api/v1/imports/{job['id']}").json()["can_confirm"] is False
    assert client.post(f"/api/v1/imports/{job['id']}/confirm").status_code == 403
    assert post_file(client, REPORT, import_type=TYPE_KEY).status_code == 403


def test_extension_size_and_failed_uploads(operator: TestClient) -> None:
    from app.main import app

    response = post_file(operator, b"codigo\n1\n", "items.txt")
    assert (response.status_code, response.json()["code"]) == (422, "invalid_extension")

    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(update={"upload_max_size_mb": 1})
    try:
        response = post_file(operator, b"x" * (1024 * 1024 + 1), "items.csv")
    finally:
        app.dependency_overrides.pop(get_settings)
    assert (response.status_code, response.json()["code"]) == (413, "request_too_large")

    failed = post_file(operator, b"<html><table></table></html>", "ventas.xls")
    assert failed.status_code == 201
    assert (failed.json()["status"], failed.json()["failure_reason"]) == ("failed", "unsupported_format")


def test_requires_login(client: TestClient) -> None:
    assert client.get("/api/v1/imports").status_code == 401
    assert post_file(client, REPORT).status_code == 401


# --- CLI ----------------------------------------------------------------------------------------------------------


def summary(capsys) -> dict:  # type: ignore[no-untyped-def,type-arg]
    """The command's JSON summary: its last stdout line (structured logs go to stdout too)."""
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # type: ignore[no-any-return]


@pytest.fixture
def report_file(tmp_path: Path) -> Path:
    path = tmp_path / "items.xls"
    path.write_bytes(REPORT)
    return path


def test_cli_analyze_confirm_reprocess_recover(make_user, grant, report_file: Path, capsys, db: Session) -> None:  # type: ignore[no-untyped-def]
    make_user(email="operator@districom.test", role="viewer")
    grant("operator", "commercial", "operator")
    email = "operator@districom.test"

    assert cli(["imports", "analyze", "--file", str(report_file), "--email", email]) == 0
    analyzed = summary(capsys)
    assert (analyzed["status"], analyzed["import_type"]) == ("pending", TYPE_KEY)
    assert "Almacén" not in json.dumps(analyzed)  # a summary of counts, never row values

    assert cli(["imports", "confirm", "--job", str(analyzed["job_id"]), "--email", email]) == 0
    assert summary(capsys)["status"] == "completed_with_warnings"
    assert cli(["imports", "reprocess", "--job", str(analyzed["job_id"]), "--email", email]) == 1  # admin only
    capsys.readouterr()

    assert cli(["imports", "recover"]) == 0
    assert summary(capsys) == {"recovered_job_ids": []}

    bad = report_file.with_name("roto.csv")
    bad.write_bytes(b"\x89PNG\x00")
    assert cli(["imports", "analyze", "--file", str(bad), "--email", email]) == 2
    assert summary(capsys)["failure_reason"] == "unsupported_format"
    assert cli(["imports", "analyze", "--file", str(bad), "--email", "nadie@districom.test"]) == 1
