"""Pending collections of a settlement: load, correction, void and what each close publishes (R3-1).

Load (operator, settlement OPEN, including a correction):
- creation is idempotent by `client_request_id`, like every load of a settlement;
- the responsible employee is the driver unless the operator chooses another active employee (R3-D3);
- every changed field and the void write a `pending_collection_events` row and an audit event.

What was already applied to a pending (collected or resolved, R3-2, `applications`) is a fact the load
cannot contradict (Enzo's R3 decision 8): its amount never goes below it, it is not voided and its customer
does not change while it has activity.

The close of the settlement (`delivery.closing`) calls `publish`: with the settlement and the pendings
locked, it freezes in `settlement_closing_pendings` what the new close publishes and records PUBLISHED /
WITHDRAWN events. Until then the amount in effect is the one of the previous close.

Lock order, always: the settlement row, then its pendings by id (then, in the ledger, the accounts).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery import idempotency, service
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.pending import schemas as s
from app.modules.settlements.pending.models import (
    PendingCollection,
    PendingCollectionEvent,
    PendingCollectionReceipt,
    PendingCollectionResolution,
    SettlementClosingPending,
)
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.parties.models import Employee

ZERO = Decimal("0.00")


@dataclass(frozen=True)
class InEffect:
    """What the close in effect published for a pending."""

    amount: Decimal
    customer_name: str


# --- load -----------------------------------------------------------------------------------------


def create(db: Session, actor: User, settlement_id: int, body: s.PendingIn) -> service.Created:
    customer = _clean_customer(body.customer_name)
    reason = _clean_reason(body.reason)
    fingerprint = idempotency.fingerprint(
        actor.id,
        "pending_collection",
        {
            "settlement_id": settlement_id,
            "customer_name": customer,
            "amount": service.money(body.amount),
            "reason": reason,
            "responsible_employee_id": body.responsible_employee_id,
            "expected_date": body.expected_date.isoformat() if body.expected_date else None,
        },
    )
    existing = _by_request_id(db, body)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "pending_id", existing.id)
        return service.Created(existing.id, created=False)

    settlement = service.lock_open(db, settlement_id)
    responsible = settlement.employee_id
    if body.responsible_employee_id is not None and body.responsible_employee_id != settlement.employee_id:
        responsible = active_employee(db, body.responsible_employee_id).id
    _check_expected_date(settlement, body.expected_date)
    pending = PendingCollection(
        settlement_id=settlement.id,
        customer_name=customer,
        amount=body.amount,
        reason=reason,
        responsible_employee_id=responsible,
        expected_date=body.expected_date,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(pending)
            db.flush()
    except IntegrityError:
        winner = _by_request_id(db, body)
        if winner is None:
            raise
        db.rollback()  # release the locks taken for the insert that lost
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "pending_id", winner.id)
        return service.Created(winner.id, created=False)
    snapshot = _snapshot(pending)
    record_event(db, actor, settlement, pending.id, "CREATED", None, snapshot)
    record_change(
        db,
        "settlements.pending_collection.created",
        action="create",
        user_id=actor.id,
        entity_type="pending_collection",
        entity_id=pending.id,
        before=None,
        after={"settlement_id": settlement.id, "settlement_version": settlement.version, **snapshot},
    )
    service.commit(db)
    return service.Created(pending.id, created=True)


# Event of each field of the load that can be corrected.
_FIELD_EVENTS = (
    ("amount", "AMOUNT_CHANGED"),
    ("customer_name", "CUSTOMER_CHANGED"),
    ("responsible_employee_id", "RESPONSIBLE_CHANGED"),
    ("reason", "REASON_CHANGED"),
    ("expected_date", "EXPECTED_DATE_CHANGED"),
)


def update(db: Session, actor: User, settlement_id: int, pending_id: int, body: s.PendingPatch) -> None:
    settlement = service.lock_open(db, settlement_id)
    pending = _lock_pending(db, settlement.id, pending_id)
    if pending.voided_at is not None:
        raise AppError(409, "pending_voided", "El pendiente está anulado y no se modifica.")
    before = _snapshot(pending)
    fields = body.model_fields_set
    for name in ("customer_name", "amount", "reason", "responsible_employee_id"):
        if name in fields and getattr(body, name) is None:
            raise AppError(422, "field_required", "Ese dato del pendiente es obligatorio.", {"field": name})
    if body.customer_name is not None:
        pending.customer_name = _clean_customer(body.customer_name)
    if body.amount is not None:
        pending.amount = body.amount
    if body.reason is not None:
        pending.reason = _clean_reason(body.reason)
    if body.responsible_employee_id is not None and body.responsible_employee_id != pending.responsible_employee_id:
        if body.responsible_employee_id != settlement.employee_id:
            active_employee(db, body.responsible_employee_id)
        pending.responsible_employee_id = body.responsible_employee_id
    if "expected_date" in fields:
        _check_expected_date(settlement, body.expected_date)
        pending.expected_date = body.expected_date
    after = _snapshot(pending)
    if before == after:
        db.commit()
        return

    applied = applied_amounts(db, [pending.id]).get(pending.id, ZERO)
    if pending.amount < applied:
        raise _below_applied(pending.id, pending.amount, applied)
    if applied > 0 and pending.customer_name != before["customer_name"]:
        raise AppError(
            409,
            "pending_customer_locked",
            "El pendiente ya tiene cobros o resoluciones de ese cliente: no se le cambia el cliente.",
            {"pending_id": pending.id, "applied": service.money(applied)},
        )
    pending.updated_by = actor.id
    for name, event_type in _FIELD_EVENTS:
        if before[name] != after[name]:
            record_event(db, actor, settlement, pending.id, event_type, {name: before[name]}, {name: after[name]})
    record_change(
        db,
        "settlements.pending_collection.updated",
        action="update",
        user_id=actor.id,
        entity_type="pending_collection",
        entity_id=pending.id,
        before=before,
        after={**after, "settlement_version": settlement.version},
    )
    service.commit(db)


def void(db: Session, actor: User, settlement_id: int, pending_id: int, reason: str) -> None:
    """Void the load. A pending in effect stays in effect until the next close (marked for removal)."""
    settlement = service.lock_open(db, settlement_id)
    pending = _lock_pending(db, settlement.id, pending_id)
    if pending.voided_at is not None:
        raise AppError(409, "pending_already_voided", "El pendiente ya estaba anulado.")
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la anulación.")
    applied = applied_amounts(db, [pending.id]).get(pending.id, ZERO)
    if applied > 0:
        raise AppError(
            409,
            "pending_has_activity",
            "El pendiente ya tiene cobros o resoluciones: primero hay que revertirlos.",
            {"pending_id": pending.id, "applied": service.money(applied)},
        )
    pending.voided_by = actor.id
    pending.voided_at = datetime.now(UTC)
    pending.void_reason = reason
    record_event(db, actor, settlement, pending.id, "VOIDED", {"voided": False}, {"voided": True}, reason=reason)
    record_change(
        db,
        "settlements.pending_collection.voided",
        action="void",
        user_id=actor.id,
        entity_type="pending_collection",
        entity_id=pending.id,
        before={"voided": False},
        after={"voided": True, "reason": reason, "settlement_version": settlement.version},
    )
    service.commit(db)


# --- close ----------------------------------------------------------------------------------------


def lock_for_close(db: Session, settlement: Settlement) -> list[PendingCollection]:
    """Every pending of the (already locked) settlement, locked in id order."""
    return list(
        db.scalars(
            select(PendingCollection)
            .where(PendingCollection.settlement_id == settlement.id)
            .order_by(PendingCollection.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def check_against_applied(db: Session, pendings: Sequence[PendingCollection]) -> None:
    """The close of a correction never contradicts what was already applied (`pending_below_applied`)."""
    applied = applied_amounts(db, [p.id for p in pendings])
    for pending in pendings:
        done = applied.get(pending.id, ZERO)
        if done == 0:
            continue
        if pending.voided_at is not None:
            raise AppError(
                409,
                "pending_has_activity",
                "Un pendiente marcado para quitar ya tiene cobros o resoluciones: primero hay que revertirlos.",
                {"pending_id": pending.id, "applied": service.money(done)},
            )
        if pending.amount < done:
            raise _below_applied(pending.id, pending.amount, done)


def publish(
    db: Session,
    actor: User,
    settlement: Settlement,
    pendings: Sequence[PendingCollection],
    close_operation_id: int,
    previous_close_id: int | None,
) -> None:
    """Freeze what the new close publishes per pending and record it in each pending's history."""
    previous = in_effect(db, previous_close_id)
    live = [p for p in pendings if p.voided_at is None]
    db.add_all(
        SettlementClosingPending(
            close_operation_id=close_operation_id,
            settlement_id=settlement.id,
            settlement_version=settlement.version,
            pending_id=p.id,
            amount=p.amount,
            customer_name=p.customer_name,
        )
        for p in live
    )
    for p in live:
        before = previous.get(p.id)
        record_event(
            db,
            actor,
            settlement,
            p.id,
            "PUBLISHED",
            {"amount": service.money(before.amount), "customer_name": before.customer_name} if before else None,
            {"amount": service.money(p.amount), "customer_name": p.customer_name},
            close_operation_id=close_operation_id,
        )
    published = {p.id for p in live}
    for pending_id, before in sorted(previous.items()):
        if pending_id not in published:
            record_event(
                db,
                actor,
                settlement,
                pending_id,
                "WITHDRAWN",
                {"amount": service.money(before.amount), "customer_name": before.customer_name},
                None,
                close_operation_id=close_operation_id,
            )


