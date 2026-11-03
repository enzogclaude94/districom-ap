"""F1.2: commercial configuration (DATA_MODEL §4.2, Enzo 2026-11-03).

Zones: LUVI/Lumivi, MAJU/Majusa, Bandera, Boca de Expendio without visits, MISA unclassified; a rule configures
a zone once and never overwrites an explicit configuration; visit days keep their history. Price lists 1 to 5.
Document types as confirmed; a new one stays outside the commercial universe. Seed idempotent."""

from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: quality codes)
from app.core.errors import AppError
from app.modules.commercial.config import service
from app.modules.commercial.config.models import (
    DocumentType,
    NoPurchaseReasonMapping,
    PriceList,
    ZoneClassificationRule,
    ZoneVisitDay,
)
from app.modules.commercial.config.seed import seed_config
from app.platform.audit.models import AuditEvent
from app.platform.quality.models import DataQualityIssue

DAY = date(2026, 11, 2)  # a Monday


@pytest.fixture
def admin(make_user):  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")


@pytest.fixture
def seeded(db: Session) -> None:
    seed_config(db)


def zone(db: Session, name: str):  # type: ignore[no-untyped-def]
    created, _ = service.ensure_zone(db, name, effective_from=DAY, job_id=None, user_id=None)
    db.commit()
    return created


def days(db: Session, zone_id: int, on: date = DAY) -> list[int]:
    return service.current_weekdays(db, zone_id, on)


def issues(db: Session, code: str) -> list[DataQualityIssue]:
    return list(db.scalars(select(DataQualityIssue).where(DataQualityIssue.code == code)))


# --- seed -------------------------------------------------------------------------------------------------------


def test_seed_is_idempotent_and_never_overwrites_an_edit(db: Session, admin) -> None:  # type: ignore[no-untyped-def]
    first = seed_config(db)
    assert (first.zone_rules, first.price_lists, first.document_types) == (6, 5, 9)
    assert (first.reason_categories, first.reason_mappings) == (5, 6)
    bandera = db.scalars(select(PriceList).where(PriceList.list_number == 5)).one()
    service.update_price_list(db, admin, bandera.id, {"name": "Bandera (editada)"})
    again = seed_config(db)
    assert again.total() == 0
    db.refresh(bandera)
    assert bandera.name == "Bandera (editada)"
    assert db.scalar(select(text("count(*)")).select_from(PriceList)) == 5
    assert db.scalars(select(AuditEvent.event_type).where(AuditEvent.event_type == "commercial.config.seeded")).all()


def test_no_rule_for_misa(db: Session, seeded: None) -> None:
    patterns = set(db.scalars(select(ZoneClassificationRule.pattern)))
    assert patterns == {"BOCA DE EXPENDIO", "BANDERA", "LUVI", "LUMIVI", "MAJU", "MAJUSA"}


# --- zones ------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "zone_type", "visit", "weekdays"),
    [
        ("RUTA 01 - LUVI", "PRESALE", True, [1, 3, 5]),
        ("Ruta 02 - Lumivi", "PRESALE", True, [1, 3, 5]),
        ("RUTA 04 - MAJU", "PRESALE", True, [2, 4, 5]),
        ("RUTA 06 - MAJUSA", "PRESALE", True, [2, 4, 5]),
        ("RUTA 07 - TACAÑITAS/BANDERA", "BANDERA", True, [4]),
        ("RUTA 08 - BOCA DE EXPENDIO", "OUTLET", False, []),
    ],
)
def test_rules_configure_a_new_zone(  # type: ignore[no-untyped-def]
    db: Session, seeded: None, name, zone_type, visit, weekdays
) -> None:
    created = zone(db, name)
    assert (created.zone_type, created.requires_visit, created.configuration_origin) == (zone_type, visit, "RULE")
    assert created.rule_id is not None
    assert days(db, created.id) == weekdays
    assert issues(db, "commercial.zone.unconfigured") == []


