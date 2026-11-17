"""F1.5: Datos > Mapeos over the API (/api/v1/commercial/mappings…): the queue (summary, filters, search, order,
pagination), the detail with its impact, the link targets, the decisions with their re-resolution, the batch, a
state changed in another tab, permissions and privacy."""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.no_purchase.models import NoPurchaseEvent
from app.platform.audit.models import AuditEvent
from app.platform.mappings import service as mappings
from app.platform.parties.models import Employee, EmployeeFunction
from tests.conftest import PASSWORD
from tests.modules.commercial.axum import NO_PURCHASE, axum_file, visit
from tests.modules.commercial.store_sys import CUSTOMERS, customer, customers_file, run

API = "/api/v1/commercial"
PHONE = "11-5555-0101"
CUIT = "20-12345678-9"
ADDRESS = "CALLE PRIVADA 742"


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
def data(db: Session, make_user, grant) -> dict[str, int]:  # type: ignore[no-untyped-def]
    owner = make_user(email="owner@districom.test", role="viewer")
    grant("owner", "commercial", "operator")
    seed_config(db)
    db.refresh(owner)
    run(
        db,
        owner,
        customers_file(
            [
                customer(1, "ALMACEN UNO", legal="ALMACEN UNO SRL", phone=PHONE, cuit=CUIT, address=ADDRESS),
                customer(2, "KIOSCO DOS"),
                customer(3, "DESPENSA TRES", seller="BOCA DE EXPENDIO"),
            ]
        ),
        CUSTOMERS,
        snapshot_date=date(2026, 10, 20),
    )
    run(
        db,
        owner,
        axum_file(
            [
                ("01", [visit(1, "ALMACEN UNO", "CERRADO"), visit(2, "KIOSCO DOS", "CERRADO")]),
                ("02", [visit(77, "SIN MAESTRO", "TIENE STOCK")]),
                ("40", [visit(1, "ALMACEN UNO", "Si")]),
            ]
        ),
        NO_PURCHASE,
    )
    ana = Employee(full_name="Ana Preventista")
    bruno = Employee(full_name="Bruno Chofer")
    gone = Employee(full_name="Carla Inactiva", active=False)
    db.add_all([ana, bruno, gone])
    db.flush()
    db.add(EmployeeFunction(employee_id=ana.id, function_code="commercial.salesperson", valid_from=date(2026, 1, 1)))
    db.commit()

    def mapping(entity_type: str, system: str, namespace: str, external_id: str) -> int:
        found = mappings.resolve(db, mappings.MappingKey(entity_type, system, namespace, external_id)).mapping_id
        assert found is not None
        return found

    return {
        "01": mapping("employee", "AXUM", "salesperson", "01"),
        "02": mapping("employee", "AXUM", "salesperson", "02"),
        "40": mapping("employee", "AXUM", "salesperson", "40"),
        "axum77": mapping("customer", "AXUM", "customer", "77"),
        "uno": mapping("employee", "STORE_SYS", "seller", "VENDEDOR UNO"),
        "boca": mapping("employee", "STORE_SYS", "seller", "BOCA DE EXPENDIO"),
        "c1": mappings.resolve(db, mappings.MappingKey("customer", "STORE_SYS", "customer", "1")).entity_id or 0,
        "ana": ana.id,
        "bruno": bruno.id,
        "gone": gone.id,
    }


def _as(sign_in, grant, role: str) -> TestClient:  # type: ignore[no-untyped-def]
    client = sign_in(role, "admin" if role == "platform" else "viewer")
    if role != "platform":
        grant(role, "commercial", role)
    return client  # type: ignore[no-any-return]


# --- the queue ------------------------------------------------------------------------------------------------------


def test_summary_filters_search_order_and_pagination(sign_in, grant, data: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "viewer")
    counts = client.get(f"{API}/mappings/summary").json()["counts"]
    assert counts["pending"] == 6  # 01, 02, 40, AXUM 77, VENDEDOR UNO, BOCA DE EXPENDIO
    assert set(counts) == {"pending", "ambiguous", "confirmed", "rejected", "excluded"}
    assert (
        client.get(f"{API}/mappings/summary?source_system=AXUM&entity_type=employee").json()["counts"]["pending"] == 3
    )

    page = client.get(f"{API}/mappings?status=pending&source_system=AXUM&namespace=salesperson").json()
    assert page["total"] == 3
    first = page["items"][0]
    assert [i["id"] for i in page["items"]] == [data["01"], data["02"], data["40"]]  # oldest pending first
    assert first["kind_label"] == "Preventista AXUM"
    assert first["first_seen_at"] is not None
    assert first["last_seen_at"] is not None
    assert first["facts"] == [
        {"kind": "commercial.no_purchase_events", "label": "eventos de no compra", "total": 2, "linked": 0}
    ]

    # Open ones before the rest, whatever the id order.
    everything = client.get(f"{API}/mappings?limit=200").json()["items"]
    statuses = [i["status"] for i in everything]
    assert statuses.index("confirmed") > max(i for i, s in enumerate(statuses) if s == "pending")

    assert [i["id"] for i in client.get(f"{API}/mappings?q=bOca").json()["items"]] == [data["boca"]]
    assert [i["id"] for i in client.get(f"{API}/mappings?q=4&namespace=salesperson").json()["items"]] == [data["40"]]
    two = client.get(f"{API}/mappings?status=pending&limit=2&offset=2").json()
    assert (two["total"], len(two["items"])) == (6, 2)
    assert client.get(f"{API}/mappings?source_system=NOPE").json() == {"items": [], "total": 0}


