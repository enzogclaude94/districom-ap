"""The consolidated fuel history (R5-4): read-only, over `fuel_loads` + `expenses` + the settlements and their
closes. No table of its own, no second accounting.

A row is a real fuel load (a legacy fuel expense without detail has none and never shows here). Its state
and the fuel it puts **in effect** come from where its money is:

- a direct expense: its own values while in effect; nothing once reversed;
- a settlement load: what the settlement's close in effect published for it
  (`settlement_closing_fuel_loads`, frozen by each close). So a settlement never closed has nothing in
  effect yet, a reopened one keeps the published values until the new close (whatever the correction
  proposes), and a voided load counts only while the close in effect still holds it.

The summary adds up exactly that, for the same filters as the page. Dates are business dates: the
settlement's for its loads, the expense's for a direct one.
"""

from collections.abc import Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import ColumnElement, and_, case, func, literal, or_, select
from sqlalchemy.orm import Session, aliased

from app.modules.settlements.delivery.models import Expense, FuelLoad, Settlement, SettlementClosingFuelLoad
from app.modules.settlements.fuel_history import schemas as s
from app.modules.settlements.ledger import service as ledger
from app.platform.auth.models import User
from app.platform.parties.models import Employee
from app.platform.vehicles import plates
from app.platform.vehicles.models import Vehicle

CENT = Decimal("0.01")

Published = aliased(SettlementClosingFuelLoad, name="published")

DIRECT = Expense.origin == "DIRECT"
DAY = func.coalesce(Expense.business_date, Settlement.business_date)
STATE = case(
    (and_(DIRECT, Expense.reversal_operation_id.is_not(None)), literal("REVERSED")),
    (DIRECT, literal("PUBLISHED")),
    (and_(Settlement.status == "CLOSED", Published.id.is_not(None)), literal("PUBLISHED")),
    (Settlement.status == "CLOSED", literal("VOIDED")),
    (
        and_(Settlement.current_close_operation_id.is_(None), Expense.voided_at.is_not(None)),
        literal("VOIDED"),
    ),
    (Settlement.current_close_operation_id.is_(None), literal("OPEN_SETTLEMENT")),
    (Published.id.is_not(None), literal("IN_CORRECTION")),
    (Expense.voided_at.is_not(None), literal("VOIDED")),
    else_=literal("IN_CORRECTION"),
)
# The fuel in effect of the row: a direct expense's own values while in effect, else the published ones.
DIRECT_IN_EFFECT = and_(DIRECT, Expense.reversal_operation_id.is_(None))
LITERS_IN_EFFECT = case((DIRECT_IN_EFFECT, FuelLoad.liters), else_=Published.liters)
AMOUNT_IN_EFFECT = case((DIRECT_IN_EFFECT, Expense.amount), else_=Published.amount)


def _base() -> Any:
    return (
        select(
            FuelLoad,
            Expense,
            Settlement,
            Published,
            DAY.label("day"),
            STATE.label("state"),
            LITERS_IN_EFFECT.label("liters_in_effect"),
            AMOUNT_IN_EFFECT.label("amount_in_effect"),
        )
        .join(Expense, Expense.id == FuelLoad.expense_id)
        .outerjoin(Settlement, Settlement.id == Expense.settlement_id)
        .outerjoin(
            Published,
            and_(
                Published.fuel_load_id == FuelLoad.id,
                Published.close_operation_id == Settlement.current_close_operation_id,
            ),
        )
    )


def _filters(q: s.FuelLoadQuery, *, in_effect: bool = False) -> list[ColumnElement[bool]]:
    """The filters of the page. A row matches a vehicle or employee as the load is now or as its close in
    effect holds it, so a load whose vehicle a correction changes shows under both until the new close. The
    summary (`in_effect`) counts it only under the one in effect."""
    where: list[ColumnElement[bool]] = []
    for value, current, published in (
        (q.vehicle_id, FuelLoad.vehicle_id, Published.vehicle_id),
        (q.employee_id, FuelLoad.employee_id, Published.employee_id),
    ):
        if value is None:
            continue
        if in_effect:
            where.append(case((DIRECT, current), else_=published) == value)
        else:
            where.append(or_(current == value, published == value))
    if q.date_from is not None:
        where.append(q.date_from <= DAY)
    if q.date_to is not None:
        where.append(q.date_to >= DAY)
    if q.origin is not None:
        where.append(Expense.origin == q.origin)
    if q.state is not None:
        where.append(STATE == q.state)  # noqa: SIM300 - a SQL expression, not a comparison
    return where


