"""F1.3: STORE SYS · Clientes (docs/IMPORTS.md §3.2). The code is the identity, the file is a dated snapshot:
assignments keep their history, nobody absent is deactivated, sellers are labels until someone maps them."""

import json
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types and quality codes)
from app.modules.commercial.assignments.models import CustomerSalespersonAssignment, CustomerZoneAssignment
from app.modules.commercial.config.models import Zone, ZoneVisitDay
from app.modules.commercial.config.seed import seed_config
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.customers.models import Customer
from app.platform.imports.models import ImportJob, ImportRow
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.parties.models import Employee
from app.platform.quality.models import DataQualityIssue
from app.platform.third_parties.tax_ids import check_digit
from tests.modules.commercial.store_sys import CUSTOMERS, NO_CUIT, analyze, confirm, customer, customers_file, run

D1, D2, D3 = date(2026, 10, 20), date(2026, 10, 23), date(2026, 10, 27)


def cuit(first_ten: str) -> str:
    digit = check_digit(first_ten)
    assert digit is not None
    return f"{first_ten[:2]}-{first_ten[2:]}-{digit}"


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


def by_code(db: Session, code: int) -> Customer:
    resolution = mappings.resolve(db, mappings.MappingKey("customer", "STORE_SYS", "customer", str(code)))
    assert resolution.resolved
    return db.get_one(Customer, resolution.entity_id)


def zones_of(db: Session, customer_id: int) -> list[tuple[str, date, date | None, bool]]:
    rows = db.execute(
        select(Zone.source_name, CustomerZoneAssignment.valid_from, CustomerZoneAssignment.valid_to)
        .join(Zone, Zone.id == CustomerZoneAssignment.zone_id)
        .add_columns(CustomerZoneAssignment.voided_by_job_id.is_not(None))
        .where(CustomerZoneAssignment.customer_id == customer_id)
        .order_by(CustomerZoneAssignment.id)
    ).all()
    return [tuple(r) for r in rows]  # type: ignore[misc]


def sellers_of(db: Session, customer_id: int) -> list[tuple[str | None, int | None, date, date | None]]:
    rows = db.execute(
        select(
            CustomerSalespersonAssignment.source_seller_label,
            CustomerSalespersonAssignment.salesperson_id,
            CustomerSalespersonAssignment.valid_from,
            CustomerSalespersonAssignment.valid_to,
        )
        .where(
            CustomerSalespersonAssignment.customer_id == customer_id,
            CustomerSalespersonAssignment.voided_by_job_id.is_(None),
        )
        .order_by(CustomerSalespersonAssignment.id)
    ).all()
    return [tuple(r) for r in rows]  # type: ignore[misc]


def open_issues(db: Session, code: str) -> list[DataQualityIssue]:
    return list(
        db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == code, DataQualityIssue.status == "open"))
    )


def row_status(db: Session, job_id: int) -> dict[int, tuple[str, str | None]]:
    rows = db.execute(
        select(ImportRow.row_number, ImportRow.status, ImportRow.outcome).where(ImportRow.job_id == job_id)
    ).all()
    return {n: (s, o) for n, s, o in rows}


def message_codes(db: Session, job_id: int, row_number: int) -> set[str]:
    row = db.scalars(select(ImportRow).where(ImportRow.job_id == job_id, ImportRow.row_number == row_number)).one()
    return {m["code"] for m in row.messages}


