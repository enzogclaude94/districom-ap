"""F1.3: the identity of an import (docs/IMPORTS.md §5.1) is sha256 + type + the declarations the type names. A
master is a dated snapshot: the same file as another day's photo is a new import. The sales export's declared scope
decides what it replaces: the same file with another scope is a new import, so a wrong declaration can be corrected.
Types that name nothing keep sha256 + type (F1.1b)."""

from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types and quality codes)
from app.core.clock import business_today
from app.modules.commercial.assignments.models import CustomerSalespersonAssignment, CustomerZoneAssignment
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.sales.models import SaleLine
from app.platform.auth.models import User
from app.platform.customers.models import Customer
from app.platform.imports.registry import Decision, ImportTypeSpec
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    DOCUMENTS,
    PRODUCTS,
    SALES,
    analyze,
    article,
    articles_file,
    confirm,
    customer,
    customers_file,
    document,
    documents_file,
    run,
    sale,
    sales_file,
)

D1, D2 = date(2026, 10, 20), date(2026, 10, 23)
MASTER = customers_file([customer(1, "ALMACEN UNO"), customer(2, "KIOSCO DOS", zone="RUTA 02 - MAJU")])


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


def history(db: Session) -> tuple[int, int]:
    return (
        db.scalar(select(func.count()).select_from(CustomerZoneAssignment)) or 0,
        db.scalar(select(func.count()).select_from(CustomerSalespersonAssignment)) or 0,
    )


def test_the_same_master_with_the_same_date_is_a_duplicate(db: Session, operator: User) -> None:
    first = run(db, operator, MASTER, CUSTOMERS, snapshot_date=D1)
    again = analyze(db, operator, MASTER, CUSTOMERS, snapshot_date=D1)
    assert (again.status, again.duplicate_of_job_id) == ("duplicate", first.id)
    assert again.job_metadata["identity"] == {"snapshot_date": D1.isoformat()}


def test_the_same_master_with_a_new_date_is_a_new_snapshot(db: Session, operator: User) -> None:
    run(db, operator, MASTER, CUSTOMERS, snapshot_date=D1)
    before = history(db)
    job = analyze(db, operator, MASTER, CUSTOMERS, snapshot_date=D2)
    assert job.status == "pending"
    job = confirm(db, operator, job)
    assert job.status in ("completed", "completed_with_warnings")  # sellers stay unmapped
    assert (job.rows_unchanged, job.rows_inserted, job.rows_updated) == (2, 0, 0)
    # The new photo says the customers were still there on D2, without a second customer or a false history.
    assert db.scalar(select(func.count()).select_from(Customer)) == 2
    assert {c.last_seen_on for c in db.scalars(select(Customer))} == {D2}
    assert {c.last_seen_job_id for c in db.scalars(select(Customer))} == {job.id}
    assert history(db) == before
    assert {a.valid_to for a in db.scalars(select(CustomerZoneAssignment))} == {None}


def test_an_undeclared_snapshot_is_the_upload_day_and_counts_for_the_identity(db: Session, operator: User) -> None:
    first = run(db, operator, MASTER, CUSTOMERS)
    assert first.snapshot_date == business_today()
    assert first.job_metadata["defaulted"] == ["snapshot_date"]
    assert analyze(db, operator, MASTER, CUSTOMERS).status == "duplicate"
    assert analyze(db, operator, MASTER, CUSTOMERS, snapshot_date=business_today()).status == "duplicate"


def test_the_articles_master_is_a_snapshot_too(db: Session, operator: User) -> None:
    data = articles_file([article(1), article(2)])
    run(db, operator, data, PRODUCTS, snapshot_date=D1)
    assert analyze(db, operator, data, PRODUCTS, snapshot_date=D1).status == "duplicate"
    job = run(db, operator, data, PRODUCTS, snapshot_date=D2)
    assert (job.status, job.rows_unchanged) == ("completed", 2)


def test_the_same_sales_file_is_a_duplicate_only_with_the_same_scope(db: Session, operator: User, admin: User) -> None:
    run(db, operator, customers_file([customer(1, "ALMACEN UNO")]), CUSTOMERS, snapshot_date=D1)
    data = sales_file([sale(100, "ALMACEN UNO", day=D2), sale(200, "ALMACEN UNO", day=D2)])
    first = run(db, operator, data, SALES, scope="FILTERED", scope_note="Por error")
    assert analyze(db, operator, data, SALES, scope="FILTERED", scope_note="Otra nota").status == "duplicate"

    # The scope was declared wrong: the same file declared FULL is a new import, and replacing what the FILTERED
    # one loaded is an admin's decision (never automatic).
    corrected = analyze(db, operator, data, SALES, scope="FULL")
    assert corrected.status == "pending"
    assert corrected.job_metadata["identity"] == {"scope": "FULL"}
    assert corrected.job_metadata["preview"]["replacement"]["reasons"] == ["previous_scope_not_full"]
    confirm(db, admin, corrected, Decision("replace", "El export no tenía filtros"))
    assert {line.import_job_id for line in db.scalars(select(SaleLine).where(SaleLine.status == "ACTIVE"))} == {
        corrected.id
    }
    assert (
        db.scalar(select(func.count()).select_from(SaleLine).where(SaleLine.superseded_by_job_id == corrected.id)) == 2
    )
    assert analyze(db, operator, data, SALES, scope="FULL").status == "duplicate"
    # An undeclared scope is its own declaration: it matches only another undeclared one.
    assert analyze(db, operator, data, SALES).status == "pending"
    assert first.status in ("completed", "completed_with_warnings")


def test_types_without_declarations_keep_sha256_and_type(db: Session, operator: User) -> None:
    data = documents_file([document("FACTURA A 0003-00000001", code=0)])
    run(db, operator, data, DOCUMENTS)
    # A declaration the type does not use changes nothing: the same file is the same import.
    assert analyze(db, operator, data, DOCUMENTS, snapshot_date=D1, scope="FULL").status == "duplicate"
    assert analyze(db, operator, data, DOCUMENTS, name="otro-nombre.xls").status == "duplicate"


def test_an_unknown_identity_declaration_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="unknown identity"):
        ImportTypeSpec("commercial.x.y", "X", "STORE_SYS", object(), identity=("file_name",))  # type: ignore[arg-type]
