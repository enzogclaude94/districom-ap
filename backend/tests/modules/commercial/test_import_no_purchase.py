"""F1.4: AXUM · Compró / No compró (ADR 018, docs/IMPORTS.md §3.6, §5.4). Only the rows with a reason become
no-purchase events; a purchase stays traceable in the staging. Identical rows survive as separate events. Any overlap
with events already loaded waits for an admin's decision; a replace supersedes, never deletes."""

from datetime import date, datetime, time

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types and quality codes)
from app.core.errors import AppError
from app.modules.commercial.config.models import NoPurchaseReasonCategory, NoPurchaseReasonMapping
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.config.service import axum_customer_code_matches_store_sys, set_axum_customer_code_rule
from app.modules.commercial.imports.no_purchase import customer_key, salesperson_key
from app.modules.commercial.no_purchase.models import NoPurchaseEvent
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.customers.models import Customer
from app.platform.imports.models import ImportJob, ImportRow
from app.platform.imports.registry import Decision
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.parties.models import Employee
from app.platform.quality.models import DataQualityIssue
from tests.modules.commercial.axum import HEADER, NO_PURCHASE, axum_file, visit
from tests.modules.commercial.store_sys import CUSTOMERS, analyze, confirm, customer, customers_file, run

D20, D21, D22 = (date(2026, 10, d) for d in (20, 21, 22))
PURCHASE_TEXT = "Compró: la fila queda trazable pero no genera un evento de no compra."


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
                customer(101, "ALMACEN UNO"),
                customer(102, "KIOSCO DOS", legal="KIOSCO DOS SRL"),
                customer(103, "DESPENSA TRES"),
            ]
        ),
        CUSTOMERS,
        snapshot_date=D20,
    )
    return user  # type: ignore[no-any-return]


@pytest.fixture
def admin(make_user) -> User:  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")  # type: ignore[no-any-return]


def report(day: date = D20, *, extra: int = 0) -> bytes:
    """Salesperson 01 with a purchase and two reasons, 02 with one reason, 40 with purchases only (as in the real
    report). `extra` adds reasons for 01 on the same day: a later export of the same period."""
    first = [
        visit(101, "ALMACEN UNO", "Si", day=day),
        visit(101, "ALMACEN UNO", "TIENE STOCK", day=day, start="10:00:00", end="10:01:00", minutes="1:0"),
        visit(102, "KIOSCO DOS", "CERRADO", day=day),
        *[
            visit(103, "DESPENSA TRES", "SIN DINERO", day=day, start=f"11:0{i}:00", end=f"11:0{i}:00", minutes="0:0")
            for i in range(extra)
        ],
    ]
    return axum_file(
        [
            ("01", first),
            ("02", [visit(103, "DESPENSA TRES", "COMPRA POR BEES", day=day)]),
            (
                "40",
                [
                    visit(
                        101,
                        "ALMACEN UNO",
                        "Si",
                        day=day,
                        uploaded="16:00:00",
                        start="15:00:00",
                        end="15:00:00",
                        minutes="0:0",
                    )
                ],
            ),
        ]
    )


def events(db: Session, *, status: str = "ACTIVE") -> list[NoPurchaseEvent]:
    return list(
        db.scalars(select(NoPurchaseEvent).where(NoPurchaseEvent.status == status).order_by(NoPurchaseEvent.id))
    )


def rows(db: Session, job_id: int) -> dict[int, tuple[str, set[str]]]:
    found = db.scalars(select(ImportRow).where(ImportRow.job_id == job_id)).all()
    return {r.row_number: (r.status, {m["code"] for m in r.messages}) for r in found}


def open_issues(db: Session, code: str) -> list[DataQualityIssue]:
    return list(
        db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == code, DataQualityIssue.status == "open"))
    )


def store_sys_customer(db: Session, code: int) -> int | None:
    return mappings.resolve(db, mappings.MappingKey("customer", "STORE_SYS", "customer", str(code))).entity_id


def axum_mapping(db: Session, key: mappings.MappingKey) -> ExternalEntityMapping:
    found = mappings.resolve(db, key)
    assert found.mapping_id is not None, key
    return db.get_one(ExternalEntityMapping, found.mapping_id)


# --- the report ---------------------------------------------------------------------------------------------------


