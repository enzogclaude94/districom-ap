"""F1.2: products (source vs canonical), analytics eligibility, families and price history (DATA_MODEL §4.3).

A reimport refreshes only `source_*`; DistriCom's decisions stay. No category → not eligible. A rubro gets its
category only through an explicit mapping. Families are assigned by hand, never inferred. Prices keep history."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: quality codes)
from app.core.errors import AppError
from app.modules.commercial.catalog import service
from app.modules.commercial.catalog.models import ProductPrice
from app.modules.commercial.catalog.service import PriceOutOfOrderError, SourceProduct
from app.modules.commercial.config import service as config
from app.modules.commercial.config.seed import seed_config
from app.platform.audit.models import AuditEvent
from app.platform.quality.models import DataQualityIssue
from tests.imports.jobs import make_job


@pytest.fixture
def admin(make_user):  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")


@pytest.fixture
def jobs(db: Session, admin) -> list[int]:  # type: ignore[no-untyped-def]
    seed_config(db)
    ids = [make_job(db, admin.id) for _ in range(3)]
    db.commit()
    return ids


def category(db: Session, admin, code: str, *, eligible: bool, kind: str | None = "PRODUCT"):  # type: ignore[no-untyped-def]
    return config.create_category(
        db,
        admin,
        code=code,
        name=code.title(),
        default_product_kind=kind,
        default_analytics_eligible=eligible,
        parent_id=None,
        sort_order=0,
    )


QUILMES = SourceProduct(
    code="1001", description="QUILMES 1L RET", category="CERVEZAS", capacity="1000", pack="12", vat=Decimal("21")
)


def without_category(db: Session) -> list[DataQualityIssue]:
    return list(
        db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == "commercial.product.without_category"))
    )


def test_new_product_without_category_is_not_eligible(db: Session, jobs: list[int]) -> None:
    product, outcome = service.upsert_from_source(db, QUILMES, job_id=jobs[0])
    db.commit()
    assert outcome == "inserted"
    assert (product.source_category, product.canonical_category_id, product.category_origin) == (
        "CERVEZAS",
        None,
        "NONE",
    )
    assert (product.analytics_eligible, product.eligibility_origin, product.product_kind) == (
        False,
        "NONE",
        "UNCLASSIFIED",
    )
    assert len(without_category(db)) == 1
    with pytest.raises(IntegrityError, match="eligible_needs_category"):
        db.execute(
            text(
                "UPDATE commercial.products SET analytics_eligible = true, eligibility_origin = 'MANUAL' WHERE id = :i"
            ),
            {"i": product.id},
        )
    db.rollback()


def test_reimport_updates_source_only_and_never_canonical(db: Session, admin, jobs: list[int]) -> None:  # type: ignore[no-untyped-def]
    product, _ = service.upsert_from_source(db, QUILMES, job_id=jobs[0])
    db.commit()
    beer = category(db, admin, "CERVEZAS", eligible=True)
    family = service.create_family(db, admin, code="quilmes", name="Quilmes", description=None)
    service.set_canonical(
        db,
        admin,
        product.id,
        {"category_id": beer.id, "family_id": family.id, "product_kind": "PRODUCT"},
        reason="Clasificación inicial",
    )
    same, outcome = service.upsert_from_source(db, QUILMES, job_id=jobs[1])
    assert outcome == "unchanged"
    assert same.last_seen_job_id == jobs[1]
    changed = SourceProduct(code="1001", description="QUILMES CLASICA 1L", category="", capacity="1000", pack="12")
    updated, outcome = service.upsert_from_source(db, changed, job_id=jobs[2])
    db.commit()
    assert outcome == "updated"
    assert (updated.source_description, updated.source_category, updated.source_vat) == ("QUILMES CLASICA 1L", "", None)
    # Canonical: untouched, though the source rubro went blank.
    assert (updated.canonical_category_id, updated.category_origin, updated.family_id) == (beer.id, "MANUAL", family.id)
    assert (updated.analytics_eligible, updated.eligibility_origin) == (True, "CATEGORY_DEFAULT")
    assert (updated.first_seen_job_id, updated.last_seen_job_id) == (jobs[0], jobs[2])


def test_explicit_rubro_mapping_classifies_only_what_has_no_category(db: Session, admin, jobs: list[int]) -> None:  # type: ignore[no-untyped-def]
    beer = category(db, admin, "CERVEZAS", eligible=True)
    containers = category(db, admin, "ENVASES", eligible=False, kind="CONTAINER")
    a, _ = service.upsert_from_source(db, QUILMES, job_id=jobs[0])
    b, _ = service.upsert_from_source(
        db, SourceProduct(code="1002", description="BRAHMA", category="Cervezas"), job_id=jobs[0]
    )
    c, _ = service.upsert_from_source(
        db, SourceProduct(code="1003", description="CAJON", category="CERVEZAS"), job_id=jobs[0]
    )
    db.commit()
    service.set_canonical(db, admin, c.id, {"category_id": containers.id}, reason="Es un cajón")
    service.set_source_category(db, admin, source_category="cervezas", category_id=beer.id)
    for product in (a, b, c):
        db.refresh(product)
    assert (a.canonical_category_id, a.category_origin, a.analytics_eligible, a.product_kind) == (
        beer.id,
        "SOURCE_MAPPING",
        True,
        "PRODUCT",
    )
    assert b.canonical_category_id == beer.id  # same key: case and accents do not matter
    assert (c.canonical_category_id, c.category_origin, c.analytics_eligible, c.product_kind) == (
        containers.id,
        "MANUAL",
        False,
        "CONTAINER",
    )
    assert {i.status for i in without_category(db)} == {"resolved"}
    # A new product of a mapped rubro gets the category at import; a blank rubro never does.
    d, _ = service.upsert_from_source(db, SourceProduct(code="1004", category="CERVEZAS"), job_id=jobs[1])
    e, _ = service.upsert_from_source(db, SourceProduct(code="1005", category=None), job_id=jobs[1])
    db.commit()
    assert d.canonical_category_id == beer.id
    assert e.canonical_category_id is None
    assert service.unmapped_source_categories(db) == [("", 1)]


def test_manual_eligibility_override_and_category_rules(db: Session, admin, jobs: list[int]) -> None:  # type: ignore[no-untyped-def]
    beer = category(db, admin, "CERVEZAS", eligible=True)
    product, _ = service.upsert_from_source(db, QUILMES, job_id=jobs[0])
    db.commit()
    with pytest.raises(AppError) as exc:
        service.set_canonical(db, admin, product.id, {"analytics_eligible": True}, reason="x")
    assert exc.value.code == "eligible_needs_category"
    db.rollback()
    with pytest.raises(AppError) as exc:
        service.set_canonical(db, admin, product.id, {"category_id": beer.id}, reason=" ")
    assert exc.value.code == "reason_required"
    db.rollback()
    service.set_canonical(db, admin, product.id, {"category_id": beer.id}, reason="Cerveza")
    service.set_canonical(db, admin, product.id, {"analytics_eligible": False}, reason="Muestra gratis")
    db.refresh(product)
    assert (product.analytics_eligible, product.eligibility_origin) == (False, "MANUAL")
    # Changing the category's default never rewrites a product already decided.
    config.update_category(db, admin, beer.id, {"default_analytics_eligible": False})
    service.set_canonical(db, admin, product.id, {"category_id": None}, reason="Sin categoría")
    db.refresh(product)
    assert (product.canonical_category_id, product.category_origin, product.analytics_eligible) == (None, "NONE", False)
    [open_issue] = [i for i in without_category(db) if i.status == "open"]
    assert open_issue.entity_id == product.id
    events = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "commercial.catalog.product_updated")).all()
    assert len(events) == 3
    assert all(e.user_id == admin.id for e in events)


def test_families_are_explicit_never_inferred(db: Session, admin, jobs: list[int]) -> None:  # type: ignore[no-untyped-def]
    family = service.create_family(db, admin, code="Coca Cola", name="Coca-Cola", description="Línea Coca")
    assert family.code == "COCA_COLA"
    product, _ = service.upsert_from_source(
        db, SourceProduct(code="2001", description="COCA COLA 2.25L", category="GASEOSAS"), job_id=jobs[0]
    )
    db.commit()
    assert product.family_id is None  # the description names the family; nothing infers it
    with pytest.raises(AppError) as exc:
        service.create_family(db, admin, code="COCA COLA", name="Otra", description=None)
    assert exc.value.code == "family_code_taken"
    with pytest.raises(AppError) as exc:
        service.set_canonical(db, admin, product.id, {"family_id": 999}, reason="x")
    assert exc.value.code == "invalid_family"
    db.rollback()
    service.set_canonical(db, admin, product.id, {"family_id": family.id}, reason="Familia Coca")
    db.refresh(product)
    assert product.family_id == family.id
    service.update_family(db, admin, family.id, {"active": False})
    assert [f.active for f in service.list_families(db)] == [False]


def test_price_history(db: Session, jobs: list[int]) -> None:
    product, _ = service.upsert_from_source(db, QUILMES, job_id=jobs[0])
    one = config.price_list_by_number(db, 1)
    two = config.price_list_by_number(db, 2)
    assert one is not None
    assert two is not None
    nov1, nov5, nov9 = date(2026, 11, 1), date(2026, 11, 5), date(2026, 11, 9)
    assert service.record_price(db, product.id, one.id, Decimal("1500.00"), valid_from=nov1, job_id=jobs[0]) == "opened"
    assert service.record_price(db, product.id, two.id, Decimal("1400.00"), valid_from=nov1, job_id=jobs[0]) == "opened"
    assert (
        service.record_price(db, product.id, one.id, Decimal("1500.00"), valid_from=nov5, job_id=jobs[1]) == "unchanged"
    )
    assert (
        service.record_price(db, product.id, one.id, Decimal("1650.00"), valid_from=nov5, job_id=jobs[1]) == "changed"
    )
    # Two snapshots the same day: the row opened that day is voided, not updated nor overlapped.
    assert (
        service.record_price(db, product.id, one.id, Decimal("1600.00"), valid_from=nov5, job_id=jobs[2]) == "changed"
    )
    with pytest.raises(PriceOutOfOrderError):
        service.record_price(db, product.id, one.id, Decimal("1.00"), valid_from=nov1, job_id=jobs[2])
    db.commit()
    rows = [
        (p.price_list_id, p.price, p.valid_from, p.valid_to, p.voided_by_job_id)
        for p in db.scalars(select(ProductPrice).order_by(ProductPrice.id))
    ]
    assert rows == [
        (one.id, Decimal("1500.00"), nov1, date(2026, 11, 4), None),
        (two.id, Decimal("1400.00"), nov1, None, None),
        (one.id, Decimal("1650.00"), nov5, None, jobs[2]),
        (one.id, Decimal("1600.00"), nov5, None, None),
    ]
    assert service.price_on(db, product.id, one.id, date(2026, 11, 3)) == Decimal("1500.00")
    assert service.price_on(db, product.id, one.id, nov9) == Decimal("1600.00")
    assert service.price_on(db, product.id, two.id, nov9) == Decimal("1400.00")
    db.add(ProductPrice(product_id=product.id, price_list_id=two.id, price=1, valid_from=nov9, import_job_id=jobs[0]))
    with pytest.raises(IntegrityError, match="ex_product_prices_no_overlap"):
        db.flush()
    db.rollback()
    for table in ("product_prices", "products"):
        with pytest.raises(DBAPIError, match="never deleted"):
            db.execute(text(f"DELETE FROM commercial.{table}"))  # noqa: S608
        db.rollback()
