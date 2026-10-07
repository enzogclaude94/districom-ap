"""Global list of pending collections and their alerts (R3-2), computed on read: nothing is scheduled or
stored.

The financial state is derived in SQL exactly as `state.financial` does in Python (the amount in effect,
minus receipts and resolutions not reversed), so filters and pagination run in the database. The amount in
effect is the one of the settlement's close in effect or, for a pending born from a rejected or returned
cheque (R4-2), its own amount while it is not voided. By default the list holds what is still owed in Cajas:
OPEN and PARTIAL (Enzo's D2).

A pending alerts when it is in effect, has a balance and was registered more than
`settlements.pending_alert_hours` ago (24 by default). DRAFT, VOIDED, COLLECTED and RESOLVED never alert;
PARTIAL alerts on what is left. An expected date in the past is shown apart and does not change the alert.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import Select, Subquery, and_, case, func, or_, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.pending.models import (
    PendingCollection,
    PendingCollectionReceipt,
    PendingCollectionResolution,
    SettlementClosingPending,
)
from app.modules.settlements.pending.state import State
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.settings.registry import registry
from app.platform.settings.service import get_setting, set_setting

ALERT_HOURS_KEY = "settlements.pending_alert_hours"
# From one hour to 30 days: below that every pending would alert at once, above it the alert says nothing.
ALERT_HOURS_MIN = 1
ALERT_HOURS_MAX = 720
OWED_STATES: tuple[State, ...] = ("OPEN", "PARTIAL")


@dataclass(frozen=True)
class Filters:
    states: Sequence[State] = OWED_STATES
    customer: str | None = None
    driver_id: int | None = None
    responsible_employee_id: int | None = None
    settlement_id: int | None = None
    origin_type: str | None = None
    # Registered more than this many hours ago.
    min_age_hours: int | None = None
    alert_only: bool = False


def utc_now() -> datetime:
    """The moment ages are measured from (a function so tests can move it)."""
    return datetime.now(UTC)


def alert_hours(db: Session) -> int:
    return int(get_setting(db, ALERT_HOURS_KEY))


def alert_hours_is_default(db: Session) -> bool:
    return alert_hours(db) == int(registry.spec(ALERT_HOURS_KEY).default)


def update_alert_hours(db: Session, actor: User, hours: int) -> None:
    """Store the alert threshold in `app_settings` (admin, from the module's configuration); audited.
    Alerts are computed on read, so the new value applies to every pending on the next read."""
    if not ALERT_HOURS_MIN <= hours <= ALERT_HOURS_MAX:
        raise AppError(
            422,
            "invalid_alert_hours",
            f"Las horas de alerta van de {ALERT_HOURS_MIN} a {ALERT_HOURS_MAX}.",
            {"min": ALERT_HOURS_MIN, "max": ALERT_HOURS_MAX},
        )
    before = alert_hours(db)
    if before == hours:
        db.commit()
        return
    set_setting(db, ALERT_HOURS_KEY, hours, user_id=actor.id)
    record_change(
        db,
        "settlements.pending_alert_hours.updated",
        action="update",
        user_id=actor.id,
        entity_type="setting",
        entity_id=ALERT_HOURS_KEY,
        before={"hours": before},
        after={"hours": hours},
    )
    db.commit()


def is_alert(*, in_effect: bool, balance: Decimal | None, created_at: datetime, hours: int, now: datetime) -> bool:
    return in_effect and balance is not None and balance > 0 and now - created_at > timedelta(hours=hours)


def age_hours(created_at: datetime, now: datetime) -> int:
    return max(0, int((now - created_at).total_seconds() // 3600))


def page(db: Session, filters: Filters, *, limit: int, offset: int) -> tuple[list[PendingCollection], int]:
    """Pendings matching the filters, oldest first, and how many match."""
    rows = _rows()
    query = select(rows.c.id).where(rows.c.state.in_(list(filters.states)))
    if filters.customer:
        query = query.where(
            or_(
                rows.c.customer_name.icontains(filters.customer, autoescape=True),
                rows.c.customer_in_effect.icontains(filters.customer, autoescape=True),
            )
        )
    if filters.driver_id is not None:
        query = query.where(rows.c.driver_id == filters.driver_id)
    if filters.responsible_employee_id is not None:
        query = query.where(rows.c.responsible_employee_id == filters.responsible_employee_id)
    if filters.settlement_id is not None:
        query = query.where(rows.c.settlement_id == filters.settlement_id)
    if filters.origin_type is not None:
        query = query.where(rows.c.origin_type == filters.origin_type)
    now = utc_now()
    if filters.min_age_hours is not None:
        query = query.where(rows.c.created_at < now - timedelta(hours=filters.min_age_hours))
    if filters.alert_only:
        query = _alerting(query, rows, now - timedelta(hours=alert_hours(db)))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    ids = list(db.scalars(query.order_by(rows.c.created_at, rows.c.id).limit(limit).offset(offset)))
    found = {p.id: p for p in db.scalars(select(PendingCollection).where(PendingCollection.id.in_(ids)))}
    return [found[pid] for pid in ids], int(total)


def summary(db: Session) -> tuple[int, int, Decimal]:
    """(alert hours, how many pendings alert, their total balance)."""
    hours = alert_hours(db)
    rows = _rows()
    query = _alerting(
        select(func.count(), func.coalesce(func.sum(rows.c.balance), 0)), rows, utc_now() - timedelta(hours=hours)
    )
    count, balance = db.execute(query).one()
    return hours, int(count), Decimal(balance)


def _alerting(query: Select[Any], rows: Subquery, cutoff: datetime) -> Select[Any]:
    return query.where(rows.c.state.in_(OWED_STATES), rows.c.created_at < cutoff)


def _rows() -> Subquery:
    """One row per pending with its derived financial state and balance (null while not in effect)."""
    receipts = (
        select(PendingCollectionReceipt.pending_id, func.sum(PendingCollectionReceipt.amount).label("amount"))
        .where(PendingCollectionReceipt.reversal_operation_id.is_(None))
        .group_by(PendingCollectionReceipt.pending_id)
        .subquery()
    )
    resolutions = (
        select(PendingCollectionResolution.pending_id, func.sum(PendingCollectionResolution.amount).label("amount"))
        .where(PendingCollectionResolution.reversal_operation_id.is_(None))
        .group_by(PendingCollectionResolution.pending_id)
        .subquery()
    )
    collected = func.coalesce(receipts.c.amount, 0)
    resolved = func.coalesce(resolutions.c.amount, 0)
    from_settlement = PendingCollection.origin_type == "SETTLEMENT"
    effect = case(
        (from_settlement, SettlementClosingPending.amount),
        (PendingCollection.voided_at.is_(None), PendingCollection.amount),
    )
    customer_in_effect = case(
        (from_settlement, SettlementClosingPending.customer_name),
        (PendingCollection.voided_at.is_(None), PendingCollection.customer_name),
    )
    balance = effect - collected - resolved
    state = case(
        (and_(effect.is_(None), PendingCollection.voided_at.is_not(None)), "VOIDED"),
        (effect.is_(None), "DRAFT"),
        (balance > 0, case((collected + resolved > 0, "PARTIAL"), else_="OPEN")),
        (resolved > 0, "RESOLVED"),
        else_="COLLECTED",
    )
    return (
        select(
            PendingCollection.id,
            PendingCollection.settlement_id,
            PendingCollection.origin_type,
            PendingCollection.customer_name,
            PendingCollection.responsible_employee_id,
            PendingCollection.created_at,
            Settlement.employee_id.label("driver_id"),
            customer_in_effect.label("customer_in_effect"),
            balance.label("balance"),
            state.label("state"),
        )
        .outerjoin(Settlement, Settlement.id == PendingCollection.settlement_id)
        .outerjoin(
            SettlementClosingPending,
            and_(
                SettlementClosingPending.pending_id == PendingCollection.id,
                SettlementClosingPending.close_operation_id == Settlement.current_close_operation_id,
            ),
        )
        .outerjoin(receipts, receipts.c.pending_id == PendingCollection.id)
        .outerjoin(resolutions, resolutions.c.pending_id == PendingCollection.id)
        .subquery()
    )
