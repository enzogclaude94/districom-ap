"""Responses of pending collections, built from the rows and the close in effect of each settlement."""

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.settlements.delivery import closing
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.pending import listing, service, state
from app.modules.settlements.pending import schemas as s
from app.modules.settlements.pending.models import (
    PendingCollection,
    PendingCollectionReceipt,
    PendingCollectionResolution,
    PendingResolutionAllocation,
    SettlementClosingPending,
)
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
    hours = listing.alert_hours(db)
    now = listing.utc_now()
    today = business_today()
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
                age_hours=listing.age_hours(p.created_at, now),
                alert=listing.is_alert(
                    in_effect=in_effect is not None,
                    balance=financial.balance,
                    created_at=p.created_at,
                    hours=hours,
                    now=now,
                ),
                expected_date_overdue=p.expected_date is not None
                and p.expected_date < today
                and financial.state in ("DRAFT", "OPEN", "PARTIAL"),
            )
        )
    return result


def list_items(db: Session, rows: Sequence[PendingCollection]) -> list[s.PendingListItem]:
    """Rows of the global list: each pending with its settlement and driver."""
    settlements = {
        row.id: row for row in db.scalars(select(Settlement).where(Settlement.id.in_({p.settlement_id for p in rows})))
    }
    drivers = _names(db, Employee, [st.employee_id for st in settlements.values()])
    result = []
    for item in items(db, rows):
        settlement = settlements[item.settlement_id]
        result.append(
            s.PendingListItem(
                **item.model_dump(),
                settlement_business_date=settlement.business_date,
                settlement_sequence_number=settlement.sequence_number,
                driver_id=settlement.employee_id,
                driver_name=drivers.get(settlement.employee_id, ""),
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
    receipts = list(
        db.scalars(
            select(PendingCollectionReceipt)
            .where(PendingCollectionReceipt.pending_id == pending.id)
            .order_by(PendingCollectionReceipt.id)
        )
    )
    resolutions = list(
        db.scalars(
            select(PendingCollectionResolution)
            .where(PendingCollectionResolution.pending_id == pending.id)
            .order_by(PendingCollectionResolution.id)
        )
    )
    parts: dict[int, list[PendingResolutionAllocation]] = {r.id: [] for r in resolutions}
    for part in db.scalars(
        select(PendingResolutionAllocation)
        .where(PendingResolutionAllocation.resolution_id.in_(list(parts)))
        .order_by(PendingResolutionAllocation.id)
    ):
        parts[part.resolution_id].append(part)
    users = _names(
        db,
        User,
        [e.created_by for e in events]
        + [r.created_by for r in receipts]
        + [r.created_by for r in resolutions]
        + [r.reversed_by for r in receipts if r.reversed_by]
        + [r.reversed_by for r in resolutions if r.reversed_by],
    )
    employees = _names(db, Employee, [p.employee_id for ps in parts.values() for p in ps if p.employee_id])
    accounts = {a.account_id: a.name for a in ledger.account_balances(db, include_inactive=True)}
    driver = db.get(Employee, settlement.employee_id)
    return s.PendingDetailOut(
        **base.model_dump(),
        settlement_business_date=settlement.business_date,
        settlement_sequence_number=settlement.sequence_number,
        driver_id=settlement.employee_id,
        driver_name=driver.full_name if driver else "",
        settlement_financial_state=closing.financial_state(settlement),
        published=[_published(row, settlement) for row in published],
        receipts=[
            s.ReceiptOut(
                id=r.id,
                amount=r.amount,
                business_date=r.business_date,
                method=r.method,
                destination_account_id=r.destination_account_id,
                destination_name=accounts.get(r.destination_account_id, ""),
                operation_id=r.operation_id,
                created_by=r.created_by,
                created_by_name=users.get(r.created_by, ""),
                created_at=r.created_at,
                reversal=_reversal(r, users),
            )
            for r in receipts
        ],
        resolutions=[
            s.ResolutionOut(
                id=r.id,
                amount=r.amount,
                business_date=r.business_date,
                reason=r.reason,
                allocations=[
                    s.ResolutionPartOut(
                        target=p.target,
                        employee_id=p.employee_id,
                        employee_name=employees.get(p.employee_id) if p.employee_id else None,
                        amount=p.amount,
                    )
                    for p in parts[r.id]
                ],
                operation_id=r.operation_id,
                created_by=r.created_by,
                created_by_name=users.get(r.created_by, ""),
                created_at=r.created_at,
                reversal=_reversal(r, users),
            )
            for r in resolutions
        ],
        events=[
            s.PendingEventOut(
                id=e.id,
                event_type=e.event_type,
                settlement_version=e.settlement_version,
                before=e.before,
                after=e.after,
                reason=e.reason,
                close_operation_id=e.close_operation_id,
                receipt_id=e.receipt_id,
                resolution_id=e.resolution_id,
                created_by=e.created_by,
                created_by_name=users.get(e.created_by, ""),
                created_at=e.created_at,
            )
            for e in events
        ],
    )


def _reversal(
    row: PendingCollectionReceipt | PendingCollectionResolution, users: dict[int, str]
) -> s.ReversalOut | None:
    if row.reversal_operation_id is None or row.reversed_by is None or row.reversed_at is None:
        return None
    return s.ReversalOut(
        operation_id=row.reversal_operation_id,
        reversed_by=row.reversed_by,
        reversed_by_name=users.get(row.reversed_by, ""),
        reversed_at=row.reversed_at,
        reason=row.reversal_reason or "",
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