def test_misa_and_unknown_zones_stay_unclassified_with_an_issue(db: Session, seeded: None) -> None:
    misa = zone(db, "RUTA 03 - MISA")
    assert (misa.zone_type, misa.requires_visit, misa.configuration_origin, misa.rule_id) == (
        "UNCLASSIFIED",
        False,
        "NONE",
        None,
    )
    assert days(db, misa.id) == []
    zone(db, "RUTA 09 - LUVIANO")  # whole words: LUVI does not match LUVIANO
    assert len(issues(db, "commercial.zone.unconfigured")) == 2
    again, created = service.ensure_zone(db, "ruta 03 -  misa", effective_from=DAY, job_id=None, user_id=None)
    assert (again.id, created) == (misa.id, False)  # same key, same zone


def test_a_rule_never_changes_an_existing_zone(db: Session, seeded: None, admin) -> None:  # type: ignore[no-untyped-def]
    misa = zone(db, "RUTA 03 - MISA")
    luvi = zone(db, "RUTA 01 - LUVI")
    service.create_rule(
        db, admin, priority=5, pattern="MISA", zone_type="PRESALE", requires_visit=True, weekdays=[2], description=None
    )
    rule = db.scalars(select(ZoneClassificationRule).where(ZoneClassificationRule.pattern == "LUVI")).one()
    service.update_rule(db, admin, rule.id, {"weekdays": [2, 4]})
    db.refresh(misa)
    db.refresh(luvi)
    assert misa.zone_type == "UNCLASSIFIED"
    assert days(db, misa.id) == []
    assert days(db, luvi.id) == [1, 3, 5]
    # Only zones created afterwards take the new rule.
    assert days(db, zone(db, "RUTA 05 - MISA").id) == [2]
    assert days(db, zone(db, "RUTA 02 - LUVI").id) == [2, 4]


def test_manual_configuration_wins_and_keeps_history(db: Session, seeded: None, admin) -> None:  # type: ignore[no-untyped-def]
    luvi = zone(db, "RUTA 01 - LUVI")
    later = date(2026, 11, 10)
    configured = service.configure_zone(
        db,
        admin,
        luvi.id,
        zone_type="PRESALE",
        requires_visit=True,
        weekdays=[1, 4],
        effective_from=later,
        display_name="Lumivi",
        active=None,
        reason="Cambio de recorrido",
    )
    assert (configured.configuration_origin, configured.configured_by, configured.display_name) == (
        "MANUAL",
        admin.id,
        "Lumivi",
    )
    assert days(db, luvi.id, DAY) == [1, 3, 5]  # before: as the rule set it
    assert days(db, luvi.id, later) == [1, 4]
    history = {(d.weekday, d.valid_from, d.valid_to) for d in service.visit_day_history(db, luvi.id)}
    assert history == {
        (1, DAY, None),  # Monday goes on: the same row stays open
        (3, DAY, date(2026, 11, 9)),
        (5, DAY, date(2026, 11, 9)),
        (4, later, None),
    }
    # Same day again: the row that had just opened is voided, never updated nor overlapped.
    service.configure_zone(
        db,
        admin,
        luvi.id,
        zone_type="PRESALE",
        requires_visit=True,
        weekdays=[1],
        effective_from=later,
        display_name=None,
        active=None,
        reason="Corrección",
    )
    assert days(db, luvi.id, later) == [1]
    voided = db.scalars(select(ZoneVisitDay).where(ZoneVisitDay.voided_at.is_not(None))).all()
    assert [(d.weekday, d.valid_from) for d in voided] == [(4, later)]
    # History is written forward only: a change dated before the last one is refused, not overlapped.
    with pytest.raises(AppError) as exc:
        service.configure_zone(
            db,
            admin,
            luvi.id,
            zone_type="PRESALE",
            requires_visit=True,
            weekdays=[2],
            effective_from=DAY,
            display_name=None,
            active=None,
            reason="Fecha vieja",
        )
    assert exc.value.code == "zone_change_out_of_order"
    db.rollback()
    # The rule is still there, but the zone is MANUAL now: a new rule or re-seeding never touches it.
    seed_config(db)
    db.refresh(luvi)
    assert luvi.configuration_origin == "MANUAL"
    assert days(db, luvi.id, later) == [1]
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type == "commercial.config.zone_configured").order_by(AuditEvent.id)
    ).all()
    assert len(events) == 2
    assert events[0].payload["before"]["weekdays"] == [1, 3, 5]
    assert events[0].payload["after"]["weekdays"] == [1, 4]