def test_the_original_export_is_detected_and_creates_customers(db: Session, operator: User) -> None:
    data = customers_file(
        [
            customer(1, "ALMACEN UNO", cuit=cuit("2012345678"), legal="UNO SRL", phone="(0381) 15-555"),
            customer(2, "KIOSCO DOS", zone="RUTA 03 - MISA", seller="BOCA DE EXPENDIO"),
            customer(3, "BOCA CENTRO", zone="BOCA DE EXPENDIO", seller=None),
        ]
    )
    job = analyze(db, operator, data, None, snapshot_date=D1)
    assert (job.status, job.import_type) == ("pending", CUSTOMERS), (job.failure_reason, job.failure_detail)
    assert job.snapshot_date == D1
    preview = job.job_metadata["preview"]
    assert preview["customers"] == {"new": 3, "updated": 0, "unchanged": 0}
    assert preview["new_zones"] == ["BOCA DE EXPENDIO", "RUTA 01 - LUVI", "RUTA 03 - MISA"]
    assert db.scalar(select(func.count()).select_from(Customer)) == 0  # analysis writes nothing

    job = confirm(db, operator, job)
    assert job.status == "completed_with_warnings"
    assert (job.rows_inserted, job.rows_updated, job.rows_unchanged) == (3, 0, 0)
    one = by_code(db, 1)
    assert (one.display_name, one.legal_name, one.phone) == ("ALMACEN UNO", "UNO SRL", "038115555")
    assert one.tax_id == cuit("2012345678").replace("-", "")
    assert zones_of(db, one.id) == [("RUTA 01 - LUVI", D1, None, False)]
    assert sellers_of(db, one.id) == [("VENDEDOR UNO", None, D1, None)]

    luvi, misa, boca = (
        db.scalars(select(Zone).where(Zone.source_name == n)).one()
        for n in ("RUTA 01 - LUVI", "RUTA 03 - MISA", "BOCA DE EXPENDIO")
    )
    assert (luvi.zone_type, luvi.requires_visit) == ("PRESALE", True)
    assert sorted(db.scalars(select(ZoneVisitDay.weekday).where(ZoneVisitDay.zone_id == luvi.id))) == [1, 3, 5]
    assert (misa.zone_type, misa.configuration_origin) == ("UNCLASSIFIED", "NONE")
    assert [i.entity_id for i in open_issues(db, "commercial.zone.unconfigured")] == [misa.id]
    assert (boca.zone_type, boca.requires_visit) == ("OUTLET", False)
    assert db.scalar(select(func.count()).where(ZoneVisitDay.zone_id == boca.id)) == 0

    sellers = db.scalars(
        select(ExternalEntityMapping)
        .where(ExternalEntityMapping.namespace == "seller")
        .order_by(ExternalEntityMapping.id)
    ).all()
    assert [(m.external_id, m.status, m.entity_id) for m in sellers] == [
        ("VENDEDOR UNO", "pending", None),
        ("BOCA DE EXPENDIO", "pending", None),
    ]
    assert len(open_issues(db, "commercial.seller.unmapped")) == 2
    assert [i.entity_id for i in open_issues(db, "commercial.customer.without_salesperson")] == [by_code(db, 3).id]
    assert db.scalar(select(func.count()).select_from(Employee)) == 0  # never creates employees
    assert row_status(db, job.id)[2] == ("pending_resolution", "inserted")


def test_reimport_same_content_is_unchanged_and_changes_update(db: Session, operator: User) -> None:
    rows = [customer(1, "ALMACEN UNO"), customer(2, "KIOSCO DOS")]
    run(db, operator, customers_file(rows), CUSTOMERS, snapshot_date=D1)
    # The same snapshot in another file (another byte layout): nothing changes.
    again = run(db, operator, customers_file([*rows, [None] * 8]), CUSTOMERS, snapshot_date=D2)
    assert (again.rows_inserted, again.rows_updated, again.rows_unchanged) == (0, 0, 2)
    assert zones_of(db, by_code(db, 1).id) == [("RUTA 01 - LUVI", D1, None, False)]

    changed = run(
        db, operator, customers_file([customer(1, "ALMACEN UNO NUEVO"), rows[1]]), CUSTOMERS, snapshot_date=D3
    )
    assert (changed.rows_updated, changed.rows_unchanged) == (1, 1)
    assert by_code(db, 1).display_name == "ALMACEN UNO NUEVO"
    assert db.scalar(select(func.count()).select_from(Customer)) == 2


