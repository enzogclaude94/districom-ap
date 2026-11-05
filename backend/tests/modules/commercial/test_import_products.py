"""F1.3: STORE SYS · Maestro de artículos (docs/IMPORTS.md §3.5). `codart` is the identity; a re-import refreshes only
`source_*`; the export has no rubro, so products start without category; `precio` feeds only the confirmed list, with
history; a product missing from the snapshot is kept and flagged."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types and quality codes)
from app.modules.commercial.catalog import service as catalog
from app.modules.commercial.catalog.models import Product, ProductPrice
from app.modules.commercial.config.models import PriceList, ProductCategory
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.imports.products import PRICE_LIST_SETTING
from app.modules.commercial.sales.models import SaleLine
from app.platform.auth.models import User
from app.platform.imports.models import ImportRow
from app.platform.quality.models import DataQualityIssue
from app.platform.settings.service import set_setting
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    PRODUCTS,
    SALES,
    analyze,
    article,
    articles_file,
    confirm,
    customer,
    customers_file,
    run,
    sale,
    sales_file,
)

D1, D2, D3 = date(2026, 10, 20), date(2026, 10, 23), date(2026, 10, 27)


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


def price_list_is(db: Session, admin: User, number: int | None) -> None:
    set_setting(db, PRICE_LIST_SETTING, number, user_id=admin.id)
    db.commit()


def product(db: Session, code: str) -> Product:
    return db.scalars(select(Product).where(Product.source_code == code)).one()


def prices(db: Session, code: str) -> list[tuple[Decimal, date, date | None, bool]]:
    found = db.scalars(
        select(ProductPrice).where(ProductPrice.product_id == product(db, code).id).order_by(ProductPrice.id)
    ).all()
    return [(p.price, p.valid_from, p.valid_to, p.voided_by_job_id is not None) for p in found]


def rows(db: Session, job_id: int) -> dict[int, tuple[str, set[str]]]:
    found = db.scalars(select(ImportRow).where(ImportRow.job_id == job_id)).all()
    return {r.row_number: (r.status, {m["code"] for m in r.messages}) for r in found}


def open_issues(db: Session, code: str) -> list[DataQualityIssue]:
    return list(
        db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == code, DataQualityIssue.status == "open"))
    )


def test_the_original_export_is_detected_and_creates_products_without_category(db: Session, operator: User) -> None:
    data = articles_file(
        [article(187, "AGUA SINTETICA 2L X6", Decimal("8100")), article(9000102, "GASEOSA SINTETICA 1.5 X8")]
    )
    job = analyze(db, operator, data, None, snapshot_date=D1)
    assert (job.status, job.import_type, job.snapshot_date) == ("pending", PRODUCTS, D1), job.failure_detail
    preview = job.job_metadata["preview"]
    assert preview["products"] == {"new": 2, "updated": 0, "unchanged": 0}
    # `precio` feeds Lista 1: confirmed by Enzo and loaded by the seed as an audited setting, not by the export.
    assert preview["prices"] == {"rows_with_price": 2, "price_list_number": 1, "loaded": True}
    job = confirm(db, operator, job)
    assert job.rows_inserted == 2
    agua = product(db, "187")
    assert (agua.source_description, agua.source_category) == ("AGUA SINTETICA 2L X6", None)
    # Nothing is read out of the description: no category, kind, family, capacity or pack.
    assert (agua.canonical_category_id, agua.analytics_eligible, agua.product_kind) == (None, False, "UNCLASSIFIED")
    assert (agua.family_id, agua.source_capacity, agua.source_pack, agua.source_extra) == (None, None, None, {})
    assert {i.entity_id for i in open_issues(db, "commercial.product.without_category")} == {
        agua.id,
        product(db, "9000102").id,
    }
    lista1 = db.scalars(select(PriceList).where(PriceList.list_number == 1)).one()
    assert [(p.price_list_id, p.price) for p in db.scalars(select(ProductPrice).order_by(ProductPrice.id))] == [
        (lista1.id, Decimal("8100.00")),
        (lista1.id, Decimal("1000.00")),
    ]
    assert open_issues(db, "commercial.product.prices_not_loaded") == []


def test_the_seed_sets_lista_1_once_with_its_reason_and_never_undoes_a_decision(
    db: Session, operator: User, admin: User
) -> None:
    from app.platform.audit.models import AuditEvent
    from app.platform.settings.models import AppSetting

    setting = db.get_one(AppSetting, PRICE_LIST_SETTING)
    assert (setting.value, setting.updated_by) == (1, None)
    assert "Lista 1" in (setting.description or "")
    event = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "commercial.config.articles_price_list_set")
    ).one()
    assert event.payload["after"]["list_number"] == 1
    # An admin stops loading those prices; running the seed again leaves that decision.
    price_list_is(db, admin, None)
    assert seed_config(db).settings == 0
    job = run(db, operator, articles_file([article(1)]), PRODUCTS, snapshot_date=D1)
    assert job.job_metadata["preview"]["prices"] == {"rows_with_price": 1, "price_list_number": None, "loaded": False}
    assert db.scalar(select(func.count()).select_from(ProductPrice)) == 0
    assert len(open_issues(db, "commercial.product.prices_not_loaded")) == 1


def test_prices_feed_the_confirmed_list_and_keep_their_history(db: Session, operator: User, admin: User) -> None:
    price_list_is(db, admin, 1)
    run(db, operator, articles_file([article(1, price=Decimal("100.00")), article(2)]), PRODUCTS, snapshot_date=D1)
    lista1 = db.scalars(select(PriceList).where(PriceList.list_number == 1)).one()
    assert {p.price_list_id for p in db.scalars(select(ProductPrice))} == {lista1.id}
    assert prices(db, "1") == [(Decimal("100.00"), D1, None, False)]

    same = run(
        db, operator, articles_file([article(1, price=Decimal("100.00")), article(2)]), PRODUCTS, snapshot_date=D2
    )
    assert (same.rows_unchanged, same.rows_updated) == (2, 0)
    assert prices(db, "1") == [(Decimal("100.00"), D1, None, False)]  # the same price changes nothing

    changed = run(
        db, operator, articles_file([article(1, price=Decimal("120.50")), article(2)]), PRODUCTS, snapshot_date=D3
    )
    assert changed.rows_updated == 1
    assert prices(db, "1") == [(Decimal("100.00"), D1, date(2026, 10, 26), False), (Decimal("120.50"), D3, None, False)]
    # A second photo of the same day with another price voids the first one of that day, never edits it.
    run(
        db,
        operator,
        articles_file([article(1, price=Decimal("125.00")), article(2), [None] * 3]),
        PRODUCTS,
        snapshot_date=D3,
    )
    assert prices(db, "1")[1:] == [(Decimal("120.50"), D3, None, True), (Decimal("125.00"), D3, None, False)]
    assert open_issues(db, "commercial.product.prices_not_loaded") == []


def test_a_list_that_is_not_configured_is_never_invented(db: Session, operator: User, admin: User) -> None:
    price_list_is(db, admin, 9)
    job = run(db, operator, articles_file([article(1)]), PRODUCTS, snapshot_date=D1)
    assert job.job_metadata["preview"]["prices"]["loaded"] is False
    assert db.scalar(select(func.count()).select_from(PriceList).where(PriceList.list_number == 9)) == 0
    assert db.scalar(select(func.count()).select_from(ProductPrice)) == 0


def test_a_reimport_refreshes_only_source_fields_and_never_canonical_decisions(
    db: Session, operator: User, admin: User
) -> None:
    run(db, operator, articles_file([article(1, "CERVEZA SINTETICA 1L X12")]), PRODUCTS, snapshot_date=D1)
    category = db.scalars(select(ProductCategory).order_by(ProductCategory.id)).first()
    assert category is not None
    catalog.set_canonical(
        db,
        admin,
        product(db, "1").id,
        {"category_id": category.id, "product_kind": "PRODUCT", "analytics_eligible": True},
        reason="Clasificado por el admin",
    )
    job = run(db, operator, articles_file([article(1, "CERVEZA SINTETICA 1 LITRO X12")]), PRODUCTS, snapshot_date=D2)
    assert job.rows_updated == 1
    db.expire_all()
    cerveza = product(db, "1")
    assert cerveza.source_description == "CERVEZA SINTETICA 1 LITRO X12"
    assert (cerveza.canonical_category_id, cerveza.category_origin) == (category.id, "MANUAL")
    assert (cerveza.product_kind, cerveza.analytics_eligible, cerveza.eligibility_origin) == ("PRODUCT", True, "MANUAL")
    assert db.scalar(select(func.count()).select_from(Product)) == 1


def test_a_repeated_code_rejects_all_its_rows_and_opens_an_issue(db: Session, operator: User) -> None:
    job = run(
        db, operator, articles_file([article(5, "UNO"), article(5, "DOS"), article(6)]), PRODUCTS, snapshot_date=D1
    )
    assert [rows(db, job.id)[n] for n in (2, 3)] == [("rejected", {"duplicate_code"})] * 2
    assert db.scalars(select(Product.source_code)).all() == ["6"]
    assert [i.external_ref["external_id"] for i in open_issues(db, "commercial.product.duplicate_code")] == ["5"]


def test_invalid_and_doubtful_rows(db: Session, operator: User, admin: User) -> None:
    price_list_is(db, admin, 1)
    job = run(
        db,
        operator,
        articles_file(
            [
                article(None),
                article("ABC"),
                article(10, price=Decimal("-5")),
                article(11, price=0),
                article(12, price=None),
                article(13, price="N/D"),
                article(14, description=None),
                article(15, price=Decimal("10.005")),
            ]
        ),
        PRODUCTS,
        snapshot_date=D1,
    )
    statuses = rows(db, job.id)
    assert statuses[2] == ("rejected", {"missing_code"})
    assert statuses[3] == ("rejected", {"invalid_code"})
    assert statuses[4] == ("warning", {"invalid_price"})
    assert statuses[5] == ("warning", {"zero_price"})
    assert statuses[6] == ("valid", {"without_price"})
    assert statuses[7] == ("warning", {"invalid_price"})
    assert statuses[8] == ("warning", {"without_description"})
    assert statuses[9] == ("warning", {"invalid_price"})
    # The product is kept; only a price that is valid reaches the history (0 is what STORE SYS says).
    assert sorted(db.scalars(select(Product.source_code))) == ["10", "11", "12", "13", "14", "15"]
    assert prices(db, "11") == [(Decimal("0.00"), D1, None, False)]
    assert prices(db, "10") == prices(db, "12") == prices(db, "13") == prices(db, "15") == []


def test_a_product_missing_from_the_snapshot_is_kept_and_flagged(db: Session, operator: User) -> None:
    run(db, operator, articles_file([article(1), article(2)]), PRODUCTS, snapshot_date=D1)
    job = run(db, operator, articles_file([article(1)]), PRODUCTS, snapshot_date=D2)
    assert job.job_metadata["preview"]["missing_from_master"] == 1
    assert product(db, "2").active
    assert [i.entity_id for i in open_issues(db, "commercial.product.missing_from_master")] == [product(db, "2").id]
    run(db, operator, articles_file([article(1), article(2)]), PRODUCTS, snapshot_date=D3)
    assert open_issues(db, "commercial.product.missing_from_master") == []


def test_an_older_snapshot_is_refused(db: Session, operator: User) -> None:
    run(db, operator, articles_file([article(1)]), PRODUCTS, snapshot_date=D2)
    job = analyze(db, operator, articles_file([article(1), article(2)]), PRODUCTS, snapshot_date=D1)
    assert (job.status, job.failure_reason) == ("failed", "snapshot_out_of_order")


def test_columns_outside_the_contract_are_kept_as_source_extra(db: Session, operator: User) -> None:
    data = articles_file(
        [[1, "ARTICULO", Decimal("10"), "7790000000000"]], header=["codart", "desart", "precio", "codbar"]
    )
    job = run(db, operator, data, PRODUCTS, snapshot_date=D1)
    assert job.job_metadata["preview"]["source_columns_without_contract"] == ["codbar"]
    assert (product(db, "1").source_extra, product(db, "1").source_barcode) == ({"codbar": "7790000000000"}, None)


def test_sales_lines_find_the_products_of_the_master(db: Session, operator: User) -> None:
    run(db, operator, customers_file([customer(1, "ALMACEN UNO")]), CUSTOMERS, snapshot_date=D1)
    run(db, operator, articles_file([article(100)]), PRODUCTS, snapshot_date=D1)
    run(
        db,
        operator,
        sales_file([sale(100, "ALMACEN UNO", day=D2), sale(999, "ALMACEN UNO", day=D2)]),
        SALES,
        scope="FULL",
    )
    found = {line.source_product_code: line.product_id for line in db.scalars(select(SaleLine))}
    assert found == {"100": product(db, "100").id, "999": None}


def test_an_admin_confirms_the_list_of_precio_with_a_reason(client, db: Session, admin: User, make_user, grant) -> None:  # type: ignore[no-untyped-def]
    from app.platform.audit.models import AuditEvent
    from tests.conftest import PASSWORD

    seed_config(db)
    make_user(email="operator@districom.test", role="viewer")
    grant("operator", "commercial", "operator")
    url = "/api/v1/commercial/price-lists/articles-master"

    def login(email: str) -> None:
        client.post("/api/v1/auth/logout")
        assert client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200

    login("operator@districom.test")
    assert client.get(url).json() == {"list_number": 1}  # the seed's confirmed value
    assert client.put(url, json={"list_number": 1, "reason": "Confirmado"}).status_code == 403
    login("admin@districom.test")
    assert client.put(url, json={"list_number": 1, "reason": " "}).json()["code"] == "reason_required"
    assert client.put(url, json={"list_number": 9, "reason": "No existe"}).json()["code"] == "price_list_not_found"
    response = client.put(url, json={"list_number": 2, "reason": "Cambio de lista, decidido por Enzo"})
    assert (response.status_code, response.json()) == (200, {"list_number": 2})
    events = db.scalars(
        select(AuditEvent)
        .where(AuditEvent.event_type == "commercial.config.articles_price_list_set")
        .order_by(AuditEvent.id)
    ).all()
    assert [e.payload["after"]["list_number"] for e in events] == [1, 2]  # the seed's, then the admin's
    assert events[-1].payload["before"] == {"list_number": 1}
    assert events[-1].payload["after"] == {"list_number": 2, "reason": "Cambio de lista, decidido por Enzo"}
    assert client.put(url, json={"list_number": None, "reason": "Se deja de cargar"}).json() == {"list_number": None}
