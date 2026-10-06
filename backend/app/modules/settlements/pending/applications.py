"""What happens to a pending in effect after the close (R3-2): receipts, resolutions, their reversals and
the change of responsible.

- Receipt (operator): the customer paid all or part of the balance. PENDING_COLLECTION: "Pendientes de
  cobro" -X and the cash box (CASH) or the bank (TRANSFER) +X, on the day the customer paid (Enzo's D1: not
  before the settlement, not in the future).
- Resolution (admin, with a reason): the company gives up collecting all or part of the balance.
  PENDING_WRITE_OFF: "Pendientes de cobro" -total, "Incobrables" +the company's part and each employee's
  internal account +their part. The parts add up exactly to the total.
- Reversal (admin, with a reason) of either: "the operation was wrong", never a new economic fact. It
  carries the original business date and is checked against the balances since then, so it may be
  `insufficient_funds` (Enzo's R3-2 precision). A reversed resolution also undoes the employee's part,
  even if their account goes negative (the company owes them: Enzo's D3). Nothing is ever deleted.

Only a pending in effect (its settlement's close in effect published it) is collected or resolved, and
never above its balance: the amount in effect minus what was applied, recomputed inside the transaction.
During a correction that is still the amount of the previous close; a receipt changes what a closing
preview depends on, so it makes earlier previews stale.

Lock order, as everywhere: the settlement (FOR SHARE: receipts never wait for each other on it, but a
close or a load change waits for them), then the pending (FOR UPDATE: two receipts of one pending
serialize), then, inside the ledger, the accounts. Every domain row, its ledger operation and its event
are written in one transaction; the database refuses one without the other (migration 0010).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery import service as settlements
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import employee_account_id
from app.modules.settlements.pending import schemas as s
from app.modules.settlements.pending import service
from app.modules.settlements.pending.models import (
    PendingCollection,
    PendingCollectionReceipt,
    PendingCollectionResolution,
    PendingResolutionAllocation,
)
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User
from app.platform.parties.models import Employee

MODULE_KEY = "settlements"
SOURCE_TYPE = "pending_collection"
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("8e5b7a52-4c0e-4f7b-9d36-2f1a6b0c9e47")
DESTINATION_KIND = {"CASH": "CASH_BOX", "TRANSFER": "BANK"}
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Locked:
    settlement: Settlement
    pending: PendingCollection


# --- receipts -------------------------------------------------------------------------------------


def record_receipt(db: Session, actor: User, pending_id: int, body: s.ReceiptIn) -> settlements.Created:
    business_date = body.business_date or business_today()
    fingerprint = idempotency.fingerprint(
        actor.id,
        "pending_receipt",
        {
            "pending_id": pending_id,
            "amount": settlements.money(body.amount),
            "method": body.method,
            "destination_account_id": body.destination_account_id,
            "business_date": business_date.isoformat(),
        },
    )
    locked = _lock(db, pending_id)
    existing = db.scalars(
        select(PendingCollectionReceipt).where(PendingCollectionReceipt.client_request_id == body.client_request_id)
    ).one_or_none()
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "receipt_id", existing.id)
        db.commit()
        return settlements.Created(existing.id, created=False)

    settlement, pending = locked.settlement, locked.pending
    balance = _balance(db, settlement, pending)
    _check_date(settlement, business_date, "invalid_receipt_date", "El cobro no puede ser anterior a la rendición.")
    destination = _account(db, body.destination_account_id)
    if destination is None or not destination.active or destination.kind != DESTINATION_KIND[body.method]:
        raise AppError(
            422,
            "invalid_destination",
            "El efectivo entra en una caja activa y la transferencia en un banco activo.",
            {"method": body.method, "destination_account_id": body.destination_account_id},
        )
    _check_balance(pending.id, body.amount, balance)

    operation = ledger.post_operation(
        db,
        actor=actor,
        type="PENDING_COLLECTION",
        legs=[
            ledger.Leg(ledger.system_account_id(db, "pending_collections"), -body.amount),
            ledger.Leg(destination.account_id, body.amount),
        ],
        client_request_id=uuid5(NAMESPACE, f"receipt:{body.client_request_id}"),
        business_date=business_date,
        description=f"Cobro del pendiente #{pending.id} de la rendición #{settlement.id}",
        source=ledger.SourceRef(SOURCE_TYPE, pending.id),
    ).operation
    receipt = PendingCollectionReceipt(
        pending_id=pending.id,
        amount=body.amount,
        business_date=business_date,
        method=body.method,
        destination_account_id=destination.account_id,
        operation_id=operation.id,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    db.add(receipt)
    db.flush()
    service.record_event(db, actor, settlement, pending.id, "RECEIPT_RECORDED", None, None, receipt_id=receipt.id)
    _audit(
        db,
        actor,
        "settlements.pending_collection.receipt_recorded",
        "receipt",
        receipt.id,
        {
            "pending_id": pending.id,
            "amount": settlements.money(body.amount),
            "method": body.method,
            "destination_account_id": destination.account_id,
            "business_date": business_date.isoformat(),
            "operation_id": operation.id,
            "balance_before": settlements.money(balance),
        },
    )
    settlements.commit(db)
    return settlements.Created(receipt.id, created=True)


def reverse_receipt(
    db: Session, actor: User, pending_id: int, receipt_id: int, body: s.DocumentReversalIn
) -> settlements.Created:
    _require_admin(actor, "Solo un administrador puede revertir un cobro.")
    reason = _reason(body.reason)
    locked = _lock(db, pending_id)
    receipt = db.scalars(
        select(PendingCollectionReceipt)
        .where(PendingCollectionReceipt.id == receipt_id, PendingCollectionReceipt.pending_id == pending_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if receipt is None:
        raise AppError(404, "receipt_not_found", "El cobro no existe en este pendiente.")
    request_id = uuid5(NAMESPACE, f"receipt-reversal:{body.client_request_id}")
    if _already_reversed(db, actor, receipt, request_id, reason, "receipt_already_reversed", "El cobro"):
        return settlements.Created(receipt.id, created=False)

    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=receipt.operation_id,
        source=ledger.SourceRef(SOURCE_TYPE, pending_id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    _mark_reversed(receipt, actor, reversal.id, reason)
    service.record_event(
        db, actor, locked.settlement, pending_id, "RECEIPT_REVERSED", None, None, reason=reason, receipt_id=receipt.id
    )
    _audit_reversal(db, actor, "receipt", receipt.id, receipt.operation_id, reversal.id, reversal.business_date, reason)
    settlements.commit(db)
    return settlements.Created(receipt.id, created=True)


# --- resolutions ----------------------------------------------------------------------------------


def resolve(db: Session, actor: User, pending_id: int, body: s.ResolutionIn) -> settlements.Created:
    _require_admin(actor, "Solo un administrador puede resolver un pendiente.")
    reason = _reason(body.reason)
    business_date = body.business_date or business_today()
    parts = _parts(body)
    fingerprint = idempotency.fingerprint(
        actor.id,
        "pending_resolution",
        {
            "pending_id": pending_id,
            "amount": settlements.money(body.amount),
            "reason": reason,
            "business_date": business_date.isoformat(),
            "allocations": sorted(
                [target, employee or 0, settlements.money(amount)] for target, employee, amount in parts
            ),
        },
    )
    locked = _lock(db, pending_id)
    existing = db.scalars(
        select(PendingCollectionResolution).where(
            PendingCollectionResolution.client_request_id == body.client_request_id
        )
    ).one_or_none()
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "resolution_id", existing.id)
        db.commit()
        return settlements.Created(existing.id, created=False)

    settlement, pending = locked.settlement, locked.pending
    balance = _balance(db, settlement, pending)
    _check_date(
        settlement, business_date, "invalid_resolution_date", "La resolución no puede ser anterior a la rendición."
    )
    employees = [employee for _, employee, _ in parts if employee is not None]
    names = dict(db.execute(select(Employee.id, Employee.full_name).where(Employee.id.in_(employees))).all())
    missing = [e for e in employees if e not in names]
    if missing:
        raise AppError(422, "employee_not_found", "El empleado no existe.", {"employee_id": missing[0]})
    _check_balance(pending.id, body.amount, balance)

    legs = [ledger.Leg(ledger.system_account_id(db, "pending_collections"), -body.amount)]
    for _, employee, amount in parts:
        if employee is None:
            account_id = ledger.system_account_id(db, "uncollectible")
        else:
            account_id = employee_account_id(db, employee_id=employee, employee_name=names[employee], user_id=actor.id)
        legs.append(ledger.Leg(account_id, amount))
    operation = ledger.post_operation(
        db,
        actor=actor,
        type="PENDING_WRITE_OFF",
        legs=legs,
        client_request_id=uuid5(NAMESPACE, f"resolution:{body.client_request_id}"),
        business_date=business_date,
        description=f"Resolución del pendiente #{pending.id} de la rendición #{settlement.id}: {reason}",
        source=ledger.SourceRef(SOURCE_TYPE, pending.id),
    ).operation
    resolution = PendingCollectionResolution(
        pending_id=pending.id,
        amount=body.amount,
        business_date=business_date,
        reason=reason,
        operation_id=operation.id,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    db.add(resolution)
    db.flush()
    db.add_all(
        PendingResolutionAllocation(resolution_id=resolution.id, target=target, employee_id=employee, amount=amount)
        for target, employee, amount in parts
    )
    service.record_event(
        db, actor, settlement, pending.id, "RESOLVED", None, None, reason=reason, resolution_id=resolution.id
    )
    _audit(
        db,
        actor,
        "settlements.pending_collection.resolved",
        "resolution",
        resolution.id,
        {
            "pending_id": pending.id,
            "amount": settlements.money(body.amount),
            "reason": reason,
            "business_date": business_date.isoformat(),
            "allocations": [
                {"target": target, "employee_id": employee, "amount": settlements.money(amount)}
                for target, employee, amount in parts
            ],
            "operation_id": operation.id,
            "balance_before": settlements.money(balance),
        },
    )
    settlements.commit(db)
    return settlements.Created(resolution.id, created=True)


def reverse_resolution(
    db: Session, actor: User, pending_id: int, resolution_id: int, body: s.DocumentReversalIn
) -> settlements.Created:
    _require_admin(actor, "Solo un administrador puede revertir una resolución.")
    reason = _reason(body.reason)
    locked = _lock(db, pending_id)
    resolution = db.scalars(
        select(PendingCollectionResolution)
        .where(PendingCollectionResolution.id == resolution_id, PendingCollectionResolution.pending_id == pending_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if resolution is None:
        raise AppError(404, "resolution_not_found", "La resolución no existe en este pendiente.")
    request_id = uuid5(NAMESPACE, f"resolution-reversal:{body.client_request_id}")
    if _already_reversed(db, actor, resolution, request_id, reason, "resolution_already_reversed", "La resolución"):
        return settlements.Created(resolution.id, created=False)

    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=resolution.operation_id,
        source=ledger.SourceRef(SOURCE_TYPE, pending_id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    _mark_reversed(resolution, actor, reversal.id, reason)
    service.record_event(
        db,
        actor,
        locked.settlement,
        pending_id,
        "RESOLUTION_REVERSED",
        None,
        None,
        reason=reason,
        resolution_id=resolution.id,
    )
    _audit_reversal(
        db, actor, "resolution", resolution.id, resolution.operation_id, reversal.id, reversal.business_date, reason
    )
    settlements.commit(db)
    return settlements.Created(resolution.id, created=True)


# --- responsible ----------------------------------------------------------------------------------


def change_responsible(db: Session, actor: User, pending_id: int, body: s.ResponsibleIn) -> None:
    """Who answers for collecting the pending (admin, also after the close). Nothing else of the load changes,
    the ledger is not touched and the settlement is not reopened; the database allows only this column."""
    _require_admin(actor, "Solo un administrador puede cambiar el responsable de un pendiente cerrado.")
    locked = _lock(db, pending_id)
    pending = locked.pending
    if pending.voided_at is not None:
        raise AppError(409, "pending_voided", "El pendiente está anulado y no se modifica.")
    before = pending.responsible_employee_id
    if body.responsible_employee_id == before:
        db.commit()
        return
    if body.responsible_employee_id != locked.settlement.employee_id:
        service.active_employee(db, body.responsible_employee_id)
    reason = (body.reason or "").strip() or None
    pending.responsible_employee_id = body.responsible_employee_id
    pending.updated_by = actor.id
    service.record_event(
        db,
        actor,
        locked.settlement,
        pending.id,
        "RESPONSIBLE_CHANGED",
        {"responsible_employee_id": before},
        {"responsible_employee_id": body.responsible_employee_id},
        reason=reason,
    )
    record_change(
        db,
        "settlements.pending_collection.responsible_changed",
        action="update",
        user_id=actor.id,
        entity_type="pending_collection",
        entity_id=pending.id,
        before={"responsible_employee_id": before},
        after={
            "responsible_employee_id": body.responsible_employee_id,
            "reason": reason,
            "settlement_status": locked.settlement.status,
        },
    )
    settlements.commit(db)


# --- internals ------------------------------------------------------------------------------------


def _lock(db: Session, pending_id: int) -> Locked:
    """The settlement FOR SHARE, then the pending FOR UPDATE (the lock order of every pending write)."""
    found = db.get(PendingCollection, pending_id)
    if found is None:
        raise AppError(404, "pending_not_found", "El pendiente no existe.")
    settlement = db.scalars(
        select(Settlement)
        .where(Settlement.id == found.settlement_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one()
    pending = db.scalars(
        select(PendingCollection)
        .where(PendingCollection.id == pending_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()
    return Locked(settlement, pending)


def _balance(db: Session, settlement: Settlement, pending: PendingCollection) -> Decimal:
    """Amount in effect minus what was applied, with the pending locked: the only balance there is."""
    in_effect = service.in_effect(db, settlement.current_close_operation_id).get(pending.id)
    if in_effect is None:
        raise AppError(
            409,
            "pending_not_in_effect",
            "El pendiente no está vigente en Cajas (todavía no se cerró su rendición, o fue anulado): "
            "no se cobra ni se resuelve.",
            {"pending_id": pending.id},
        )
    applied = service.applied_amounts(db, [pending.id]).get(pending.id, ZERO)
    return in_effect.amount - applied


def _check_balance(pending_id: int, amount: Decimal, balance: Decimal) -> None:
    if amount > balance:
        raise AppError(
            409,
            "pending_amount_exceeds_balance",
            f"El importe supera el saldo del pendiente (${settlements.money(balance)}).",
            {"pending_id": pending_id, "amount": settlements.money(amount), "balance": settlements.money(balance)},
        )


def _check_date(settlement: Settlement, business_date: date, code: str, message: str) -> None:
    if business_date < settlement.business_date:
        raise AppError(422, code, message, {"settlement_business_date": settlement.business_date.isoformat()})
    if business_date > business_today():
        raise AppError(422, "future_business_date", "La fecha no puede ser futura.")


def _parts(body: s.ResolutionIn) -> list[tuple[str, int | None, Decimal]]:
    """Who absorbs the resolution: distinct employees and at most one company part, adding up exactly."""
    parts: list[tuple[str, int | None, Decimal]] = []
    for part in body.allocations:
        if (part.target == "EMPLOYEE") != (part.employee_id is not None):
            raise AppError(
                422,
                "invalid_allocation",
                "Cada parte de la resolución es de un empleado (con su id) o de la empresa (sin empleado).",
            )
        parts.append((part.target, part.employee_id, part.amount))
    employees = [employee for _, employee, _ in parts if employee is not None]
    total = sum((amount for _, _, amount in parts), ZERO)
    details = {"amount": settlements.money(body.amount), "allocated": settlements.money(total)}
    if len(set(employees)) != len(employees) or len(parts) - len(employees) > 1:
        raise AppError(
            422,
            "resolution_allocation_mismatch",
            "Cada empleado y la empresa pueden aparecer una sola vez en la resolución.",
            details,
        )
    if total != body.amount:
        raise AppError(
            422,
            "resolution_allocation_mismatch",
            "Las partes de la resolución tienen que sumar exactamente su importe.",
            details,
        )
    return parts


def _already_reversed(
    db: Session,
    actor: User,
    row: PendingCollectionReceipt | PendingCollectionResolution,
    request_id: UUID,
    reason: str,
    code: str,
    label: str,
) -> bool:
    """True when this request already reversed the row (a retry); 409 when something else did."""
    if row.reversal_operation_id is None:
        return False
    reversal = ledger.get_operation(db, row.reversal_operation_id)
    if reversal.client_request_id == request_id:
        if (row.reversed_by, row.reversal_reason) != (actor.id, reason):
            raise AppError(
                409,
                "idempotency_conflict",
                "Ese identificador de solicitud ya se usó para un reverso distinto.",
                {"operation_id": reversal.id},
            )
        db.commit()
        return True
    raise AppError(409, code, f"{label} ya fue revertido.", {"reversal_operation_id": row.reversal_operation_id})


def _mark_reversed(
    row: PendingCollectionReceipt | PendingCollectionResolution, actor: User, reversal_id: int, reason: str
) -> None:
    row.reversal_operation_id = reversal_id
    row.reversed_by = actor.id
    row.reversed_at = datetime.now(UTC)
    row.reversal_reason = reason


def _account(db: Session, account_id: int) -> ledger.AccountBalance | None:
    try:
        return ledger.account_balance(db, account_id)
    except AppError:
        return None


def _require_admin(actor: User, message: str) -> None:
    if not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(403, "forbidden", message)


def _reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo.")
    return reason


def _audit(db: Session, actor: User, event: str, entity: str, entity_id: int, after: dict[str, Any]) -> None:
    record_change(
        db,
        event,
        action="create",
        user_id=actor.id,
        entity_type=f"pending_collection_{entity}",
        entity_id=entity_id,
        before=None,
        after=after,
    )


def _audit_reversal(
    db: Session,
    actor: User,
    entity: str,
    entity_id: int,
    operation_id: int,
    reversal_id: int,
    business_date: date,
    reason: str,
) -> None:
    record_change(
        db,
        f"settlements.pending_collection.{entity}_reversed",
        action="reverse",
        user_id=actor.id,
        entity_type=f"pending_collection_{entity}",
        entity_id=entity_id,
        before={"operation_id": operation_id},
        after={
            "reversal_operation_id": reversal_id,
            "business_date": business_date.isoformat(),
            "reason": reason,
        },
    )