def _summary(db: Session, q: s.FuelLoadQuery) -> s.FuelSummary:
    sub = _base().where(*_filters(q, in_effect=True)).subquery()
    liters, amount = db.execute(
        select(
            func.coalesce(func.sum(sub.c.liters_in_effect), 0),
            func.coalesce(func.sum(sub.c.amount_in_effect), 0),
        )
    ).one()
    liters, amount = Decimal(liters), Decimal(amount)
    return s.FuelSummary(
        liters=liters,
        amount=amount,
        weighted_price_per_liter=(amount / liters).quantize(CENT, ROUND_HALF_UP) if liters > 0 else None,
    )


def list_loads(db: Session, q: s.FuelLoadQuery) -> s.FuelLoadPage:
    where = _filters(q)
    total = db.scalar(select(func.count()).select_from(_base().where(*where).subquery())) or 0
    rows = db.execute(
        _base().where(*where).order_by(DAY.desc(), FuelLoad.id.desc()).limit(q.limit).offset(q.offset)
    ).all()
    return s.FuelLoadPage(items=_rows(db, rows), total=total, limit=q.limit, offset=q.offset, summary=_summary(db, q))


def employees(db: Session) -> list[s.FuelEmployee]:
    """Everyone who loaded fuel, active or not: a former employee stays findable in the history."""
    ids = select(FuelLoad.employee_id).union(select(SettlementClosingFuelLoad.employee_id)).subquery()
    found = db.execute(
        select(Employee.id, Employee.full_name, Employee.active)
        .where(Employee.id.in_(select(ids.c[0])))
        .order_by(Employee.full_name, Employee.id)
    ).all()
    return [s.FuelEmployee(id=i, full_name=name, active=active) for i, name, active in found]


def _rows(db: Session, rows: Sequence[Any]) -> list[s.FuelLoadRow]:
    loads = [r.FuelLoad for r in rows]
    published = [r.published for r in rows if r.published is not None]
    vehicles = {
        v.id: v
        for v in db.scalars(
            select(Vehicle).where(Vehicle.id.in_({x.vehicle_id for x in loads} | {p.vehicle_id for p in published}))
        )
    }
    people = {
        e.id: e
        for e in db.scalars(
            select(Employee).where(
                Employee.id.in_(
                    {x.employee_id for x in loads}
                    | {p.employee_id for p in published}
                    | {r.Settlement.employee_id for r in rows if r.Settlement is not None}
                )
            )
        )
    }
    users = dict(
        db.execute(select(User.id, User.full_name).where(User.id.in_({r.Expense.created_by for r in rows}))).all()
    )
    paying = {r.Expense.paid_from_account_id for r in rows if r.Expense.paid_from_account_id}
    accounts = (
        {a.account_id: a.name for a in ledger.accounts(db, include_inactive=True) if a.account_id in paying}
        if paying
        else {}
    )
    result = []
    for r in rows:
        load, expense, settlement, pub = r.FuelLoad, r.Expense, r.Settlement, r.published
        vehicle, person = vehicles[load.vehicle_id], people[load.employee_id]
        correcting = r.state == "IN_CORRECTION"
        in_effect = (
            s.FuelInEffect(
                vehicle_id=pub.vehicle_id,
                vehicle_plate=plates.display(vehicles[pub.vehicle_id].plate),
                employee_id=pub.employee_id,
                employee_name=people[pub.employee_id].full_name,
                liters=pub.liters,
                price_per_liter=pub.price_per_liter,
                amount=pub.amount,
            )
            if correcting and pub is not None
            else None
        )
        changed = in_effect is not None and (
            (pub.vehicle_id, pub.employee_id, pub.liters, pub.price_per_liter, pub.amount)
            != (load.vehicle_id, load.employee_id, load.liters, load.price_per_liter, expense.amount)
        )
        result.append(
            s.FuelLoadRow(
                id=load.id,
                expense_id=expense.id,
                origin=expense.origin,
                state=r.state,
                business_date=r.day,
                vehicle_id=vehicle.id,
                vehicle_plate=plates.display(vehicle.plate),
                vehicle_description=vehicle.description,
                vehicle_active=vehicle.active,
                employee_id=person.id,
                employee_name=person.full_name,
                employee_active=person.active,
                liters=load.liters,
                price_per_liter=load.price_per_liter,
                amount=expense.amount,
                odometer_km=load.odometer_km,
                settlement_id=settlement.id if settlement else None,
                settlement_sequence=settlement.sequence_number if settlement else None,
                settlement_driver_name=people[settlement.employee_id].full_name if settlement else None,
                paid_from_account_name=accounts.get(expense.paid_from_account_id)
                if expense.paid_from_account_id
                else None,
                created_by_name=users.get(expense.created_by, ""),
                created_at=expense.created_at,
                in_effect=in_effect,
                proposed_change=correcting and (in_effect is None or changed),
                proposed_void=correcting and expense.voided_at is not None,
            )
        )
    return result
