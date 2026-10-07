"""A pending collection paid with a cheque (R4-2): `PENDING_COLLECTION` with method CHEQUE.

- Physical: received in hand, it pays the pending at once. One operation, atomically with the cheque and the
  receipt: "Pendientes de cobro" -X and "Cartera de cheques" +X (naming the cheque).
- ECHEQ: declared, it lowers nothing and does not enter the portfolio. When verified, the pending's balance is
  checked again (other receipts may have come in meanwhile): if it still covers the ECHEQ, the same operation
  is published; otherwise `pending_amount_exceeds_balance` and nothing changes. A rejected verification
  records `ECHEQ_DECLINED` on the pending.
- A cheque larger than the balance is refused, never accepted in part and never leaves a credit balance.

Reversing such a receipt (`pending.applications.reverse_receipt`, admin) also voids the cheque, which must
still be in the portfolio.

Lock order: the settlement of the pending (FOR SHARE, if any), the pending, the cheque (then the accounts).
"""

from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.cheques import identity, service, terms
from app.modules.settlements.cheques import schemas as s
from app.modules.settlements.cheques.models import Cheque
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery.service import Created, money
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.pending import applications
from app.modules.settlements.pending import service as pendings
from app.modules.settlements.pending.models import PendingCollectionReceipt
from app.platform.audit.service import record_change
from app.platform.auth.models import User

# Namespace of the ids derived for a verified ECHEQ's receipt; never change it.
NAMESPACE = UUID("c7f2a8e4-1d3b-4f6a-9e5c-0b8d2a4f6e19")


def receive(db: Session, actor: User, pending_id: int, body: s.ChequeReceiptIn) -> Created:
    """Register the cheque a customer gave for a pending; returns the cheque."""
    business_date = body.business_date or business_today()
    data = service._clean(body.model_dump())
    fingerprint = idempotency.fingerprint(
        actor.id,
        "pending_cheque_receipt",
        {
            "pending_id": pending_id,
            **data,
            "business_date": business_date.isoformat(),
            "physical_receipt_confirmed": body.physical_receipt_confirmed,
        },
    )
    locked = applications.lock(db, pending_id)
    existing = service._by_request_id(db, body.client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "cheque_id", existing.id)
        db.commit()
        return Created(existing.id, created=False)

    pending = locked.pending
    balance = applications.balance_of(db, pending)
    applications.check_date(
        db, pending, business_date, "invalid_receipt_date", "El cobro no puede ser anterior al origen del pendiente."
    )
    service._check_receipt_confirmation(body.kind, body.physical_receipt_confirmed)
    instrument = service._instrument(db, data, received_on=business_date)
    applications.check_balance(pending.id, instrument.amount, balance)
    confirmation = service._check_identity(db, instrument, None, body)
    cheque = service._new_cheque(
        actor,
        instrument,
        origin_type="PENDING_RECEIPT",
        settlement_id=None,
        received_on=business_date,
        received_by=None,
        verification="DECLARED",
        request=(body.client_request_id, fingerprint),
    )
    cheque.origin_pending_id = pending.id
    winner = service._insert(db, cheque, body.client_request_id, fingerprint)
    if winner is not None:
        return winner
    after = {
        **service._snapshot(cheque),
        "pending_id": pending.id,
        **({"duplicate_confirmation": confirmation} if confirmation else {}),
    }
    service.record_event(db, actor, cheque, "RECEIVED", None, after)
    service._audit(db, actor, cheque, "settlements.cheque.received_for_pending", "create", None, after)
    if cheque.kind == "PHYSICAL":
        _pay(db, actor, locked, cheque, business_date, body.client_request_id, fingerprint, balance)
    else:
        pendings.record_event(
            db,
            actor,
            locked.settlement,
            pending.id,
            "ECHEQ_DECLARED",
            None,
            {"amount": money(cheque.amount), "cheque_number": cheque.cheque_number},
            cheque_id=cheque.id,
        )
    service._commit_identity(db)
    return Created(cheque.id, created=True)


