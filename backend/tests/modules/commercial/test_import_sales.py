"""F1.3: STORE SYS · Ventas artículos/clientes (docs/IMPORTS.md §3.3, §5.3). Lines are never deduplicated by content;
a re-import replaces by period and declared scope only when the system can prove it, otherwise an admin decides."""

from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types and quality codes)
from app.core.errors import AppError
from app.modules.commercial.catalog.service import SourceProduct, upsert_from_source
from app.modules.commercial.config.models import PriceList
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.sales.models import SaleLine
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.imports.models import ImportJob, ImportRow
from app.platform.imports.registry import Decision
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.parties.models import Employee
from app.platform.quality.models import DataQualityIssue
from tests.imports.jobs import make_job
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    SALES,
    analyze,
    confirm,
    customer,
    customers_file,
    run,
    sale,
    sales_file,
)

D20, D21, D22, D23 = (date(2026, 10, d) for d in (20, 21, 22, 23))


@pytest.fixture
def operator(db: Session, make_user, grant) -> User:  # type: ignore[no-untyped-def]
    user = make_user(email="operator@districom.test", role="viewer")
    grant("operator", "commercial", "operator")
    seed_config(db)
    db.refresh(user)
    run(
        db,
        user,
        customers_file(
            [
                customer(1, "ALMACEN UNO"),
                customer(2, "KIOSCO REPETIDO"),
                customer(3, "KIOSCO REPETIDO"),
                customer(4, "BOCA", zone="BOCA DE EXPENDIO", seller="BOCA DE EXPENDIO"),
            ]
        ),
        CUSTOMERS,
        snapshot_date=D20,
    )
    job_id = make_job(db, user.id)
    for code in ("100", "200"):
        upsert_from_source(db, SourceProduct(code=code, description=f"ARTICULO {code}"), job_id=job_id)
    db.commit()
    return user  # type: ignore[no-any-return]


@pytest.fixture
def admin(make_user) -> User:  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")  # type: ignore[no-any-return]


def lines(db: Session, *, status: str = "ACTIVE") -> list[SaleLine]:
    return list(db.scalars(select(SaleLine).where(SaleLine.status == status).order_by(SaleLine.id)))


def rows(db: Session, job_id: int) -> dict[int, tuple[str, set[str]]]:
    found = db.scalars(select(ImportRow).where(ImportRow.job_id == job_id)).all()
    return {r.row_number: (r.status, {m["code"] for m in r.messages}) for r in found}


def open_issues(db: Session, code: str) -> list[DataQualityIssue]:
    return list(
        db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == code, DataQualityIssue.status == "open"))
    )


def link_customer_name(db: Session, admin: User, name: str, code: int) -> None:
    mapping = db.scalars(
        select(ExternalEntityMapping).where(
            ExternalEntityMapping.namespace == "customer_name", ExternalEntityMapping.external_id == name
        )
    ).one()
    target = mappings.resolve(db, mappings.MappingKey("customer", "STORE_SYS", "customer", str(code))).entity_id
    assert target is not None
    mappings.confirm(db, admin, mapping.id, target, reason=None, is_admin=True)
    db.commit()


def test_the_original_export_is_detected_and_every_line_is_kept(db: Session, operator: User) -> None:
    data = sales_file(
        [
            sale(100, "ALMACEN UNO", quantity=6, price_list="LISTA 1"),
            sale(100, "ALMACEN UNO", quantity=6, price_list="LISTA 1"),  # identical: another sale
            sale(200, "ALMACEN UNO", quantity=-2),  # a return
            sale(200, "KIOSCO REPETIDO", quantity=Decimal("0.5"), price_list="LISTA 5"),
            sale(999, "CLIENTE NUEVO", quantity=1, price_list="LISTA 9", seller="VENDEDOR NUEVO", zone=None),
        ]
    )
    job = analyze(db, operator, data, None, scope="FULL")
    assert (job.status, job.import_type) == ("pending", SALES), (job.failure_reason, job.failure_detail)
    assert (job.period_from, job.period_to) == (D23, D23)
    assert job.scope is not None
    assert job.scope["declared"] == "FULL"
    assert job.scope["days"] == ["2026-10-23"]
    assert job.scope["price_lists"] == ["LISTA 1", "LISTA 5", "LISTA 9"]
    assert job.job_metadata["preview"]["replacement"]["action"] == "insert"
    statuses = rows(db, job.id)
    assert statuses[3][1] >= {"identical_row"}
    assert "customer_ambiguous" in statuses[5][1]
    assert statuses[6][0] == "pending_resolution"
    assert statuses[6][1] >= {"product_unknown", "customer_pending", "seller_unmapped", "unknown_price_list"}
    assert db.scalar(select(func.count()).select_from(SaleLine)) == 0  # analysis writes nothing

    job = confirm(db, operator, job)
    assert job.rows_inserted == 5
    found = lines(db)
    assert [(line.quantity, line.occurrence) for line in found] == [
        (Decimal("6.000"), 1),
        (Decimal("6.000"), 2),
        (Decimal("-2.000"), 1),
        (Decimal("0.500"), 1),
        (Decimal("1.000"), 1),
    ]
    lists = dict(db.execute(select(PriceList.list_number, PriceList.id)).all())
    assert [line.price_list_id for line in found] == [lists[1], lists[1], None, lists[5], None]
    assert found[4].source_price_list == "LISTA 9"  # an unknown list is never invented
    assert (found[4].product_id, found[4].source_product_code) == (None, "999")
    assert found[0].product_id is not None
    assert found[4].zone_id is None
    assert found[0].zone_id is not None
    # Customers come by name: never confirmed automatically.
    assert {line.customer_id for line in found} == {None}
    by_name = {
        m.external_id: m
        for m in db.scalars(select(ExternalEntityMapping).where(ExternalEntityMapping.namespace == "customer_name"))
    }
    assert (by_name["ALMACEN UNO"].status, len(by_name["ALMACEN UNO"].candidates)) == ("pending", 1)
    assert (by_name["KIOSCO REPETIDO"].status, len(by_name["KIOSCO REPETIDO"].candidates)) == ("ambiguous", 2)
    assert (by_name["CLIENTE NUEVO"].status, by_name["CLIENTE NUEVO"].candidates) == ("pending", [])
    assert [i.details["product_code"] for i in open_issues(db, "commercial.product.unknown")] == ["999"]
    assert len(open_issues(db, "commercial.sale_line.customer_pending")) == 3
    assert [i.details["lines"] for i in open_issues(db, "commercial.sale_line.product_pending")] == [1]
    assert db.scalar(select(func.count()).select_from(Employee)) == 0


