"""Reading payments (R6-1): every payment with its third party, concept, items and state, and one payment
with its operation, reversal and history."""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.cash.schemas import EntryOut, OperationOut
from app.modules.settlements.cheques import views as cheque_views
from app.modules.settlements.cheques.models import Cheque, ChequeEvent
from app.modules.settlements.delivery.models import ExpenseCategory
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.payments import schemas as s
from app.modules.settlements.payments.models import COUNTER_ACCOUNTS, Payment, PaymentItem
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.third_parties import tax_ids
from app.platform.third_parties.models import ThirdParty


def list_payments(db: Session, q: s.PaymentListQuery) -> s.PaymentPage:
    query = select(Payment)
    if q.third_party_id is not None:
        query = query.where(Payment.third_party_id == q.third_party_id)
    if q.concept is not None:
        query = query.where(Payment.concept == q.concept)
    if q.date_from is not None:
        query = query.where(Payment.business_date >= q.date_from)
    if q.date_to is not None:
        query = query.where(Payment.business_date <= q.date_to)
    if q.account_id is not None:
        query = query.where(
            exists().where(PaymentItem.payment_id == Payment.id, PaymentItem.account_id == q.account_id)
        )
    if q.state == "IN_EFFECT":
        query = query.where(Payment.reversal_operation_id.is_(None))
    elif q.state == "REVERSED":
        query = query.where(Payment.reversal_operation_id.is_not(None))
    filtered = query.subquery()
    total, amount = db.execute(
        select(
            func.count(),
            func.coalesce(func.sum(filtered.c.amount).filter(filtered.c.reversal_operation_id.is_(None)), 0),
        ).select_from(filtered)
    ).one()
    rows = list(
        db.scalars(query.order_by(Payment.business_date.desc(), Payment.id.desc()).limit(q.limit).offset(q.offset))
    )
    return s.PaymentPage(items=outs(db, rows), total=total, amount_in_effect=Decimal(amount))


def detail(db: Session, payment_id: int) -> s.PaymentDetail:
    payment = db.get(Payment, payment_id)
    if payment is None:
        raise AppError(404, "payment_not_found", "El pago no existe.")
    db.refresh(payment)
    (out,) = outs(db, [payment])
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "payment", AuditEvent.entity_id == str(payment.id))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )
    names = _users(db, {e.user_id for e in events if e.user_id is not None})
    return s.PaymentDetail(
        **out.model_dump(),
        operation=_operation(db, payment.operation_id),
        reversal=_operation(db, payment.reversal_operation_id),
        history=[
            s.PaymentEventOut(
                id=e.id,
                event_type=e.event_type.removeprefix("settlements.payment."),
                occurred_at=e.occurred_at,
                user_name=names.get(e.user_id) if e.user_id is not None else None,
                before=(e.payload or {}).get("before"),
                after=(e.payload or {}).get("after"),
            )
            for e in events
        ],
    )