def test_detail_and_impact_come_from_the_backend(sign_in, grant, data: dict[str, int]) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "viewer")
    detail = client.get(f"{API}/mappings/{data['01']}").json()
    assert (detail["status"], detail["external_id"], detail["external_label"]) == ("pending", "01", "Vendedor 01")
    impact = client.get(f"{API}/mappings/{data['01']}/impact?entity_id={data['ana']}").json()
    assert (impact["on_link"], impact["on_unlink"]) == (2, 0)
    assert impact["facts"][0]["label"] == "eventos de no compra"
    assert client.get(f"{API}/mappings/{data['40']}/impact").json()["on_link"] == 0
    assert client.get(f"{API}/mappings/999999").status_code == 404


# --- link targets ---------------------------------------------------------------------------------------------------


def test_targets_offer_active_employees_salespeople_first_and_never_create(sign_in, grant, data) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "operator")
    found = client.get(f"{API}/mappings/{data['01']}/targets").json()["items"]
    assert [t["name"] for t in found] == ["Ana Preventista", "Bruno Chofer"]  # no inactive person
    assert found[0]["is_salesperson"] is True
    assert found[0]["functions"] == ["Preventista"]
    assert "phone" not in found[0]
    assert [t["name"] for t in client.get(f"{API}/mappings/{data['01']}/targets?q=chof").json()["items"]] == [
        "Bruno Chofer"
    ]


def test_customer_targets_by_name_legal_name_or_code_without_contact_data(sign_in, grant, data) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "operator")
    for q in ("almacen", "uno srl", "1"):
        items = client.get(f"{API}/mappings/{data['axum77']}/targets", params={"q": q}).json()["items"]
        assert "ALMACEN UNO" in [t["name"] for t in items], q
    item = next(t for t in client.get(f"{API}/mappings/{data['axum77']}/targets?q=1").json()["items"])
    assert (item["store_sys_code"], item["legal_name"], item["zone"]) == ("1", "ALMACEN UNO SRL", "RUTA 01 - LUVI")
    assert not ({PHONE, CUIT, ADDRESS} & set(map(str, item.values())))
    assert {"tax_id", "phone", "address"}.isdisjoint(item)


# --- decisions --------------------------------------------------------------------------------------------------------


