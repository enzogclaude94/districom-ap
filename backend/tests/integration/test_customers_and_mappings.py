"""F1.2: platform customers and the mapping core (ADR 014 §2.6, docs/IMPORTS.md §6).

Covers: CUIT optional, validated, not unique; no name matching beyond exact candidates; resolution
existing / exact / pending / ambiguous; decisions manual / rejected / excluded / reassigned with their permissions;
every decision audited without labels; constraints and no deletes."""

import json
from datetime import date

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: entity types, quality codes)
from app.core.errors import AppError
from app.platform.audit.models import AuditEvent
from app.platform.customers import service as customers
from app.platform.customers.models import Customer
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.mappings.service import MappingKey
from app.platform.third_parties.tax_ids import check_digit
from tests.imports.jobs import make_job

TODAY = date(2026, 11, 3)


def cuit(body: str) -> str:
    return body + str(check_digit(body))


VALID = cuit("2012345678")


@pytest.fixture
def admin(make_user):  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")


@pytest.fixture
def job(db: Session, admin) -> int:  # type: ignore[no-untyped-def]
    job_id = make_job(db, admin.id)
    db.commit()
    return job_id


def new_customer(db: Session, job: int, name: str, tax_id: str | None = None) -> Customer:
    record = customers.normalize(customers.MasterRecord(display_name=name, tax_id=tax_id))
    customer = customers.create_from_master(db, record, job_id=job, seen_on=TODAY)
    db.commit()
    return customer


# --- customers --------------------------------------------------------------------------------------------------


def test_tax_id_is_optional_validated_and_not_unique(db: Session, job: int) -> None:
    assert customers.normalize_tax_id("  -        -") == (None, None)  # STORE SYS's "no CUIT"
    assert customers.normalize_tax_id(VALID[:2] + "-" + VALID[2:10] + "-" + VALID[10]) == (VALID, None)
    assert customers.normalize_tax_id("20-12345678-0" if VALID[-1] != "0" else "20-12345678-1") == (
        None,
        "invalid_tax_id",
    )
    assert customers.normalize_tax_id("123") == (None, "invalid_tax_id")
    first = new_customer(db, job, "Almacén Doña Rosa", VALID)
    second = new_customer(db, job, "Kiosco Rosa (sucursal)", VALID)  # same CUIT, other customer: allowed
    assert first.id != second.id
    assert first.tax_id == second.tax_id == VALID
    with pytest.raises(IntegrityError, match="ck_customers_tax_id_valid"):
        db.execute(text("UPDATE customers SET tax_id = '20123456780' WHERE id = :i"), {"i": first.id})
    db.rollback()


def test_master_refresh_never_touches_decided_flags_and_names_never_match_fuzzily(db: Session, admin, job) -> None:  # type: ignore[no-untyped-def]
    rosa = new_customer(db, job, "Almacén  Doña Rosa")
    new_customer(db, job, "ALMACEN DOÑA ROSITA")
    customers.set_flags(db, admin, rosa.id, is_generic=True, active=None, reason="Consumidor final de mostrador")
    record = customers.normalize(customers.MasterRecord(display_name="Almacen Doña Rosa", phone="(0385) 15-555"))
    assert customers.update_from_master(db, rosa, record, job_id=job, seen_on=TODAY) is True
    db.commit()
    db.refresh(rosa)
    assert rosa.is_generic is True
    assert rosa.phone == "038515555"
    # Exact key only: accents, case and spaces ignored; ROSITA is not ROSA; no similarity.
    assert customers.exact_name_candidates(db, "almacen dona rosa") == [rosa.id]
    assert customers.exact_name_candidates(db, "Almacén Doña Ros") == []


def test_customer_flags_need_a_reason_and_audit_carries_no_pii(db: Session, admin, job) -> None:  # type: ignore[no-untyped-def]
    rosa = new_customer(db, job, "Almacén Doña Rosa", VALID)
    with pytest.raises(AppError) as exc:
        customers.set_flags(db, admin, rosa.id, is_generic=None, active=False, reason="  ")
    assert exc.value.code == "reason_required"
    customers.set_flags(db, admin, rosa.id, is_generic=None, active=False, reason="Cerró el local")
    event = db.scalars(select(AuditEvent).where(AuditEvent.event_type == "platform.customer.updated")).one()
    payload = json.dumps(event.payload, ensure_ascii=False)
    assert "Rosa" not in payload
    assert VALID not in payload
    assert customers.exact_name_candidates(db, "Almacén Doña Rosa") == []  # inactive is no candidate


# --- mappings: resolution ---------------------------------------------------------------------------------------


def seller(code: str) -> MappingKey:
    return MappingKey("employee", "STORE_SYS", "seller", mappings.code_key(code))


