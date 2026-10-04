"""Additions (R2-D5): cash added to a settlement without redoing the count, e.g. $25.000 found later.

An addition keeps amount, cash box, optional description, who and when. It never changes the count,
is never edited nor deleted, and one loaded by mistake is voided with a reason while the settlement
is OPEN, keeping who voided it and when (the database allows no other change, migration 0007).
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery import idempotency, service
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import CashAddition
from app.platform.audit.service import record_change
from app.platform.auth.models import User


def create(db: Session, actor: User, settlement_id: int, body: s.AdditionIn) -> service.Created:
    description = (body.description or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        "cash_addition",
        {
            "settlement_id": settlement_id,
            "amount": service.money(body.amount),
            "destination_account_id": body.destination_account_id,
            "description": description,
        },
    )
    existing = _by_request_id(db, body)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "addition_id", existing.id)
        return service.Created(existing.id, created=False)

    settlement = service.lock_open(db, settlement_id)
    service.require_cash_box(db, body.destination_account_id)
    addition = CashAddition(
        settlement_id=settlement.id,
        amount=body.amount,
        destination_account_id=body.destination_account_id,
        description=description,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(addition)
            db.flush()
    except IntegrityError:
        winner = _by_request_id(db, body)
        if winner is None:
            raise
        db.rollback()  # release the locks taken for the insert that lost
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "addition_id", winner.id)
        return service.Created(winner.id, created=False)
    record_change(
        db,
        "settlements.cash_addition.created",
        action="create",
        user_id=actor.id,
        entity_type="cash_addition",
        entity_id=addition.id,
        before=None,
        after={
            "settlement_id": settlement.id,
            "amount": service.money(addition.amount),
            "destination_account_id": addition.destination_account_id,
        },
    )
    service.commit(db)
    return service.Created(addition.id, created=True)


def void(db: Session, actor: User, settlement_id: int, addition_id: int, reason: str) -> None:
    settlement = service.lock_open(db, settlement_id)
    addition = db.get(CashAddition, addition_id)
    if addition is None or addition.settlement_id != settlement.id:
        raise AppError(404, "addition_not_found", "El agregado no existe en esta rendición.")
    if addition.voided_at is not None:
        raise AppError(409, "addition_already_voided", "El agregado ya estaba anulado.")
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la anulación.")
    addition.voided_by = actor.id
    addition.voided_at = datetime.now(UTC)
    addition.void_reason = reason
    record_change(
        db,
        "settlements.cash_addition.voided",
        action="void",
        user_id=actor.id,
        entity_type="cash_addition",
        entity_id=addition.id,
        before={"voided": False},
        after={"voided": True, "reason": reason},
    )
    service.commit(db)


def of(db: Session, settlement_id: int) -> list[CashAddition]:
    return list(
        db.scalars(select(CashAddition).where(CashAddition.settlement_id == settlement_id).order_by(CashAddition.id))
    )


def _by_request_id(db: Session, body: s.AdditionIn) -> CashAddition | None:
    return db.scalars(select(CashAddition).where(CashAddition.client_request_id == body.client_request_id)).first()