def test_confirmed_mappings_resolve_customer_and_seller(db: Session, operator: User, admin: User) -> None:
    run(db, operator, sales_file([sale(100, "ALMACEN UNO")]), SALES, scope="FULL")
    link_customer_name(db, admin, "ALMACEN UNO", 1)
    employee = Employee(full_name="Vendedor Uno")
    db.add(employee)
    db.flush()
    seller = db.scalars(select(ExternalEntityMapping).where(ExternalEntityMapping.external_id == "VENDEDOR UNO")).one()
    mappings.confirm(db, admin, seller.id, employee.id, reason=None, is_admin=True)
    db.commit()
    job = analyze(db, operator, sales_file([sale(100, "ALMACEN UNO", day=D22)]), SALES, scope="FULL")
    assert rows(db, job.id)[2] == ("valid", set())
    confirm(db, operator, job)
    line = lines(db)[-1]
    assert (line.customer_id, line.salesperson_id, line.source_seller) == (
        mappings.resolve(db, mappings.MappingKey("customer", "STORE_SYS", "customer", "1")).entity_id,
        employee.id,
        "VENDEDOR UNO",
    )


def test_invalid_rows_are_rejected(db: Session, operator: User) -> None:
    job = analyze(
        db,
        operator,
        sales_file(
            [
                [None, "SIN CODIGO", 1, None, "ALMACEN UNO", "V", "Z", D23, None],
                [100, "X", "seis", None, "ALMACEN UNO", "V", "Z", D23, None],
                [100, "X", 1, None, None, "V", "Z", D23, None],
                [100, "X", 1, None, "ALMACEN UNO", "V", "Z", None, None],
            ]
        ),
        SALES,
    )
    statuses = rows(db, job.id)
    assert statuses[2] == ("rejected", {"missing_product_code"})
    assert statuses[3] == ("rejected", {"invalid_quantity"})
    assert statuses[4] == ("rejected", {"missing_customer"})
    assert statuses[5] == ("rejected", {"missing_business_date"})


# --- idempotency by period and scope ----------------------------------------------------------------------------


def three_days(extra: int = 0) -> bytes:
    return sales_file(
        [sale(100, "ALMACEN UNO", day=day) for day in (D20, D21, D22)]
        + [sale(200, "ALMACEN UNO", day=D22) for _ in range(extra)]
    )


def test_the_identical_file_again_is_a_duplicate(db: Session, operator: User) -> None:
    run(db, operator, three_days(), SALES, scope="FULL")
    again = analyze(db, operator, three_days(), SALES, scope="FULL")
    assert again.status == "duplicate"
    assert len(lines(db)) == 3


def test_a_full_reimport_of_the_same_period_replaces_it(db: Session, operator: User) -> None:
    first = run(db, operator, three_days(), SALES, scope="FULL")
    job = analyze(db, operator, three_days(extra=2), SALES, scope="FULL")
    replacement = job.job_metadata["preview"]["replacement"]
    assert replacement["action"] == "replace"
    assert replacement["lines"] == 3
    assert replacement["replaces"] == [
        {"job_id": first.id, "scope": "FULL", "days": ["2026-10-20", "2026-10-21", "2026-10-22"], "lines": 3}
    ]
    confirm(db, operator, job)
    assert len(lines(db)) == 5
    assert {line.import_job_id for line in lines(db)} == {job.id}
    old = lines(db, status="SUPERSEDED")
    assert len(old) == 3
    assert {line.superseded_by_job_id for line in old} == {job.id}
    event = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "commercial.sales.lines_superseded")).one()
    assert event.payload["automatic"] is True
    assert event.payload["lines"] == 3