def test_the_report_is_detected_and_only_the_reasons_become_events(db: Session, operator: User) -> None:
    job = analyze(db, operator, report(), None, name="Compro no compro.xlsx")
    assert job.import_type == NO_PURCHASE
    assert (job.period_from, job.period_to) == (D20, D20)
    assert job.scope == {"days": ["2026-10-20"], "salespeople": ["01", "02", "40"], "filters_detected": []}
    preview = job.job_metadata["preview"]
    assert preview["report"] == {
        "data_rows": 5,
        "salesperson_groups": 3,
        "purchases_skipped": 2,
        "no_purchase_events": 3,
        "rejected": 0,
        "days": 1,
    }
    assert preview["replacement"]["action"] == "insert"
    assert preview["reasons"] == {
        "by_category": {"STOCK": 1, "NO_DISPONIBLE": 1, "OTRO_CANAL": 1},
        "unmapped": 0,
        "unmapped_events": 0,
    }
    assert preview["salespeople"] == {"seen": ["01", "02", "40"], "pending": ["01", "02", "40"], "pending_events": 3}

    # Header 1, group 2, data 3-5, blank 6, group 7, data 8, blank 9, group 10, data 11.
    staged = rows(db, job.id)
    assert staged[3] == ("skipped", {"purchase_reported"})
    assert staged[11] == ("skipped", {"purchase_reported"})
    assert {staged[n][0] for n in (1, 2, 6, 7, 9, 10)} == {"skipped"}
    assert staged[4][0] == "pending_resolution"
    purchase = db.scalars(select(ImportRow).where(ImportRow.job_id == job.id, ImportRow.row_number == 3)).one()
    assert {(m["code"], m["text"]) for m in purchase.messages} == {("purchase_reported", PURCHASE_TEXT)}

    job = confirm(db, operator, job)
    assert job.status == "completed_with_warnings"
    assert job.rows_inserted == 3
    loaded = events(db)
    assert [(e.source_salesperson, e.source_customer_code, e.reason_source) for e in loaded] == [
        ("01", "101", "TIENE STOCK"),
        ("01", "102", "CERRADO"),
        ("02", "103", "COMPRA POR BEES"),
    ]
    assert db.scalar(select(func.count()).select_from(Employee)) == 0
    assert db.scalar(select(func.count()).select_from(Customer)) == 3


def test_an_event_keeps_the_source_values_and_its_trace(db: Session, operator: User) -> None:
    job = run(db, operator, report(), NO_PURCHASE)
    event = events(db)[0]
    assert event.business_date == D20
    assert event.uploaded_at_source == datetime(2026, 10, 20, 12, 30)
    assert (event.start_time_source, event.end_time_source) == (time(10, 0), time(10, 1))
    assert (event.duration_source, event.duration_seconds) == ("1:0", 60)
    assert (event.source_customer_name, event.source_customer_address) == ("ALMACEN UNO", "CALLE SINTETICA 100")
    assert event.import_job_id == job.id
    row = db.get_one(ImportRow, event.import_row_id)
    assert (row.job_id, row.row_number, row.entity_id) == (job.id, 4, event.id)
    assert event.occurrence == 1
    assert (event.status, event.superseded_by_job_id) == ("ACTIVE", None)


def test_a_file_without_the_report_columns_fails(db: Session, operator: User) -> None:
    broken = axum_file([("01", [visit(101, "ALMACEN UNO")])], header=[c for c in HEADER if c != "Compro?"])
    job = analyze(db, operator, broken, NO_PURCHASE)
    assert job.status == "failed"
    assert job.failure_reason == "missing_columns"


def test_invalid_rows_are_rejected_and_never_written(db: Session, operator: User) -> None:
    data = axum_file(
        [
            (
                "01",
                [
                    ["", "", "SIN CODIGO", "X", "CERRADO", "20/10/2026 12:00:00", "09:00:00", "09:00:00", "0:0"],
                    ["", "ABC", "CODIGO MALO", "X", "CERRADO", "20/10/2026 12:00:00", "09:00:00", "09:00:00", "0:0"],
                    ["", "101", "ALMACEN UNO", "X", "", "20/10/2026 12:00:00", "09:00:00", "09:00:00", "0:0"],
                    ["", "101", "ALMACEN UNO", "X", "CERRADO", "32/10/2026 12:00:00", "09:00:00", "09:00:00", "0:0"],
                    ["", "101", "ALMACEN UNO", "X", "CERRADO", "", "09:00:00", "09:00:00", "0:0"],
                    visit(101, "ALMACEN UNO", "CERRADO"),
                ],
            ),
        ]
    )
    job = analyze(db, operator, data, NO_PURCHASE)
    staged = rows(db, job.id)
    assert staged[3] == ("rejected", {"missing_customer_code"})
    assert staged[4] == ("rejected", {"invalid_customer_code"})
    assert staged[5] == ("rejected", {"missing_result"})
    assert staged[6] == ("rejected", {"invalid_uploaded_at"})
    assert staged[7] == ("rejected", {"missing_uploaded_at"})
    assert job.job_metadata["preview"]["report"]["rejected"] == 5
    job = confirm(db, operator, job)
    assert job.rows_inserted == 1
    assert len(events(db)) == 1


