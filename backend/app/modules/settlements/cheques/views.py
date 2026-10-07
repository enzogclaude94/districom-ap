"""Responses of cheques: the instrument, where it is (derived from the ledger) and what a correction proposes."""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.settlements.cheques import identity, service, state, terms
from app.modules.settlements.cheques import schemas as s
from app.modules.settlements.cheques.models import Cheque
from app.modules.settlements.delivery import closing
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.ledger import service as ledger
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
            kind=c.kind, voided=voided, verification_status=c.verification_status, position=positions[c.id]
        )
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
                presentation_deadline=terms.presentation_deadline(c.payment_type, c.issue_date, c.payment_date),
                drawer_name=c.drawer_name,
                drawer_tax_id=c.drawer_tax_id,
                delivered_by_name=c.delivered_by_name,
                origin_type=c.origin_type,
                settlement_id=c.settlement_id,
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
