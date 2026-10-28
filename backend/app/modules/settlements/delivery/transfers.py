"""Transfers of a settlement and their verification (Enzo's R2-P3 and R2-P5).

A transfer is loaded as DECLARED: the driver says a customer transferred. Only a VERIFIED transfer is
money received; DECLARED and REJECTED never justify anything, and a promise to transfer later is not a
transfer but a pending collection (R3). An operator verifies or rejects (rejecting needs a reason).
While the settlement is OPEN an admin can correct or undo a decision already taken, always with a
reason. Every decision is kept in `transfer_status_events`; none is overwritten silently.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery import idempotency, service
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import SettlementTransfer, TransferStatusEvent
from app.modules.settlements.ledger import service as ledger
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User

MODULE_KEY = "settlements"


def create(db: Session, actor: User, settlement_id: int, body: s.TransferIn) -> service.Created:
    bank_account_id = body.bank_account_id or ledger.system_account_id(db, "bank")
    reference = (body.reference or "").strip() or None
    payer_name = (body.payer_name or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        "transfer",
        {
            "settlement_id": settlement_id,
            "amount": service.money(body.amount),
            "bank_account_id": bank_account_id,
            "reference": reference,
            "payer_name": payer_name,
        },
    )
    existing = _by_request_id(db, body)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "transfer_id", existing.id)
        return service.Created(existing.id, created=False)

    settlement = service.lock_open(db, settlement_id)
    _require_bank(db, bank_account_id)
    transfer = SettlementTransfer(
        settlement_id=settlement.id,
        amount=body.amount,
        bank_account_id=bank_account_id,
        reference=reference,
        payer_name=payer_name,
        status="DECLARED",
        status_changed_by=actor.id,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(transfer)
            db.flush()
    except IntegrityError:
        winner = _by_request_id(db, body)
        if winner is None:
            raise
        db.rollback()  # release the locks taken for the insert that lost
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "transfer_id", winner.id)
        return service.Created(winner.id, created=False)
    db.add(TransferStatusEvent(transfer_id=transfer.id, from_status=None, to_status="DECLARED", changed_by=actor.id))
    record_change(
        db,
        "settlements.transfer.created",
        action="create",
        user_id=actor.id,
        entity_type="settlement_transfer",
        entity_id=transfer.id,
        before=None,
        after={"settlement_id": settlement.id, **_snapshot(transfer)},
    )
    service.commit(db)
    return service.Created(transfer.id, created=True)


def update(db: Session, actor: User, settlement_id: int, transfer_id: int, body: s.TransferPatch) -> None:
    """Correct the data of a DECLARED transfer; a decided one is corrected through its status."""
    settlement = service.lock_open(db, settlement_id)
    transfer = _get(db, settlement.id, transfer_id)
    if transfer.status != "DECLARED":
        raise AppError(
            409,
            "transfer_already_decided",
            "La transferencia ya fue verificada o rechazada: para corregirla, cambiá primero su estado.",
        )
    before = _snapshot(transfer)
    fields = body.model_fields_set
    if "amount" in fields:
        if body.amount is None:
            raise AppError(422, "invalid_amount", "El importe es obligatorio.")
        transfer.amount = body.amount
    if "bank_account_id" in fields:
        transfer.bank_account_id = body.bank_account_id or ledger.system_account_id(db, "bank")
        _require_bank(db, transfer.bank_account_id)
    if "reference" in fields:
        transfer.reference = (body.reference or "").strip() or None
    if "payer_name" in fields:
        transfer.payer_name = (body.payer_name or "").strip() or None
    after = _snapshot(transfer)
    if before != after:
        record_change(
            db,
            "settlements.transfer.updated",
            action="update",
            user_id=actor.id,
            entity_type="settlement_transfer",
            entity_id=transfer.id,
            before=before,
            after=after,
        )
    service.commit(db)


def change_status(db: Session, actor: User, settlement_id: int, transfer_id: int, body: s.TransferStatusIn) -> None:
    """Verify, reject, or (admin) correct a decision. Asking for the current status changes nothing."""
    settlement = service.lock_open(db, settlement_id)
    transfer = _get(db, settlement.id, transfer_id)
    target = body.status
    if transfer.status == target:
        service.commit(db)  # nothing changed: just release the lock
        return
    reason = (body.reason or "").strip() or None
    is_correction = transfer.status != "DECLARED"
    if is_correction:
        if not has_module_access(actor, MODULE_KEY, "admin"):
            raise AppError(
                403,
                "forbidden",
                "Solo un administrador puede corregir una transferencia ya verificada o rechazada.",
            )
        if reason is None:
            raise AppError(422, "reason_required", "Indicá el motivo de la corrección.")
    elif target == "REJECTED" and reason is None:
        raise AppError(422, "reason_required", "Indicá el motivo del rechazo.")

    previous = transfer.status
    transfer.status = target
    transfer.status_reason = reason
    transfer.status_changed_by = actor.id
    transfer.status_changed_at = datetime.now(UTC)
    db.add(
        TransferStatusEvent(
            transfer_id=transfer.id,
            from_status=previous,
            to_status=target,
            reason=reason,
            is_correction=is_correction,
            changed_by=actor.id,
        )
    )
    record_change(
        db,
        "settlements.transfer.status_changed",
        action="correct" if is_correction else "decide",
        user_id=actor.id,
        entity_type="settlement_transfer",
        entity_id=transfer.id,
        before={"status": previous},
        after={"status": target, "reason": reason},
    )
    service.commit(db)


def of(db: Session, settlement_id: int) -> list[SettlementTransfer]:
    return list(
        db.scalars(
            select(SettlementTransfer)
            .where(SettlementTransfer.settlement_id == settlement_id)
            .order_by(SettlementTransfer.id)
        )
    )


def events_of(db: Session, transfer_ids: list[int]) -> dict[int, list[TransferStatusEvent]]:
    result: dict[int, list[TransferStatusEvent]] = {tid: [] for tid in transfer_ids}
    if transfer_ids:
        rows = db.scalars(
            select(TransferStatusEvent)
            .where(TransferStatusEvent.transfer_id.in_(transfer_ids))
            .order_by(TransferStatusEvent.id)
        )
        for event in rows:
            result[event.transfer_id].append(event)
    return result


def _get(db: Session, settlement_id: int, transfer_id: int) -> SettlementTransfer:
    transfer = db.get(SettlementTransfer, transfer_id)
    if transfer is None or transfer.settlement_id != settlement_id:
        raise AppError(404, "transfer_not_found", "La transferencia no existe en esta rendición.")
    return transfer


def _require_bank(db: Session, account_id: int) -> None:
    banks = {a.account_id for a in ledger.accounts(db) if a.kind == "BANK" and a.active}
    if account_id not in banks:
        raise AppError(422, "invalid_bank_account", "La cuenta de destino tiene que ser un banco activo.")


def _snapshot(transfer: SettlementTransfer) -> dict[str, Any]:
    # The payer's name is not audited: only whether there is one.
    return {
        "amount": service.money(transfer.amount),
        "bank_account_id": transfer.bank_account_id,
        "reference": transfer.reference,
        "has_payer_name": transfer.payer_name is not None,
    }


def _by_request_id(db: Session, body: s.TransferIn) -> SettlementTransfer | None:
    return db.scalars(
        select(SettlementTransfer).where(SettlementTransfer.client_request_id == body.client_request_id)
    ).first()