def test_a_partially_overlapping_full_period_replaces_only_the_shared_days(db: Session, operator: User) -> None:
    run(db, operator, three_days(), SALES, scope="FULL")  # 20, 21, 22
    later = sales_file([sale(100, "ALMACEN UNO", day=day, quantity=2) for day in (D21, D22, D23)])
    job = run(db, operator, later, SALES, scope="FULL")
    assert job.status in ("completed", "completed_with_warnings")
    active = lines(db)
    assert sorted((line.business_date, line.quantity) for line in active) == [
        (D20, Decimal("1.000")),
        (D21, Decimal("2.000")),
        (D22, Decimal("2.000")),
        (D23, Decimal("2.000")),
    ]
    assert sorted(line.business_date for line in lines(db, status="SUPERSEDED")) == [D21, D22]


def test_a_day_the_new_file_does_not_cover_blocks_the_confirmation(db: Session, operator: User) -> None:
    run(db, operator, three_days(), SALES, scope="FULL")
    gap = sales_file([sale(100, "ALMACEN UNO", day=day) for day in (D20, D22)])  # nothing on the 21st
    job = analyze(db, operator, gap, SALES, scope="FULL")
    replacement = job.job_metadata["preview"]["replacement"]
    assert replacement["action"] == "blocked"
    assert (replacement["reasons"], replacement["uncovered_days"]) == (["days_without_rows"], ["2026-10-21"])
    with pytest.raises(AppError) as blocked:
        confirm(db, operator, job)
    assert (blocked.value.status_code, blocked.value.code) == (409, "import_decision_required")
    db.refresh(job)
    assert job.status == "pending"  # nothing written, still confirmable
    assert len(lines(db)) == 3


@pytest.mark.parametrize(("old_scope", "new_scope"), [("FULL", "FILTERED"), ("FILTERED", "FULL"), ("FULL", None)])
def test_a_filtered_or_undeclared_scope_never_replaces_by_itself(
    db: Session, operator: User, old_scope: str, new_scope: str | None
) -> None:
    run(
        db,
        operator,
        three_days(),
        SALES,
        scope=old_scope,
        scope_note="Solo un vendedor" if old_scope == "FILTERED" else None,
    )
    job = analyze(db, operator, three_days(extra=1), SALES, scope=new_scope)
    assert job.job_metadata["preview"]["replacement"]["action"] == "blocked"
    with pytest.raises(AppError, match="administrador"):
        confirm(db, operator, job)
    assert len(lines(db)) == 3


def test_a_filtered_export_of_new_days_loads_without_a_decision(db: Session, operator: User) -> None:
    run(db, operator, three_days(), SALES, scope="FULL")
    job = run(db, operator, sales_file([sale(100, "ALMACEN UNO", day=D23)]), SALES, scope="FILTERED")
    assert job.rows_inserted == 1
    assert len(lines(db)) == 4


def test_an_admin_decides_a_blocked_confirmation(db: Session, operator: User, admin: User) -> None:
    run(db, operator, three_days(), SALES, scope="FULL")
    job = analyze(db, operator, three_days(extra=1), SALES, scope="FILTERED", scope_note="Solo VENDEDOR UNO")
    with pytest.raises(AppError) as forbidden:
        confirm(db, operator, job, Decision("replace", "Reemplazo pedido"))
    assert forbidden.value.status_code == 403
    with pytest.raises(AppError) as no_reason:
        confirm(db, admin, job, Decision("replace", ""))
    assert no_reason.value.code == "decision_reason_required"

    job = confirm(db, admin, job, Decision("replace", "El export filtrado es el día completo"))
    assert job.status in ("completed", "completed_with_warnings")
    assert len(lines(db)) == 4
    assert len(lines(db, status="SUPERSEDED")) == 3
    decided = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "platform.import.decision_applied")).one()
    assert decided.payload == {
        "import_type": SALES,
        "action": "replace",
        "reason": "El export filtrado es el día completo",
    }


def test_an_admin_loads_a_blocked_file_as_additional(db: Session, operator: User, admin: User) -> None:
    run(db, operator, three_days(), SALES, scope="FULL")
    extra = sales_file([sale(200, "ALMACEN UNO", day=D21)])
    job = analyze(db, operator, extra, SALES, scope="FILTERED", scope_note="Ventas cargadas tarde")
    confirm(db, admin, job, Decision("additional", "No se superpone: ventas cargadas tarde"))
    assert len(lines(db)) == 4
    assert lines(db, status="SUPERSEDED") == []


def test_a_decision_is_refused_when_none_is_needed(db: Session, operator: User, admin: User) -> None:
    job = analyze(db, operator, three_days(), SALES, scope="FULL")
    with pytest.raises(AppError) as refused:
        confirm(db, admin, job, Decision("replace", "Por las dudas"))
    assert (refused.value.status_code, refused.value.code) == (409, "import_decision_not_needed")
    db.refresh(job)
    assert job.status == "pending"
    assert db.scalar(select(func.count()).select_from(ImportJob).where(ImportJob.status == "pending")) == 1
