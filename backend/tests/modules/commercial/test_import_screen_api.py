"""F1.3b: what the Imports screen asks of the API, over HTTP with the real STORE SYS types. The summary of what the
analysis found (messages grouped by type, the sales preview's counts), the rows behind each message, the roles of
each step, a second confirmation and a file of the real export's size."""

import io
import time
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types and quality codes)
from app.modules.commercial.catalog.service import SourceProduct, upsert_from_source
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.sales.models import SaleLine
from app.platform.auth.models import User
from tests.conftest import PASSWORD
from tests.imports.jobs import make_job
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    SALES_HEADER,
    customer,
    customers_file,
    run,
    sale,
    sales_file,
)

XLS = "application/vnd.ms-excel"
D20, D21, D22 = (date(2026, 10, d) for d in (20, 21, 22))


@pytest.fixture
def masters(db: Session, make_user, grant) -> User:  # type: ignore[no-untyped-def]
    """Customers 1 to 3 and products 100 and 200, loaded by a commercial admin."""
    user = make_user(email="admin@districom.test", role="viewer")
    grant("admin", "commercial", "admin")
    seed_config(db)
    db.refresh(user)
    run(db, user, customers_file([customer(n, f"CLIENTE {n}") for n in (1, 2, 3)]), CUSTOMERS, snapshot_date=D20)
    job_id = make_job(db, user.id)
    for code in ("100", "200"):
        upsert_from_source(db, SourceProduct(code=code, description=f"ARTICULO {code}"), job_id=job_id)
    db.commit()
    return user  # type: ignore[no-any-return]


@pytest.fixture
def sign_in(client: TestClient, make_user, grant):  # type: ignore[no-untyped-def]
    def _sign_in(name: str, module_role: str | None) -> TestClient:
        client.post("/api/v1/auth/logout")
        email = f"{name}@districom.test"
        if name != "admin":
            make_user(email=email, role="viewer")
            if module_role:
                grant(name, "commercial", module_role)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _sign_in


def upload(client: TestClient, data: bytes, **form: str) -> dict:  # type: ignore[type-arg]
    response = client.post("/api/v1/imports", files={"file": ("ventas.xls", data, XLS)}, data=form)
    assert response.status_code == 201, response.text
    return response.json()  # type: ignore[no-any-return]


def three_days() -> bytes:
    return sales_file(
        [sale(100, 1, day=day) for day in (D20, D21, D22)]
        + [sale(999, 1, day=D22), sale(100, 77, day=D22), sale(200, 2, day=D22, price_list="LISTA 9")]
    )


def test_the_summary_says_what_needs_attention_without_row_values(masters: User, sign_in) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("operator", "operator")
    job = upload(client, three_days(), scope="FULL")
    assert job["status"] == "pending"
    preview = job["metadata"]["preview"]
    assert preview["products"] == {"unknown": 1, "unknown_lines": 1}
    assert preview["customers"] == {"unresolved": 1, "unresolved_lines": 1}
    assert preview["sellers"]["unmapped"] == ["VENDEDOR UNO"]
    assert preview["sellers"]["unmapped_lines"] == 6
    assert preview["price_lists"] == {"unknown": ["LISTA 9"], "unknown_lines": 1}
    assert preview["replacement"]["action"] == "insert"

    messages = client.get(f"/api/v1/imports/{job['id']}/messages").json()
    by_code = {m["code"]: m for m in messages}
    assert by_code["product_unknown"] == {
        "code": "product_unknown",
        "severity": "warning",
        "field": "product_code",
        "message": "El artículo no está en el maestro de artículos.",
        "rows": 1,
    }
    assert by_code["customer_unresolved"]["rows"] == 1
    assert by_code["unknown_price_list"]["rows"] == 1
    assert [m["severity"] for m in messages] == sorted(
        (m["severity"] for m in messages), key=["error", "warning", "info"].index
    )
    assert "CLIENTE" not in str(messages)  # adapter texts only, never a name from the file

    rows = client.get(f"/api/v1/imports/{job['id']}/rows", params={"code": "customer_unresolved"}).json()
    assert rows["total"] == 1
    assert rows["items"][0]["status"] == "pending_resolution"
    assert client.get(f"/api/v1/imports/{job['id']}/rows", params={"code": "nope"}).json()["total"] == 0