def test_configuring_misa_resolves_its_issue_and_rejects_inconsistent_settings(  # type: ignore[no-untyped-def]
    db: Session, seeded: None, admin
) -> None:
    misa = zone(db, "RUTA 03 - MISA")
    boca = zone(db, "RUTA 08 - BOCA DE EXPENDIO")
    common = {"effective_from": DAY, "display_name": None, "active": None, "reason": "x"}
    for kwargs, code in (
        ({"zone_type": "OUTLET", "requires_visit": True, "weekdays": [1]}, "zone_without_visit"),
        ({"zone_type": "UNCLASSIFIED", "requires_visit": True, "weekdays": [1]}, "zone_without_visit"),
        ({"zone_type": "PRESALE", "requires_visit": True, "weekdays": []}, "visit_days_required"),
        ({"zone_type": "PRESALE", "requires_visit": False, "weekdays": [2]}, "visit_days_without_visit"),
        ({"zone_type": "PRESALE", "requires_visit": True, "weekdays": [8]}, "invalid_weekday"),
        ({"zone_type": "ROUTE", "requires_visit": False, "weekdays": []}, "invalid_zone_type"),
    ):
        with pytest.raises(AppError) as exc:
            service.configure_zone(db, admin, boca.id, **kwargs, **common)  # type: ignore[arg-type]
        assert exc.value.code == code
        db.rollback()
    with pytest.raises(AppError) as exc:
        service.configure_zone(
            db,
            admin,
            misa.id,
            zone_type="PRESALE",
            requires_visit=True,
            weekdays=[2],
            effective_from=DAY,
            display_name=None,
            active=None,
            reason=" ",
        )
    assert exc.value.code == "reason_required"
    db.rollback()
    service.configure_zone(
        db,
        admin,
        misa.id,
        zone_type="PRESALE",
        requires_visit=True,
        weekdays=[2, 4],
        effective_from=DAY,
        display_name=None,
        active=None,
        reason="Enzo definió los días",
    )
    [issue] = issues(db, "commercial.zone.unconfigured")
    assert issue.status == "resolved"
    assert issue.resolved_by == admin.id