def test_a_row_before_any_salesperson_group_is_rejected(db: Session, operator: User) -> None:
    import io

    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Data"
    sheet.append(HEADER)
    sheet.append(visit(101, "ALMACEN UNO", "CERRADO"))
    sheet.append(["Vendedor: 01"])
    sheet.append(visit(101, "ALMACEN UNO", "CERRADO"))
    out = io.BytesIO()
    book.save(out)
    job = analyze(db, operator, out.getvalue(), NO_PURCHASE)
    staged = rows(db, job.id)
    assert staged[2] == ("rejected", {"missing_salesperson"})
    assert staged[4][0] == "pending_resolution"


def test_time_warnings_keep_the_day_of_the_upload(db: Session, operator: User) -> None:
    """D4: the business date is the day of «Fecha y Hora Subida»; a visit that starts after the upload keeps it, with
    a warning, and its times are never combined into another day."""
    data = axum_file(
        [
            (
                "01",
                [
                    visit(
                        101,
                        "ALMACEN UNO",
                        "CERRADO",
                        uploaded="08:00:00",
                        start="13:30:00",
                        end="13:31:00",
                        minutes="1:0",
                    ),
                    visit(102, "KIOSCO DOS", "CERRADO", start="09:05:00", end="09:00:00", minutes="0:0"),
                    visit(103, "DESPENSA TRES", "CERRADO", minutes="9:99"),
                    visit(
                        101, "ALMACEN UNO", "Si", uploaded="08:00:00", start="13:30:00", end="13:30:00", minutes="0:0"
                    ),
                ],
            ),
        ]
    )
    job = analyze(db, operator, data, NO_PURCHASE)
    staged = rows(db, job.id)
    assert "start_after_upload" in staged[3][1]
    assert "end_before_start" in staged[4][1]
    assert "invalid_minutes" in staged[5][1]
    assert staged[6] == ("skipped", {"start_after_upload", "purchase_reported"})
    assert (job.job_metadata["preview"]["date_warnings"], job.job_metadata["preview"]["date_warnings_events"]) == (2, 1)
    confirm(db, operator, job)
    late = events(db)[0]
    assert late.business_date == D20
    assert late.start_time_source == time(13, 30)
    assert late.uploaded_at_source == datetime(2026, 10, 20, 8, 0)


# --- customers (D3) -----------------------------------------------------------------------------------------------


def test_a_code_and_name_that_agree_link_by_the_approved_rule(db: Session, operator: User) -> None:
    assert axum_customer_code_matches_store_sys(db) is True  # seeded with Enzo's decision (ADR 018)
    job = analyze(db, operator, report(), NO_PURCHASE)
    assert job.job_metadata["preview"]["customers"] == {
        "rule_enabled": True,
        "mapped": 0,
        "by_rule": 3,
        "pending": 0,
        "unknown_code": 0,
        "name_mismatch": 0,
        "unresolved_events": 0,
    }
    job = confirm(db, operator, job)
    for code in (101, 102, 103):
        mapping = axum_mapping(db, customer_key(str(code)))
        assert (mapping.status, mapping.method, mapping.entity_id) == (
            "confirmed",
            "rule",
            store_sys_customer(db, code),
        )
    assert {e.customer_id for e in events(db)} == {store_sys_customer(db, c) for c in (101, 102, 103)}
    linked = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "commercial.axum.customers_linked_by_rule")
    ).one()
    assert linked.payload["rule"] == "commercial.axum_customer_code_matches_store_sys"
    assert len(linked.payload["mapping_ids"]) == 3


def test_the_legal_name_also_agrees(db: Session, operator: User) -> None:
    run(db, operator, axum_file([("02", [visit(102, "KIOSCO DOS SRL", "CERRADO")])]), NO_PURCHASE)
    assert events(db)[0].customer_id == store_sys_customer(db, 102)