def test_cuit_is_not_identity(db: Session, operator: User) -> None:
    shared = cuit("3071234567")
    job = run(
        db,
        operator,
        customers_file(
            [
                customer(1, "SUCURSAL A", cuit=shared),
                customer(2, "SUCURSAL B", cuit=shared),
                customer(3, "SIN CUIT", cuit=NO_CUIT),
                customer(4, "VACIO", cuit=None),
                customer(5, "INVALIDO", cuit="20-1234567-1"),
            ]
        ),
        CUSTOMERS,
        snapshot_date=D1,
    )
    assert job.rows_inserted == 5
    assert by_code(db, 1).tax_id == by_code(db, 2).tax_id
    assert by_code(db, 1).id != by_code(db, 2).id
    assert (by_code(db, 3).tax_id, by_code(db, 4).tax_id, by_code(db, 5).tax_id) == (None, None, None)
    assert "invalid_tax_id" in message_codes(db, job.id, 6)


def test_repeated_name_creates_distinct_customers(db: Session, operator: User) -> None:
    run(db, operator, customers_file([customer(1, "KIOSCO"), customer(2, "KIOSCO")]), CUSTOMERS, snapshot_date=D1)
    assert by_code(db, 1).id != by_code(db, 2).id


def test_repeated_code_in_the_file_rejects_all_its_rows(db: Session, operator: User) -> None:
    job = analyze(db, operator, customers_file([customer(7, "A"), customer(7, "B"), customer(8, "C")]), CUSTOMERS)
    statuses = row_status(db, job.id)
    assert (statuses[2][0], statuses[3][0], statuses[4][0]) == ("rejected", "rejected", "pending_resolution")
    assert "duplicate_code" in message_codes(db, job.id, 2)


def test_zone_and_seller_changes_close_and_open_history(db: Session, operator: User) -> None:
    run(db, operator, customers_file([customer(1, "A")]), CUSTOMERS, snapshot_date=D1)
    run(
        db,
        operator,
        customers_file([customer(1, "A", zone="RUTA 02 - MAJU", seller="VENDEDOR DOS")]),
        CUSTOMERS,
        snapshot_date=D2,
    )
    one = by_code(db, 1)
    assert zones_of(db, one.id) == [
        ("RUTA 01 - LUVI", D1, date(2026, 10, 22), False),
        ("RUTA 02 - MAJU", D2, None, False),
    ]
    assert sellers_of(db, one.id) == [
        ("VENDEDOR UNO", None, D1, date(2026, 10, 22)),
        ("VENDEDOR DOS", None, D2, None),
    ]


def test_two_snapshots_of_the_same_day_void_the_first_change(db: Session, operator: User) -> None:
    run(db, operator, customers_file([customer(1, "A")]), CUSTOMERS, snapshot_date=D1)
    run(db, operator, customers_file([customer(1, "A", zone="RUTA 02 - MAJU")]), CUSTOMERS, snapshot_date=D2)
    run(db, operator, customers_file([customer(1, "A", zone="RUTA 05 - BANDERA")]), CUSTOMERS, snapshot_date=D2)
    assert zones_of(db, by_code(db, 1).id) == [
        ("RUTA 01 - LUVI", D1, date(2026, 10, 22), False),
        ("RUTA 02 - MAJU", D2, None, True),
        ("RUTA 05 - BANDERA", D2, None, False),
    ]


def test_an_older_snapshot_is_refused(db: Session, operator: User) -> None:
    run(db, operator, customers_file([customer(1, "A")]), CUSTOMERS, snapshot_date=D2)
    job = analyze(db, operator, customers_file([customer(1, "A", zone="RUTA 02 - MAJU")]), CUSTOMERS, snapshot_date=D1)
    assert (job.status, job.failure_reason) == ("failed", "snapshot_out_of_order")
    assert zones_of(db, by_code(db, 1).id) == [("RUTA 01 - LUVI", D2, None, False)]


