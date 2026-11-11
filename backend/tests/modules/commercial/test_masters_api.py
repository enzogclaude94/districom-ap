"""F1.2: the masters API under /api/v1/commercial and its permissions (viewer reads, operator decides open
mappings, module admin configures), plus `commercial seed-config`."""

import json
from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.cli import main as cli
from app.modules.commercial.catalog import service as catalog
from app.modules.commercial.catalog.service import SourceProduct
from app.modules.commercial.config import service as config
from app.modules.commercial.config.seed import seed_config
from app.platform.customers import service as customers
from app.platform.mappings import service as mappings
from app.platform.mappings.service import MappingKey
from tests.conftest import PASSWORD
from tests.imports.jobs import make_job


@pytest.fixture
def sign_in(client: TestClient, make_user):  # type: ignore[no-untyped-def]
    def _sign_in(name: str, role: str = "viewer") -> TestClient:
        client.post("/api/v1/auth/logout")
        email = f"{name}@districom.test"
        make_user(email=email, role=role)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _sign_in


@pytest.fixture
def masters(db: Session, make_user) -> dict[str, int]:  # type: ignore[no-untyped-def]
    seed_config(db)
    owner = make_user(email="owner@districom.test", role="admin")
    job = make_job(db, owner.id)
    rosa = customers.create_from_master(
        db,
        customers.normalize(customers.MasterRecord(display_name="Almacén Doña Rosa")),
        job_id=job,
        seen_on=date.today(),
    )
    mapping = mappings.observe(
        db, MappingKey("customer", "STORE_SYS", "customer_name", "ROSA"), label="Rosa", job_id=job, candidates=[rosa.id]
    )
    misa, _ = config.ensure_zone(db, "RUTA 03 - MISA", effective_from=date.today(), job_id=job, user_id=None)
    product, _ = catalog.upsert_from_source(db, SourceProduct(code="1001", category="CERVEZAS"), job_id=job)
    db.commit()
    return {"customer": rosa.id, "mapping": mapping.id, "zone": misa.id, "product": product.id}