def test_zone_constraints_and_no_deletes(db: Session, seeded: None) -> None:
    boca = zone(db, "RUTA 08 - BOCA DE EXPENDIO")
    luvi = zone(db, "RUTA 01 - LUVI")
    for statement, constraint in (
        ("UPDATE commercial.zones SET requires_visit = true WHERE id = :i", "ck_zones_visit_by_type"),
        ("UPDATE commercial.zones SET configuration_origin = 'NONE' WHERE id = :i", "origin_none_unconfigured"),
        ("UPDATE commercial.zones SET configuration_origin = 'MANUAL' WHERE id = :i", "manual_has_author"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            db.execute(text(statement), {"i": boca.id})
        db.rollback()
    db.add(ZoneVisitDay(zone_id=luvi.id, weekday=1, valid_from=date(2026, 12, 1)))
    with pytest.raises(IntegrityError, match="ex_zone_visit_days_no_overlap"):
        db.flush()
    db.rollback()
    for table, row in (("zones", boca.id), ("zone_visit_days", 1)):
        with pytest.raises(DBAPIError, match="never deleted"):
            db.execute(text(f"DELETE FROM commercial.{table} WHERE id = :i"), {"i": row})  # noqa: S608
        db.rollback()


# --- price lists ------------------------------------------------------------------------------------------------


def test_price_lists_one_to_five_and_extensible(db: Session, seeded: None, admin) -> None:  # type: ignore[no-untyped-def]
    lists = [(p.list_number, p.code, p.name, p.kind) for p in service.list_price_lists(db)]
    assert lists == [
        (1, "LISTA_1", "Preventa oficial", "PRESALE_OFFICIAL"),
        (2, "LISTA_2", "Boca de Expendio", "OUTLET"),
        (3, "LISTA_3", "Promo 3+ bultos", "PROMO_VOLUME"),
        (4, "LISTA_4", "Especiales", "SPECIAL"),
        (5, "LISTA_5", "Bandera", "BANDERA"),
    ]
    six = service.create_price_list(
        db, admin, list_number=6, name="Lista 6", kind="UNASSIGNED", commercially_relevant=False, description=None
    )
    assert (six.code, six.kind) == ("LISTA_6", "UNASSIGNED")
    with pytest.raises(AppError) as exc:
        service.create_price_list(
            db, admin, list_number=6, name="Otra", kind="SPECIAL", commercially_relevant=True, description=None
        )
    assert exc.value.code == "price_list_taken"


# --- document types ---------------------------------------------------------------------------------------------


def test_document_types_as_confirmed(db: Session, seeded: None) -> None:
    rows = {t.source_name: (t.document_class, t.sign, t.commercial_universe) for t in service.list_document_types(db)}
    assert rows == {
        "FACTURA A": ("INVOICE", 1, True),
        "FACTURA B": ("INVOICE", 1, True),
        "NOTA DE CREDITO A": ("CREDIT_NOTE", -1, True),
        "DEVOLUCION X": ("RETURN", -1, True),
        "RETIRO PR.": ("INTERNAL_MOVEMENT", 0, False),
        "RECIBOS X": ("RECEIPT", 0, False),
        "VALE PERSONAL V": ("EMPLOYEE_VOUCHER", 0, False),
        "COMP. INTERNO X": ("UNCLASSIFIED", 0, False),
        "FACTURA": ("UNCLASSIFIED", 0, False),
    }


def test_a_new_type_stays_outside_until_an_admin_classifies_it(db: Session, seeded: None, admin) -> None:  # type: ignore[no-untyped-def]
    found, created = service.ensure_document_type(db, "Nota de Débito A", job_id=None)
    db.commit()
    assert created
    assert (found.document_class, found.sign, found.commercial_universe) == ("UNCLASSIFIED", 0, False)
    assert service.ensure_document_type(db, "NOTA DE DEBITO A", job_id=None) == (found, False)
    [issue] = issues(db, "commercial.document.unclassified_type")
    assert issue.status == "open"
    with pytest.raises(AppError) as exc:
        service.classify_document_type(
            db, admin, found.id, document_class="DEBIT_NOTE", sign=0, commercial_universe=True, reason="x"
        )
    assert exc.value.code == "universe_needs_sign"
    with pytest.raises(AppError) as exc:
        service.classify_document_type(
            db, admin, found.id, document_class="UNCLASSIFIED", sign=1, commercial_universe=True, reason="x"
        )
    assert exc.value.code == "unclassified_outside"
    classified = service.classify_document_type(
        db, admin, found.id, document_class="DEBIT_NOTE", sign=1, commercial_universe=True, reason="Confirmado"
    )
    assert (classified.decided_by, classified.commercial_universe) == (admin.id, True)
    db.refresh(issue)
    assert issue.status == "resolved"
    with pytest.raises(IntegrityError, match="unclassified_outside"):
        db.execute(
            text("UPDATE commercial.document_types SET commercial_universe = true WHERE source_name = 'FACTURA'")
        )
    db.rollback()
    with pytest.raises(DBAPIError, match="never deleted"):
        db.execute(text("DELETE FROM commercial.document_types"))
    db.rollback()


# --- no-purchase reasons ----------------------------------------------------------------------------------------


def test_reasons_seeded_and_new_ones_arrive_without_category(db: Session, seeded: None, admin) -> None:  # type: ignore[no-untyped-def]
    mapped = {m.reason_source: m.category_id is not None for m in service.list_reason_mappings(db)}
    assert mapped == dict.fromkeys(
        ["TIENE STOCK", "CERRADO", "NO ESTA EL ENCARGADO", "VACACIONES", "COMPRA POR BEES", "SIN DINERO"], True
    )
    new, created = service.ensure_reason(db, "AXUM", "Precio alto")
    db.commit()
    assert created
    assert new.category_id is None
    assert service.ensure_reason(db, "AXUM", "  precio  ALTO ")[0].id == new.id
    other = next(c for c in service.list_reason_categories(db) if c.code == "OTRO")
    service.set_reason_category(db, admin, new.id, other.id, reason=None)
    row = db.get(NoPurchaseReasonMapping, new.id)
    assert row is not None
    assert row.category_id == other.id
    assert row.decided_by == admin.id


def test_document_type_lookup_is_by_normalized_key(db: Session, seeded: None) -> None:
    found = service.find_document_type(db, "  retiro   pr. ")
    assert isinstance(found, DocumentType)
    assert found.document_class == "INTERNAL_MOVEMENT"
