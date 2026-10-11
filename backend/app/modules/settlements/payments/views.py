"""Reading payments (R6-1): every payment with its third party, concept, items and state, and one payment
with its operation, reversal and history."""

from collections.abc import Sequence
from decimal import Decimal
from typing import get_args

from sqlalchemy import ColumnElement, exists, func, or_, select
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
from app.platform.third_parties import service as third_parties
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
    if q.expense_category_id is not None:
        query = query.where(Payment.expense_category_id == q.expense_category_id)
    if q.account_id is not None:
        query = query.where(_has_item(PaymentItem.account_id == q.account_id))
    if q.method is not None:
        query = query.where(_has_item(PaymentItem.method == q.method))
    if q.cheque_id is not None:
        query = query.where(_has_item(PaymentItem.cheque_id == q.cheque_id))
    if q.cheque_number is not None and q.cheque_number.strip():
        numbered = select(Cheque.id).where(Cheque.cheque_number == q.cheque_number.strip())
        query = query.where(_has_item(PaymentItem.cheque_id.in_(numbered)))
    if q.reference is not None and q.reference.strip():
        reference = q.reference.strip()
        query = query.where(
            or_(
                Payment.reference.icontains(reference, autoescape=True),
                _has_item(PaymentItem.reference.icontains(reference, autoescape=True)),
            )
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


def _has_item(condition: ColumnElement[bool]) -> ColumnElement[bool]:
    """The payment has at least one item matching: a mixed payment is kept whole, never split."""
    return exists().where(PaymentItem.payment_id == Payment.id, condition)


def third_party_summary(db: Session, third_party_id: int) -> s.ThirdPartyPaymentSummary:
    """Totals of what was paid to the third party, from the payments in effect only (R6-3). By concept each
    payment adds its amount once; by means each item adds its own amount, so a mixed payment is split among
    its means and their sum is the total in effect. Never a balance: there is no debt to compare it with."""
    third_parties.get(db, third_party_id)
    of_party = Payment.third_party_id == third_party_id
    in_effect = Payment.reversal_operation_id.is_(None)
    concepts = {
        concept: (count, amount)
        for concept, count, amount in db.execute(
            select(Payment.concept, func.count(), func.sum(Payment.amount))
            .where(of_party, in_effect)
            .group_by(Payment.concept)
        )
    }
    methods = {
        method: (count, amount)
        for method, count, amount in db.execute(
            select(PaymentItem.method, func.count(), func.sum(PaymentItem.amount))
            .join(Payment, Payment.id == PaymentItem.payment_id)
            .where(of_party, in_effect)
            .group_by(PaymentItem.method)
        )
    }
    reversed_count = db.scalar(select(func.count()).select_from(Payment).where(of_party, ~in_effect)) or 0
    zero = (0, Decimal(0))
    by_concept = [
        s.ConceptTotal(concept=c, count=concepts.get(c, zero)[0], amount=concepts.get(c, zero)[1])
        for c in get_args(s.Concept)
    ]
    by_method = [
        s.MethodTotal(method=m, count=methods.get(m, zero)[0], amount=methods.get(m, zero)[1])
        for m in get_args(s.Method)
    ]
    cheques = methods.get("CHEQUE", zero)
    return s.ThirdPartyPaymentSummary(
        third_party_id=third_party_id,
        payments_in_effect=sum(t.count for t in by_concept),
        amount_in_effect=sum((t.amount for t in by_concept), Decimal(0)),
        payments_reversed=reversed_count,
        by_concept=by_concept,
        by_method=by_method,
        cheques_delivered=cheques[0],
        cheques_delivered_amount=cheques[1],
    )


def delivered_cheques(db: Session, third_party_id: int, q: s.PageQuery) -> s.DeliveredChequePage:
    """Every cheque given to the third party in a payment, newest first, with its current state. One
    recovered after a reversal stays here, marked as recovered, and is not counted as delivered."""
    third_parties.get(db, third_party_id)
    query = (
        select(PaymentItem, Payment)
        .join(Payment, Payment.id == PaymentItem.payment_id)
        .where(Payment.third_party_id == third_party_id, PaymentItem.cheque_id.is_not(None))
    )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(Payment.business_date.desc(), PaymentItem.id.desc()).limit(q.limit).offset(q.offset)
    ).all()
    current = _cheques(db, [(payment.id, item.cheque_id) for item, payment in rows if item.cheque_id is not None])
    result = []
    for item, payment in rows:
        assert item.cheque_id is not None
        c = current[(payment.id, item.cheque_id)]
        reversed_ = payment.reversal_operation_id is not None
        result.append(
            s.DeliveredChequeOut(
                cheque_id=c.cheque_id,
                cheque_number=c.cheque_number,
                bank_name=c.bank_name,
                amount=item.amount,
                delivered_on=payment.business_date,
                payment_id=payment.id,
                payment_state="REVERSED" if reversed_ else "IN_EFFECT",
                # A payment with cheques is reversed only after every cheque was recovered (R6-2).
                recovered=reversed_,
                recovered_at=c.recovered_at,
                state=c.state,
                state_label=c.state_label,
            )
        )
    return s.DeliveredChequePage(items=result, total=total)


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
