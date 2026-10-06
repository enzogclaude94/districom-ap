"""Responses of pending collections, built from the rows and the close in effect of each settlement."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.settlements.delivery import closing
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.pending import schemas as s
from app.modules.settlements.pending import service, state
from app.modules.settlements.pending.models import PendingCollection, SettlementClosingPending
from app.platform.auth.models import User
from app.platform.parties.models import Employee


def items(db: Session, rows: Sequence[PendingCollection]) -> list[s.PendingOut]:
    """Each pending with its load, its financial state and what a correction in progress proposes."""
    if not rows:
        return []
    settlements = {
        row.id: row for row in db.scalars(select(Settlement).where(Settlement.id.in_({p.settlement_id for p in rows})))
    }
    published: dict[int, service.InEffect] = {}
    for settlement in settlements.values():
        published.update(service.in_effect(db, settlement.current_close_operation_id))
    applied = service.collected_and_resolved(db, [p.id for p in rows])
    users = _names(
        db,
        User,
        [p.created_by for p in rows] + [u for p in rows for u in (p.updated_by, p.voided_by) if u],
    )
    employees = _names(db, Employee, [p.responsible_employee_id for p in rows])
    result = []
    for p in rows:
        settlement = settlements[p.settlement_id]
        in_effect = published.get(p.id)
        collected, resolved = applied[p.id]
        financial = state.financial(
            amount_in_effect=in_effect.amount if in_effect else None,
            voided=p.voided_at is not None,
            collected=collected,
            resolved=resolved,
        )
        change = state.load_change(
            being_corrected=closing.financial_state(settlement) == "BEING_CORRECTED",
            in_effect=(in_effect.amount, in_effect.customer_name) if in_effect else None,
            amount=p.amount,
            customer_name=p.customer_name,
            voided=p.voided_at is not None,
        )
        result.append(
            s.PendingOut(
                id=p.id,
                settlement_id=p.settlement_id,
                customer_name=p.customer_name,
                amount=p.amount,
                reason=p.reason,
                responsible_employee_id=p.responsible_employee_id,
                responsible_name=employees.get(p.responsible_employee_id, ""),
                expected_date=p.expected_date,
                created_by=p.created_by,
                created_by_name=users.get(p.created_by, ""),
                created_at=p.created_at,
                updated_by=p.updated_by,
                updated_by_name=users.get(p.updated_by) if p.updated_by else None,
                updated_at=p.updated_at,
                voided=p.voided_at is not None,
                voided_by=p.voided_by,
                voided_by_name=users.get(p.voided_by) if p.voided_by else None,
                voided_at=p.voided_at,
                void_reason=p.void_reason,
                state=financial.state,
                amount_in_effect=financial.amount_in_effect,
                customer_in_effect=in_effect.customer_name if in_effect else None,
                collected=financial.collected,
                resolved=financial.resolved,
                balance=financial.balance,
                load_change=change,
                load_change_detail=state.LOAD_CHANGE_DETAIL[change] if change else None,
            )
        )
    return result


def detail(db: Session, pending_id: int) -> s.PendingDetailOut:
    pending = service.get(db, pending_id)
    base = items(db, [pending])[0]
    settlement = db.get(Settlement, pending.settlement_id)
    assert settlement is not None  # foreign key
    published = service.published_of(db, pending.id)
    events = service.events_of(db, pending.id)
    users = _names(db, User, [e.created_by for e in events])
    driver = db.get(Employee, settlement.employee_id)
    return s.PendingDetailOut(
        **base.model_dump(),
        settlement_business_date=settlement.business_date,
        settlement_sequence_number=settlement.sequence_number,
        driver_id=settlement.employee_id,
        driver_name=driver.full_name if driver else "",
        settlement_financial_state=closing.financial_state(settlement),
        published=[_published(row, settlement) for row in published],
        events=[
            s.PendingEventOut(
                id=e.id,
                event_type=e.event_type,
                settlement_version=e.settlement_version,
                before=e.before,
                after=e.after,
                reason=e.reason,
                close_operation_id=e.close_operation_id,
                created_by=e.created_by,
                created_by_name=users.get(e.created_by, ""),
                created_at=e.created_at,
            )
            for e in events
        ],
    )


def _published(row: SettlementClosingPending, settlement: Settlement) -> s.PublishedOut:
    return s.PublishedOut(
        settlement_version=row.settlement_version,
        close_operation_id=row.close_operation_id,
        amount=row.amount,
        customer_name=row.customer_name,
        in_effect=row.close_operation_id == settlement.current_close_operation_id,
        published_at=row.created_at,
    )


def _names(db: Session, model: type[User] | type[Employee], ids: list[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(model.id, model.full_name).where(model.id.in_(set(ids)))).all())
