"""Reading expenses (R5-3): every expense with its origin, state and paying account, and one expense with its
operation, reversal and history. A settlement expense is dated by its settlement."""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.cash.schemas import EntryOut, OperationOut
from app.modules.settlements.delivery import expenses as delivery_expenses
from app.modules.settlements.delivery.models import Expense, ExpenseCategory, Settlement
from app.modules.settlements.delivery.views import fuel_out
from app.modules.settlements.expenses import schemas as s
from app.modules.settlements.ledger import service as ledger
from app.platform.audit.models import AuditEvent
from app.platform.auth.models import User
from app.platform.parties.models import Employee
from app.platform.vehicles.models import Vehicle


def list_expenses(db: Session, q: s.ExpenseListQuery) -> s.ExpensePage:
    day = func.coalesce(Expense.business_date, Settlement.business_date)
    query = select(Expense, day).outerjoin(Settlement, Settlement.id == Expense.settlement_id)
    if q.origin is not None:
        query = query.where(Expense.origin == q.origin)
    if q.category_id is not None:
        query = query.where(Expense.category_id == q.category_id)
    if q.date_from is not None:
        query = query.where(day >= q.date_from)
    if q.date_to is not None:
        query = query.where(day <= q.date_to)
    if q.account_id is not None:
        query = query.where(Expense.paid_from_account_id == q.account_id)
    if q.state == "IN_EFFECT":
        query = query.where(Expense.voided_at.is_(None))
    elif q.state == "VOIDED":
        query = query.where(Expense.origin == "SETTLEMENT", Expense.voided_at.is_not(None))
    elif q.state == "REVERSED":
        query = query.where(Expense.reversal_operation_id.is_not(None))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(query.order_by(day.desc(), Expense.id.desc()).limit(q.limit).offset(q.offset)).all()
    return s.ExpensePage(items=outs(db, [(row[0], row[1]) for row in rows]), total=total)


def detail(db: Session, expense_id: int) -> s.ExpenseDetail:
    row = db.execute(
        select(Expense, func.coalesce(Expense.business_date, Settlement.business_date))
        .outerjoin(Settlement, Settlement.id == Expense.settlement_id)
        .where(Expense.id == expense_id)
    ).one_or_none()
    if row is None:
        raise AppError(404, "expense_not_found", "El gasto no existe.")
    expense = row[0]
    (out,) = outs(db, [(expense, row[1])])
    events = list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == "expense", AuditEvent.entity_id == str(expense.id))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )
    names = _users(db, {e.user_id for e in events if e.user_id is not None})
    return s.ExpenseDetail(
        **out.model_dump(),
        operation=_operation(db, expense.operation_id),
        reversal=_operation(db, expense.reversal_operation_id),
        history=[
            s.ExpenseEventOut(
                id=e.id,
                event_type=e.event_type.removeprefix("settlements.expense."),
                occurred_at=e.occurred_at,
                user_name=names.get(e.user_id) if e.user_id is not None else None,
                before=(e.payload or {}).get("before"),
                after=(e.payload or {}).get("after"),
            )
            for e in events
        ],
    )


def outs(db: Session, rows: Sequence[tuple[Expense, date]]) -> list[s.ExpenseOut]:
    expenses = [e for e, _ in rows]
    categories = {
        c.id: c
        for c in db.scalars(select(ExpenseCategory).where(ExpenseCategory.id.in_({e.category_id for e in expenses})))
    }
    # Names and kinds of the paying accounts, read through the ledger service (its models are its own).
    paying = {e.paid_from_account_id for e in expenses if e.paid_from_account_id}
    accounts = (
        {a.account_id: a for a in ledger.accounts(db, include_inactive=True) if a.account_id in paying}
        if paying
        else {}
    )
    loads = delivery_expenses.loads_of(db, [e.id for e in expenses])
    vehicles = {
        v.id: v for v in db.scalars(select(Vehicle).where(Vehicle.id.in_({x.vehicle_id for x in loads.values()})))
    }
    employees = dict(
        db.execute(
            select(Employee.id, Employee.full_name).where(Employee.id.in_({x.employee_id for x in loads.values()}))
        ).all()
    )
    users = _users(
        db,
        {e.created_by for e in expenses}
        | {e.voided_by for e in expenses if e.voided_by}
        | {x.created_by for x in loads.values()}
        | {x.updated_by for x in loads.values()},
    )
    result = []
    for expense, day in rows:
        category = categories[expense.category_id]
        account = accounts.get(expense.paid_from_account_id) if expense.paid_from_account_id else None
        state: s.ExpenseState = (
            "IN_EFFECT"
            if expense.voided_at is None
            else ("REVERSED" if expense.reversal_operation_id is not None else "VOIDED")
        )
        result.append(
            s.ExpenseOut(
                id=expense.id,
                origin=expense.origin,
                state=state,
                business_date=day,
                category_id=category.id,
                category_name=category.name,
                category_code=category.code,
                amount=expense.amount,
                description=expense.description,
                settlement_id=expense.settlement_id,
                paid_from_account_id=expense.paid_from_account_id,
                paid_from_account_name=account.name if account else None,
                paid_from_account_kind=account.kind if account else None,
                operation_id=expense.operation_id,
                reversal_operation_id=expense.reversal_operation_id,
                fuel=fuel_out(loads[expense.id], vehicles, employees, users) if expense.id in loads else None,
                legacy_fuel_without_detail=expense.legacy_fuel_without_detail,
                created_by=expense.created_by,
                created_by_name=users.get(expense.created_by, ""),
                created_at=expense.created_at,
                ended_by_name=users.get(expense.voided_by) if expense.voided_by else None,
                ended_at=expense.voided_at,
                end_reason=expense.void_reason,
            )
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