def test_each_step_belongs_to_its_role(masters: User, sign_in) -> None:  # type: ignore[no-untyped-def]
    viewer = sign_in("viewer", "viewer")
    refused = viewer.post("/api/v1/imports", files={"file": ("ventas.xls", three_days(), XLS)}, data={"scope": "FULL"})
    assert refused.status_code == 403

    operator = sign_in("operator", "operator")
    first = upload(operator, three_days(), scope="FULL")
    assert (first["can_confirm"], first["can_decide"]) == (True, False)
    assert operator.post(f"/api/v1/imports/{first['id']}/confirm").status_code == 200

    blocked = upload(operator, sales_file([sale(100, 1, day=D21)]), scope="FILTERED", scope_note="solo un vendedor")
    assert blocked["metadata"]["preview"]["replacement"]["action"] == "blocked"
    response = operator.post(f"/api/v1/imports/{blocked['id']}/confirm")
    assert response.status_code == 409
    assert response.json()["code"] == "import_decision_required"
    assert (
        operator.post(
            f"/api/v1/imports/{blocked['id']}/confirm", json={"decision": "replace", "reason": "export completo"}
        ).status_code
        == 403
    )

    viewer = sign_in("reader", "viewer")
    assert viewer.get(f"/api/v1/imports/{blocked['id']}").json()["can_confirm"] is False
    assert viewer.post(f"/api/v1/imports/{blocked['id']}/cancel").status_code == 403

    admin = sign_in("admin", None)
    detail = admin.get(f"/api/v1/imports/{blocked['id']}").json()
    assert (detail["status"], detail["can_decide"]) == ("pending", True)
    no_reason = admin.post(f"/api/v1/imports/{blocked['id']}/confirm", json={"decision": "replace", "reason": " "})
    assert no_reason.status_code == 422
    done = admin.post(
        f"/api/v1/imports/{blocked['id']}/confirm", json={"decision": "additional", "reason": "días distintos"}
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] in ("completed", "completed_with_warnings")


def test_a_second_confirmation_is_refused_and_writes_nothing(masters: User, sign_in, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("operator", "operator")
    job = upload(client, three_days(), scope="FULL")
    assert client.post(f"/api/v1/imports/{job['id']}/confirm").status_code == 200
    again = client.post(f"/api/v1/imports/{job['id']}/confirm")
    assert again.status_code == 409
    assert again.json()["code"] == "import_job_not_pending"
    assert db.scalar(select(func.count()).select_from(SaleLine)) == 6
    cancelled_after = client.post(f"/api/v1/imports/{job['id']}/cancel")
    assert cancelled_after.status_code == 409


def large_sales_file(lines: int) -> bytes:
    """An XLSX with the real export's columns and size (the BIFF7 test writer tops out at one FAT sector)."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "ventas"
    sheet.append(SALES_HEADER)
    days = [D20 - timedelta(days=d) for d in range(27)]
    for n in range(lines):
        sheet.append(sale(100 + 100 * (n % 2), 1 + n % 3, day=days[n % 27], quantity=1 + n % 5))
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def test_a_file_the_size_of_the_real_export_confirms_in_one_request(masters: User, sign_in, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("operator", "operator")
    started = time.monotonic()
    response = client.post(
        "/api/v1/imports",
        files={"file": ("ventas.xlsx", large_sales_file(7500), "application/octet-stream")},
        data={"scope": "FULL"},
    )
    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["status"], job["counts"]["detected"]) == ("pending", 7501)
    confirmed = client.post(f"/api/v1/imports/{job['id']}/confirm")
    elapsed = time.monotonic() - started
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["counts"]["inserted"] == 7500
    assert db.scalar(select(func.count()).select_from(SaleLine)) == 7500
    # Before F1.3b every row's outcome was its own UPDATE and this took minutes. Generous bound: it guards against
    # the quadratic write coming back, not against a slow CI runner.
    assert elapsed < 60, elapsed
