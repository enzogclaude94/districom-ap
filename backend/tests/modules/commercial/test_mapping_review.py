"""F1.5: a mapping decision re-resolves the facts that took their identity from it (docs/IMPORTS.md §6.4).

The four namespaces (Cliente STORE SYS, Cliente AXUM, Vendedor STORE SYS, Preventista AXUM), reassign A → B,
exclude, reject, the all-or-nothing batch, the issues a decision closes or keeps open, the row lock, and an audit
without names, codes or labels."""

from datetime import date
from typing import Any

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

import app.api.v1.router  # noqa: F401  (installs the modules: import types, quality codes and resolvers)
from app.core.errors import AppError
from app.db.session import get_db
from app.modules.commercial.assignments.models import CustomerSalespersonAssignment
from app.modules.commercial.config.seed import seed_config
from app.modules.commercial.documents.models import Document
from app.modules.commercial.no_purchase.models import NoPurchaseEvent
from app.modules.commercial.sales.models import SaleLine
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.mappings import resolvers
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.parties.models import Employee
from app.platform.quality.models import DataQualityIssue
from tests.modules.commercial.axum import NO_PURCHASE, axum_file, visit
from tests.modules.commercial.store_sys import (
    CUSTOMERS,
    DOCUMENTS,
    SALES,
    customer,
    customers_file,
    document,
    documents_file,
    run,
    sale,
    sales_file,
)

D20 = date(2026, 10, 20)
SECRET_SELLER = "VENDEDOR SECRETO"
SECRET_CUSTOMER = "ALMACEN PRIVADO"


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
                customer(1, "ALMACEN UNO", seller=SECRET_SELLER),
                customer(2, "KIOSCO DOS", seller=SECRET_SELLER),
                customer(3, "DESPENSA TRES", seller="BOCA DE EXPENDIO"),
            ]
        ),
        CUSTOMERS,
        snapshot_date=D20,
    )
    return user  # type: ignore[no-any-return]


@pytest.fixture
def admin(make_user) -> User:  # type: ignore[no-untyped-def]
    return make_user(email="admin@districom.test", role="admin")  # type: ignore[no-any-return]


def person(db: Session, name: str, *, active: bool = True) -> Employee:
    employee = Employee(full_name=name, active=active)
    db.add(employee)
    db.commit()
    return employee


def mapping_of(db: Session, entity_type: str, system: str, namespace: str, external_id: str) -> ExternalEntityMapping:
    found = mappings.resolve(db, mappings.MappingKey(entity_type, system, namespace, external_id))
    assert found.mapping_id is not None, (namespace, external_id)
    return db.get_one(ExternalEntityMapping, found.mapping_id)


def customer_id(db: Session, code: int) -> int:
    found = mapping_of(db, "customer", "STORE_SYS", "customer", str(code)).entity_id
    assert found is not None
    return found


def open_issues(db: Session, code: str) -> set[str]:
    return set(
        db.scalars(
            select(DataQualityIssue.dedupe_key).where(DataQualityIssue.code == code, DataQualityIssue.status == "open")
        )
    )


def events(db: Session) -> list[NoPurchaseEvent]:
    return list(db.scalars(select(NoPurchaseEvent).order_by(NoPurchaseEvent.id)))


def axum_report() -> bytes:
    """01 with two reasons (one for a customer not in the master), 02 with one, 40 with purchases only."""
    return axum_file(
        [
            ("01", [visit(1, "ALMACEN UNO", "CERRADO"), visit(77, SECRET_CUSTOMER, "TIENE STOCK")]),
            ("02", [visit(2, "KIOSCO DOS", "SIN DINERO")]),
            ("40", [visit(1, "ALMACEN UNO", "Si")]),
        ]
    )


def mapping_audit(db: Session) -> list[AuditEvent]:
    return list(
        db.scalars(select(AuditEvent).where(AuditEvent.event_type.like("platform.mapping.%")).order_by(AuditEvent.id))
    )