def test_viewer_reads_everything_and_changes_nothing(sign_in, grant, masters: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer")
    grant("viewer", "commercial", "viewer")
    for path in (
        "/customers",
        f"/customers/{masters['customer']}",
        "/mappings?status=pending",
        f"/mappings/{masters['mapping']}",
        "/zones",
        f"/zones/{masters['zone']}",
        "/zone-rules",
        "/price-lists",
        "/product-categories",
        "/document-types",
        "/no-purchase-reason-categories",
        "/no-purchase-reasons",
        "/products?without_category=true",
        f"/products/{masters['product']}",
        "/product-families",
        "/source-category-mappings",
        "/source-category-mappings/unmapped",
    ):
        response = client.get(f"/api/v1/commercial{path}")
        assert response.status_code == 200, (path, response.text)
    assert client.get("/api/v1/commercial/mappings?status=pending").json()["total"] == 1
    assert len(client.get("/api/v1/commercial/price-lists").json()) == 5
    denied = (
        ("post", f"/mappings/{masters['mapping']}/confirm", {"entity_id": masters["customer"]}),
        ("patch", f"/customers/{masters['customer']}", {"active": False, "reason": "x"}),
        ("put", f"/zones/{masters['zone']}/configuration", {}),
        ("post", "/price-lists", {}),
        ("patch", f"/products/{masters['product']}", {"reason": "x"}),
    )
    for method, path, body in denied:
        response = getattr(client, method)(f"/api/v1/commercial{path}", json=body)
        assert response.status_code == 403, (path, response.text)


def test_without_the_module_nothing_and_without_login_401(client: TestClient, sign_in, masters) -> None:  # type: ignore[no-untyped-def]
    assert client.get("/api/v1/commercial/customers").status_code == 401
    outsider = sign_in("outsider")
    assert outsider.get("/api/v1/commercial/customers").status_code == 403
    assert outsider.get("/api/v1/commercial/zones").status_code == 403


def test_operator_decides_open_mappings_and_admin_the_rest(sign_in, grant, masters: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("operator")
    grant("operator", "commercial", "operator")
    base = f"/api/v1/commercial/mappings/{masters['mapping']}"
    confirmed = client.post(f"{base}/confirm", json={"entity_id": masters["customer"]})
    assert confirmed.status_code == 200, confirmed.text
    assert (confirmed.json()["status"], confirmed.json()["method"], confirmed.json()["source_system"]) == (
        "confirmed",
        "manual",
        "STORE_SYS",
    )
    assert client.post(f"{base}/reassign", json={"entity_id": 1, "reason": "x"}).status_code == 403
    assert client.post(f"{base}/exclude", json={"reason": "x"}).status_code == 403  # confirmed: admin only

    admin = sign_in("boss")
    grant("boss", "commercial", "admin")
    excluded = admin.post(f"{base}/exclude", json={"reason": "Pseudo cliente"})
    assert excluded.status_code == 200
    assert excluded.json()["status"] == "excluded"
    assert admin.post(f"{base}/exclude", json={"reason": "x"}).json()["code"] == "mapping_already_excluded"


def test_module_admin_configures_zone_type_and_product(sign_in, grant, masters: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("boss")
    grant("boss", "commercial", "admin")
    today = date.today().isoformat()
    zone = client.put(
        f"/api/v1/commercial/zones/{masters['zone']}/configuration",
        json={
            "zone_type": "PRESALE",
            "requires_visit": True,
            "weekdays": [2, 4],
            "effective_from": today,
            "reason": "Enzo definió MISA",
        },
    )
    assert zone.status_code == 200, zone.text
    body = zone.json()
    assert (body["configuration_origin"], body["weekdays"], len(body["visit_days"])) == ("MANUAL", [2, 4], 2)
    bad = client.put(
        f"/api/v1/commercial/zones/{masters['zone']}/configuration",
        json={"zone_type": "OUTLET", "requires_visit": True, "weekdays": [1], "effective_from": today, "reason": "x"},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "zone_without_visit"

    categories = {c["code"]: c for c in client.get("/api/v1/commercial/product-categories").json()}
    category = categories["CERVEZAS"]  # seeded
    assert (category["default_product_kind"], category["default_analytics_eligible"]) == ("PRODUCT", True)
    duplicate = client.post(
        "/api/v1/commercial/product-categories",
        json={
            "code": "cervezas",
            "name": "Otra",
            "default_product_kind": "PRODUCT",
            "default_analytics_eligible": True,
        },
    )
    assert duplicate.status_code == 409
    mapped = client.put(
        "/api/v1/commercial/source-category-mappings",
        json={"source_category": "CERVEZAS", "category_id": category["id"]},
    )
    assert mapped.status_code == 200, mapped.text
    product = client.get(f"/api/v1/commercial/products/{masters['product']}").json()
    assert (product["category_origin"], product["analytics_eligible"], product["prices"]) == (
        "SOURCE_MAPPING",
        True,
        [],
    )
    patched = client.patch(
        f"/api/v1/commercial/products/{masters['product']}", json={"analytics_eligible": False, "reason": "Envase"}
    )
    assert patched.status_code == 200
    assert patched.json()["eligibility_origin"] == "MANUAL"

    types = {t["source_name"]: t for t in client.get("/api/v1/commercial/document-types").json()}
    factura = types["FACTURA"]
    classified = client.put(
        f"/api/v1/commercial/document-types/{factura['id']}/classification",
        json={"document_class": "INVOICE", "sign": 1, "commercial_universe": True, "reason": "Confirmado"},
    )
    assert classified.status_code == 200
    assert classified.json()["commercial_universe"] is True


def test_seed_config_cli_only_adds_what_is_missing(capsys, db: Session) -> None:  # type: ignore[no-untyped-def]
    assert cli(["commercial", "seed-config"]) == 0
    first = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert first["inserted"]["product_categories"] == 7
    assert first["inserted"]["price_lists"] == 5
    assert first["inserted"]["document_types"] == 9
    # The articles master's `precio` → Lista 1, and the AXUM customer-code rule Enzo approved (ADR 018).
    assert first["inserted"]["settings"] == 2
    assert cli(["commercial", "seed-config"]) == 0
    again = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert set(again["inserted"].values()) == {0}


# --- customer privacy (Enzo 2026-11-04): CUIT, address and phone only for operator and admin -------------------

PII_FIELDS = {"tax_id", "address", "phone"}
TAX_ID = "20123456786"  # valid check digit
ADDRESS = "Calle Falsa 742"
PHONE = "3855551234"


@pytest.fixture
def contact(db: Session, make_user) -> int:  # type: ignore[no-untyped-def]
    owner = make_user(email="owner@districom.test", role="admin")
    job = make_job(db, owner.id)
    record = customers.normalize(
        customers.MasterRecord(
            display_name="Kiosco Privado", legal_name="Privado SRL", tax_id=TAX_ID, address=ADDRESS, phone=PHONE
        )
    )
    assert record.tax_id == TAX_ID
    customer = customers.create_from_master(db, record, job_id=job, seen_on=date.today())
    db.commit()
    return customer.id


def _as(sign_in, grant, name: str, module_role: str | None, platform_role: str = "viewer") -> TestClient:  # type: ignore[no-untyped-def]
    client: TestClient = sign_in(name, platform_role)
    if module_role:
        grant(name, "commercial", module_role)
    return client


def test_viewer_never_receives_tax_id_address_or_phone(sign_in, grant, contact: int) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "viewer", "viewer")
    [item] = client.get("/api/v1/commercial/customers").json()["items"]
    assert PII_FIELDS.isdisjoint(item)  # absent, not masked nor null
    assert (item["id"], item["display_name"], item["legal_name"]) == (contact, "Kiosco Privado", "Privado SRL")
    detail = client.get(f"/api/v1/commercial/customers/{contact}")
    assert detail.status_code == 200
    assert PII_FIELDS.isdisjoint(detail.json())
    for value in (TAX_ID, ADDRESS, PHONE):
        assert value not in detail.text


@pytest.mark.parametrize(
    ("name", "module_role", "platform_role"),
    [("operator", "operator", "viewer"), ("boss", "admin", "viewer"), ("platform", None, "admin")],
)
def test_operator_and_admin_receive_contact_data(
    sign_in, grant, contact: int, name, module_role, platform_role
) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, name, module_role, platform_role)
    [item] = client.get("/api/v1/commercial/customers").json()["items"]
    assert (item["tax_id"], item["address"], item["phone"]) == (TAX_ID, ADDRESS, PHONE)
    detail = client.get(f"/api/v1/commercial/customers/{contact}").json()
    assert (detail["tax_id"], detail["address"], detail["phone"]) == (TAX_ID, ADDRESS, PHONE)


def test_contact_data_stays_out_of_logs_and_audit(sign_in, grant, contact: int, db: Session, caplog) -> None:  # type: ignore[no-untyped-def]
    from sqlalchemy import text as sql

    caplog.set_level("DEBUG")
    client = _as(sign_in, grant, "boss", "admin")
    client.get("/api/v1/commercial/customers")
    client.get(f"/api/v1/commercial/customers/{contact}")
    patched = client.patch(f"/api/v1/commercial/customers/{contact}", json={"is_generic": True, "reason": "Prueba"})
    assert patched.status_code == 200
    assert patched.json()["tax_id"] == TAX_ID
    audit = db.execute(sql("SELECT coalesce(string_agg(payload::text, ' '), '') FROM audit_events")).scalar_one()
    for value in (TAX_ID, ADDRESS, PHONE, "Kiosco Privado", "Privado SRL"):
        assert value not in caplog.text
        assert value not in audit