def test_operator_confirms_and_the_events_follow(sign_in, grant, data, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "operator")
    response = client.post(
        f"{API}/mappings/{data['01']}/confirm", json={"entity_id": data["ana"], "expected_status": "pending"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "confirmed"
    assert body["resolution"] == {"facts_updated": {"commercial.no_purchase_events": 2}, "issues_closed": 1}
    assert body["entity"]["name"] == "Ana Preventista"
    assert body["facts"][0]["linked"] == 2
    db.expire_all()
    assert {
        e.salesperson_id for e in db.scalars(select(NoPurchaseEvent).where(NoPurchaseEvent.source_salesperson == "01"))
    } == {data["ana"]}


def test_a_decision_taken_in_another_tab_is_never_overwritten(sign_in, grant, data) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "admin")
    assert client.post(f"{API}/mappings/{data['02']}/confirm", json={"entity_id": data["ana"]}).status_code == 200
    # The other tab still shows it pending and tries to exclude it.
    stale = client.post(f"{API}/mappings/{data['02']}/exclude", json={"reason": "x", "expected_status": "pending"})
    assert stale.status_code == 409
    assert stale.json()["code"] == "mapping_changed"
    assert stale.json()["details"]["status"] == "confirmed"
    again = client.post(f"{API}/mappings/{data['02']}/confirm", json={"entity_id": data["bruno"]})
    assert (again.status_code, again.json()["code"]) == (409, "mapping_already_confirmed")


def test_permissions_and_reasons(sign_in, grant, data) -> None:  # type: ignore[no-untyped-def]
    viewer = _as(sign_in, grant, "viewer")
    assert viewer.post(f"{API}/mappings/{data['01']}/confirm", json={"entity_id": data["ana"]}).status_code == 403
    assert viewer.get(f"{API}/mappings/{data['01']}/targets").status_code == 403
    assert viewer.post(f"{API}/mappings/batch-confirm", json={"items": []}).status_code == 403

    operator = _as(sign_in, grant, "operator")
    assert operator.post(f"{API}/mappings/{data['01']}/confirm", json={"entity_id": data["ana"]}).status_code == 200
    assert (
        operator.post(
            f"{API}/mappings/{data['01']}/reassign", json={"entity_id": data["bruno"], "reason": "x"}
        ).status_code
        == 403
    )
    assert operator.post(f"{API}/mappings/{data['01']}/exclude", json={"reason": "x"}).status_code == 403
    assert operator.post(f"{API}/mappings/{data['boca']}/exclude", json={}).json()["code"] == "reason_required"
    assert operator.post(f"{API}/mappings/{data['boca']}/exclude", json={"reason": "Pseudo"}).status_code == 200
    relink = operator.post(f"{API}/mappings/{data['boca']}/confirm", json={"entity_id": data["ana"], "reason": "x"})
    assert relink.status_code == 403

    admin = _as(sign_in, grant, "admin")
    assert (
        admin.post(f"{API}/mappings/{data['01']}/reassign", json={"entity_id": data["bruno"]}).json()["code"]
        == "reason_required"
    )
    moved = admin.post(
        f"{API}/mappings/{data['01']}/reassign", json={"entity_id": data["bruno"], "reason": "Era Bruno"}
    )
    assert moved.status_code == 200
    assert moved.json()["resolution"]["facts_updated"] == {"commercial.no_purchase_events": 2}
    assert (
        admin.post(f"{API}/mappings/{data['boca']}/confirm", json={"entity_id": data["ana"]}).json()["code"]
        == "reason_required"
    )
    linked = admin.post(f"{API}/mappings/{data['boca']}/confirm", json={"entity_id": data["ana"], "reason": "Es Ana"})
    assert linked.status_code == 200
    assert linked.json()["resolution"]["facts_updated"] == {
        "commercial.customer_salesperson_assignments": 1,
        "commercial.sale_lines": 0,
    }
    gone = admin.post(f"{API}/mappings/{data['40']}/confirm", json={"entity_id": data["gone"]})
    assert (gone.status_code, gone.json()["code"]) == (422, "invalid_mapping_entity")


# --- batch ----------------------------------------------------------------------------------------------------------


def test_batch_confirm_all_or_nothing(sign_in, grant, data, db: Session) -> None:  # type: ignore[no-untyped-def]
    client = _as(sign_in, grant, "operator")
    failed = client.post(
        f"{API}/mappings/batch-confirm",
        json={
            "items": [
                {"mapping_id": data["01"], "entity_id": data["ana"]},
                {"mapping_id": data["02"], "entity_id": data["gone"]},
            ]
        },
    )
    assert failed.status_code == 422
    assert (failed.json()["code"], failed.json()["details"]["mapping_id"]) == ("invalid_mapping_entity", data["02"])
    assert client.get(f"{API}/mappings/{data['01']}").json()["status"] == "pending"  # nothing applied

    done = client.post(
        f"{API}/mappings/batch-confirm",
        json={
            "items": [
                {"mapping_id": data["01"], "entity_id": data["ana"]},
                {"mapping_id": data["02"], "entity_id": data["bruno"]},
            ]
        },
    )
    assert done.status_code == 200, done.text
    body = done.json()
    assert (body["confirmed"], body["failed"]) == (2, 0)
    assert body["facts_updated"] == {"commercial.no_purchase_events": 3}
    assert body["issues_closed"] == 2
    twice = client.post(
        f"{API}/mappings/batch-confirm", json={"items": [{"mapping_id": data["01"], "entity_id": data["ana"]}]}
    )
    assert (twice.status_code, twice.json()["code"]) == (409, "mapping_not_open")
    too_many = [{"mapping_id": i + 1, "entity_id": 1} for i in range(201)]
    assert client.post(f"{API}/mappings/batch-confirm", json={"items": too_many}).status_code == 422
    db.expire_all()
    assert db.scalars(select(AuditEvent).where(AuditEvent.event_type == "platform.mapping.batch_confirmed")).one()


# --- privacy ----------------------------------------------------------------------------------------------------------


def test_a_viewer_sees_names_and_statuses_but_no_contact_data(sign_in, grant, data, db: Session) -> None:  # type: ignore[no-untyped-def]
    operator = _as(sign_in, grant, "operator")
    operator.post(f"{API}/mappings/{data['axum77']}/confirm", json={"entity_id": data["c1"]})
    viewer = _as(sign_in, grant, "viewer")
    detail = viewer.get(f"{API}/mappings/{data['axum77']}").json()
    text = str(detail) + str(viewer.get(f"{API}/mappings?limit=200").json())
    for secret in (PHONE, CUIT, ADDRESS, "ALMACEN UNO SRL"):
        assert secret not in text
    if detail["entity"] is not None:
        assert detail["entity"]["legal_name"] is None
        assert detail["entity"]["store_sys_code"] is None
