"""F1.6: the lifecycle of Comercial's data quality issues (docs/IMPORTS.md §7.1). Each condition opens its issue
and, where the contract closes it automatically, the issue closes when the condition goes away and only then. A
closed (resolved or ignored) issue is never reopened: the same condition later opens a new one."""

from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types, quality codes and resolvers)
from app.modules.commercial.config import service as config
from app.modules.commercial.config.models import NoPurchaseReasonMapping, Zone
from app.modules.commercial.config.seed import seed_config
from app.platform.auth.models import User
from app.platform.mappings import service as mappings
from app.platform.parties.models import Employee
from app.platform.quality import review
from app.platform.quality.models import DataQualityIssue
from app.platform.quality.registry import registry
from tests.modules.commercial.axum import NO_PURCHASE, axum_file, visit
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    PRODUCTS,
    SALES,
    article,
    articles_file,
    customer,
    customers_file,
    run,
    sale,
    sales_file,
)

D20, D21, D22 = date(2026, 10, 20), date(2026, 10, 21), date(2026, 10, 22)


@pytest.fixture
def operator(db: Session, make_user, grant) -> User:  # type: ignore[no-untyped-def]
    user = make_user(email="operator@districom.test", role="viewer")
    grant("operator", "commercial", "operator")
    seed_config(db)
    db.refresh(user)
    return user  # type: ignore[no-any-return]


@pytest.fixture
def admin(make_user) -> User:  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")  # type: ignore[no-any-return]


def issues(db: Session, code: str) -> list[tuple[str, str]]:
    """(dedupe_key, status) of every issue of the code, oldest first."""
    db.expire_all()
    rows = db.execute(
        select(DataQualityIssue.dedupe_key, DataQualityIssue.status)
        .where(DataQualityIssue.code == code)
        .order_by(DataQualityIssue.id)
    ).all()
    return [(k, s) for k, s in rows]


def statuses(db: Session, code: str) -> list[str]:
    return [s for _, s in issues(db, code)]


def master(db: Session, user: User, rows: list[list[object]], day: date) -> None:
    run(db, user, customers_file(rows), CUSTOMERS, snapshot_date=day)


def test_every_registered_code_has_its_presentation() -> None:
    """The screen never branches on a code: each one declares its group, meaning and action."""
    for spec in registry.all():
        assert spec.group.label, spec.code
        assert spec.description, spec.code
        assert spec.action, spec.code
        if spec.fix == "mapping":
            assert spec.mapping_entity_type in ("customer", "employee"), spec.code


def test_mapping_pending_then_confirmed_closes_seller_unmapped(db: Session, operator: User) -> None:
    master(db, operator, [customer(1, "ALMACEN UNO", seller="VENDEDOR NUEVO")], D20)
    assert statuses(db, "commercial.seller.unmapped") == ["open"]
    seller = mappings.resolve(db, mappings.MappingKey("employee", "STORE_SYS", "seller", "VENDEDOR NUEVO"))
    assert seller.mapping_id is not None
    employee = Employee(full_name="Empleado Nuevo")
    db.add(employee)
    db.commit()

    mappings.confirm(db, operator, seller.mapping_id, employee.id, reason=None, is_admin=False)
    db.commit()

    assert statuses(db, "commercial.seller.unmapped") == ["resolved"]
    [issue] = db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "commercial.seller.unmapped"))
    [view] = review.views(db, [issue])
    assert view.mapping_id == seller.mapping_id
    assert not view.by_hand


def test_zone_and_salesperson_close_when_a_later_master_brings_them(db: Session, operator: User) -> None:
    master(db, operator, [customer(1, "ALMACEN UNO", zone=None, seller=None)], D20)
    assert statuses(db, "commercial.customer.without_zone") == ["open"]
    assert statuses(db, "commercial.customer.without_salesperson") == ["open"]

    master(db, operator, [customer(1, "ALMACEN UNO", zone=None, seller=None)], D21)
    assert statuses(db, "commercial.customer.without_zone") == ["open"]  # still without: one issue, two occurrences
    [count] = db.scalars(
        select(DataQualityIssue.occurrences).where(DataQualityIssue.code == "commercial.customer.without_zone")
    )
    assert count == 2

    master(db, operator, [customer(1, "ALMACEN UNO")], D22)
    assert statuses(db, "commercial.customer.without_zone") == ["resolved"]
    assert statuses(db, "commercial.customer.without_salesperson") == ["resolved"]


def test_customer_and_product_missing_from_master_close_when_they_come_back(db: Session, operator: User) -> None:
    master(db, operator, [customer(1, "ALMACEN UNO"), customer(2, "KIOSCO DOS")], D20)
    master(db, operator, [customer(1, "ALMACEN UNO")], D21)
    assert statuses(db, "commercial.customer.missing_from_master") == ["open"]
    master(db, operator, [customer(1, "ALMACEN UNO"), customer(2, "KIOSCO DOS")], D22)
    assert statuses(db, "commercial.customer.missing_from_master") == ["resolved"]

    run(db, operator, articles_file([article(1), article(2)]), PRODUCTS, snapshot_date=D20)
    run(db, operator, articles_file([article(1)]), PRODUCTS, snapshot_date=D21)
    assert statuses(db, "commercial.product.missing_from_master") == ["open"]
    run(db, operator, articles_file([article(1), article(2)]), PRODUCTS, snapshot_date=D22)
    assert statuses(db, "commercial.product.missing_from_master") == ["resolved"]