def outs(db: Session, payments: Sequence[Payment]) -> list[s.PaymentOut]:
    if not payments:
        return []
    ids = [p.id for p in payments]
    items: dict[int, list[PaymentItem]] = {pid: [] for pid in ids}
    for item in db.scalars(select(PaymentItem).where(PaymentItem.payment_id.in_(ids)).order_by(PaymentItem.id)):
        items[item.payment_id].append(item)
    third_parties = {
        t.id: t for t in db.scalars(select(ThirdParty).where(ThirdParty.id.in_({p.third_party_id for p in payments})))
    }
    categories = dict(
        db.execute(
            select(ExpenseCategory.id, ExpenseCategory.name).where(
                ExpenseCategory.id.in_({p.expense_category_id for p in payments if p.expense_category_id})
            )
        ).all()
    )
    # Names and kinds of the accounts, read through the ledger service (its models are its own).
    accounts = {a.account_id: a for a in ledger.account_balances(db, include_inactive=True)}
    by_code = {a.code: a.name for a in accounts.values() if a.code}
    cheques = _cheques(db, [(i.payment_id, i.cheque_id) for rows in items.values() for i in rows if i.cheque_id])
    users = _users(db, {p.created_by for p in payments} | {p.reversed_by for p in payments if p.reversed_by})
    result = []
    for payment in payments:
        third_party = third_parties[payment.third_party_id]
        result.append(
            s.PaymentOut(
                id=payment.id,
                state="REVERSED" if payment.reversal_operation_id is not None else "IN_EFFECT",
                business_date=payment.business_date,
                third_party_id=third_party.id,
                third_party_name=third_party.name,
                third_party_tax_id_display=tax_ids.display(third_party.tax_id) if third_party.tax_id else None,
                third_party_active=third_party.active,
                concept=payment.concept,
                expense_category_id=payment.expense_category_id,
                expense_category_name=categories.get(payment.expense_category_id)
                if payment.expense_category_id
                else None,
                counter_account_name=by_code.get(COUNTER_ACCOUNTS[payment.concept], ""),
                amount=payment.amount,
                reference=payment.reference,
                description=payment.description,
                items=[
                    s.PaymentItemOut(
                        id=item.id,
                        method=item.method,
                        account_id=item.account_id,
                        account_name=accounts[item.account_id].name if item.account_id in accounts else None,
                        account_kind=accounts[item.account_id].kind if item.account_id in accounts else None,
                        cheque_id=item.cheque_id,
                        cheque=cheques.get((payment.id, item.cheque_id)) if item.cheque_id is not None else None,
                        amount=item.amount,
                        reference=item.reference,
                    )
                    for item in items[payment.id]
                ],
                operation_id=payment.operation_id,
                reversal_operation_id=payment.reversal_operation_id,
                created_by=payment.created_by,
                created_by_name=users.get(payment.created_by, ""),
                created_at=payment.created_at,
                reversed_by_name=users.get(payment.reversed_by) if payment.reversed_by else None,
                reversed_at=payment.reversed_at,
                reversal_reason=payment.reversal_reason,
            )
        )
    return result


def _cheques(db: Session, pairs: list[tuple[int, int]]) -> dict[tuple[int, int], s.PaymentChequeOut]:
    """Each delivered cheque as it is now, by (payment, cheque), with who confirmed its recovery and when."""
    if not pairs:
        return {}
    cheque_ids = {cheque_id for _, cheque_id in pairs}
    now = {c.id: c for c in cheque_views.items(db, list(db.scalars(select(Cheque).where(Cheque.id.in_(cheque_ids)))))}
    recoveries = {
        (e.payment_id, e.cheque_id): e
        for e in db.scalars(
            select(ChequeEvent).where(
                ChequeEvent.payment_id.in_({payment_id for payment_id, _ in pairs}),
                ChequeEvent.event_type == "RECOVERED_FROM_PAYMENT",
            )
        )
    }
    names = _users(db, {e.created_by for e in recoveries.values()})
    result = {}
    for pair in pairs:
        c, event = now[pair[1]], recoveries.get(pair)
        result[pair] = s.PaymentChequeOut(
            cheque_id=c.id,
            kind=c.kind,
            payment_type=c.payment_type,
            cheque_number=c.cheque_number,
            bank_name=c.bank_name,
            drawer_name=c.drawer_name,
            delivered_by_name=c.delivered_by_name,
            payable_from=c.payable_from,
            state=c.state,
            state_label=c.state_label,
            recovered_at=event.created_at if event else None,
            recovered_by_name=names.get(event.created_by) if event else None,
        )
    return result


def _operation(db: Session, operation_id: int | None) -> OperationOut | None:
    if operation_id is None:
        return None
    d = ledger.operation_detail(db, operation_id)
    return OperationOut(
        **{k: v for k, v in d.__dict__.items() if k != "entries"},
        entries=[EntryOut(**entry.__dict__) for entry in d.entries],
    )


def _users(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(User.id, User.full_name).where(User.id.in_(ids))).all())
