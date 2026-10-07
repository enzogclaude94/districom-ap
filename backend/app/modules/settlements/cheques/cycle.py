"""The financial cycle of a cheque (R4-2): deposit, crediting, rejection, return and their reversals.

Each step is a document with exactly one ledger operation (source = the document), written in one
transaction with the cheque's events; the database checks at commit that the operation is exactly the
document, leg by leg and cheque by cheque (migration 0013).

- Deposit (operator): a slip takes cheques of the portfolio to a bank account. ONE `CHEQUE_DEPOSIT` with a
  pair of legs per cheque: "Cartera de cheques" -X / "Cheques depositados" +X. A cheque is deposited only
  once payable and never after its presentation deadline (`cheque_presentation_expired`): it is a rule of
  the instrument, no role or setting bypasses it (Enzo's R4-2 decision A). An expired cheque stays in the
  portfolio, shown as expired, and can be returned.
- Crediting (operator): the bank credited one or several deposited cheques. `CHEQUE_CREDIT`: per cheque
  "Cheques depositados" -X, the bank +total. Only to the bank of each cheque's deposit.
- Rejection (operator): the bank rejected a deposited cheque. In transit: "Cheques depositados" -X; after
  crediting: the bank -X, even into an overdraft («Banco en descubierto», `ledger.service`). Either way
  "Pendientes de cobro" +X with a NEW pending owed by who delivered it (never the old one reopened).
- Return (admin, with a reason): a cheque of the portfolio given back to who delivered it. `CHEQUE_RETURN`:
  "Cartera de cheques" -X, "Pendientes de cobro" +X, a new pending. Not a delivery to a supplier (R6).
- Reversal (admin, with a reason) of any of them: "that document was wrong". It keeps the original's
  business date (`created_at` says when it was corrected), is owned by the document (never from Cajas), is
  refused while a later document of the same cheque is in effect, and a reversed rejection or return voids
  the pending it created, which must have nothing applied.

Lock order, always: the cheques by id (FOR UPDATE), the document, the pending (then, in the ledger, the
accounts). Two deposits of one cheque, or a crediting racing a deposit's reversal, serialize on the
cheque and the second sees where the first left it.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.cheques import schemas as s
from app.modules.settlements.cheques import terms
from app.modules.settlements.cheques.models import (
    Cheque,
    ChequeCredit,
    ChequeCreditItem,
    ChequeDeposit,
    ChequeDepositItem,
    ChequeRejection,
    ChequeReturn,
)
from app.modules.settlements.cheques.service import record_event
from app.modules.settlements.cheques.state import Outcome
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.delivery.service import Created, money
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.pending import service as pendings
from app.modules.settlements.pending.models import PendingCollection
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("3b0f6c1e-9a7d-4e25-8c41-5d2e7f9a1b63")
ZERO = Decimal("0.00")

Document = ChequeDeposit | ChequeCredit | ChequeRejection | ChequeReturn


@dataclass(frozen=True)
class Kind:
    """How each document of the cycle is named in the ledger, the events and the messages."""

    operation_type: str
    source_type: str
    event: str
    reversed_event: str
    label: str
    already_reversed: str


DEPOSIT = Kind(
    "CHEQUE_DEPOSIT", "cheque_deposit", "DEPOSITED", "DEPOSIT_REVERSED", "El depósito", "deposit_already_reversed"
)
CREDIT = Kind(
    "CHEQUE_CREDIT", "cheque_credit", "CREDITED", "CREDIT_REVERSED", "La acreditación", "credit_already_reversed"
)
REJECTION = Kind(
    "CHEQUE_REJECTION", "cheque_rejection", "REJECTED", "REJECTION_REVERSED", "El rechazo", "rejection_already_reversed"
)
RETURN = Kind(
    "CHEQUE_RETURN", "cheque_return", "RETURNED", "RETURN_REVERSED", "La devolución", "return_already_reversed"
)


# --- deposit --------------------------------------------------------------------------------------


def deposit(db: Session, actor: User, body: s.DepositIn) -> Created:
    business_date = body.business_date or business_today()
    cheque_ids = sorted(set(body.cheque_ids))
    slip = (body.slip_number or "").strip() or None
    notes = (body.notes or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        "cheque_deposit",
        {
            "bank_account_id": body.bank_account_id,
            "cheque_ids": cheque_ids,
            "business_date": business_date.isoformat(),
            "slip_number": slip,
            "notes": notes,
        },
    )
    replay = _replay(db, ChequeDeposit, body.client_request_id, fingerprint, "deposit_id")
    if replay is not None:
        return replay
    cheques = _lock_cheques(db, cheque_ids)
    replay = _replay(db, ChequeDeposit, body.client_request_id, fingerprint, "deposit_id")
    if replay is not None:
        return replay

    _check_not_future(business_date)
    bank = _bank_account(db, body.bank_account_id)
    positions = ledger.cheque_positions(db, cheque_ids)
    entered = ledger.cheque_entry_dates(db, cheque_ids, "CHEQUE_PORTFOLIO")
    for c in cheques:
        if c.voided_at is not None or positions[c.id].get("CHEQUE_PORTFOLIO", ZERO) <= 0:
            raise AppError(
                409, "cheque_not_in_portfolio", "Solo se deposita un cheque que está en cartera.", {"cheque_id": c.id}
            )
        payable = terms.payable_from(c.payment_type, c.issue_date, c.payment_date)
        if business_date < payable:
            raise AppError(
                422,
                "cheque_not_yet_payable",
                f"El cheque es de pago diferido: se deposita desde el {payable:%d/%m/%Y}.",
                {"cheque_id": c.id, "payable_from": payable.isoformat()},
            )
        # Never an expired cheque, not even an admin (Enzo's R4-2 decision A).
        terms.check_presentable(c.payment_type, c.issue_date, c.payment_date, business_date)
        if business_date < entered[c.id]:
            raise AppError(
                422,
                "invalid_deposit_date",
                "El depósito no puede ser anterior al ingreso del cheque a la cartera.",
                {"cheque_id": c.id, "entered_on": entered[c.id].isoformat()},
            )

    document = ChequeDeposit(
        bank_account_id=bank.account_id,
        business_date=business_date,
        slip_number=slip,
        notes=notes,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    db.add(document)
    db.flush()
    db.add_all(ChequeDepositItem(deposit_id=document.id, cheque_id=c.id, amount=c.amount) for c in cheques)
    portfolio = ledger.system_account_id(db, "cheque_portfolio")
    transit = ledger.system_account_id(db, "cheques_in_transit")
    legs = [
        leg for c in cheques for leg in (ledger.Leg(portfolio, -c.amount, c.id), ledger.Leg(transit, c.amount, c.id))
    ]
    total = sum((c.amount for c in cheques), ZERO)
    operation = _post(
        db,
        actor,
        DEPOSIT,
        document,
        legs,
        body.client_request_id,
        f"Depósito de {len(cheques)} cheque(s) en {bank.name}" + (f", boleta {slip}" if slip else ""),
    )
    for c in cheques:
        record_event(
            db,
            actor,
            c,
            DEPOSIT.event,
            None,
            {"deposit_id": document.id, "bank_account_id": bank.account_id, "business_date": business_date.isoformat()},
            operation_id=operation.id,
        )
    _audit(
        db,
        actor,
        "deposit",
        document.id,
        {
            "bank_account_id": bank.account_id,
            "business_date": business_date.isoformat(),
            "slip_number": slip,
            "cheque_ids": cheque_ids,
            "total": money(total),
            "operation_id": operation.id,
        },
    )
    commit(db)
    return Created(document.id, created=True)


# --- crediting ------------------------------------------------------------------------------------


def credit(db: Session, actor: User, body: s.CreditIn) -> Created:
    business_date = body.business_date or business_today()
    cheque_ids = sorted(set(body.cheque_ids))
    notes = (body.notes or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        "cheque_credit",
        {
            "bank_account_id": body.bank_account_id,
            "cheque_ids": cheque_ids,
            "business_date": business_date.isoformat(),
            "notes": notes,
        },
    )
    replay = _replay(db, ChequeCredit, body.client_request_id, fingerprint, "credit_id")
    if replay is not None:
        return replay
    cheques = _lock_cheques(db, cheque_ids)
    replay = _replay(db, ChequeCredit, body.client_request_id, fingerprint, "credit_id")
    if replay is not None:
        return replay

    _check_not_future(business_date)
    bank = _bank_account(db, body.bank_account_id)
    positions = ledger.cheque_positions(db, cheque_ids)
    deposited = deposit_items_in_effect(db, cheque_ids)
    for c in cheques:
        found = deposited.get(c.id)
        if found is None or positions[c.id].get("CHEQUES_IN_TRANSIT", ZERO) <= 0:
            raise AppError(
                409,
                "cheque_not_deposited",
                "Solo se acredita un cheque depositado que el banco todavía no acreditó ni rechazó.",
                {"cheque_id": c.id},
            )
        _, slip = found
        if slip.bank_account_id != bank.account_id:
            raise AppError(
                422,
                "bank_account_mismatch",
                "El cheque se acredita en el banco donde se depositó.",
                {"cheque_id": c.id, "bank_account_id": slip.bank_account_id},
            )
        if business_date < slip.business_date:
            raise AppError(
                422,
                "invalid_credit_date",
                "La acreditación no puede ser anterior al depósito.",
                {"cheque_id": c.id, "deposit_business_date": slip.business_date.isoformat()},
            )

    document = ChequeCredit(
        bank_account_id=bank.account_id,
        business_date=business_date,
        notes=notes,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    db.add(document)
    db.flush()
    db.add_all(
        ChequeCreditItem(credit_id=document.id, cheque_id=c.id, deposit_item_id=deposited[c.id][0].id, amount=c.amount)
        for c in cheques
    )
    transit = ledger.system_account_id(db, "cheques_in_transit")
    total = sum((c.amount for c in cheques), ZERO)
    legs = [ledger.Leg(transit, -c.amount, c.id) for c in cheques] + [ledger.Leg(bank.account_id, total)]
    operation = _post(
        db,
        actor,
        CREDIT,
        document,
        legs,
        body.client_request_id,
        f"Acreditación de {len(cheques)} cheque(s) en {bank.name}",
    )
    for c in cheques:
        record_event(
            db,
            actor,
            c,
            CREDIT.event,
            None,
            {"credit_id": document.id, "bank_account_id": bank.account_id, "business_date": business_date.isoformat()},
            operation_id=operation.id,
        )
    _audit(
        db,
        actor,
        "credit",
        document.id,
        {
            "bank_account_id": bank.account_id,
            "business_date": business_date.isoformat(),
            "cheque_ids": cheque_ids,
            "total": money(total),
            "operation_id": operation.id,
        },
    )
    commit(db)
    return Created(document.id, created=True)


# --- rejection ------------------------------------------------------------------------------------


def reject(db: Session, actor: User, cheque_id: int, body: s.RejectionIn) -> Created:
    business_date = body.business_date or business_today()
    reason = _reason(body.reason)
    fingerprint = idempotency.fingerprint(
        actor.id,
        "cheque_rejection",
        {
            "cheque_id": cheque_id,
            "reason": reason,
            "business_date": business_date.isoformat(),
            "responsible_employee_id": body.responsible_employee_id,
        },
    )
    replay = _replay(db, ChequeRejection, body.client_request_id, fingerprint, "rejection_id")
    if replay is not None:
        return replay
    cheque = _lock_cheques(db, [cheque_id])[0]
    replay = _replay(db, ChequeRejection, body.client_request_id, fingerprint, "rejection_id")
    if replay is not None:
        return replay

    _check_not_future(business_date)
    position = ledger.cheque_positions(db, [cheque.id])[cheque.id]
    deposited = deposit_items_in_effect(db, [cheque.id]).get(cheque.id)
    credited = credit_items_in_effect(db, [cheque.id]).get(cheque.id)
    rejected = _live(db, ChequeRejection, [cheque.id])
    if deposited is not None and position.get("CHEQUES_IN_TRANSIT", ZERO) > 0:
        stage, since, bank_account_id = "IN_TRANSIT", deposited[1].business_date, None
    elif deposited is not None and credited is not None and not rejected:
        stage, since, bank_account_id = "AFTER_CREDIT", credited[1].business_date, credited[1].bank_account_id
    else:
        raise AppError(
            409,
            "cheque_not_rejectable",
            "Solo se rechaza un cheque depositado (en tránsito o ya acreditado) que no tenga un rechazo vigente.",
            {"cheque_id": cheque.id},
        )
    if business_date < since:
        raise AppError(
            422,
            "invalid_rejection_date",
            "El rechazo no puede ser anterior al depósito o a la acreditación del cheque.",
            {"since": since.isoformat()},
        )
    responsible = _responsible(db, cheque, body.responsible_employee_id)

    document = ChequeRejection(
        cheque_id=cheque.id,
        stage=stage,
        deposit_item_id=deposited[0].id,
        credit_item_id=credited[0].id if stage == "AFTER_CREDIT" and credited else None,
        bank_account_id=bank_account_id,
        business_date=business_date,
        reason=reason,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    db.add(document)
    db.flush()
    pending_account = ledger.system_account_id(db, "pending_collections")
    if stage == "IN_TRANSIT":
        debit = ledger.Leg(ledger.system_account_id(db, "cheques_in_transit"), -cheque.amount, cheque.id)
    else:
        assert bank_account_id is not None
        debit = ledger.Leg(bank_account_id, -cheque.amount)
    operation = _post(
        db,
        actor,
        REJECTION,
        document,
        [debit, ledger.Leg(pending_account, cheque.amount)],
        body.client_request_id,
        f"Rechazo del cheque #{cheque.id} ({cheque.delivered_by_name}): {reason}",
    )
    pending = _new_pending(
        db,
        actor,
        cheque,
        responsible,
        f"Cheque #{cheque.id} rechazado: {reason}",
        uuid5(NAMESPACE, f"rejection-pending:{body.client_request_id}"),
        fingerprint,
        origin_cheque_rejection_id=document.id,
    )
    record_event(
        db,
        actor,
        cheque,
        REJECTION.event,
        None,
        {
            "rejection_id": document.id,
            "stage": stage,
            "pending_id": pending.id,
            "business_date": business_date.isoformat(),
        },
        reason=reason,
        operation_id=operation.id,
    )
    _audit(
        db,
        actor,
        "rejection",
        document.id,
        {
            "cheque_id": cheque.id,
            "stage": stage,
            "bank_account_id": bank_account_id,
            "business_date": business_date.isoformat(),
            "reason": reason,
            "pending_id": pending.id,
            "operation_id": operation.id,
        },
    )
    commit(db)
    return Created(document.id, created=True)


# --- return ---------------------------------------------------------------------------------------


def give_back(db: Session, actor: User, cheque_id: int, body: s.ReturnIn) -> Created:
    """Return a cheque of the portfolio to who delivered it (admin, with a reason)."""
    _require_admin(actor, "Solo un administrador devuelve un cheque de la cartera.")
    business_date = body.business_date or business_today()
    reason = _reason(body.reason)
    fingerprint = idempotency.fingerprint(
        actor.id,
        "cheque_return",
        {
            "cheque_id": cheque_id,
            "reason": reason,
            "business_date": business_date.isoformat(),
            "responsible_employee_id": body.responsible_employee_id,
        },
    )
    replay = _replay(db, ChequeReturn, body.client_request_id, fingerprint, "return_id")
    if replay is not None:
        return replay
    cheque = _lock_cheques(db, [cheque_id])[0]
    replay = _replay(db, ChequeReturn, body.client_request_id, fingerprint, "return_id")
    if replay is not None:
        return replay

    _check_not_future(business_date)
    position = ledger.cheque_positions(db, [cheque.id])[cheque.id]
    if cheque.voided_at is not None or position.get("CHEQUE_PORTFOLIO", ZERO) <= 0:
        raise AppError(
            409, "cheque_not_in_portfolio", "Solo se devuelve un cheque que está en cartera.", {"cheque_id": cheque.id}
        )
    entered = ledger.cheque_entry_dates(db, [cheque.id], "CHEQUE_PORTFOLIO")[cheque.id]
    if business_date < entered:
        raise AppError(
            422,
            "invalid_return_date",
            "La devolución no puede ser anterior al ingreso del cheque a la cartera.",
            {"entered_on": entered.isoformat()},
        )
    responsible = _responsible(db, cheque, body.responsible_employee_id)

    document = ChequeReturn(
        cheque_id=cheque.id,
        business_date=business_date,
        reason=reason,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    db.add(document)
    db.flush()
    operation = _post(
        db,
        actor,
        RETURN,
        document,
        [
            ledger.Leg(ledger.system_account_id(db, "cheque_portfolio"), -cheque.amount, cheque.id),
            ledger.Leg(ledger.system_account_id(db, "pending_collections"), cheque.amount),
        ],
        body.client_request_id,
        f"Devolución del cheque #{cheque.id} a {cheque.delivered_by_name}: {reason}",
    )
    pending = _new_pending(
        db,
        actor,
        cheque,
        responsible,
        f"Cheque #{cheque.id} devuelto: {reason}",
        uuid5(NAMESPACE, f"return-pending:{body.client_request_id}"),
        fingerprint,
        origin_cheque_return_id=document.id,
    )
    record_event(
        db,
        actor,
        cheque,
        RETURN.event,
        None,
        {"return_id": document.id, "pending_id": pending.id, "business_date": business_date.isoformat()},
        reason=reason,
        operation_id=operation.id,
    )
    _audit(
        db,
        actor,
        "return",
        document.id,
        {
            "cheque_id": cheque.id,
            "business_date": business_date.isoformat(),
            "reason": reason,
            "pending_id": pending.id,
            "operation_id": operation.id,
        },
    )
    commit(db)
    return Created(document.id, created=True)


# --- reversals (admin, with a reason) -------------------------------------------------------------


def reverse_deposit(db: Session, actor: User, deposit_id: int, body: s.CycleReversalIn) -> Created:
    def check(document: Document, cheques: list[Cheque]) -> None:
        positions = ledger.cheque_positions(db, [c.id for c in cheques])
        moved = [c.id for c in cheques if positions[c.id].get("CHEQUES_IN_TRANSIT", ZERO) <= 0]
        if moved:
            raise _has_activity(moved[0], "El banco ya acreditó o rechazó un cheque del depósito: revertí eso primero.")

    return _reverse(db, actor, ChequeDeposit, DEPOSIT, deposit_id, body, check)


def reverse_credit(db: Session, actor: User, credit_id: int, body: s.CycleReversalIn) -> Created:
    def check(document: Document, cheques: list[Cheque]) -> None:
        rejected = _live(db, ChequeRejection, [c.id for c in cheques])
        if rejected:
            raise _has_activity(
                min(rejected), "Un cheque de la acreditación fue rechazado: revertí el rechazo primero."
            )

    return _reverse(db, actor, ChequeCredit, CREDIT, credit_id, body, check)


def reverse_rejection(db: Session, actor: User, rejection_id: int, body: s.CycleReversalIn) -> Created:
    return _reverse(db, actor, ChequeRejection, REJECTION, rejection_id, body, None)


def reverse_return(db: Session, actor: User, return_id: int, body: s.CycleReversalIn) -> Created:
    return _reverse(db, actor, ChequeReturn, RETURN, return_id, body, None)


def _reverse(
    db: Session,
    actor: User,
    model: type[Document],
    kind: Kind,
    document_id: int,
    body: s.CycleReversalIn,
    check: Any,
) -> Created:
    _require_admin(actor, "Solo un administrador revierte un documento del ciclo del cheque.")
    reason = _reason(body.reason)
    found = cast(Document | None, db.get(model, document_id))
    if found is None:
        raise AppError(404, "cheque_document_not_found", f"{kind.label} no existe.")
    cheques = _lock_cheques(db, cheque_ids_of(db, found))
    document = cast(
        Document,
        db.scalars(
            select(model).where(model.id == document_id).with_for_update().execution_options(populate_existing=True)
        ).one(),
    )
    request_id = uuid5(NAMESPACE, f"{kind.source_type}-reversal:{body.client_request_id}")
    if document.reversal_operation_id is not None:
        reversal = ledger.get_operation(db, document.reversal_operation_id)
        if reversal.client_request_id == request_id:
            if (document.reversed_by, document.reversal_reason) != (actor.id, reason):
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Ese identificador de solicitud ya se usó para un reverso distinto.",
                    {"operation_id": reversal.id},
                )
            db.commit()
            return Created(document.id, created=False)
        raise AppError(
            409, kind.already_reversed, f"{kind.label} ya fue revertido.", {"reversal_operation_id": reversal.id}
        )

    pending = None
    if isinstance(document, ChequeRejection | ChequeReturn):
        pending = _lock_origin_pending(db, document)
        _check_pending_untouched(db, pending)
    elif check is not None:
        check(document, cheques)
    original = ledger.document_operation(db, kind.operation_type, ledger.SourceRef(kind.source_type, document.id))
    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=original.id,
        source=ledger.SourceRef(kind.source_type, document.id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    document.reversal_operation_id = reversal.id
    document.reversed_by = actor.id
    document.reversed_at = datetime.now(UTC)
    document.reversal_reason = reason
    db.flush()
    if pending is not None:
        pending.voided_by = actor.id
        pending.voided_at = datetime.now(UTC)
        pending.void_reason = f"{kind.label} del cheque fue revertido: {reason}"
        pendings.record_event(
            db, actor, None, pending.id, "VOIDED", {"voided": False}, {"voided": True}, reason=pending.void_reason
        )
    for c in cheques:
        record_event(
            db,
            actor,
            c,
            kind.reversed_event,
            None,
            {f"{kind.source_type.removeprefix('cheque_')}_id": document.id, "reversal_operation_id": reversal.id},
            reason=reason,
            operation_id=reversal.id,
        )
    record_change(
        db,
        f"settlements.{kind.source_type}.reversed",
        action="reverse",
        user_id=actor.id,
        entity_type=kind.source_type,
        entity_id=document.id,
        before={"operation_id": original.id},
        after={
            "reversal_operation_id": reversal.id,
            "business_date": reversal.business_date.isoformat(),
            "reason": reason,
            "voided_pending_id": pending.id if pending else None,
        },
    )
    commit(db)
    return Created(document.id, created=True)


# --- reading --------------------------------------------------------------------------------------


def deposit_items_in_effect(
    db: Session, cheque_ids: Sequence[int]
) -> dict[int, tuple[ChequeDepositItem, ChequeDeposit]]:
    """The deposit in effect (not reversed) of each cheque, if any: at most one, by the position rules."""
    if not cheque_ids:
        return {}
    rows = db.execute(
        select(ChequeDepositItem, ChequeDeposit)
        .join(ChequeDeposit, ChequeDeposit.id == ChequeDepositItem.deposit_id)
        .where(ChequeDepositItem.cheque_id.in_(cheque_ids), ChequeDeposit.reversal_operation_id.is_(None))
        .order_by(ChequeDepositItem.id)
    )
    return {item.cheque_id: (item, slip) for item, slip in rows.all()}


def credit_items_in_effect(db: Session, cheque_ids: Sequence[int]) -> dict[int, tuple[ChequeCreditItem, ChequeCredit]]:
    if not cheque_ids:
        return {}
    rows = db.execute(
        select(ChequeCreditItem, ChequeCredit)
        .join(ChequeCredit, ChequeCredit.id == ChequeCreditItem.credit_id)
        .where(ChequeCreditItem.cheque_id.in_(cheque_ids), ChequeCredit.reversal_operation_id.is_(None))
        .order_by(ChequeCreditItem.id)
    )
    return {item.cheque_id: (item, doc) for item, doc in rows.all()}


def outcomes(db: Session, cheque_ids: Sequence[int]) -> dict[int, Outcome]:
    """What took each cheque out of the company's hands, by the documents in effect: a return, a rejection
    (in transit or after crediting) or a crediting."""
    result: dict[int, Outcome] = dict.fromkeys(credit_items_in_effect(db, cheque_ids), "CREDITED")
    result.update(dict.fromkeys(_live(db, ChequeRejection, cheque_ids), "REJECTED"))
    result.update(dict.fromkeys(_live(db, ChequeReturn, cheque_ids), "RETURNED"))
    return result


def cheque_ids_of(db: Session, document: Document) -> list[int]:
    if isinstance(document, ChequeDeposit):
        return list(
            db.scalars(
                select(ChequeDepositItem.cheque_id)
                .where(ChequeDepositItem.deposit_id == document.id)
                .order_by(ChequeDepositItem.cheque_id)
            )
        )
    if isinstance(document, ChequeCredit):
        return list(
            db.scalars(
                select(ChequeCreditItem.cheque_id)
                .where(ChequeCreditItem.credit_id == document.id)
                .order_by(ChequeCreditItem.cheque_id)
            )
        )
    return [document.cheque_id]


def origin_pending(db: Session, document: ChequeRejection | ChequeReturn) -> PendingCollection:
    column = (
        PendingCollection.origin_cheque_rejection_id
        if isinstance(document, ChequeRejection)
        else PendingCollection.origin_cheque_return_id
    )
    return db.scalars(select(PendingCollection).where(column == document.id)).one()


# --- internals ------------------------------------------------------------------------------------


def commit(db: Session) -> None:
    """Commit; a rule only the database saw (a concurrent change of a cheque) is 409, never a raw error."""
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        logger.warning("cheque_cycle_commit_rejected", extra={"sqlstate": getattr(exc.orig, "sqlstate", None)})
        raise AppError(
            409, "invalid_cheque_operation", "El movimiento del cheque no cumple sus reglas y no se registró."
        ) from exc


def _post(
    db: Session, actor: User, kind: Kind, document: Document, legs: list[ledger.Leg], request: UUID, description: str
) -> Any:
    return ledger.post_operation(
        db,
        actor=actor,
        type=kind.operation_type,
        legs=legs,
        client_request_id=uuid5(NAMESPACE, f"{kind.source_type}:{request}"),
        business_date=document.business_date,
        description=description,
        source=ledger.SourceRef(kind.source_type, document.id),
    ).operation


def _replay(db: Session, model: type[Document], request_id: UUID, fingerprint: str, entity: str) -> Created | None:
    existing = cast(
        Document | None, db.scalars(select(model).where(model.client_request_id == request_id)).one_or_none()
    )
    if existing is None:
        return None
    idempotency.check_replay(existing.request_fingerprint, fingerprint, entity, existing.id)
    db.commit()  # release the locks taken, if any
    return Created(existing.id, created=False)


def _lock_cheques(db: Session, cheque_ids: Sequence[int]) -> list[Cheque]:
    ids = sorted(set(cheque_ids))
    rows = list(
        db.scalars(
            select(Cheque)
            .where(Cheque.id.in_(ids))
            .order_by(Cheque.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    missing = sorted(set(ids) - {c.id for c in rows})
    if missing:
        raise AppError(404, "cheque_not_found", "El cheque no existe.", {"cheque_id": missing[0]})
    return rows


def _lock_origin_pending(db: Session, document: ChequeRejection | ChequeReturn) -> PendingCollection:
    pending = origin_pending(db, document)
    return db.scalars(
        select(PendingCollection)
        .where(PendingCollection.id == pending.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _check_pending_untouched(db: Session, pending: PendingCollection) -> None:
    """A rejection or a return is reversed only while the pending it created has nothing applied nor an ECHEQ
    declared to pay it: those happened, and are reversed first."""
    applied = pendings.applied_amounts(db, [pending.id]).get(pending.id, ZERO)
    declared = db.scalar(
        select(Cheque.id).where(
            Cheque.origin_pending_id == pending.id,
            Cheque.voided_at.is_(None),
            Cheque.verification_status == "DECLARED",
        )
    )
    if applied > 0 or declared is not None:
        raise AppError(
            409,
            "pending_has_activity",
            "El pendiente que generó ya tiene cobros, resoluciones o un ECHEQ declarado: revertilos primero.",
            {"pending_id": pending.id, "applied": money(applied), "declared_cheque_id": declared},
        )


def _new_pending(
    db: Session,
    actor: User,
    cheque: Cheque,
    responsible: int,
    reason: str,
    request_id: UUID,
    fingerprint: str,
    **origin: int,
) -> PendingCollection:
    """The NEW pending a rejection or a return creates: owed by who delivered the cheque, for its amount."""
    pending = PendingCollection(
        origin_type="CHEQUE_REJECTION" if "origin_cheque_rejection_id" in origin else "CHEQUE_RETURN",
        customer_name=cheque.delivered_by_name,
        amount=cheque.amount,
        reason=reason,
        responsible_employee_id=responsible,
        client_request_id=request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
        **origin,
    )
    db.add(pending)
    db.flush()
    snapshot = {
        "origin_type": pending.origin_type,
        "cheque_id": cheque.id,
        "customer_name": pending.customer_name,
        "amount": money(pending.amount),
        "reason": reason,
        "responsible_employee_id": responsible,
    }
    pendings.record_event(db, actor, None, pending.id, "CREATED", None, snapshot)
    record_change(
        db,
        "settlements.pending_collection.created",
        action="create",
        user_id=actor.id,
        entity_type="pending_collection",
        entity_id=pending.id,
        before=None,
        after=snapshot,
    )
    return pending


def _responsible(db: Session, cheque: Cheque, chosen: int | None) -> int:
    """Who answers for the new pending: the one chosen, else the driver of the cheque's settlement, else the
    responsible of the pending the cheque paid."""
    if chosen is not None:
        return pendings.active_employee(db, chosen).id
    if cheque.settlement_id is not None:
        settlement = db.get(Settlement, cheque.settlement_id)
        assert settlement is not None  # foreign key
        return settlement.employee_id
    if cheque.origin_pending_id is not None:
        paid = db.get(PendingCollection, cheque.origin_pending_id)
        assert paid is not None  # foreign key
        return paid.responsible_employee_id
    raise AppError(
        422,
        "responsible_required",
        "El cheque no vino de una rendición: indicá qué empleado se ocupa de cobrar el pendiente.",
    )


def _live(db: Session, model: type[ChequeRejection] | type[ChequeReturn], cheque_ids: Sequence[int]) -> set[int]:
    if not cheque_ids:
        return set()
    return set(
        db.scalars(
            select(model.cheque_id).where(model.cheque_id.in_(cheque_ids), model.reversal_operation_id.is_(None))
        )
    )


def _bank_account(db: Session, account_id: int) -> ledger.AccountBalance:
    try:
        account = ledger.account_balance(db, account_id)
    except AppError:
        account = None
    if account is None or not account.active or account.kind != "BANK":
        raise AppError(
            422,
            "invalid_bank_account",
            "Los cheques se depositan en una cuenta bancaria activa.",
            {"bank_account_id": account_id},
        )
    return account


def _check_not_future(business_date: date) -> None:
    if business_date > business_today():
        raise AppError(422, "future_business_date", "La fecha no puede ser futura.")


def _has_activity(cheque_id: int, message: str) -> AppError:
    return AppError(409, "cheque_has_activity", message, {"cheque_id": cheque_id})


def _require_admin(actor: User, message: str) -> None:
    if not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(403, "forbidden", message)


def _reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo.")
    return reason


def _audit(db: Session, actor: User, entity: str, entity_id: int, after: dict[str, Any]) -> None:
    record_change(
        db,
        f"settlements.cheque_{entity}.created",
        action="create",
        user_id=actor.id,
        entity_type=f"cheque_{entity}",
        entity_id=entity_id,
        before=None,
        after=after,
    )
