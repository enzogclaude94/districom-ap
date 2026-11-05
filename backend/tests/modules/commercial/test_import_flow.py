"""F1.3: the STORE SYS imports end to end through the generic API: customers → products → documents → sales, with
the declared snapshot date and scope, an admin's decision on a blocked confirmation, and a coherent final model."""

import json
from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (composition root: installs the modules)
from app.modules.commercial.assignments.service import zone_on
from app.modules.commercial.catalog.service import SourceProduct, upsert_from_source
from app.modules.commercial.config.models import PriceList, Zone
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.documents.models import Document
from app.modules.commercial.sales.models import SaleLine
from app.platform.auth.models import User
from app.platform.customers.models import Customer
from app.platform.imports.models import ImportJob
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from tests.conftest import PASSWORD
from tests.imports.jobs import make_job
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    DOCUMENTS,
    SALES,
    customer,
    customers_file,
    document,
    documents_file,
    sale,
    sales_file,
)

XLS = "application/vnd.ms-excel"
D23 = date(2026, 10, 23)


@pytest.fixture
def sign_in(client: TestClient, make_user):  # type: ignore[no-untyped-def]
    """Log in as `<name>@districom.test`; the users exist from the start (operator: viewer + commercial operator)."""
    make_user(email="operator@districom.test", role="viewer")
    make_user(email="admin@districom.test", role="admin")

    def _sign_in(name: str) -> TestClient:
        client.post("/api/v1/auth/logout")
        response = client.post("/api/v1/auth/login", json={"email": f"{name}@districom.test", "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _sign_in


def post(client: TestClient, data: bytes, **form: str) -> dict:  # type: ignore[type-arg]
    response = client.post("/api/v1/imports", files={"file": ("export.xls", data, XLS)}, data=form)
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["status"] == "pending", (job["failure_reason"], job["failure_detail"])
    return job  # type: ignore[no-any-return]


def confirm(client: TestClient, job_id: int, **body: str) -> dict:  # type: ignore[type-arg]
    response = client.post(f"/api/v1/imports/{job_id}/confirm", json=body or None)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_customers_products_documents_and_sales_end_to_end(
    db: Session,
    sign_in,
    grant,
    make_user,  # type: ignore[no-untyped-def]
) -> None:
    operator = sign_in("operator")
    grant("operator", "commercial", "operator")
    seed_config(db)
    types = {t["key"] for t in operator.get("/api/v1/imports/types").json()}
    assert {CUSTOMERS, DOCUMENTS, SALES} <= types
    assert "commercial.store_sys.products" not in types  # blocked until the real articles master

    # 1. Customers, as a snapshot of the 20th.
    job = post(
        operator,
        customers_file(
            [customer(1, "ALMACEN UNO"), customer(2, "KIOSCO DOS", zone="RUTA 02 - MAJU", seller="VENDEDOR DOS")]
        ),
        snapshot_date="2026-10-20",
    )
    assert (job["import_type"], job["snapshot_date"]) == (CUSTOMERS, "2026-10-20")
    assert job["metadata"]["declared"]["snapshot_date"] == "2026-10-20"
    assert not job["can_decide"]  # an operator does not decide blocked confirmations
    assert confirm(operator, job["id"])["counts"]["inserted"] == 2

    # 2. Products: the articles master is blocked, so the catalog gets them as a master import would.
    importer = db.scalars(select(User).where(User.email == "operator@districom.test")).one()
    job_id = make_job(db, importer.id)
    for code in ("100", "200"):
        upsert_from_source(db, SourceProduct(code=code, description=f"ARTICULO {code}"), job_id=job_id)
    db.commit()

    # 3. Documents of the 23rd.
    job = post(
        operator,
        documents_file(
            [
                document("FACTURA A 0003-00000001", code=1, day=D23),
                document("RETIRO PR.  0001-00000002", code=0, name=None, net=0, total=0, day=D23),
            ]
        ),
    )
    assert job["import_type"] == DOCUMENTS
    assert confirm(operator, job["id"])["counts"]["inserted"] == 2

    # 4. Sales of the 23rd, FULL, every price list.
    lines = [sale(100, "ALMACEN UNO", day=D23, price_list=f"LISTA {n}", quantity=n) for n in range(1, 6)] + [
        sale(200, "KIOSCO DOS", day=D23, seller="VENDEDOR DOS", zone="RUTA 02 - MAJU")
    ]
    job = post(operator, sales_file(lines), scope="FULL")
    assert (job["import_type"], job["scope"]["declared"], job["period_from"]) == (SALES, "FULL", "2026-10-23")
    assert confirm(operator, job["id"])["counts"]["inserted"] == 6

    # 5. A FILTERED export of the same day: blocked for the operator, decided by an admin.
    job = post(operator, sales_file(lines[:2]), scope="FILTERED", scope_note="Solo lista 1 y 2")
    assert job["metadata"]["preview"]["replacement"]["action"] == "blocked"
    blocked = operator.post(f"/api/v1/imports/{job['id']}/confirm")
    assert (blocked.status_code, blocked.json()["code"]) == (409, "import_decision_required")
    assert blocked.json()["details"]["reasons"] == ["new_scope_not_full"]
    refused = operator.post(f"/api/v1/imports/{job['id']}/confirm", json={"decision": "additional", "reason": "x" * 10})
    assert refused.status_code == 403
    admin = sign_in("admin")
    detail = admin.get(f"/api/v1/imports/{job['id']}").json()
    assert detail["can_decide"]
    decided = confirm(admin, job["id"], decision="additional", reason="Ventas cargadas después del cierre")
    assert decided["status"] in ("completed", "completed_with_warnings")

    # The final model: one customer per code, documents and lines linked where the identity is exact.
    assert db.scalar(select(func.count()).select_from(Customer)) == 2
    uno = mappings.resolve(db, mappings.MappingKey("customer", "STORE_SYS", "customer", "1")).entity_id
    factura = db.scalars(select(Document).where(Document.number == "00000001")).one()
    assert factura.customer_id == uno
    assert db.scalar(select(func.count()).select_from(SaleLine).where(SaleLine.status == "ACTIVE")) == 8
    lists = dict(db.execute(select(PriceList.list_number, PriceList.id)).all())
    sold = db.scalars(select(SaleLine).where(SaleLine.import_job_id == job["id"] - 1).order_by(SaleLine.id)).all()
    assert [line.price_list_id for line in sold[:5]] == [lists[n] for n in range(1, 6)]
    assert [line.quantity for line in sold[:5]] == [Decimal(n) for n in range(1, 6)]
    assert {line.customer_id for line in sold} == {None}  # by name: proposed, never auto-confirmed
    proposal = db.scalars(
        select(ExternalEntityMapping).where(
            ExternalEntityMapping.namespace == "customer_name", ExternalEntityMapping.external_id == "ALMACEN UNO"
        )
    ).one()
    assert (proposal.status, proposal.candidates) == ("pending", [uno])
    # Attribution on the day of the sale comes from the assignment history, not the line.
    assert uno is not None
    luvi = db.scalars(select(Zone).where(Zone.source_name == "RUTA 01 - LUVI")).one()
    assert zone_on(db, uno, D23) == luvi.id
    assert zone_on(db, uno, date(2026, 10, 19)) is None


def test_cli_declares_snapshot_date_and_scope_and_an_admin_decides(  # type: ignore[no-untyped-def]
    db: Session, make_user, grant, tmp_path, capsys
) -> None:
    from app.cli import main as cli

    make_user(email="operator@districom.test", role="viewer")
    make_user(email="admin@districom.test", role="admin")
    grant("operator", "commercial", "operator")
    seed_config(db)
    clients = tmp_path / "clientes.xls"
    clients.write_bytes(customers_file([customer(1, "ALMACEN UNO")]))
    ventas = tmp_path / "ventas.xls"
    ventas.write_bytes(sales_file([sale(100, "ALMACEN UNO", day=D23)]))
    otra = tmp_path / "ventas-filtradas.xls"
    otra.write_bytes(sales_file([sale(100, "ALMACEN UNO", day=D23, quantity=2)]))
    operator, admin = "operator@districom.test", "admin@districom.test"

    def last() -> dict:  # type: ignore[type-arg]
        return json.loads(capsys.readouterr().out.strip().splitlines()[-1])  # type: ignore[no-any-return]

    assert (
        cli(["imports", "analyze", "--file", str(clients), "--email", operator, "--snapshot-date", "2026-10-20"]) == 0
    )
    job = last()
    assert cli(["imports", "confirm", "--job", str(job["job_id"]), "--email", operator]) == 0
    assert last()["status"] in ("completed", "completed_with_warnings")
    assert db.scalar(select(func.max(ImportJob.snapshot_date))) == date(2026, 10, 20)

    assert cli(["imports", "analyze", "--file", str(ventas), "--email", operator, "--scope", "FULL"]) == 0
    assert cli(["imports", "confirm", "--job", str(last()["job_id"]), "--email", operator]) == 0
    capsys.readouterr()
    assert cli(["imports", "analyze", "--file", str(otra), "--email", operator, "--scope", "FILTERED"]) == 0
    blocked = last()["job_id"]
    assert cli(["imports", "confirm", "--job", str(blocked), "--email", operator]) == 1  # needs an admin decision
    args = ["imports", "confirm", "--job", str(blocked), "--email", admin, "--decision", "replace"]
    assert cli([*args, "--reason", "El export filtrado cubre el día"]) == 0
    assert db.scalar(select(func.count()).select_from(SaleLine).where(SaleLine.status == "ACTIVE")) == 1
