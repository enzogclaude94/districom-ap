"""F1.7: Comercial > Datos > Maestros over the API. It exposes and organizes what exists: customers (platform) with
their code, zone and seller and their history, products with the articles-master price, zones and their rules,
sellers (employees + mappings), price lists, document types and no-purchase reasons. Viewer reads what privacy
allows, admin edits through the existing services, a save over a newer version is a 409, and a fix made from
Maestros closes the Calidad issue through its own lifecycle."""

from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types, quality codes and resolvers)
from app.core.clock import business_today
from app.modules.commercial.catalog.models import Product
from app.modules.commercial.config import service as config
from app.modules.commercial.config.models import NoPurchaseReasonMapping, ProductCategory, Zone
from app.modules.commercial.config.seed import seed_config
from app.platform.auth.models import User
from app.platform.mappings import service as mappings
from app.platform.parties.models import Employee, EmployeeFunction
from app.platform.quality.models import DataQualityIssue
from tests.conftest import PASSWORD
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    PRODUCTS,
    article,
    articles_file,
    customer,
    customers_file,
    run,
)

API = "/api/v1/commercial"
TAX_ID = "20123456786"
ADDRESS = "CALLE SINTETICA 742"
PHONE = "3855550000"
PII = {"tax_id", "address", "phone"}
TODAY = business_today()
EARLIER, LATER = TODAY - timedelta(days=10), TODAY - timedelta(days=5)


@pytest.fixture
def sign_in(client: TestClient, make_user, grant):  # type: ignore[no-untyped-def]
    def _sign_in(name: str, module_role: str | None) -> TestClient:
        client.post("/api/v1/auth/logout")
        email = f"{name}@districom.test"
        make_user(email=email, role="viewer")
        if module_role:
            grant(name, "commercial", module_role)
        response = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
        assert response.status_code == 200, response.text
        return client

    return _sign_in


