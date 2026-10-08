"""Responses of cheques: the instrument, where it is (derived from the ledger) and what a correction proposes."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.cheques import cycle, identity, service, state, terms
from app.modules.settlements.cheques import schemas as s
from app.modules.settlements.cheques.models import (
    Cheque,
    ChequeCredit,
    ChequeCreditItem,
    ChequeDeposit,
    ChequeDepositItem,
    ChequeRejection,
    ChequeReturn,
)
from app.modules.settlements.delivery import closing
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.pending.models import PendingCollectionReceipt
from app.platform.auth.models import User
from app.platform.banks.models import FinancialInstitution
from app.platform.parties.models import Employee


def items(db: Session, rows: Sequence[Cheque]) -> list[s.ChequeOut]:
    if not rows:
        return []
    ids = [c.id for c in rows]
    positions = ledger.cheque_positions(db, ids)
    published = ledger.cheques_with_effect(db, ids)
    settlements = {
        st.id: st
        for st in db.scalars(
            select(Settlement).where(Settlement.id.in_({c.settlement_id for c in rows if c.settlement_id}))
        )
    }
    in_effect: set[int] = set()
    for st in settlements.values():
        in_effect.update(service.in_effect(db, st.current_close_operation_id))
    banks = {
        b.id: b
        for b in db.scalars(
            select(FinancialInstitution).where(FinancialInstitution.id.in_({c.issuer_bank_id for c in rows}))
        )
    }
    done = cycle.outcomes(db, ids)
    today = business_today()
    users = _names(
        db,
        User,
        [
            u
            for c in rows
            for u in (c.created_by, c.voided_by, c.physical_receipt_confirmed_by, c.verification_changed_by)
            if u
        ],
    )
    employees = _names(db, Employee, [c.received_by_employee_id for c in rows if c.received_by_employee_id])
    result = []
    for c in rows:
        voided = c.voided_at is not None
        derived = state.derive(
            kind=c.kind,
            voided=voided,
            verification_status=c.verification_status,
            position=positions[c.id],
            outcome=done.get(c.id),
        )
        deadline = terms.presentation_deadline(c.payment_type, c.issue_date, c.payment_date)
        settlement = settlements.get(c.settlement_id) if c.settlement_id else None
        change = state.load_change(
            being_corrected=settlement is not None and closing.financial_state(settlement) == "BEING_CORRECTED",
            in_effect=c.id in in_effect,
            voided=voided,
            justifies_now=state.justifies(kind=c.kind, voided=voided, verification_status=c.verification_status),
        )
        ident = identity.compute(
            kind=c.kind,
            bank_id=c.issuer_bank_id,
            number=c.cheque_number,
            branch=c.branch_code,
            account_ref=c.account_ref,
            echeq_id=c.echeq_external_id,
            drawer_tax_id=c.drawer_tax_id,
        )
        bank = banks[c.issuer_bank_id]
        payable_from = terms.payable_from(c.payment_type, c.issue_date, c.payment_date)
        result.append(
            s.ChequeOut(
                id=c.id,
                kind=c.kind,
                payment_type=c.payment_type,
                amount=c.amount,
                issuer_bank_id=c.issuer_bank_id,
                bank_bcra_code=bank.bcra_code,
                bank_name=bank.name,
                branch_code=c.branch_code,
                account_ref=c.account_ref,
                cheque_number=c.cheque_number,
                echeq_external_id=c.echeq_external_id,
                identity_strategy=ident.strategy,
                identity_complete=identity.is_complete(c.kind, ident),
                issue_date=c.issue_date,
                payment_date=c.payment_date,
                payable_from=payable_from,
                presentation_deadline=deadline,
                presentation_expired=derived == "IN_PORTFOLIO" and today > deadline,
                drawer_name=c.drawer_name,
                drawer_tax_id=c.drawer_tax_id,
                delivered_by_name=c.delivered_by_name,
                origin_type=c.origin_type,
                settlement_id=c.settlement_id,
                origin_pending_id=c.origin_pending_id,
                received_business_date=c.received_business_date,
                received_by_employee_id=c.received_by_employee_id,
                received_by_name=employees.get(c.received_by_employee_id) if c.received_by_employee_id else None,
                physical_receipt_confirmed_by=c.physical_receipt_confirmed_by,
                physical_receipt_confirmed_by_name=_name(users, c.physical_receipt_confirmed_by),
                physical_receipt_confirmed_at=c.physical_receipt_confirmed_at,
                verification_status=c.verification_status,
                verification_changed_by=c.verification_changed_by,
                verification_changed_by_name=_name(users, c.verification_changed_by),
                verification_changed_at=c.verification_changed_at,
                verification_reason=c.verification_reason,
                notes=c.notes,
                state=derived,
                state_label=state.LABELS[derived],
                published=c.id in published,
                in_effect=c.id in in_effect,
                load_change=change,
                load_change_detail=state.LOAD_CHANGE_DETAIL[change] if change else None,
                created_by=c.created_by,
                created_by_name=users.get(c.created_by, ""),
                created_at=c.created_at,
                updated_at=c.updated_at,
                voided=voided,
                voided_by=c.voided_by,
                voided_by_name=_name(users, c.voided_by),
                voided_at=c.voided_at,
                void_reason=c.void_reason,
            )
        )
    return result


def detail(db: Session, cheque_id: int) -> s.ChequeDetailOut:
    cheque = service.get(db, cheque_id)
    base = items(db, [cheque])[0]
    events = service.events_of(db, cheque.id)
    users = _names(db, User, [e.created_by for e in events])
    return s.ChequeDetailOut(
        **base.model_dump(),
        events=[
            s.ChequeEventOut(
                id=e.id,
                event_type=e.event_type,
                before=e.before,
                after=e.after,
                reason=e.reason,
                settlement_id=e.settlement_id,
                settlement_version=e.settlement_version,
                close_operation_id=e.close_operation_id,
                operation_id=e.operation_id,
                created_by=e.created_by,
                created_by_name=users.get(e.created_by, ""),
                created_at=e.created_at,
            )
            for e in events
        ],
        closings=[
            s.ChequeClosingOut(
                close_operation_id=row.close_operation_id, settlement_version=row.settlement_version, amount=row.amount
            )
            for row in service.closings_of(db, cheque.id)
        ],
        entry_operation_id=service.entry_operation_id(db, cheque.id),
        documents=documents(db, cheque.id),
    )


@dataclass(frozen=True)
class Filters:
    settlement_id: int | None = None
    kind: str | None = None
    origin_type: str | None = None
    # Only the cheques the ledger has in "Cartera de cheques" right now.
    in_portfolio: bool = False


def page(db: Session, filters: Filters, *, limit: int, offset: int) -> s.ChequePage:
    query = select(Cheque)
    if filters.settlement_id is not None:
        query = query.where(Cheque.settlement_id == filters.settlement_id)
    if filters.kind is not None:
        query = query.where(Cheque.kind == filters.kind)
    if filters.origin_type is not None:
        query = query.where(Cheque.origin_type == filters.origin_type)
    if filters.in_portfolio:
        query = query.where(Cheque.id.in_(list(ledger.cheques_held(db, "CHEQUE_PORTFOLIO"))))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(query.order_by(Cheque.id.desc()).limit(limit).offset(offset)))
    return s.ChequePage(items=items(db, rows), total=total)


def summary(db: Session) -> s.PortfolioSummaryOut:
    portfolio = ledger.cheques_held(db, "CHEQUE_PORTFOLIO")
    in_transit = ledger.cheques_held(db, "CHEQUES_IN_TRANSIT")
    return s.PortfolioSummaryOut(
        portfolio_count=len(portfolio),
        portfolio_amount=sum(portfolio.values(), start=service.ZERO),
        in_transit_count=len(in_transit),
        in_transit_amount=sum(in_transit.values(), start=service.ZERO),
    )


def _name(users: dict[int, str], user_id: int | None) -> str | None:
    return users.get(user_id) if user_id else None


def _names(db: Session, model: type[User] | type[Employee], ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(model.id, model.full_name).where(model.id.in_(set(ids)))).all())


# --- the financial cycle (R4-2) -------------------------------------------------------------------


def documents(db: Session, cheque_id: int) -> list[s.ChequeDocumentOut]:
    """Every document of a cheque's cycle, oldest first: deposits, creditings, rejections, returns, and the
    receipt of the pending it paid."""
    result: list[tuple[datetime, s.ChequeDocumentOut]] = []
    deposits = list(
        db.scalars(
            select(ChequeDeposit)
            .join(ChequeDepositItem, ChequeDepositItem.deposit_id == ChequeDeposit.id)
            .where(ChequeDepositItem.cheque_id == cheque_id)
        )
    )
    credits = list(
        db.scalars(
            select(ChequeCredit)
            .join(ChequeCreditItem, ChequeCreditItem.credit_id == ChequeCredit.id)
            .where(ChequeCreditItem.cheque_id == cheque_id)
        )
    )
    rejections = list(db.scalars(select(ChequeRejection).where(ChequeRejection.cheque_id == cheque_id)))
    returns = list(db.scalars(select(ChequeReturn).where(ChequeReturn.cheque_id == cheque_id)))
    groups: list[tuple[str, cycle.Kind, Sequence[cycle.Document]]] = [
        ("DEPOSIT", cycle.DEPOSIT, deposits),
        ("CREDIT", cycle.CREDIT, credits),
        ("REJECTION", cycle.REJECTION, rejections),
        ("RETURN", cycle.RETURN, returns),
    ]
    for label, kind, docs in groups:
        operations = ledger.document_operation_ids(db, kind.operation_type, kind.source_type, [d.id for d in docs])
        for d in docs:
            pending_id = cycle.origin_pending(db, d).id if isinstance(d, ChequeRejection | ChequeReturn) else None
            result.append(
                (
                    d.created_at,
                    s.ChequeDocumentOut(
                        type=label,
                        id=d.id,
                        business_date=d.business_date,
                        operation_id=operations[d.id],
                        reversed=d.reversal_operation_id is not None,
                        reversal_operation_id=d.reversal_operation_id,
                        pending_id=pending_id,
                    ),
                )
            )
    for r in db.scalars(select(PendingCollectionReceipt).where(PendingCollectionReceipt.cheque_id == cheque_id)):
        result.append(
            (
                r.created_at,
                s.ChequeDocumentOut(
                    type="PENDING_RECEIPT",
                    id=r.id,
                    business_date=r.business_date,
                    operation_id=r.operation_id,
                    reversed=r.reversal_operation_id is not None,
                    reversal_operation_id=r.reversal_operation_id,
                    pending_id=r.pending_id,
                ),
            )
        )
    return [doc for _, doc in sorted(result, key=lambda pair: (pair[0], pair[1].operation_id))]


def deposit(db: Session, deposit_id: int) -> s.DepositOut:
    doc = _get(db, ChequeDeposit, deposit_id)
    items = list(
        db.scalars(
            select(ChequeDepositItem).where(ChequeDepositItem.deposit_id == doc.id).order_by(ChequeDepositItem.id)
        )
    )
    users = _names(db, User, [u for u in (doc.created_by, doc.reversed_by) if u])
    return s.DepositOut(
        id=doc.id,
        bank_account_id=doc.bank_account_id,
        bank_account_name=_account_name(db, doc.bank_account_id),
        business_date=doc.business_date,
        slip_number=doc.slip_number,
        notes=doc.notes,
        total=sum((i.amount for i in items), start=service.ZERO),
        items=_cycle_items(db, [(i.cheque_id, i.amount) for i in items]),
        operation_id=_operation_id(db, cycle.DEPOSIT, doc.id),
        created_by=doc.created_by,
        created_by_name=users.get(doc.created_by, ""),
        created_at=doc.created_at,
        reversal=_document_reversal(doc, users),
    )


def credit(db: Session, credit_id: int) -> s.CreditOut:
    doc = _get(db, ChequeCredit, credit_id)
    items = list(
        db.scalars(select(ChequeCreditItem).where(ChequeCreditItem.credit_id == doc.id).order_by(ChequeCreditItem.id))
    )
    users = _names(db, User, [u for u in (doc.created_by, doc.reversed_by) if u])
    return s.CreditOut(
        id=doc.id,
        bank_account_id=doc.bank_account_id,
        bank_account_name=_account_name(db, doc.bank_account_id),
        business_date=doc.business_date,
        notes=doc.notes,
        total=sum((i.amount for i in items), start=service.ZERO),
        items=_cycle_items(db, [(i.cheque_id, i.amount) for i in items]),
        operation_id=_operation_id(db, cycle.CREDIT, doc.id),
        created_by=doc.created_by,
        created_by_name=users.get(doc.created_by, ""),
        created_at=doc.created_at,
        reversal=_document_reversal(doc, users),
    )


def rejection(db: Session, rejection_id: int) -> s.RejectionOut:
    doc = _get(db, ChequeRejection, rejection_id)
    users = _names(db, User, [u for u in (doc.created_by, doc.reversed_by) if u])
    return s.RejectionOut(
        id=doc.id,
        cheque_id=doc.cheque_id,
        stage=doc.stage,
        business_date=doc.business_date,
        reason=doc.reason,
        bank_account_id=doc.bank_account_id,
        pending_id=cycle.origin_pending(db, doc).id,
        operation_id=_operation_id(db, cycle.REJECTION, doc.id),
        created_by=doc.created_by,
        created_by_name=users.get(doc.created_by, ""),
        created_at=doc.created_at,
        reversal=_document_reversal(doc, users),
    )


def given_back(db: Session, return_id: int) -> s.ReturnOut:
    doc = _get(db, ChequeReturn, return_id)
    users = _names(db, User, [u for u in (doc.created_by, doc.reversed_by) if u])
    return s.ReturnOut(
        id=doc.id,
        cheque_id=doc.cheque_id,
        business_date=doc.business_date,
        reason=doc.reason,
        pending_id=cycle.origin_pending(db, doc).id,
        operation_id=_operation_id(db, cycle.RETURN, doc.id),
        created_by=doc.created_by,
        created_by_name=users.get(doc.created_by, ""),
        created_at=doc.created_at,
        reversal=_document_reversal(doc, users),
    )


def _get[D: (ChequeDeposit, ChequeCredit, ChequeRejection, ChequeReturn)](
    db: Session, model: type[D], document_id: int
) -> D:
    doc = db.get(model, document_id)
    if doc is None:
        raise AppError(404, "cheque_document_not_found", "El documento no existe.")
    return doc


def _operation_id(db: Session, kind: cycle.Kind, document_id: int) -> int:
    return ledger.document_operation(db, kind.operation_type, ledger.SourceRef(kind.source_type, document_id)).id


def _account_name(db: Session, account_id: int) -> str:
    return ledger.account_balance(db, account_id).name


def _cycle_items(db: Session, rows: list[tuple[int, Decimal]]) -> list[s.CycleItemOut]:
    cheques = {c.id: c for c in items(db, list(db.scalars(select(Cheque).where(Cheque.id.in_([r[0] for r in rows])))))}
    return [
        s.CycleItemOut(
            cheque_id=cid,
            amount=amount,
            kind=cheques[cid].kind,
            cheque_number=cheques[cid].cheque_number,
            bank_name=cheques[cid].bank_name,
            delivered_by_name=cheques[cid].delivered_by_name,
            state=cheques[cid].state,
            state_label=cheques[cid].state_label,
        )
        for cid, amount in rows
    ]


def _document_reversal(doc: cycle.Document, users: dict[int, str]) -> s.DocumentReversalOut | None:
    if doc.reversal_operation_id is None or doc.reversed_by is None or doc.reversed_at is None:
        return None
    return s.DocumentReversalOut(
        operation_id=doc.reversal_operation_id,
        reversed_by=doc.reversed_by,
        reversed_by_name=users.get(doc.reversed_by, ""),
        reversed_at=doc.reversed_at,
        reason=doc.reversal_reason or "",
    )
