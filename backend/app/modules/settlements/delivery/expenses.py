"""Expenses of a settlement and their categories.

An expense is money the driver paid out of what he collected (toll, a repair...): it justifies that
amount. While the settlement is OPEN it can be corrected or voided with a reason, never deleted.
Categories are edited by an admin and deactivated, never deleted; none is fixed in code.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery import idempotency, service
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import Expense, ExpenseCategory
from app.platform.audit.service import record_change
from app.platform.auth.models import User

_ACCENTS = str.maketrans("áàäâéèëêíìïîóòöôúùüû", "aaaaeeeeiiiioooouuuu")


def name_key(name: str) -> str:
    """Comparison key of a category name, same rule as account names: case, accents and spacing do not count."""
    return " ".join(name.split()).lower().translate(_ACCENTS)


# --- categories -----------------------------------------------------------------------------------


def categories(db: Session, include_inactive: bool) -> list[ExpenseCategory]:
    query = select(ExpenseCategory).order_by(ExpenseCategory.name, ExpenseCategory.id)
    if not include_inactive:
        query = query.where(ExpenseCategory.active.is_(True))
    return list(db.scalars(query))


def create_category(db: Session, actor: User, body: s.ExpenseCategoryIn) -> ExpenseCategory:
    name = _clean_name(body.name)
    _require_free_name(db, name, None)
    category = ExpenseCategory(name=name, name_key=name_key(name))
    try:
        with db.begin_nested():
            db.add(category)
            db.flush()
    except IntegrityError as exc:
        raise _name_taken(db, name) from exc
    record_change(
        db,
        "settlements.expense_category.created",
        action="create",
        user_id=actor.id,
        entity_type="expense_category",
        entity_id=category.id,
        before=None,
        after={"name": category.name, "active": True},
    )
    service.commit(db)
    return category


def update_category(db: Session, actor: User, category_id: int, body: s.ExpenseCategoryPatch) -> ExpenseCategory:
    category = db.get(ExpenseCategory, category_id, with_for_update=True)
    if category is None:
        raise AppError(404, "expense_category_not_found", "La categoría no existe.")
    before = {"name": category.name, "active": category.active}
    if body.name is not None:
        name = _clean_name(body.name)
        _require_free_name(db, name, category.id)
        category.name = name
        category.name_key = name_key(name)
    if body.active is not None:
        category.active = body.active
    after = {"name": category.name, "active": category.active}
    if before != after:
        record_change(
            db,
            "settlements.expense_category.updated",
            action="update",
            user_id=actor.id,
            entity_type="expense_category",
            entity_id=category.id,
            before=before,
            after=after,
        )
    service.commit(db)
    return category


def _clean_name(name: str) -> str:
    name = " ".join(name.split())
    if not name:
        raise AppError(422, "invalid_category_name", "Ingresá el nombre de la categoría.")
    return name


def _require_free_name(db: Session, name: str, own_id: int | None) -> None:
    other = db.scalars(select(ExpenseCategory).where(ExpenseCategory.name_key == name_key(name))).first()
    if other is not None and other.id != own_id:
        raise _name_taken(db, name)


def _name_taken(db: Session, name: str) -> AppError:
    other = db.scalars(select(ExpenseCategory).where(ExpenseCategory.name_key == name_key(name))).first()
    details = {"category_id": other.id, "active": other.active} if other else None
    return AppError(409, "expense_category_name_taken", f"Ya existe la categoría «{name}».", details)


def _active_category(db: Session, category_id: int) -> ExpenseCategory:
    category = db.get(ExpenseCategory, category_id)
    if category is None or not category.active:
        raise AppError(422, "invalid_expense_category", "La categoría no existe o está desactivada.")
    return category


# --- expenses -------------------------------------------------------------------------------------


def create(db: Session, actor: User, settlement_id: int, body: s.ExpenseIn) -> service.Created:
    description = (body.description or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        "expense",
        {
            "settlement_id": settlement_id,
            "category_id": body.category_id,
            "amount": service.money(body.amount),
            "description": description,
        },
    )
    existing = _by_request_id(db, body)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "expense_id", existing.id)
        return service.Created(existing.id, created=False)

    settlement = service.lock_open(db, settlement_id)
    _active_category(db, body.category_id)
    expense = Expense(
        settlement_id=settlement.id,
        category_id=body.category_id,
        amount=body.amount,
        description=description,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(expense)
            db.flush()
    except IntegrityError:
        winner = _by_request_id(db, body)
        if winner is None:
            raise
        db.rollback()  # release the locks taken for the insert that lost
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "expense_id", winner.id)
        return service.Created(winner.id, created=False)
    record_change(
        db,
        "settlements.expense.created",
        action="create",
        user_id=actor.id,
        entity_type="expense",
        entity_id=expense.id,
        before=None,
        after={"settlement_id": settlement.id, **_snapshot(expense)},
    )
    service.commit(db)
    return service.Created(expense.id, created=True)


def update(db: Session, actor: User, settlement_id: int, expense_id: int, body: s.ExpensePatch) -> None:
    settlement = service.lock_open(db, settlement_id)
    expense = _get(db, settlement.id, expense_id)
    if expense.voided_at is not None:
        raise AppError(409, "expense_voided", "El gasto está anulado y no se modifica.")
    before = _snapshot(expense)
    fields = body.model_fields_set
    if "category_id" in fields:
        if body.category_id is None:
            raise AppError(422, "invalid_expense_category", "La categoría es obligatoria.")
        if body.category_id != expense.category_id:
            _active_category(db, body.category_id)
        expense.category_id = body.category_id
    if "amount" in fields:
        if body.amount is None:
            raise AppError(422, "invalid_amount", "El importe es obligatorio.")
        expense.amount = body.amount
    if "description" in fields:
        expense.description = (body.description or "").strip() or None
    after = _snapshot(expense)
    if before != after:
        record_change(
            db,
            "settlements.expense.updated",
            action="update",
            user_id=actor.id,
            entity_type="expense",
            entity_id=expense.id,
            before=before,
            after=after,
        )
    service.commit(db)


def void(db: Session, actor: User, settlement_id: int, expense_id: int, reason: str) -> None:
    settlement = service.lock_open(db, settlement_id)
    expense = _get(db, settlement.id, expense_id)
    if expense.voided_at is not None:
        raise AppError(409, "expense_already_voided", "El gasto ya estaba anulado.")
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la anulación.")
    expense.voided_by = actor.id
    expense.voided_at = datetime.now(UTC)
    expense.void_reason = reason
    record_change(
        db,
        "settlements.expense.voided",
        action="void",
        user_id=actor.id,
        entity_type="expense",
        entity_id=expense.id,
        before={"voided": False},
        after={"voided": True, "reason": reason},
    )
    service.commit(db)


def of(db: Session, settlement_id: int) -> list[Expense]:
    return list(db.scalars(select(Expense).where(Expense.settlement_id == settlement_id).order_by(Expense.id)))


def _get(db: Session, settlement_id: int, expense_id: int) -> Expense:
    expense = db.get(Expense, expense_id)
    if expense is None or expense.settlement_id != settlement_id:
        raise AppError(404, "expense_not_found", "El gasto no existe en esta rendición.")
    return expense


def _snapshot(expense: Expense) -> dict[str, Any]:
    return {
        "category_id": expense.category_id,
        "amount": service.money(expense.amount),
        "description": expense.description,
    }


def _by_request_id(db: Session, body: s.ExpenseIn) -> Expense | None:
    return db.scalars(select(Expense).where(Expense.client_request_id == body.client_request_id)).first()