def preview_rows(db: Session, settlement_id: int) -> list[list[Any]]:
    """What of the pendings a closing preview depends on, sorted by id: any change makes it stale."""
    rows = list(
        db.scalars(
            select(PendingCollection)
            .where(PendingCollection.settlement_id == settlement_id)
            .order_by(PendingCollection.id)
        )
    )
    applied = applied_amounts(db, [p.id for p in rows])
    return [
        [
            p.id,
            service.money(p.amount),
            p.customer_name,
            p.responsible_employee_id,
            p.voided_at is not None,
            service.money(applied.get(p.id, ZERO)),
        ]
        for p in rows
    ]


# --- reading --------------------------------------------------------------------------------------


def collected_and_resolved(db: Session, pending_ids: Sequence[int]) -> dict[int, tuple[Decimal, Decimal]]:
    """(collected, resolved) per pending: its receipts and resolutions not reversed. Never stored."""
    result = dict.fromkeys(pending_ids, (ZERO, ZERO))
    if not pending_ids:
        return result
    for model, position in ((PendingCollectionReceipt, 0), (PendingCollectionResolution, 1)):
        sums = db.execute(
            select(model.pending_id, func.sum(model.amount))
            .where(model.pending_id.in_(pending_ids), model.reversal_operation_id.is_(None))
            .group_by(model.pending_id)
        )
        for pid, amount in sums:
            pair = list(result[pid])
            pair[position] = amount
            result[pid] = (pair[0], pair[1])
    return result