def test_a_name_that_contradicts_the_code_stays_pending(db: Session, operator: User) -> None:
    job = analyze(db, operator, axum_file([("01", [visit(101, "OTRO NEGOCIO", "CERRADO")])]), NO_PURCHASE)
    assert job.job_metadata["preview"]["customers"]["name_mismatch"] == 1
    assert "customer_name_mismatch" in rows(db, job.id)[3][1]
    confirm(db, operator, job)
    mapping = axum_mapping(db, customer_key("101"))
    assert (mapping.status, mapping.entity_id) == ("pending", None)
    assert store_sys_customer(db, 101) in mapping.candidates
    event = events(db)[0]
    assert event.customer_id is None
    assert event.customer_mapping_id == mapping.id
    assert len(open_issues(db, "commercial.axum_customer.name_mismatch")) == 1


def test_an_unknown_code_stays_pending_and_never_creates_a_customer(db: Session, operator: User) -> None:
    job = run(db, operator, axum_file([("01", [visit(999, "NEGOCIO NUEVO", "CERRADO")])]), NO_PURCHASE)
    assert job.job_metadata["preview"]["customers"]["unknown_code"] == 1
    event = events(db)[0]
    assert (event.customer_id, event.source_customer_code, event.source_customer_name) == (None, "999", "NEGOCIO NUEVO")
    assert axum_mapping(db, customer_key("999")).status == "pending"
    assert len(open_issues(db, "commercial.axum_customer.unresolved")) == 1
    assert db.scalar(select(func.count()).select_from(Customer)) == 3


def test_with_the_rule_off_every_code_stays_pending(db: Session, operator: User, admin: User) -> None:
    set_axum_customer_code_rule(db, admin, False, reason="Prueba sin la regla")
    job = run(db, operator, report(), NO_PURCHASE)
    assert job.job_metadata["preview"]["customers"]["pending"] == 3
    assert job.job_metadata["preview"]["customers"]["by_rule"] == 0
    assert {e.customer_id for e in events(db)} == {None}
    assert axum_mapping(db, customer_key("101")).status == "pending"
    assert not db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "commercial.axum.customers_linked_by_rule")
    ).all()


def test_an_own_axum_mapping_wins_over_the_rule(db: Session, operator: User, admin: User) -> None:
    """A confirmed `customer/AXUM/customer` mapping is used as is, even if it points elsewhere than the code."""
    mapping = mappings.observe(db, customer_key("101"), label="ALMACEN UNO", job_id=None)
    target = store_sys_customer(db, 103)
    assert target is not None
    mappings.confirm(db, admin, mapping.id, target, reason="Vinculado a mano", is_admin=True)
    db.commit()
    job = analyze(db, operator, axum_file([("01", [visit(101, "ALMACEN UNO", "CERRADO")])]), NO_PURCHASE)
    assert job.job_metadata["preview"]["customers"]["mapped"] == 1
    confirm(db, operator, job)
    assert events(db)[0].customer_id == target


def test_a_customer_is_never_resolved_by_name_alone(db: Session, operator: User) -> None:
    """The name of customer 101 under another code: no link, no fuzzy match."""
    run(db, operator, axum_file([("01", [visit(555, "ALMACEN UNO", "CERRADO")])]), NO_PURCHASE)
    assert events(db)[0].customer_id is None
    assert axum_mapping(db, customer_key("555")).entity_id is None


def test_the_seed_records_the_approved_rule_once(db: Session, operator: User) -> None:
    seed_config(db)
    created = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "commercial.config.axum_customer_code_rule_set")
    ).all()
    assert len(created) == 1
    assert created[0].payload["after"]["enabled"] is True
    assert "ADR 018" in created[0].payload["after"]["reason"]


# --- salespeople (D6) ---------------------------------------------------------------------------------------------


def test_every_salesperson_is_observed_pending_and_no_employee_is_created(db: Session, operator: User) -> None:
    run(db, operator, report(), NO_PURCHASE)
    for code in ("01", "02", "40"):  # 40 brings only purchases
        mapping = axum_mapping(db, salesperson_key(code))
        assert (mapping.status, mapping.entity_id, mapping.external_label) == ("pending", None, f"Vendedor {code}")
    assert len(open_issues(db, "commercial.axum_salesperson.unmapped")) == 3
    assert {e.salesperson_id for e in events(db)} == {None}
    assert {e.source_salesperson for e in events(db)} == {"01", "02"}
    assert db.scalar(select(func.count()).select_from(Employee)) == 0
    assert mappings.resolve(db, mappings.MappingKey("employee", "STORE_SYS", "seller", "01")).status == "new"