def name_of(label: str) -> MappingKey:
    return MappingKey("customer", "STORE_SYS", "customer_name", mappings.label_key(label))


def test_resolution_new_exact_pending_ambiguous_and_existing(db: Session, admin, job: int) -> None:  # type: ignore[no-untyped-def]
    rosa = new_customer(db, job, "Almacén Doña Rosa")
    twin_a = new_customer(db, job, "Kiosco Sol")
    twin_b = new_customer(db, job, "KIOSCO SOL")
    code_key = MappingKey("customer", "STORE_SYS", "customer", "1001")
    assert mappings.resolve(db, code_key).status == "new"  # read only: nothing written
    assert db.scalar(select(ExternalEntityMapping.id)) is None

    exact = mappings.observe(db, code_key, label=None, job_id=job, entity_id=rosa.id, method="created_from_master")
    one = mappings.observe(
        db,
        name_of("almacen dona rosa"),
        label="almacen dona rosa",
        job_id=job,
        candidates=customers.exact_name_candidates(db, "almacen dona rosa"),
    )
    two = mappings.observe(
        db,
        name_of("Kiosco Sol"),
        label="Kiosco Sol",
        job_id=job,
        candidates=customers.exact_name_candidates(db, "Kiosco Sol"),
    )
    none = mappings.observe(db, name_of("Desconocido"), label="Desconocido", job_id=job)
    db.commit()
    assert (exact.status, exact.entity_id, exact.method) == ("confirmed", rosa.id, "created_from_master")
    # One exact candidate is a proposal, never a confirmation.
    assert (one.status, one.entity_id, one.candidates) == ("pending", None, [rosa.id])
    assert (two.status, two.candidates) == ("ambiguous", sorted([twin_a.id, twin_b.id]))
    assert (none.status, none.candidates) == ("pending", [])

    resolved = mappings.resolve_many(db, [code_key, name_of("Kiosco Sol"), seller("99")])
    assert resolved[code_key].resolved
    assert resolved[code_key].entity_id == rosa.id
    assert resolved[name_of("Kiosco Sol")].status == "ambiguous"
    assert resolved[seller("99")].status == "new"

    # Seen again by a later import, with other data: status, entity and candidates stay; label and job refresh.
    later = make_job(db, admin.id)
    again = mappings.observe(db, name_of("Kiosco Sol"), label="KIOSCO SOL", job_id=later, candidates=[rosa.id])
    db.commit()
    assert (again.id, again.status, again.candidates) == (two.id, "ambiguous", sorted([twin_a.id, twin_b.id]))
    assert (again.external_label, again.first_seen_job_id, again.last_seen_job_id) == ("KIOSCO SOL", job, later)


def test_an_import_cannot_confirm_by_hand_nor_without_entity(db: Session, job: int) -> None:
    with pytest.raises(ValueError, match="cannot confirm"):
        mappings.observe(db, seller("7"), label=None, job_id=job, entity_id=1, method="manual")
    with pytest.raises(ValueError, match="both the entity"):
        mappings.observe(db, seller("7"), label=None, job_id=job, method="exact_external_id")
    with pytest.raises(ValueError, match="Unknown mapping entity type"):
        mappings.observe(db, MappingKey("vehicle", "STORE_SYS", "x", "1"), label=None, job_id=job)


# --- mappings: decisions ----------------------------------------------------------------------------------------


@pytest.fixture
def operator(make_user):  # type: ignore[no-untyped-def]
    return make_user(email="operator@districom.test", role="viewer")