def test_empty_zone_or_seller_closes_the_assignment_and_opens_an_issue(db: Session, operator: User) -> None:
    run(db, operator, customers_file([customer(1, "A")]), CUSTOMERS, snapshot_date=D1)
    job = run(db, operator, customers_file([customer(1, "A", zone=None, seller=None)]), CUSTOMERS, snapshot_date=D2)
    one = by_code(db, 1)
    assert job.rows_updated == 1
    assert zones_of(db, one.id) == [("RUTA 01 - LUVI", D1, date(2026, 10, 22), False)]
    assert sellers_of(db, one.id) == [("VENDEDOR UNO", None, D1, date(2026, 10, 22))]
    assert [i.entity_id for i in open_issues(db, "commercial.customer.without_zone")] == [one.id]
    assert [i.entity_id for i in open_issues(db, "commercial.customer.without_salesperson")] == [one.id]
    # Back with a zone and a seller: the issues close. (Another file: the identical one would be a duplicate.)
    run(db, operator, customers_file([customer(1, "A"), [None] * 8]), CUSTOMERS, snapshot_date=D3)
    assert open_issues(db, "commercial.customer.without_zone") == []
    assert open_issues(db, "commercial.customer.without_salesperson") == []


def test_mapped_and_excluded_sellers(db: Session, operator: User, admin: User) -> None:
    run(
        db,
        operator,
        customers_file([customer(1, "A"), customer(2, "B", seller="BOCA DE EXPENDIO")]),
        CUSTOMERS,
        snapshot_date=D1,
    )
    employee = Employee(full_name="Vendedor Uno")
    db.add(employee)
    db.flush()
    seller = db.scalars(select(ExternalEntityMapping).where(ExternalEntityMapping.external_id == "VENDEDOR UNO")).one()
    boca = db.scalars(
        select(ExternalEntityMapping).where(ExternalEntityMapping.external_id == "BOCA DE EXPENDIO")
    ).one()
    mappings.confirm(db, admin, seller.id, employee.id, reason=None, is_admin=True)
    mappings.exclude(db, admin, boca.id, reason="Pseudo-vendedor de la boca", is_admin=True)
    db.commit()

    job = analyze(
        db,
        operator,
        customers_file([customer(1, "A"), customer(2, "B", seller="BOCA DE EXPENDIO"), customer(3, "C")]),
        CUSTOMERS,
        snapshot_date=D2,
    )
    statuses = row_status(db, job.id)
    assert (statuses[2][0], statuses[3][0], statuses[4][0]) == ("valid", "valid", "valid")
    confirm(db, operator, job)
    assert sellers_of(db, by_code(db, 3).id) == [("VENDEDOR UNO", employee.id, D2, None)]
    assert sellers_of(db, by_code(db, 2).id) == [("BOCA DE EXPENDIO", None, D1, None)]
    assert db.get_one(Employee, employee.id).external_store_id is None  # never written by Comercial


def test_a_customer_missing_from_the_snapshot_is_kept_and_flagged(db: Session, operator: User) -> None:
    run(db, operator, customers_file([customer(1, "A"), customer(2, "B")]), CUSTOMERS, snapshot_date=D1)
    run(db, operator, customers_file([customer(1, "A")]), CUSTOMERS, snapshot_date=D2)
    two = by_code(db, 2)
    assert two.active
    assert zones_of(db, two.id) == [("RUTA 01 - LUVI", D1, None, False)]
    assert [i.entity_id for i in open_issues(db, "commercial.customer.missing_from_master")] == [two.id]
    run(db, operator, customers_file([customer(1, "A"), customer(2, "B"), [None] * 8]), CUSTOMERS, snapshot_date=D3)
    assert open_issues(db, "commercial.customer.missing_from_master") == []


def test_no_customer_data_reaches_audit_events(db: Session, operator: User) -> None:
    secret = "ALMACEN DOÑA ROSA"
    run(
        db,
        operator,
        customers_file([customer(1, secret, cuit=cuit("2012345678"), address="SECRETA 99", phone="3815550000")]),
        CUSTOMERS,
        snapshot_date=D1,
    )
    payloads = json.dumps([e.payload for e in db.scalars(select(AuditEvent))], ensure_ascii=False)
    issues = json.dumps([[i.details, i.external_ref] for i in db.scalars(select(DataQualityIssue))], ensure_ascii=False)
    for value in (secret, "SECRETA", "3815550000", cuit("2012345678").replace("-", "")):
        assert value not in payloads
        assert value not in issues
    assert db.scalar(select(func.count()).select_from(ImportJob)) == 1