def test_a_confirmed_salesperson_sets_the_employee(db: Session, operator: User, admin: User) -> None:
    employee = Employee(full_name="PREVENTISTA SINTETICO")
    db.add(employee)
    db.flush()
    mapping = mappings.observe(db, salesperson_key("01"), label="Vendedor 01", job_id=None)
    mappings.confirm(db, admin, mapping.id, employee.id, reason="Prueba", is_admin=True)
    db.commit()
    run(db, operator, report(), NO_PURCHASE)
    assert {e.source_salesperson: e.salesperson_id for e in events(db)} == {"01": employee.id, "02": None}


# --- reasons ------------------------------------------------------------------------------------------------------


def test_reasons_keep_the_source_text_and_bees_is_another_channel(db: Session, operator: User) -> None:
    run(db, operator, report(), NO_PURCHASE)
    categories = {c.id: c.code for c in db.scalars(select(NoPurchaseReasonCategory))}
    assert {e.reason_source: categories[e.reason_category_id] for e in events(db) if e.reason_category_id} == {
        "TIENE STOCK": "STOCK",
        "CERRADO": "NO_DISPONIBLE",
        "COMPRA POR BEES": "OTRO_CANAL",
    }


def test_a_new_reason_is_kept_without_category_and_reported(db: Session, operator: User) -> None:
    job = analyze(db, operator, axum_file([("01", [visit(101, "ALMACEN UNO", "LLUEVE MUCHO")])]), NO_PURCHASE)
    assert job.job_metadata["preview"]["reasons"]["by_category"] == {"SIN_CATEGORIA": 1}
    assert job.job_metadata["preview"]["reasons"]["unmapped"] == 1
    confirm(db, operator, job)
    event = events(db)[0]
    assert (event.reason_source, event.reason_category_id) == ("LLUEVE MUCHO", None)
    reason = db.get_one(NoPurchaseReasonMapping, event.reason_mapping_id)
    assert reason.category_id is None
    assert len(open_issues(db, "commercial.no_purchase.reason_unmapped")) == 1


# --- occurrences --------------------------------------------------------------------------------------------------


def test_identical_rows_survive_as_separate_events(db: Session, operator: User) -> None:
    same = visit(101, "ALMACEN UNO", "CERRADO")
    job = analyze(db, operator, axum_file([("01", [same, list(same), list(same)])]), NO_PURCHASE)
    assert "identical_row" in rows(db, job.id)[4][1]
    confirm(db, operator, job)
    loaded = events(db)
    assert [e.occurrence for e in loaded] == [1, 2, 3]
    assert len({e.import_row_id for e in loaded}) == 3


def test_same_customer_day_and_reason_at_other_times_are_separate_events(db: Session, operator: User) -> None:
    data = axum_file(
        [
            (
                "01",
                [
                    visit(101, "ALMACEN UNO", "CERRADO"),
                    visit(101, "ALMACEN UNO", "CERRADO", start="16:00:00", end="16:00:00", minutes="0:0"),
                ],
            )
        ]
    )
    run(db, operator, data, NO_PURCHASE)
    assert [e.occurrence for e in events(db)] == [1, 1]


def test_an_event_is_never_deleted(db: Session, operator: User) -> None:
    run(db, operator, report(), NO_PURCHASE)
    with pytest.raises(DBAPIError, match="never deleted"):
        db.execute(text("DELETE FROM commercial.no_purchase_events"))
    db.rollback()
    assert len(events(db)) == 3


# --- re-import (D5) -----------------------------------------------------------------------------------------------


def test_the_identical_file_again_is_a_duplicate(db: Session, operator: User) -> None:
    data = report()  # the same bytes: openpyxl stamps every save, so a second report() is another file
    run(db, operator, data, NO_PURCHASE)
    again = analyze(db, operator, data, NO_PURCHASE)
    assert again.status == "duplicate"
    assert len(events(db)) == 3


def test_a_period_without_events_loads_without_a_decision(db: Session, operator: User) -> None:
    run(db, operator, report(D20), NO_PURCHASE)
    job = analyze(db, operator, report(D21), NO_PURCHASE)
    assert job.job_metadata["preview"]["replacement"]["action"] == "insert"
    confirm(db, operator, job)
    assert len(events(db)) == 6