def test_orphan_customer_code_closes_when_the_master_creates_it(db: Session, operator: User) -> None:
    run(db, operator, sales_file([sale(100, 998, "OTRO NOMBRE")]), SALES, scope="FULL")
    assert issues(db, "commercial.customer.unresolved_code") == [("customer_code:998", "open")]
    master(db, operator, [customer(998, "CLIENTE NUEVO")], D20)
    assert issues(db, "commercial.customer.unresolved_code") == [("customer_code:998", "resolved")]


def test_zone_configuration_closes_unconfigured(db: Session, operator: User, admin: User) -> None:
    master(db, operator, [customer(1, "ALMACEN UNO", zone="RUTA 03 - MISA")], D20)  # MISA has no rule
    assert statuses(db, "commercial.zone.unconfigured") == ["open"]
    zone = db.scalars(select(Zone).where(Zone.source_name == "RUTA 03 - MISA")).one()
    config.configure_zone(
        db,
        admin,
        zone.id,
        zone_type="PRESALE",
        requires_visit=True,
        weekdays=[2],
        effective_from=D20,
        display_name=None,
        active=None,
        reason="Días confirmados",
    )
    assert statuses(db, "commercial.zone.unconfigured") == ["resolved"]


def test_a_reason_with_a_category_closes_reason_unmapped(db: Session, operator: User, admin: User) -> None:
    """Correction found by the F1.6 audit: giving the reason a category never closed its issue."""
    run(db, operator, axum_file([("01", [visit(1, "ALMACEN UNO", "MOTIVO NUEVO")])]), NO_PURCHASE)
    reason = db.scalars(select(NoPurchaseReasonMapping).where(NoPurchaseReasonMapping.category_id.is_(None))).first()
    assert reason is not None
    assert issues(db, "commercial.no_purchase.reason_unmapped") == [(f"reason:{reason.id}", "open")]
    category = config.create_reason_category(db, admin, code="F16_TEST", name="Prueba F1.6")
    config.set_reason_category(db, admin, reason.id, category.id, reason="Revisado")
    assert issues(db, "commercial.no_purchase.reason_unmapped") == [(f"reason:{reason.id}", "resolved")]


def test_a_code_repeated_in_the_master_closes_when_a_later_one_brings_it_once(db: Session, operator: User) -> None:
    """Correction found by the F1.6 audit: the repetition went away but its issue stayed open."""
    run(db, operator, articles_file([article(5, "UNO"), article(5, "DOS"), article(6)]), PRODUCTS, snapshot_date=D20)
    assert issues(db, "commercial.product.duplicate_code") == [("product_code:5", "open")]
    run(db, operator, articles_file([article(5, "UNO"), article(5, "DOS"), article(6)]), PRODUCTS, snapshot_date=D21)
    assert statuses(db, "commercial.product.duplicate_code") == ["open"]  # still repeated
    run(db, operator, articles_file([article(5, "UNO"), article(6)]), PRODUCTS, snapshot_date=D22)
    assert statuses(db, "commercial.product.duplicate_code") == ["resolved"]


def test_no_automatic_close_while_an_equivalent_condition_remains(db: Session, operator: User) -> None:
    master(db, operator, [customer(1, "ALMACEN UNO", zone=None), customer(2, "KIOSCO DOS", zone=None)], D20)
    master(db, operator, [customer(1, "ALMACEN UNO"), customer(2, "KIOSCO DOS", zone=None)], D21)
    assert sorted(statuses(db, "commercial.customer.without_zone")) == ["open", "resolved"]


@pytest.mark.parametrize("decision", ["ignored", "resolved"])
def test_a_closed_issue_is_never_reopened_the_condition_opens_a_new_one(
    db: Session, operator: User, admin: User, decision: str
) -> None:
    master(db, operator, [customer(1, "ALMACEN UNO", zone=None)], D20)
    [issue_id] = db.scalars(
        select(DataQualityIssue.id).where(DataQualityIssue.code == "commercial.customer.without_zone")
    )
    review.decide(db, admin, issue_id, decision, note="Revisado con el equipo")
    db.commit()

    master(db, operator, [customer(1, "ALMACEN UNO", zone=None)], D21)

    rows = db.scalars(
        select(DataQualityIssue)
        .where(DataQualityIssue.code == "commercial.customer.without_zone")
        .order_by(DataQualityIssue.id)
    ).all()
    assert [(r.id == issue_id, r.status, r.occurrences) for r in rows] == [(True, decision, 1), (False, "open", 1)]
    assert rows[0].resolution_note == "Revisado con el equipo"  # the old one keeps its history


def test_a_manual_resolution_changes_no_business_data(db: Session, operator: User, admin: User) -> None:
    master(db, operator, [customer(1, "ALMACEN UNO", seller="VENDEDOR NUEVO")], D20)
    seller = mappings.resolve(db, mappings.MappingKey("employee", "STORE_SYS", "seller", "VENDEDOR NUEVO"))
    [issue_id] = db.scalars(select(DataQualityIssue.id).where(DataQualityIssue.code == "commercial.seller.unmapped"))
    review.decide(db, admin, issue_id, "resolved", note="Se vincula la semana próxima")
    db.commit()
    after = mappings.resolve(db, mappings.MappingKey("employee", "STORE_SYS", "seller", "VENDEDOR NUEVO"))
    assert (after.status, after.entity_id) == (seller.status, seller.entity_id) == ("pending", None)