def applied_amounts(db: Session, pending_ids: Sequence[int]) -> dict[int, Decimal]:
    """What already happened to each pending (collected + resolved): the load can never go below it."""
    return {pid: collected + resolved for pid, (collected, resolved) in collected_and_resolved(db, pending_ids).items()}


def in_effect(db: Session, close_operation_id: int | None) -> dict[int, InEffect]:
    """What a close published per pending: for the settlement's close in effect, the amounts in effect."""
    if close_operation_id is None:
        return {}
    rows = db.execute(
        select(
            SettlementClosingPending.pending_id,
            SettlementClosingPending.amount,
            SettlementClosingPending.customer_name,
        ).where(SettlementClosingPending.close_operation_id == close_operation_id)
    )
    return {pid: InEffect(amount, customer) for pid, amount, customer in rows}


def of(db: Session, settlement_id: int) -> list[PendingCollection]:
    return list(
        db.scalars(
            select(PendingCollection)
            .where(PendingCollection.settlement_id == settlement_id)
            .order_by(PendingCollection.id)
        )
    )


def get(db: Session, pending_id: int) -> PendingCollection:
    pending = db.get(PendingCollection, pending_id)
    if pending is None:
        raise AppError(404, "pending_not_found", "El pendiente no existe.")
    return pending


