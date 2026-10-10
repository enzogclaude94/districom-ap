"""Payments to third parties (R6-1): what left one or several cash boxes or bank accounts, to whom, and why.

A payment is the document of its own ledger operation: in ONE transaction the service writes the payment,
its items, the `PAYMENT` operation (each item's account -X, ONE counter-leg +total in the technical account
of the concept, source `payment:<id>`, on the payment's business date) and the audit; the database checks
at commit that the payment, its items and its operation are exactly each other (migration 0019).

- Never accounts payable: there is no debt, invoice or balance owed; only a payment made.
- Means: CASH (an active cash box) and TRANSFER (an active bank account the user chooses), at most one item
  per account. CHEQUE belongs to the model and is refused until R6-2 (`payment_method_not_enabled`).
- Amount: exactly the sum of the items; a total sent by the client is verified, never trusted.
- Concepts: MERCHANDISE, EXPENSE (an active category, never fuel: fuel goes through a settlement or a direct
  expense with its load), REFUND, and OTHER (admin only, with a description).
- Third party: exists and is active, locked FOR SHARE until the commit, so a concurrent deactivation either
  waits for the payment or wins and the payment is refused.
- Funds: an ordinary debit through `ledger.service`: a cash box is never negative, today or on any day from
  the business date on; the bank never goes into an overdraft for it, nor deeper into one.
- Money is immutable once published. A mistake is a reversal (admin, with a reason, the whole payment, on
  the ORIGINAL business date) plus a new payment. Cajas never reverses a `PAYMENT` directly.
- Descriptive correction (admin, with a reason): only the reference, the description and item references.

Idempotent by `client_request_id`: the ledger request ids derive from the client's.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.delivery import idempotency
from app.modules.settlements.delivery.models import FUEL, ExpenseCategory
from app.modules.settlements.delivery.service import money
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.payments import schemas as s
from app.modules.settlements.payments.models import COUNTER_ACCOUNTS, Payment, PaymentItem
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User
from app.platform.third_parties import service as third_parties

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
SOURCE = "payment"
ENABLED_METHODS = ("CASH", "TRANSFER")
METHOD_KINDS = {"CASH": "CASH_BOX", "TRANSFER": "BANK"}
CONCEPT_LABELS = {
    "MERCHANDISE": "Pago por mercadería",
    "EXPENSE": "Pago de gasto",
    "REFUND": "Devolución",
    "OTHER": "Otro pago",
}
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("3f9b1c27-5d84-4a6e-b0c3-8e2d7a41f659")


@dataclass(frozen=True)
class Created:
    id: int
    # False when the request repeated an earlier one (same client_request_id and content).
    created: bool


def create(db: Session, actor: User, body: s.PaymentIn) -> Created:
    reference = _text(body.reference)
    description = _text(body.description)
    items = [(item, _text(item.reference)) for item in body.items]
    request: dict[str, Any] = {
        "business_date": body.business_date.isoformat(),
        "third_party_id": body.third_party_id,
        "concept": body.concept,
        "expense_category_id": body.expense_category_id,
        "amount": money(body.amount) if body.amount is not None else None,
        "reference": reference,
        "description": description,
        "items": [
            {
                "method": item.method,
                "account_id": item.account_id,
                "cheque_id": item.cheque_id,
                "amount": money(item.amount),
                "reference": item_reference,
            }
            for item, item_reference in items
        ],
    }
    fingerprint = idempotency.fingerprint(actor.id, "payment", request)
    existing = _by_request_id(db, body.client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "payment_id", existing.id)
        return Created(existing.id, created=False)

    if not items:
        raise AppError(422, "payment_items_required", "Indicá con qué se pagó: al menos un medio de pago.")
    if any(item.method not in ENABLED_METHODS for item, _ in items):
        raise AppError(
            422,
            "payment_method_not_enabled",
            "El pago con cheques todavía no está habilitado: usá efectivo o transferencia.",
        )
    if body.business_date > business_today():
        raise AppError(422, "future_business_date", "La fecha del pago no puede ser futura.")
    if body.concept == "OTHER":
        _require_admin(actor, "Solo un administrador registra un pago de concepto «Otro».")
        if description is None:
            raise AppError(422, "description_required", "Un pago de concepto «Otro» lleva una descripción.")
    _check_category(db, body.concept, body.expense_category_id)
    _paying_accounts(db, [item for item, _ in items])
    total = sum((item.amount for item, _ in items), Decimal(0))
    if body.amount is not None and body.amount != total:
        raise AppError(
            422,
            "payment_amount_mismatch",
            "El total del pago no coincide con la suma de los medios de pago.",
            {"amount": money(body.amount), "items_total": money(total)},
        )
    third_party = third_parties.lock_active(db, body.third_party_id)

    payment = Payment(
        third_party_id=third_party.id,
        business_date=body.business_date,
        concept=body.concept,
        expense_category_id=body.expense_category_id if body.concept == "EXPENSE" else None,
        amount=total,
        reference=reference,
        description=description,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(payment)
            db.flush()
    except IntegrityError:
        winner = _by_request_id(db, body.client_request_id)
        if winner is None:
            raise
        db.rollback()
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "payment_id", winner.id)
        return Created(winner.id, created=False)
    rows = [
        PaymentItem(
            payment_id=payment.id,
            method=item.method,
            account_id=item.account_id,
            amount=item.amount,
            reference=item_reference,
        )
        for item, item_reference in items
    ]
    db.add_all(rows)
    db.flush()

    counter = COUNTER_ACCOUNTS[payment.concept]
    operation = ledger.post_operation(
        db,
        actor=actor,
        type="PAYMENT",
        legs=[ledger.Leg(row.account_id, -row.amount) for row in rows if row.account_id is not None]
        + [ledger.Leg(ledger.system_account_id(db, counter), total)],
        client_request_id=uuid5(NAMESPACE, f"payment:{body.client_request_id}"),
        business_date=body.business_date,
        # Fixed with the operation: the payment's own reference and description may still be corrected.
        description=f"Pago n.º {payment.id} · {CONCEPT_LABELS[payment.concept]} · {third_party.name}",
        source=ledger.SourceRef(SOURCE, payment.id),
    ).operation
    payment.operation_id = operation.id
    db.flush()
    record_change(
        db,
        "settlements.payment.created",
        action="create",
        user_id=actor.id,
        entity_type=SOURCE,
        entity_id=payment.id,
        before=None,
        after=snapshot(payment, rows) | {"operation_id": operation.id},
    )
    commit(db)
    return Created(payment.id, created=True)


def reverse(db: Session, actor: User, payment_id: int, body: s.PaymentReversalIn) -> Created:
    """Reverse the whole payment, on its original business date; `created_at` says when."""
    _require_admin(actor, "Solo un administrador revierte un pago.")
    reason = _reason(body.reason)
    payment = _locked(db, payment_id)
    request_id = uuid5(NAMESPACE, f"payment-reversal:{body.client_request_id}")
    if payment.reversal_operation_id is not None:
        reversal = ledger.get_operation(db, payment.reversal_operation_id)
        if reversal.client_request_id == request_id:
            if (payment.reversed_by, payment.reversal_reason) != (actor.id, reason):
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Ese identificador de solicitud ya se usó para un reverso distinto.",
                    {"operation_id": reversal.id},
                )
            db.commit()
            return Created(payment.id, created=False)
        raise AppError(
            409, "payment_already_reversed", "El pago ya fue revertido.", {"reversal_operation_id": reversal.id}
        )
    assert payment.operation_id is not None  # a published payment always has it (0019)
    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=payment.operation_id,
        source=ledger.SourceRef(SOURCE, payment.id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    payment.reversal_operation_id = reversal.id
    payment.reversed_by = actor.id
    payment.reversed_at = datetime.now(UTC)
    payment.reversal_reason = reason
    db.flush()
    record_change(
        db,
        "settlements.payment.reversed",
        action="reverse",
        user_id=actor.id,
        entity_type=SOURCE,
        entity_id=payment.id,
        before={"operation_id": payment.operation_id},
        after={
            "reversal_operation_id": reversal.id,
            "business_date": reversal.business_date.isoformat(),
            "reason": reason,
        },
    )
    commit(db)
    return Created(payment.id, created=True)


def correct(db: Session, actor: User, payment_id: int, body: s.PaymentCorrectionIn) -> None:
    """Change the reference, the description and/or item references of a payment in effect; nothing else."""
    _require_admin(actor, "Solo un administrador corrige un pago.")
    reason = _reason(body.reason)
    payment = _locked(db, payment_id)
    if payment.reversal_operation_id is not None:
        raise AppError(409, "payment_reversed", "El pago fue revertido y ya no se corrige.")
    fields = body.model_fields_set - {"reason"}
    if not fields or (fields == {"items"} and not body.items):
        raise AppError(422, "nothing_to_correct", "Indicá la referencia o la descripción a corregir.")

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if "reference" in fields:
        reference = _text(body.reference)
        if reference != payment.reference:
            before["reference"], after["reference"] = payment.reference, reference
            payment.reference = reference
    if "description" in fields:
        description = _text(body.description)
        if payment.concept == "OTHER" and description is None:
            raise AppError(422, "description_required", "Un pago de concepto «Otro» lleva una descripción.")
        if description != payment.description:
            before["description"], after["description"] = payment.description, description
            payment.description = description
    if body.items:
        rows = {
            row.id: row
            for row in db.scalars(select(PaymentItem).where(PaymentItem.payment_id == payment.id).with_for_update())
        }
        for correction in body.items:
            row = rows.get(correction.id)
            if row is None:
                raise AppError(
                    422, "invalid_payment_item", "Ese medio de pago no es de este pago.", {"item_id": correction.id}
                )
            reference = _text(correction.reference)
            if reference != row.reference:
                before.setdefault("items", []).append({"id": row.id, "reference": row.reference})
                after.setdefault("items", []).append({"id": row.id, "reference": reference})
                row.reference = reference
    if not after:
        db.rollback()  # nothing changed (a repeated correction): release the locks
        return
    db.flush()
    record_change(
        db,
        "settlements.payment.corrected",
        action="update",
        user_id=actor.id,
        entity_type=SOURCE,
        entity_id=payment.id,
        before=before,
        after=after | {"reason": reason},
    )
    commit(db)


# --- shared ---------------------------------------------------------------------------------------


def snapshot(payment: Payment, items: list[PaymentItem]) -> dict[str, Any]:
    return {
        "third_party_id": payment.third_party_id,
        "business_date": payment.business_date.isoformat(),
        "concept": payment.concept,
        "expense_category_id": payment.expense_category_id,
        "amount": money(payment.amount),
        "reference": payment.reference,
        "description": payment.description,
        "items": [
            {"method": i.method, "account_id": i.account_id, "amount": money(i.amount), "reference": i.reference}
            for i in items
        ],
    }


def commit(db: Session) -> None:
    """Commit; a rule only the database saw is 409, never a raw error."""
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        logger.warning("payment_commit_rejected", extra={"sqlstate": getattr(exc.orig, "sqlstate", None)})
        raise AppError(409, "invalid_payment", "El pago no cumple las reglas de los pagos y no se registró.") from exc


def _check_category(db: Session, concept: str, category_id: int | None) -> None:
    if concept != "EXPENSE":
        if category_id is not None:
            raise AppError(
                422,
                "expense_category_not_allowed",
                "Solo un pago de gasto lleva categoría de gasto.",
                {"expense_category_id": category_id},
            )
        return
    if category_id is None:
        raise AppError(422, "expense_category_required", "Elegí la categoría del gasto.")
    category = db.get(ExpenseCategory, category_id)
    if category is None or not category.active:
        raise AppError(422, "invalid_expense_category", "La categoría no existe o está desactivada.")
    if category.code == FUEL:
        raise AppError(
            422,
            "fuel_not_allowed_in_payment",
            "El combustible no se registra como pago: cargalo en la rendición o como gasto directo con su "
            "vehículo, litros y precio.",
        )


def _paying_accounts(db: Session, items: list[s.PaymentItemIn]) -> None:
    """Each item's account: CASH an active cash box, TRANSFER an active bank account; each at most once."""
    seen: set[int] = set()
    balances = {b.account_id: b for b in ledger.account_balances(db, include_inactive=True)}
    for item in items:
        account = balances.get(item.account_id) if item.account_id is not None else None
        if (
            item.cheque_id is not None
            or account is None
            or not account.active
            or account.kind != METHOD_KINDS[item.method]
        ):
            raise AppError(
                422,
                "invalid_payment_account",
                "El efectivo sale de una caja activa y la transferencia de un banco activo.",
                {"method": item.method, "account_id": item.account_id},
            )
        if account.account_id in seen:
            raise AppError(
                422,
                "duplicate_payment_account",
                f"{account.name} aparece dos veces: sumá los importes en un solo medio de pago.",
                {"account_id": account.account_id},
            )
        seen.add(account.account_id)


def _locked(db: Session, payment_id: int) -> Payment:
    payment = db.scalars(
        select(Payment).where(Payment.id == payment_id).with_for_update().execution_options(populate_existing=True)
    ).one_or_none()
    if payment is None:
        raise AppError(404, "payment_not_found", "El pago no existe.")
    return payment


def _by_request_id(db: Session, client_request_id: UUID) -> Payment | None:
    return db.scalars(select(Payment).where(Payment.client_request_id == client_request_id)).one_or_none()


def _require_admin(actor: User, message: str) -> None:
    if not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(403, "forbidden", message)


def _text(value: str | None) -> str | None:
    return (value or "").strip() or None


def _reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo.")
    return reason