# --- Cliente STORE SYS ------------------------------------------------------------------------------------------


def test_store_sys_customer_completes_its_sales_and_documents_only(db: Session, operator: User, admin: User) -> None:
    pending = mappings.observe(
        db, mappings.MappingKey("customer", "STORE_SYS", "customer", "999"), label="NUEVO", job_id=None
    )
    db.commit()
    run(db, operator, sales_file([sale(100, 999, "NUEVO"), sale(100, 1, "ALMACEN UNO")]), SALES, scope="FULL")
    run(
        db,
        operator,
        documents_file(
            [
                document("FACTURA A 0003-00000001", code=999, name="NUEVO"),
                document("VALE PERSONAL V 0003-00000002", code=999, name="NUEVO"),  # codcli is not a customer here
                document("FACTURA B 0003-00000003", code=1, name="ALMACEN UNO"),
            ]
        ),
        DOCUMENTS,
    )
    assert open_issues(db, "commercial.customer.unresolved_code") == {"customer_code:999"}
    target = customer_id(db, 3)
    before_other = {
        (line.id, line.customer_id) for line in db.scalars(select(SaleLine).where(SaleLine.source_customer_code == "1"))
    }

    decision = mappings.confirm(db, operator, pending.id, target, reason=None, is_admin=False)
    db.commit()

    assert decision.resolution.updated == {"commercial.sale_lines": 1, "commercial.documents": 1}
    assert decision.resolution.issues_closed == 1
    lines = db.scalars(select(SaleLine).where(SaleLine.customer_mapping_id == pending.id)).all()
    assert [(line.customer_id, line.source_customer_code) for line in lines] == [(target, "999")]
    after_other = {
        (line.id, line.customer_id) for line in db.scalars(select(SaleLine).where(SaleLine.source_customer_code == "1"))
    }
    assert after_other == before_other  # a row resolved through another mapping is never touched
    docs = {d.source_type_name: (d.customer_id, d.customer_mapping_id) for d in db.scalars(select(Document))}
    assert docs["FACTURA A"] == (target, pending.id)
    assert docs["VALE PERSONAL V"] == (None, None)
    assert docs["FACTURA B"] == (customer_id(db, 1), mapping_of(db, "customer", "STORE_SYS", "customer", "1").id)
    assert open_issues(db, "commercial.customer.unresolved_code") == set()


def test_the_code_issue_stays_open_while_facts_without_a_mapping_remain(
    db: Session, operator: User, admin: User
) -> None:
    run(db, operator, sales_file([sale(100, 998, "SIN MAPPING")]), SALES, scope="FULL")  # no mapping existed then
    pending = mappings.observe(
        db, mappings.MappingKey("customer", "STORE_SYS", "customer", "998"), label="SIN MAPPING", job_id=None
    )
    db.commit()
    run(db, operator, sales_file([sale(100, 998, "SIN MAPPING", day=date(2026, 10, 24))]), SALES, scope="FULL")

    decision = mappings.confirm(db, operator, pending.id, customer_id(db, 2), reason=None, is_admin=False)
    db.commit()

    assert decision.resolution.updated["commercial.sale_lines"] == 1
    orphan = db.scalars(select(SaleLine).where(SaleLine.customer_mapping_id.is_(None))).one()
    assert orphan.customer_id is None  # never completed by its code
    assert decision.resolution.issues_closed == 0
    assert open_issues(db, "commercial.customer.unresolved_code") == {"customer_code:998"}


# --- Cliente AXUM ---------------------------------------------------------------------------------------------------