@pytest.fixture
def world(db: Session, make_user) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Two master snapshots (customer 1 moves from Lumivi to Majusa), a seller linked to a Preventista, another one
    still unlinked, a Boca customer without seller, and an articles master that feeds Lista 1."""
    seed_config(db)
    owner: User = make_user(email="owner@districom.test", role="admin")
    first = [
        customer(1, "ALMACEN UNO", zone="RUTA 01 - LUVI", seller="VENDEDOR UNO", cuit=TAX_ID, phone=PHONE),
        customer(2, "KIOSCO DOS", zone="RUTA 03 - MISA", seller="VENDEDOR DOS", address=ADDRESS),
        customer(3, "CONSUMIDOR FINAL", zone="BOCA DE EXPENDIO", seller=None),
    ]
    run(db, owner, customers_file(first), CUSTOMERS, snapshot_date=EARLIER)
    ana = Employee(full_name="Ana Preventista")
    db.add(ana)
    db.flush()
    db.add(EmployeeFunction(employee_id=ana.id, function_code="commercial.salesperson", valid_from=EARLIER))
    db.commit()
    seller = mappings.resolve(db, mappings.MappingKey("employee", "STORE_SYS", "seller", "VENDEDOR UNO"))
    assert seller.mapping_id is not None
    mappings.confirm(db, owner, seller.mapping_id, ana.id, reason=None, is_admin=True)
    db.commit()
    moved = [customer(1, "ALMACEN UNO", zone="RUTA 02 - MAJUSA", seller="VENDEDOR UNO", cuit=TAX_ID, phone=PHONE)]
    run(db, owner, customers_file([*moved, *first[1:]]), CUSTOMERS, snapshot_date=LATER)
    run(
        db,
        owner,
        articles_file([article(1001, "CERVEZA SINTETICA X6", Decimal("1500.00")), article(1002, "VARIOS", 0)]),
        PRODUCTS,
        snapshot_date=LATER,
    )
    ids = dict(db.execute(text("SELECT display_name, id FROM customers")).tuples().all())
    zones = {z.source_name: z for z in db.scalars(select(Zone))}
    products = {p.source_code: p.id for p in db.scalars(select(Product))}
    return {
        "uno": ids["ALMACEN UNO"],
        "dos": ids["KIOSCO DOS"],
        "final": ids["CONSUMIDOR FINAL"],
        "ana": ana.id,
        "seller_mapping": seller.mapping_id,
        "zones": {name: z.id for name, z in zones.items()},
        "products": products,
    }


def items(client: TestClient, path: str, **params: Any) -> list[dict[str, Any]]:
    response = client.get(f"{API}{path}", params=params)
    assert response.status_code == 200, response.text
    body = response.json()
    return body["items"] if isinstance(body, dict) else body  # type: ignore[no-any-return]


def open_issues(db: Session, code: str) -> list[str]:
    db.expire_all()
    return list(
        db.scalars(
            select(DataQualityIssue.dedupe_key).where(DataQualityIssue.code == code, DataQualityIssue.status == "open")
        )
    )


# --- Clientes ---------------------------------------------------------------------------------------------------


def test_customers_carry_code_current_zone_and_seller(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer", "viewer")
    by_name = {c["display_name"]: c for c in items(client, "/customers")}
    uno = by_name["ALMACEN UNO"]
    assert uno["store_sys_code"] == "1"
    assert uno["zone"] == {"id": world["zones"]["RUTA 02 - MAJUSA"], "name": "RUTA 02 - MAJUSA"}
    assert uno["salesperson"]["salesperson_id"] == world["ana"]
    assert uno["salesperson"]["salesperson_name"] == "Ana Preventista"
    assert uno["salesperson"]["source_label"] == "VENDEDOR UNO"
    dos = by_name["KIOSCO DOS"]
    assert dos["salesperson"]["salesperson_id"] is None  # its code is not linked yet: shown, never guessed
    assert dos["salesperson"]["source_label"] == "VENDEDOR DOS"
    assert by_name["CONSUMIDOR FINAL"]["salesperson"] is None


def test_customers_search_filters_and_paginates_in_the_database(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer", "viewer")
    names = lambda **p: [c["display_name"] for c in items(client, "/customers", **p)]  # noqa: E731
    assert names(q="kiosco") == ["KIOSCO DOS"]
    assert names(q="2") == ["KIOSCO DOS"]  # its STORE SYS code, exact
    assert names(zone_id=world["zones"]["RUTA 02 - MAJUSA"]) == ["ALMACEN UNO"]
    assert names(zone_id=world["zones"]["RUTA 01 - LUVI"]) == []  # only the zone in force
    assert names(salesperson_id=world["ana"]) == ["ALMACEN UNO"]
    page = client.get(f"{API}/customers", params={"limit": 1, "offset": 1}).json()
    assert (page["total"], len(page["items"])) == (3, 1)
    assert page["items"][0]["display_name"] == "CONSUMIDOR FINAL"  # ordered by name


def test_admin_flags_with_reason_filter_generic_and_inactive(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("boss", "admin")
    path = f"{API}/customers/{world['final']}"
    assert client.patch(path, json={"is_generic": True, "reason": ""}).status_code == 422
    current = client.get(path).json()
    done = client.patch(
        path, json={"is_generic": True, "reason": "Consumidor final", "expected_updated_at": current["updated_at"]}
    )
    assert done.status_code == 200, done.text
    assert done.json()["is_generic"] is True
    assert done.json()["zone"]["name"] == "BOCA DE EXPENDIO"
    stale = client.patch(path, json={"active": False, "reason": "Otro", "expected_updated_at": current["updated_at"]})
    assert stale.status_code == 409
    assert stale.json()["code"] == "changed_meanwhile"
    assert client.get(path).json()["active"] is True  # nothing was overwritten
    client.patch(path, json={"active": False, "reason": "Cerró"})
    names = lambda **p: [c["display_name"] for c in items(client, "/customers", **p)]  # noqa: E731
    assert names(is_generic=True, include_inactive=True) == ["CONSUMIDOR FINAL"]
    assert names(active=False) == ["CONSUMIDOR FINAL"]
    assert "CONSUMIDOR FINAL" not in names()
    assert db.scalar(text("SELECT count(*) FROM audit_events WHERE event_type = 'platform.customer.updated'")) == 2


@pytest.mark.parametrize("role", ["viewer", "operator"])
def test_only_an_admin_changes_customer_flags(sign_in, world: dict[str, Any], role: str) -> None:  # type: ignore[no-untyped-def]
    client = sign_in(role, role)
    response = client.patch(f"{API}/customers/{world['uno']}", json={"is_generic": True, "reason": "x"})
    assert response.status_code == 403


def test_customer_history_shows_codes_zones_and_sellers(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer", "viewer")
    history = client.get(f"{API}/customers/{world['uno']}/history").json()
    assert [(m["source_system"], m["namespace"], m["external_id"]) for m in history["mappings"]] == [
        ("STORE_SYS", "customer", "1")
    ]
    zones = [(z["zone"]["name"], z["valid_from"], z["valid_to"]) for z in history["zones"]]
    assert zones == [
        ("RUTA 02 - MAJUSA", LATER.isoformat(), None),
        ("RUTA 01 - LUVI", EARLIER.isoformat(), (LATER - timedelta(days=1)).isoformat()),
    ]
    [seller] = history["salespeople"]  # the same seller in both snapshots: one row
    assert (seller["salesperson_name"], seller["source_label"], seller["valid_to"]) == (
        "Ana Preventista",
        "VENDEDOR UNO",
        None,
    )
    assert seller["mapping_id"] == world["seller_mapping"]
    assert client.get(f"{API}/customers/999999/history").status_code == 404


def test_a_viewer_never_receives_contact_data_in_maestros(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer", "viewer")
    for item in items(client, "/customers"):
        assert PII.isdisjoint(item)
    for name in ("uno", "dos"):
        detail = client.get(f"{API}/customers/{world[name]}")
        history = client.get(f"{API}/customers/{world[name]}/history")
        assert PII.isdisjoint(detail.json())
        for value in (TAX_ID, PHONE, ADDRESS):
            assert value not in detail.text
            assert value not in history.text
    operator = sign_in("operator", "operator")
    detail = operator.get(f"{API}/customers/{world['uno']}").json()
    assert (detail["tax_id"], detail["phone"]) == (TAX_ID, PHONE)


# --- Productos --------------------------------------------------------------------------------------------------


def test_products_filters_and_the_articles_master_price(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer", "viewer")
    by_code = {p["source_code"]: p for p in items(client, "/products")}
    assert Decimal(by_code["1001"]["master_price"]) == Decimal("1500.00")
    assert Decimal(by_code["1002"]["master_price"]) == 0  # a price of 0 is a price, not a missing one
    assert [p["source_code"] for p in items(client, "/products", active=True)] == ["1001", "1002"]
    assert items(client, "/products", active=False) == []
    without = [p["source_code"] for p in items(client, "/products", has_category=False)]
    assert without == [p["source_code"] for p in items(client, "/products", without_category=True)]
    detail = client.get(f"{API}/products/{world['products']['1001']}").json()
    assert [(p["list_number"], p["valid_from"]) for p in detail["prices"]] == [(1, LATER.isoformat())]
    assert Decimal(detail["master_price"]) == Decimal("1500.00")


def test_admin_assigns_a_category_and_its_issue_closes(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    product_id = world["products"]["1002"]
    assert f"product:{product_id}" in open_issues(db, "commercial.product.without_category")
    category = db.scalars(select(ProductCategory).order_by(ProductCategory.id)).first()
    assert category is not None
    for role in ("viewer", "operator"):
        denied = sign_in(role, role).patch(
            f"{API}/products/{product_id}", json={"category_id": category.id, "reason": "x"}
        )
        assert denied.status_code == 403
    client = sign_in("boss", "admin")
    read = client.get(f"{API}/products/{product_id}").json()
    done = client.patch(
        f"{API}/products/{product_id}",
        json={"category_id": category.id, "reason": "Clasificado", "expected_updated_at": read["updated_at"]},
    )
    assert done.status_code == 200, done.text
    assert done.json()["canonical_category_id"] == category.id
    assert f"product:{product_id}" not in open_issues(db, "commercial.product.without_category")
    stale = client.patch(
        f"{API}/products/{product_id}",
        json={"analytics_eligible": False, "reason": "Otro", "expected_updated_at": read["updated_at"]},
    )
    assert (stale.status_code, stale.json()["code"]) == (409, "changed_meanwhile")
    assert [p["source_code"] for p in items(client, "/products", has_category=True)].count("1002") == 1


# --- Categorías y familias --------------------------------------------------------------------------------------


def test_category_defaults_never_rewrite_classified_products(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("boss", "admin")
    product_id = world["products"]["1001"]
    created = client.post(
        f"{API}/product-categories",
        json={"code": "SINTETICA", "name": "Sintética", "default_analytics_eligible": True},
    ).json()
    client.patch(f"{API}/products/{product_id}", json={"category_id": created["id"], "reason": "Prueba"})
    patched = client.patch(
        f"{API}/product-categories/{created['id']}",
        json={"default_analytics_eligible": False, "reason": "Ya no", "expected_updated_at": created["updated_at"]},
    )
    assert patched.status_code == 200, patched.text
    assert client.get(f"{API}/products/{product_id}").json()["analytics_eligible"] is True
    stale = client.patch(
        f"{API}/product-categories/{created['id']}", json={"name": "X", "expected_updated_at": created["updated_at"]}
    )
    assert stale.status_code == 409


def test_a_category_parent_must_exist_and_never_loop(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("boss", "admin")
    parent = client.post(
        f"{API}/product-categories", json={"code": "PADRE", "name": "Padre", "default_analytics_eligible": True}
    ).json()
    child = client.post(
        f"{API}/product-categories",
        json={"code": "HIJA", "name": "Hija", "default_analytics_eligible": True, "parent_id": parent["id"]},
    ).json()
    loop = client.patch(f"{API}/product-categories/{parent['id']}", json={"parent_id": child["id"]})
    assert (loop.status_code, loop.json()["code"]) == (422, "invalid_parent_category")
    assert client.patch(f"{API}/product-categories/{child['id']}", json={"parent_id": 999999}).status_code == 422
    assert client.patch(f"{API}/product-categories/{child['id']}", json={"parent_id": None}).json()["parent_id"] is None


def test_families_are_listed_created_and_edited_by_an_admin(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    viewer = sign_in("viewer", "viewer")
    assert viewer.post(f"{API}/product-families", json={"code": "F", "name": "F"}).status_code == 403
    client = sign_in("boss", "admin")
    family = client.post(f"{API}/product-families", json={"code": "LATAS", "name": "Latas"}).json()
    edited = client.patch(
        f"{API}/product-families/{family['id']}",
        json={"name": "Latas 473", "reason": "Nombre", "expected_updated_at": family["updated_at"]},
    )
    assert edited.status_code == 200, edited.text
    stale = client.patch(
        f"{API}/product-families/{family['id']}", json={"active": False, "expected_updated_at": family["updated_at"]}
    )
    assert stale.status_code == 409
    audit = db.scalar(
        text("SELECT payload::text FROM audit_events WHERE event_type = 'commercial.catalog.family_updated'")
    )
    assert audit is not None
    assert "Nombre" in audit


# --- Zonas y reglas ---------------------------------------------------------------------------------------------


def test_zones_show_their_configuration_and_origin(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer", "viewer")
    by_name = {z["source_name"]: z for z in items(client, "/zones")}
    luvi = by_name["RUTA 01 - LUVI"]
    assert (luvi["zone_type"], luvi["weekdays"], luvi["configuration_origin"]) == ("PRESALE", [1, 3, 5], "RULE")
    assert luvi["rule_id"] is not None
    assert by_name["RUTA 02 - MAJUSA"]["weekdays"] == [2, 4, 5]
    boca = by_name["BOCA DE EXPENDIO"]
    assert (boca["zone_type"], boca["requires_visit"], boca["weekdays"]) == ("OUTLET", False, [])
    misa = by_name["RUTA 03 - MISA"]  # no rule on purpose: never configured by itself
    assert (misa["zone_type"], misa["requires_visit"], misa["weekdays"]) == ("UNCLASSIFIED", False, [])
    assert misa["configuration_origin"] == "NONE"


def test_configuring_a_zone_from_maestros_closes_its_issue(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    misa = world["zones"]["RUTA 03 - MISA"]
    assert f"zone:{misa}" in open_issues(db, "commercial.zone.unconfigured")
    client = sign_in("boss", "admin")
    read = client.get(f"{API}/zones/{misa}").json()
    body = {
        "zone_type": "PRESALE",
        "requires_visit": True,
        "weekdays": [2],
        "effective_from": TODAY.isoformat(),
        "reason": "Confirmado por Enzo",
        "expected_updated_at": read["updated_at"],
    }
    outlet = client.put(f"{API}/zones/{misa}/configuration", json={**body, "zone_type": "OUTLET"})
    assert (outlet.status_code, outlet.json()["code"]) == (422, "zone_without_visit")
    done = client.put(f"{API}/zones/{misa}/configuration", json=body)
    assert done.status_code == 200, done.text
    assert (done.json()["weekdays"], done.json()["configuration_origin"]) == ([2], "MANUAL")
    assert [d["weekday"] for d in done.json()["visit_days"]] == [2]
    assert f"zone:{misa}" not in open_issues(db, "commercial.zone.unconfigured")
    stale = client.put(f"{API}/zones/{misa}/configuration", json={**body, "weekdays": [3]})
    assert (stale.status_code, stale.json()["code"]) == (409, "changed_meanwhile")
    assert sign_in("operator", "operator").put(f"{API}/zones/{misa}/configuration", json=body).status_code == 403


def test_zone_rules_are_edited_with_reason_and_version(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("boss", "admin")
    rule = client.post(
        f"{API}/zone-rules",
        json={"priority": 90, "pattern": "SINTETICA", "zone_type": "PRESALE", "requires_visit": True, "weekdays": [1]},
    ).json()
    before = {z["source_name"]: z["weekdays"] for z in items(client, "/zones")}
    edited = client.patch(
        f"{API}/zone-rules/{rule['id']}",
        json={"weekdays": [1, 2], "reason": "Cambio de días", "expected_updated_at": rule["updated_at"]},
    )
    assert edited.status_code == 200, edited.text
    assert {z["source_name"]: z["weekdays"] for z in items(client, "/zones")} == before  # existing zones untouched
    stale = client.patch(
        f"{API}/zone-rules/{rule['id']}", json={"active": False, "expected_updated_at": rule["updated_at"]}
    )
    assert stale.status_code == 409
    audit = db.scalar(
        text("SELECT payload::text FROM audit_events WHERE event_type = 'commercial.config.zone_rule_updated'")
    )
    assert audit is not None
    assert "Cambio de días" in audit


# --- Vendedores -------------------------------------------------------------------------------------------------


def test_salespeople_are_employees_with_their_codes_and_customers(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("viewer", "viewer")
    body = client.get(f"{API}/salespeople").json()
    [ana] = body["items"]
    assert (ana["id"], ana["name"], ana["is_salesperson"], ana["customers"]) == (
        world["ana"],
        "Ana Preventista",
        True,
        1,
    )
    assert [(m["source_system"], m["namespace"], m["external_id"], m["customers"]) for m in ana["mappings"]] == [
        ("STORE_SYS", "seller", "VENDEDOR UNO", 1)
    ]
    assert "phone" not in ana
    [unlinked] = body["unlinked"]
    assert (unlinked["external_id"], unlinked["status"], unlinked["customers"]) == ("VENDEDOR DOS", "pending", 1)
    db.expire_all()
    assert db.get(Employee, world["ana"]).external_store_id is None  # type: ignore[union-attr]
    assert client.post(f"{API}/salespeople", json={}).status_code == 405  # never created here


def test_linking_the_code_in_mapeos_shows_the_seller_linked(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("operator", "operator")
    [unlinked] = client.get(f"{API}/salespeople").json()["unlinked"]
    done = client.post(f"{API}/mappings/{unlinked['id']}/confirm", json={"entity_id": world["ana"]})
    assert done.status_code == 200, done.text
    body = client.get(f"{API}/salespeople").json()
    assert body["unlinked"] == []
    [ana] = body["items"]
    assert ana["customers"] == 2
    assert {m["external_id"] for m in ana["mappings"]} == {"VENDEDOR UNO", "VENDEDOR DOS"}


# --- Listas, comprobantes, motivos ------------------------------------------------------------------------------


def test_price_lists_known_ones_and_the_articles_master_list(sign_in, world: dict[str, Any]) -> None:  # type: ignore[no-untyped-def]
    client = sign_in("boss", "admin")
    lists = items(client, "/price-lists")
    assert [p["list_number"] for p in lists] == [1, 2, 3, 4, 5]  # none invented for missing numbers
    assert client.get(f"{API}/price-lists/articles-master").json() == {"list_number": 1}
    first = lists[0]
    edited = client.patch(
        f"{API}/price-lists/{first['id']}",
        json={"description": "Lista general", "reason": "Descripción", "expected_updated_at": first["updated_at"]},
    )
    assert edited.status_code == 200, edited.text
    stale = client.patch(
        f"{API}/price-lists/{first['id']}", json={"active": False, "expected_updated_at": first["updated_at"]}
    )
    assert stale.status_code == 409
    assert client.put(f"{API}/price-lists/articles-master", json={"list_number": 2, "reason": ""}).status_code == 422
    viewer = sign_in("viewer", "viewer")
    assert viewer.put(f"{API}/price-lists/articles-master", json={"list_number": 2, "reason": "x"}).status_code == 403


def test_document_types_are_classified_with_reason_and_version(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    row, _ = config.ensure_document_type(db, "COMPROBANTE SINTETICO Z", job_id=None)
    db.commit()
    client = sign_in("boss", "admin")
    read = next(t for t in items(client, "/document-types") if t["id"] == row.id)
    body = {"document_class": "INVOICE", "sign": 1, "commercial_universe": True, "reason": "Confirmado"}
    stale_body = {**body, "expected_updated_at": "2000-01-01T00:00:00+00:00"}
    assert client.put(f"{API}/document-types/{row.id}/classification", json=stale_body).status_code == 409
    done = client.put(
        f"{API}/document-types/{row.id}/classification", json={**body, "expected_updated_at": read["updated_at"]}
    )
    assert done.status_code == 200, done.text
    assert f"document_type:{row.id}" not in open_issues(db, "commercial.document.unclassified_type")


def test_no_purchase_reasons_get_a_category_with_version(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    reason, _ = config.ensure_reason(db, "AXUM", "MOTIVO SINTETICO")
    db.commit()
    client = sign_in("boss", "admin")
    categories = items(client, "/no-purchase-reason-categories")
    read = next(r for r in items(client, "/no-purchase-reasons") if r["id"] == reason.id)
    assert read["category_id"] is None
    stale = client.put(
        f"{API}/no-purchase-reasons/{reason.id}/category",
        json={"category_id": categories[0]["id"], "expected_updated_at": "2000-01-01T00:00:00+00:00"},
    )
    assert stale.status_code == 409
    done = client.put(
        f"{API}/no-purchase-reasons/{reason.id}/category",
        json={"category_id": categories[0]["id"], "reason": "Confirmado", "expected_updated_at": read["updated_at"]},
    )
    assert done.status_code == 200, done.text
    db.expire_all()
    assert db.get(NoPurchaseReasonMapping, reason.id).category_id == categories[0]["id"]  # type: ignore[union-attr]
    category = categories[0]
    renamed = client.patch(
        f"{API}/no-purchase-reason-categories/{category['id']}",
        json={"name": category["name"] + " (x)", "reason": "Nombre", "expected_updated_at": category["updated_at"]},
    )
    assert renamed.status_code == 200, renamed.text


# --- Calidad diagnostica, Maestros corrige ----------------------------------------------------------------------


def test_calidad_links_each_fixable_issue_to_its_exact_master(sign_in, world: dict[str, Any], db: Session) -> None:  # type: ignore[no-untyped-def]
    reason, _ = config.ensure_reason(db, "AXUM", "MOTIVO SINTETICO")
    db.commit()
    from app.platform.quality.service import record_issue

    record_issue(
        db, "commercial.no_purchase.reason_unmapped", f"reason:{reason.id}", details={"reason_mapping_id": reason.id}
    )
    record_issue(db, "commercial.product.prices_not_loaded", "store_sys_articles_price_list")
    db.commit()
    client = sign_in("viewer", "viewer")
    found = client.get("/api/v1/quality/issues", params={"module": "commercial", "status": "open", "limit": 100}).json()
    hrefs = {i["code"]: (i["fix"], i["fix_href"]) for i in found["items"]}
    misa, product = world["zones"]["RUTA 03 - MISA"], world["products"]["1002"]
    assert hrefs["commercial.zone.unconfigured"] == ("masters", f"/comercial/datos/maestros/zonas/{misa}")
    assert hrefs["commercial.product.without_category"][1] in {
        f"/comercial/datos/maestros/productos/{p}" for p in world["products"].values()
    }
    assert hrefs["commercial.no_purchase.reason_unmapped"] == (
        "masters",
        f"/comercial/datos/maestros/motivos?motivo={reason.id}",
    )
    assert hrefs["commercial.product.prices_not_loaded"] == ("masters", "/comercial/datos/maestros/listas")
    assert (
        hrefs["commercial.customer.without_salesperson"][1] is None
        if "commercial.customer.without_salesperson" in hrefs
        else True
    )
    assert all(h is None for fix, h in hrefs.values() if fix != "masters")
    assert hrefs["commercial.product.without_category"][0] == "masters"
    assert product in world["products"].values()


def test_a_masters_fix_always_declares_a_relative_path() -> None:
    from app.platform.quality.registry import QualityCodeSpec, registry

    masters = [s for s in registry.all() if s.fix == "masters"]
    assert {s.code for s in masters} == {
        "commercial.product.without_category",
        "commercial.product.prices_not_loaded",
        "commercial.zone.unconfigured",
        "commercial.document.unclassified_type",
        "commercial.no_purchase.reason_unmapped",
    }
    reason = registry.get("commercial.no_purchase.reason_unmapped")
    assert reason is not None
    assert reason.fix_href(None, "reason:abc") is None  # never a path built from something it cannot parse
    with pytest.raises(ValueError, match="fix_path"):
        QualityCodeSpec("x.y", "info", "X", fix="masters")
    absolute = QualityCodeSpec("x.z", "info", "X", fix="masters", fix_path=lambda _i, _k: "https://example.com/x")
    assert absolute.fix_href(1, "k") is None