def test_manual_confirm_reject_exclude_reassign_and_their_rules(db: Session, admin, operator, job) -> None:  # type: ignore[no-untyped-def]
    rosa = new_customer(db, job, "Almacén Doña Rosa")
    sol = new_customer(db, job, "Kiosco Sol")
    pending = mappings.observe(db, name_of("Rosa"), label="Rosa", job_id=job, candidates=[rosa.id])
    pseudo = mappings.observe(db, name_of("VARIOS"), label="VARIOS", job_id=job)
    db.commit()

    rejected = mappings.reject(db, operator, pending.id, reason=None)
    assert (rejected.status, rejected.entity_id) == ("rejected", None)
    confirmed = mappings.confirm(db, operator, pending.id, rosa.id, reason=None, is_admin=False)
    assert (confirmed.status, confirmed.entity_id, confirmed.method) == ("confirmed", rosa.id, "manual")
    assert confirmed.decided_by == operator.id
    assert confirmed.decided_at is not None
    with pytest.raises(AppError) as exc:
        mappings.confirm(db, operator, pending.id, sol.id, reason=None, is_admin=False)
    assert exc.value.code == "mapping_already_confirmed"
    with pytest.raises(AppError) as exc:
        mappings.reassign(db, admin, pending.id, sol.id, reason=None)
    assert exc.value.code == "reason_required"
    moved = mappings.reassign(db, admin, pending.id, sol.id, reason="Era el kiosco de enfrente")
    assert moved.entity_id == sol.id
    with pytest.raises(AppError) as exc:
        mappings.exclude(db, operator, pending.id, reason="no", is_admin=False)
    assert exc.value.status_code == 403  # unlinking a confirmed one is an admin's

    with pytest.raises(AppError) as exc:
        mappings.exclude(db, operator, pseudo.id, reason=None, is_admin=False)
    assert exc.value.code == "reason_required"
    excluded = mappings.exclude(db, operator, pseudo.id, reason="Pseudo cliente de mostrador", is_admin=False)
    assert (excluded.status, excluded.entity_id) == ("excluded", None)
    with pytest.raises(AppError) as exc:
        mappings.confirm(db, operator, pseudo.id, rosa.id, reason="x", is_admin=False)
    assert exc.value.status_code == 403
    with pytest.raises(AppError) as exc:
        mappings.confirm(db, admin, pseudo.id, rosa.id, reason=None, is_admin=True)
    assert exc.value.code == "reason_required"

    customers.set_flags(db, admin, rosa.id, is_generic=None, active=False, reason="Cerró")
    with pytest.raises(AppError) as exc:
        mappings.confirm(db, admin, pseudo.id, rosa.id, reason="x", is_admin=True)
    assert exc.value.code == "invalid_mapping_entity"  # inactive or missing entities are not linkable
    with pytest.raises(AppError) as exc:
        mappings.confirm(db, admin, pseudo.id, 999_999, reason="x", is_admin=True)
    assert exc.value.code == "invalid_mapping_entity"


def test_every_decision_is_audited_without_external_labels(db: Session, admin, operator, job) -> None:  # type: ignore[no-untyped-def]
    rosa = new_customer(db, job, "Almacén Doña Rosa")
    mapping = mappings.observe(db, name_of("Rosa Secreta"), label="Rosa Secreta", job_id=job, candidates=[rosa.id])
    db.commit()
    mappings.reject(db, operator, mapping.id, reason="No es")
    mappings.confirm(db, operator, mapping.id, rosa.id, reason=None, is_admin=False)
    mappings.exclude(db, admin, mapping.id, reason="Mal cargado", is_admin=True)
    events = db.scalars(
        select(AuditEvent).where(AuditEvent.event_type.like("platform.mapping.%")).order_by(AuditEvent.id)
    ).all()
    assert [e.event_type for e in events] == [
        "platform.mapping.rejected",
        "platform.mapping.confirmed",
        "platform.mapping.excluded",
    ]
    assert [e.user_id for e in events] == [operator.id, operator.id, admin.id]
    last = events[-1].payload
    assert last["before"]["status"] == "confirmed"
    assert last["after"]["status"] == "excluded"
    assert last["after"]["reason"] == "Mal cargado"
    for event in events:
        assert "ROSA" not in json.dumps(event.payload, ensure_ascii=False).upper()


def test_constraints_and_no_deletes(db: Session, job: int) -> None:
    rosa = new_customer(db, job, "Almacén Doña Rosa")
    mapping = mappings.observe(db, seller("3"), label=None, job_id=job)
    db.commit()
    bad = {
        "UPDATE external_entity_mappings SET status = 'confirmed' WHERE id = :i": "confirmed_has_entity",
        "UPDATE external_entity_mappings SET status = 'ambiguous' WHERE id = :i": "ambiguous_has_candidates",
        "UPDATE external_entity_mappings SET candidates = '[1, 2]' WHERE id = :i": "pending_one_candidate",
        "UPDATE external_entity_mappings SET status = 'maybe' WHERE id = :i": "ck_external_entity_mappings_status",
        "UPDATE external_entity_mappings SET method = 'fuzzy' WHERE id = :i": "ck_external_entity_mappings_method",
    }
    for statement, constraint in bad.items():
        with pytest.raises(IntegrityError, match=constraint):
            db.execute(text(statement), {"i": mapping.id})
        db.rollback()
    with pytest.raises(IntegrityError, match="uq_external_entity_mappings_key"):
        db.execute(
            text(
                "INSERT INTO external_entity_mappings (entity_type, source_system_id, namespace, external_id, status) "
                "SELECT entity_type, source_system_id, namespace, external_id, 'pending' "
                "FROM external_entity_mappings WHERE id = :i"
            ),
            {"i": mapping.id},
        )
    db.rollback()
    for table, row in (("external_entity_mappings", mapping.id), ("customers", rosa.id)):
        with pytest.raises(DBAPIError, match="never deleted"):
            db.execute(text(f"DELETE FROM {table} WHERE id = :i"), {"i": row})  # noqa: S608
        db.rollback()