def test_axum_customer_completes_its_no_purchase_events_and_closes_its_issue(db: Session, operator: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    unknown = mapping_of(db, "customer", "AXUM", "customer", "77")
    assert unknown.status == "pending"
    assert open_issues(db, "commercial.axum_customer.unresolved") == {"axum_customer:77"}
    target = customer_id(db, 3)

    decision = mappings.confirm(db, operator, unknown.id, target, reason=None, is_admin=False)
    db.commit()

    assert decision.resolution.updated == {"commercial.no_purchase_events": 1}
    assert {e.source_customer_code: e.customer_id for e in events(db)} == {
        "1": customer_id(db, 1),
        "77": target,
        "2": customer_id(db, 2),
    }
    assert open_issues(db, "commercial.axum_customer.unresolved") == set()


# --- Vendedor STORE SYS ----------------------------------------------------------------------------------------------


def test_store_sys_seller_completes_assignments_in_place_and_sales(db: Session, operator: User) -> None:
    run(db, operator, sales_file([sale(100, 1, "ALMACEN UNO", seller=SECRET_SELLER)]), SALES, scope="FULL")
    seller = mapping_of(db, "employee", "STORE_SYS", "seller", SECRET_SELLER)
    assert open_issues(db, "commercial.seller.unmapped") >= {f"mapping:{seller.id}"}
    history = [
        (a.id, a.customer_id, a.valid_from, a.valid_to)
        for a in db.scalars(select(CustomerSalespersonAssignment).order_by(CustomerSalespersonAssignment.id))
    ]
    employee = person(db, "Vendedor Secreto")

    decision = mappings.confirm(db, operator, seller.id, employee.id, reason=None, is_admin=False)
    db.commit()

    assert decision.resolution.updated == {"commercial.customer_salesperson_assignments": 2, "commercial.sale_lines": 1}
    rows = db.scalars(select(CustomerSalespersonAssignment).order_by(CustomerSalespersonAssignment.id)).all()
    # The same rows, the same validity: history preserved, no new period.
    assert [(a.id, a.customer_id, a.valid_from, a.valid_to) for a in rows] == history
    assert {a.source_seller_key: a.salesperson_id for a in rows} == {
        SECRET_SELLER: employee.id,
        "BOCA DE EXPENDIO": None,
    }
    assert db.scalars(select(SaleLine.salesperson_id)).one() == employee.id
    assert f"mapping:{seller.id}" not in open_issues(db, "commercial.seller.unmapped")


# --- Preventista AXUM ------------------------------------------------------------------------------------------------


def test_axum_salespeople_01_02_and_40(db: Session, operator: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    one, two, forty = (mapping_of(db, "employee", "AXUM", "salesperson", code) for code in ("01", "02", "40"))
    assert {m.status for m in (one, two, forty)} == {"pending"}
    ana = person(db, "Ana")

    impact = resolvers.count(db, mappings.kind_key(db, one), one.id, ana.id)
    assert [(c.kind, c.total, c.linked, c.changes_on_link()) for c in impact] == [
        ("commercial.no_purchase_events", 2, 0, 2)
    ]
    decision = mappings.confirm(db, operator, one.id, ana.id, reason=None, is_admin=False)
    db.commit()
    assert decision.resolution.updated == {"commercial.no_purchase_events": 2}
    assert {e.source_salesperson: e.salesperson_id for e in events(db)} == {"01": ana.id, "02": None}
    assert open_issues(db, "commercial.axum_salesperson.unmapped") == {f"mapping:{two.id}", f"mapping:{forty.id}"}

    # 40 only brought purchases: nothing to complete, but its issue goes away once decided.
    decision = mappings.confirm(db, operator, forty.id, ana.id, reason=None, is_admin=False)
    db.commit()
    assert (decision.resolution.facts_updated, decision.resolution.issues_closed) == (0, 1)
    assert open_issues(db, "commercial.axum_salesperson.unmapped") == {f"mapping:{two.id}"}


def test_superseded_history_is_re_resolved_and_counted_apart(db: Session, operator: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    one = mapping_of(db, "employee", "AXUM", "salesperson", "01")
    old = events(db)[0]
    db.execute(
        text(
            "UPDATE commercial.no_purchase_events SET status = 'SUPERSEDED', superseded_by_job_id = import_job_id "
            "WHERE id = :id"
        ),
        {"id": old.id},
    )
    db.commit()
    ana = person(db, "Ana")
    [impact] = resolvers.count(db, mappings.kind_key(db, one), one.id, ana.id)
    assert (impact.total, impact.current, impact.changes_on_link(), impact.current_changes_on_link()) == (2, 1, 2, 1)

    decision = mappings.confirm(db, operator, one.id, ana.id, reason=None, is_admin=False)
    db.commit()
    assert decision.resolution.updated == {"commercial.no_purchase_events": 2}  # history never contradicts its mapping
    db.expire_all()
    assert db.get_one(NoPurchaseEvent, old.id).salesperson_id == ana.id


# --- reassign, exclude, reject ---------------------------------------------------------------------------------------


def test_reassign_moves_only_the_facts_of_that_mapping(db: Session, operator: User, admin: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    one, two = (mapping_of(db, "employee", "AXUM", "salesperson", code) for code in ("01", "02"))
    ana, bruno = person(db, "Ana"), person(db, "Bruno")
    mappings.confirm(db, operator, one.id, ana.id, reason=None, is_admin=False)
    mappings.confirm(db, operator, two.id, ana.id, reason=None, is_admin=False)
    db.commit()

    decision = mappings.reassign(db, admin, one.id, bruno.id, reason="Era Bruno")
    db.commit()

    assert decision.resolution.updated == {"commercial.no_purchase_events": 2}
    assert {e.source_salesperson: e.salesperson_id for e in events(db)} == {"01": bruno.id, "02": ana.id}
    summary = [e for e in mapping_audit(db) if e.event_type == "platform.mapping.facts_resolved"][-1].payload
    assert summary is not None
    assert (summary["action"], summary["mapping_id"], summary["entity_id"]) == ("reassigned", one.id, bruno.id)
    assert summary["facts_updated"] == {"commercial.no_purchase_events": 2}
    # Deterministic: deciding the same again changes nothing more.
    assert (
        resolvers.apply(
            db, mappings.kind_key(db, one), db.get_one(ExternalEntityMapping, one.id), admin.id
        ).facts_updated
        == 0
    )


def test_exclude_keeps_every_fact_with_its_source_value_and_mapping(db: Session, operator: User, admin: User) -> None:
    run(db, operator, sales_file([sale(100, 3, "DESPENSA TRES", seller="BOCA DE EXPENDIO")]), SALES, scope="FULL")
    boca = mapping_of(db, "employee", "STORE_SYS", "seller", "BOCA DE EXPENDIO")
    lines_before = db.scalar(select(func.count()).select_from(SaleLine))

    decision = mappings.exclude(db, operator, boca.id, reason="Pseudo-vendedor de la boca", is_admin=False)
    db.commit()
    assert decision.resolution.facts_updated == 0  # nothing was linked
    assert f"mapping:{boca.id}" not in open_issues(db, "commercial.seller.unmapped")

    # Excluding a confirmed one (admin) unlinks its facts and keeps them.
    run(db, operator, axum_report(), NO_PURCHASE)
    one = mapping_of(db, "employee", "AXUM", "salesperson", "01")
    ana = person(db, "Ana")
    mappings.confirm(db, operator, one.id, ana.id, reason=None, is_admin=False)
    db.commit()
    decision = mappings.exclude(db, admin, one.id, reason="No es una persona", is_admin=True)
    db.commit()
    assert decision.resolution.updated == {"commercial.no_purchase_events": 2}
    ones = [e for e in events(db) if e.source_salesperson == "01"]
    assert [(e.salesperson_id, e.salesperson_mapping_id, e.source_salesperson) for e in ones] == [
        (None, one.id, "01")
    ] * 2
    line = db.scalars(select(SaleLine)).one()
    assert (line.salesperson_id, line.seller_mapping_id, line.source_seller) == (None, boca.id, "BOCA DE EXPENDIO")
    assert db.scalar(select(func.count()).select_from(SaleLine)) == lines_before
    assert len(events(db)) == 3

    # An admin can link the excluded code again, with a reason: the facts follow.
    decision = mappings.confirm(db, admin, one.id, ana.id, reason="Era Ana", is_admin=True)
    db.commit()
    assert decision.resolution.updated == {"commercial.no_purchase_events": 2}


def test_reject_leaves_the_code_unlinked_and_its_facts_pending(db: Session, operator: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    one = mapping_of(db, "employee", "AXUM", "salesperson", "01")

    decision = mappings.reject(db, operator, one.id, reason="No sabemos quién es")
    db.commit()

    assert (decision.mapping.status, decision.mapping.entity_id) == ("rejected", None)
    assert decision.resolution.facts_updated == 0
    assert all(e.salesperson_id is None for e in events(db))
    assert f"mapping:{one.id}" in open_issues(db, "commercial.axum_salesperson.unmapped")
    # It can be decided again.
    ana = person(db, "Ana")
    mappings.confirm(db, operator, one.id, ana.id, reason=None, is_admin=False)
    db.commit()
    assert f"mapping:{one.id}" not in open_issues(db, "commercial.axum_salesperson.unmapped")


# --- batch ----------------------------------------------------------------------------------------------------------


def test_batch_confirms_everything_in_one_transaction(db: Session, operator: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    one, two = (mapping_of(db, "employee", "AXUM", "salesperson", code) for code in ("01", "02"))
    ana, bruno = person(db, "Ana"), person(db, "Bruno")

    decisions = mappings.confirm_many(
        db, operator, [mappings.BatchItem(two.id, bruno.id), mappings.BatchItem(one.id, ana.id)]
    )
    db.commit()

    assert [d.mapping.id for d in decisions] == sorted([one.id, two.id])
    assert {e.source_salesperson: e.salesperson_id for e in events(db)} == {"01": ana.id, "02": bruno.id}
    audit = mapping_audit(db)
    assert [e.event_type for e in audit] == [
        "platform.mapping.confirmed",
        "platform.mapping.facts_resolved",
        "platform.mapping.confirmed",
        "platform.mapping.facts_resolved",
        "platform.mapping.batch_confirmed",
    ]
    batch = audit[-1].payload
    assert batch is not None
    assert batch["confirmed"] == 2
    assert batch["mapping_ids"] == sorted([one.id, two.id])
    assert batch["facts_updated"] == {"commercial.no_purchase_events": 3}

    # Running it again: those mappings are no longer open.
    with pytest.raises(AppError) as exc:
        mappings.confirm_many(db, operator, [mappings.BatchItem(one.id, ana.id)])
    assert (exc.value.status_code, exc.value.code) == (409, "mapping_not_open")
    assert exc.value.details["mapping_id"] == one.id


def test_one_failure_in_a_batch_applies_nothing(db: Session, operator: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    one, two = (mapping_of(db, "employee", "AXUM", "salesperson", code) for code in ("01", "02"))
    ana, gone = person(db, "Ana"), person(db, "Ya no trabaja", active=False)
    audit_before = len(mapping_audit(db))

    with pytest.raises(AppError) as exc:
        mappings.confirm_many(db, operator, [mappings.BatchItem(one.id, ana.id), mappings.BatchItem(two.id, gone.id)])
    assert (exc.value.code, exc.value.details["mapping_id"]) == ("invalid_mapping_entity", two.id)
    db.rollback()  # what the endpoint's session does: nothing was committed

    assert db.get_one(ExternalEntityMapping, one.id).status == "pending"
    assert all(e.salesperson_id is None for e in events(db))
    assert len(mapping_audit(db)) == audit_before
    assert f"mapping:{one.id}" in open_issues(db, "commercial.axum_salesperson.unmapped")


@pytest.mark.parametrize(
    ("items", "code"),
    [([], "batch_empty"), ([(1, 1), (1, 2)], "batch_duplicate_mapping")],
)
def test_batch_shape_is_validated(db: Session, operator: User, items: list[tuple[int, int]], code: str) -> None:
    with pytest.raises(AppError) as exc:
        mappings.confirm_many(db, operator, [mappings.BatchItem(m, e) for m, e in items])
    assert exc.value.code == code


def test_an_excluded_mapping_is_never_part_of_a_batch(db: Session, operator: User) -> None:
    boca = mapping_of(db, "employee", "STORE_SYS", "seller", "BOCA DE EXPENDIO")
    mappings.exclude(db, operator, boca.id, reason="Pseudo-vendedor", is_admin=False)
    db.commit()
    with pytest.raises(AppError) as exc:
        mappings.confirm_many(db, operator, [mappings.BatchItem(boca.id, person(db, "Ana").id)])
    assert exc.value.code == "mapping_not_open"


# --- concurrency ----------------------------------------------------------------------------------------------------


def test_a_decision_holds_the_row_and_a_stale_one_is_refused(db: Session, operator: User) -> None:
    run(db, operator, axum_report(), NO_PURCHASE)
    one = mapping_of(db, "employee", "AXUM", "salesperson", "01")
    ana, bruno = person(db, "Ana"), person(db, "Bruno")

    mappings.confirm(db, operator, one.id, ana.id, reason=None, is_admin=False)  # not committed yet: row locked
    other = next(get_db())
    try:
        other.execute(text("SET lock_timeout = '200ms'"))
        with pytest.raises(OperationalError):
            mappings.confirm(other, operator, one.id, bruno.id, reason=None, is_admin=False)
        other.rollback()
        db.commit()
        # The other tab saw it pending: once the first decision is in, its own is refused, never applied over it.
        with pytest.raises(AppError) as exc:
            mappings.exclude(other, operator, one.id, reason="x", is_admin=True, expected_status="pending")
        assert (exc.value.status_code, exc.value.code) == (409, "mapping_changed")
        with pytest.raises(AppError) as exc:
            mappings.confirm(other, operator, one.id, bruno.id, reason=None, is_admin=False)
        assert exc.value.code == "mapping_already_confirmed"
    finally:
        other.rollback()
        other.close()
    assert {e.salesperson_id for e in events(db) if e.source_salesperson == "01"} == {ana.id}


# --- audit and privacy ------------------------------------------------------------------------------------------------


def _strings(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [s for k, v in value.items() for s in [str(k), *_strings(v)]]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [str(value)]


def test_the_re_resolution_audit_carries_ids_and_counts_only(
    db: Session, operator: User, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    run(db, operator, sales_file([sale(100, 1, "ALMACEN UNO", seller=SECRET_SELLER)]), SALES, scope="FULL")
    run(db, operator, axum_report(), NO_PURCHASE)
    seller = mapping_of(db, "employee", "STORE_SYS", "seller", SECRET_SELLER)
    unknown = mapping_of(db, "customer", "AXUM", "customer", "77")
    employee = person(db, "Vendedor Secreto")
    mappings.confirm_many(
        db,
        operator,
        [mappings.BatchItem(seller.id, employee.id), mappings.BatchItem(unknown.id, customer_id(db, 3))],
    )
    db.commit()

    audit = mapping_audit(db)
    assert {e.event_type for e in audit} >= {"platform.mapping.facts_resolved", "platform.mapping.batch_confirmed"}
    for event in audit:
        values = " ".join(_strings(event.payload))
        for secret in (SECRET_SELLER, SECRET_CUSTOMER, "Vendedor Secreto", "DESPENSA TRES"):
            assert secret not in values, (event.event_type, secret)
    logged = " ".join(r.getMessage() + " " + str(r.__dict__) for r in caplog.records)
    for secret in (SECRET_SELLER, SECRET_CUSTOMER):
        assert secret not in logged