def decide_echeq(db: Session, actor: User, pending_id: int, cheque_id: int, body: s.PendingEcheqVerificationIn) -> None:
    """Verify the ECHEQ declared for a pending (and collect it) or reject its verification."""
    locked = applications.lock(db, pending_id)
    cheque = service._lock(db, cheque_id)
    if cheque.origin_pending_id != pending_id:
        raise AppError(404, "cheque_not_found", "El cheque no fue declarado para este pendiente.")
    if cheque.kind != "ECHEQ":
        raise AppError(422, "not_an_echeq", "Solo un ECHEQ se verifica: un cheque físico se recibe en mano.")
    service._require_live(cheque)
    if cheque.verification_status == body.status:
        db.commit()  # a retry: nothing changed
        return
    if cheque.verification_status != "DECLARED":
        raise AppError(
            409,
            "echeq_already_decided",
            "La verificación de este ECHEQ ya se decidió: si fue rechazada, declaralo de nuevo.",
        )
    reason = (body.reason or "").strip() or None
    business_date = body.business_date or business_today()
    pending = locked.pending
    if body.status == "VERIFICATION_REJECTED":
        if reason is None:
            raise AppError(422, "reason_required", "Indicá por qué no se pudo verificar el ECHEQ.")
        _decide(db, actor, cheque, "VERIFICATION_REJECTED", reason)
        pendings.record_event(
            db, actor, locked.settlement, pending.id, "ECHEQ_DECLINED", None, None, reason=reason, cheque_id=cheque.id
        )
        service._commit_identity(db)
        return

    if not identity.is_complete("ECHEQ", service._identity_of(cheque)):
        raise AppError(
            422,
            "echeq_identity_required",
            "Para verificar el ECHEQ cargá su identificador electrónico o, si el banco no lo muestra, la cuenta "
            "girada (CBU) y el CUIT del librador.",
        )
    applications.check_date(
        db, pending, business_date, "invalid_receipt_date", "El cobro no puede ser anterior al origen del pendiente."
    )
    if business_date < cheque.received_business_date:
        raise AppError(
            422,
            "invalid_receipt_date",
            "El ECHEQ no puede cobrarse antes de haberse declarado.",
            {"received_business_date": cheque.received_business_date.isoformat()},
        )
    terms.check_presentable(cheque.payment_type, cheque.issue_date, cheque.payment_date, business_date)
    # Revalidated now: the balance may have changed since it was declared.
    balance = applications.balance_of(db, pending)
    applications.check_balance(pending.id, cheque.amount, balance)
    _decide(db, actor, cheque, "VERIFIED", reason)
    request_id = uuid5(NAMESPACE, f"echeq-receipt:{cheque.id}")
    fingerprint = idempotency.fingerprint(
        actor.id, "pending_echeq_receipt", {"cheque_id": cheque.id, "business_date": business_date.isoformat()}
    )
    _pay(db, actor, locked, cheque, business_date, request_id, fingerprint, balance)
    service._commit_identity(db)


def _pay(
    db: Session,
    actor: User,
    locked: applications.Locked,
    cheque: Cheque,
    business_date: date,
    request_id: UUID,
    fingerprint: str,
    balance: Any,
) -> None:
    """The receipt with the cheque: Pendientes de cobro -X, Cartera de cheques +X (naming it), one operation."""
    pending = locked.pending
    portfolio = ledger.system_account_id(db, "cheque_portfolio")
    operation = ledger.post_operation(
        db,
        actor=actor,
        type="PENDING_COLLECTION",
        legs=[
            ledger.Leg(ledger.system_account_id(db, "pending_collections"), -cheque.amount),
            ledger.Leg(portfolio, cheque.amount, cheque.id),
        ],
        client_request_id=uuid5(applications.NAMESPACE, f"receipt:{request_id}"),
        business_date=business_date,
        description=f"Cobro con cheque #{cheque.id} del pendiente #{pending.id}{applications.origin_label(pending)}",
        source=ledger.SourceRef(applications.SOURCE_TYPE, pending.id),
    ).operation
    receipt = PendingCollectionReceipt(
        pending_id=pending.id,
        amount=cheque.amount,
        business_date=business_date,
        method="CHEQUE",
        cheque_id=cheque.id,
        destination_account_id=portfolio,
        operation_id=operation.id,
        client_request_id=request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    db.add(receipt)
    db.flush()
    pendings.record_event(
        db, actor, locked.settlement, pending.id, "RECEIPT_RECORDED", None, None, receipt_id=receipt.id
    )
    service.record_event(
        db,
        actor,
        cheque,
        "COLLECTED",
        None,
        {"pending_id": pending.id, "receipt_id": receipt.id, "business_date": business_date.isoformat()},
        operation_id=operation.id,
    )
    record_change(
        db,
        "settlements.pending_collection.receipt_recorded",
        action="create",
        user_id=actor.id,
        entity_type="pending_collection_receipt",
        entity_id=receipt.id,
        before=None,
        after={
            "pending_id": pending.id,
            "amount": money(cheque.amount),
            "method": "CHEQUE",
            "cheque_id": cheque.id,
            "business_date": business_date.isoformat(),
            "operation_id": operation.id,
            "balance_before": money(balance),
        },
    )


def _decide(db: Session, actor: User, cheque: Cheque, status: str, reason: str | None) -> None:
    previous = cheque.verification_status
    cheque.verification_status = status
    cheque.verification_changed_by = actor.id
    cheque.verification_changed_at = datetime.now(UTC)
    cheque.verification_reason = reason
    db.flush()  # before any leg names it: a published cheque's verification never changes
    service.record_event(
        db,
        actor,
        cheque,
        status,
        {"verification_status": previous},
        {"verification_status": status, "correction": False},
        reason=reason,
    )
    service._audit(
        db,
        actor,
        cheque,
        "settlements.cheque.verification",
        "decide",
        {"verification_status": previous},
        {"verification_status": status, "reason": reason},
    )


def declared_for(db: Session, pending_id: int) -> list[Cheque]:
    return list(db.scalars(select(Cheque).where(Cheque.origin_pending_id == pending_id).order_by(Cheque.id)))