def test_any_overlap_waits_for_an_admin(db: Session, operator: User, admin: User) -> None:
    """A later export of the same day with one more reason: nothing proves what it covers, so it never replaces nor
    adds by itself."""
    run(db, operator, report(), NO_PURCHASE)
    later = analyze(db, operator, report(extra=1), NO_PURCHASE)
    plan = later.job_metadata["preview"]["replacement"]
    assert plan["action"] == "blocked"
    assert plan["reasons"] == ["no_purchase_overlap"]
    assert plan["overlap_days"] == ["2026-10-20"]
    assert plan["lines"] == 3
    with pytest.raises(AppError) as blocked:
        confirm(db, operator, later)
    assert (blocked.value.status_code, blocked.value.code) == (409, "import_decision_required")
    with pytest.raises(AppError) as forbidden:
        confirm(db, operator, later, Decision("replace", "Export completo"))
    assert forbidden.value.status_code == 403
    with pytest.raises(AppError) as no_reason:
        confirm(db, admin, later, Decision("replace", ""))
    assert no_reason.value.code == "decision_reason_required"
    db.refresh(later)
    assert later.status == "pending"
    assert len(events(db)) == 3


def test_an_admin_replaces_and_the_old_events_are_superseded(db: Session, operator: User, admin: User) -> None:
    first = run(db, operator, report(), NO_PURCHASE)
    later = analyze(db, operator, report(extra=1), NO_PURCHASE)
    later = confirm(db, admin, later, Decision("replace", "El export nuevo es el día completo"))
    assert later.status == "completed_with_warnings"
    assert len(events(db)) == 4
    old = events(db, status="SUPERSEDED")
    assert len(old) == 3
    assert {(e.import_job_id, e.superseded_by_job_id) for e in old} == {(first.id, later.id)}
    assert db.scalar(select(func.count()).select_from(NoPurchaseEvent)) == 7  # nothing deleted
    superseded = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "commercial.no_purchase.events_superseded")
    ).one()
    assert superseded.payload["events"] == 3
    assert superseded.payload["replaced_job_ids"] == [first.id]
    decided = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "platform.import.decision_applied")).one()
    assert decided.payload["reason"] == "El export nuevo es el día completo"


def test_a_replace_keeps_the_salespeople_the_new_report_does_not_bring(
    db: Session, operator: User, admin: User
) -> None:
    run(db, operator, report(), NO_PURCHASE)
    only_01 = axum_file([("01", [visit(101, "ALMACEN UNO", "CERRADO")])])
    later = analyze(db, operator, only_01, NO_PURCHASE)
    assert later.job_metadata["preview"]["replacement"]["kept_other_salespeople"] == 1
    confirm(db, admin, later, Decision("replace", "Reemplazo del 01"))
    active = events(db)
    assert sorted((e.source_salesperson, e.reason_source) for e in active) == [
        ("01", "CERRADO"),
        ("02", "COMPRA POR BEES"),
    ]


def test_an_admin_loads_an_overlapping_file_as_additional(db: Session, operator: User, admin: User) -> None:
    run(db, operator, report(), NO_PURCHASE)
    later = analyze(db, operator, report(extra=1), NO_PURCHASE)
    confirm(db, admin, later, Decision("additional", "Visitas sincronizadas tarde"))
    assert len(events(db)) == 7
    assert events(db, status="SUPERSEDED") == []


def test_a_decision_is_refused_when_none_is_needed(db: Session, operator: User, admin: User) -> None:
    job = analyze(db, operator, report(), NO_PURCHASE)
    with pytest.raises(AppError) as refused:
        confirm(db, admin, job, Decision("replace", "Por las dudas"))
    assert (refused.value.status_code, refused.value.code) == (409, "import_decision_not_needed")
    db.refresh(job)
    assert job.status == "pending"
    assert events(db) == []


def test_a_file_of_only_purchases_creates_no_events_but_still_waits_on_an_overlap(db: Session, operator: User) -> None:
    run(db, operator, report(), NO_PURCHASE)
    purchases = axum_file([("01", [visit(101, "ALMACEN UNO", "Si", start="17:00:00", end="17:00:00", minutes="0:0")])])
    job = analyze(db, operator, purchases, NO_PURCHASE)
    assert job.job_metadata["preview"]["report"]["no_purchase_events"] == 0
    assert job.job_metadata["preview"]["replacement"]["action"] == "blocked"
    assert db.scalar(select(func.count()).select_from(ImportJob).where(ImportJob.status == "pending")) == 1