def published_of(db: Session, pending_id: int) -> list[SettlementClosingPending]:
    return list(
        db.scalars(
            select(SettlementClosingPending)
            .where(SettlementClosingPending.pending_id == pending_id)
            .order_by(SettlementClosingPending.settlement_version)
        )
    )


def events_of(db: Session, pending_id: int) -> list[PendingCollectionEvent]:
    return list(
        db.scalars(
            select(PendingCollectionEvent)
            .where(PendingCollectionEvent.pending_id == pending_id)
            .order_by(PendingCollectionEvent.id)
        )
    )


# --- internals ------------------------------------------------------------------------------------


def record_event(
    db: Session,
    actor: User,
    settlement: Settlement,
    pending_id: int,
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    reason: str | None = None,
    close_operation_id: int | None = None,
    receipt_id: int | None = None,
    resolution_id: int | None = None,
) -> None:
    db.add(
        PendingCollectionEvent(
            pending_id=pending_id,
            settlement_id=settlement.id,
            settlement_version=settlement.version,
            event_type=event_type,
            before=before,
            after=after,
            reason=reason,
            close_operation_id=close_operation_id,
            receipt_id=receipt_id,
            resolution_id=resolution_id,
            created_by=actor.id,
        )
    )


def _lock_pending(db: Session, settlement_id: int, pending_id: int) -> PendingCollection:
    pending = db.scalars(
        select(PendingCollection)
        .where(PendingCollection.id == pending_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if pending is None or pending.settlement_id != settlement_id:
        raise AppError(404, "pending_not_found", "El pendiente no existe en esta rendición.")
    return pending


def active_employee(db: Session, employee_id: int) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None or not employee.active:
        raise AppError(
            422, "employee_not_found", "El responsable tiene que ser un empleado activo.", {"employee_id": employee_id}
        )
    return employee


def _check_expected_date(settlement: Settlement, expected: date | None) -> None:
    if expected is not None and expected < settlement.business_date:
        raise AppError(
            422,
            "invalid_expected_date",
            "La fecha esperada de cobro no puede ser anterior a la fecha de la rendición.",
            {"business_date": settlement.business_date.isoformat()},
        )


def _clean_customer(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise AppError(422, "customer_required", "Indicá el cliente que debe el pendiente.")
    return name


def _clean_reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo del pendiente.")
    return reason


def _below_applied(pending_id: int, amount: Decimal, applied: Decimal) -> AppError:
    return AppError(
        409,
        "pending_below_applied",
        f"El pendiente ya tiene aplicado ${service.money(applied)}: su importe no puede quedar por debajo.",
        {"pending_id": pending_id, "amount": service.money(amount), "applied": service.money(applied)},
    )


def _snapshot(pending: PendingCollection) -> dict[str, Any]:
    return {
        "customer_name": pending.customer_name,
        "amount": service.money(pending.amount),
        "reason": pending.reason,
        "responsible_employee_id": pending.responsible_employee_id,
        "expected_date": pending.expected_date.isoformat() if pending.expected_date else None,
    }


def _by_request_id(db: Session, body: s.PendingIn) -> PendingCollection | None:
    return db.scalars(
        select(PendingCollection).where(PendingCollection.client_request_id == body.client_request_id)
    ).first()
