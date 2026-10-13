"""Expenses of a settlement and their categories.

An expense is money the driver paid out of what he collected (toll, a repair...): it justifies that
amount. While the settlement is OPEN it can be corrected or voided with a reason, never deleted.
Categories are edited by an admin and deactivated, never deleted. Only a migration gives one a `code`:
FUEL (R5-2) makes an expense carry a fuel load, whose liters x price per liter is its amount (`fuel.py`).
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery import fuel, idempotency, service
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import (
    FUEL,
    Expense,
    ExpenseCategory,
    FuelLoad,
    Settlement,
    SettlementClosingFuelLoad,
)
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
    request: dict[str, Any] = {
        "settlement_id": settlement_id,
        "category_id": body.category_id,
        "amount": service.money(body.amount) if body.amount is not None else None,
        "description": description,
    }
    if body.fuel is not None:  # absent otherwise, so an expense loaded before R5-2 keeps its fingerprint
        request["fuel"] = _fuel_request(body.fuel)
    fingerprint = idempotency.fingerprint(actor.id, "expense", request)
    existing = _by_request_id(db, body)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "expense_id", existing.id)
        return service.Created(existing.id, created=False)

    settlement = service.lock_open(db, settlement_id)
    category = _active_category(db, body.category_id)
    load: FuelLoad | None = None
    if category.code == FUEL:
        if not service.profile(settlement).fuel:
            raise AppError(
                422,
                "fuel_not_allowed_for_outlet",
                "La rendición de boca de expendio no admite gastos de combustible.",
            )
        if body.amount is not None:
            raise _amount_is_computed()
        if body.fuel is None:
            raise AppError(
                422,
                "fuel_detail_required",
                "Un gasto de combustible lleva vehículo, empleado, litros y precio por litro.",
            )
        fuel.require_vehicle(db, body.fuel.vehicle_id)
        employee_id = fuel.require_employee(db, body.fuel.employee_id or settlement.employee_id)
        amount = fuel.amount(body.fuel.liters, body.fuel.price_per_liter)
        load = FuelLoad(
            vehicle_id=body.fuel.vehicle_id,
            employee_id=employee_id,
            liters=body.fuel.liters,
            price_per_liter=body.fuel.price_per_liter,
            odometer_km=body.fuel.odometer_km,
            created_by=actor.id,
            updated_by=actor.id,
        )
    else:
        if body.fuel is not None:
            raise _detail_not_allowed()
        if body.amount is None:
            raise AppError(422, "invalid_amount", "El importe es obligatorio.")
        amount = body.amount
    expense = Expense(
        settlement_id=settlement.id,
        category_id=body.category_id,
        amount=amount,
        description=description,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(expense)
            db.flush()
            if load is not None:
                load.expense_id = expense.id
                db.add(load)
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
        after={"settlement_id": settlement.id, **_snapshot(expense, load)},
    )
    service.commit(db)
    return service.Created(expense.id, created=True)


def update(db: Session, actor: User, settlement_id: int, expense_id: int, body: s.ExpensePatch) -> None:
    """Correct an expense while its settlement is OPEN. A fuel expense changes through its load (the amount
    follows); it never becomes another category, nor another category FUEL (`fuel_category_change`): that is
    voided and loaded again. A legacy fuel expense keeps its exception for descriptive changes; a financial
    one needs its load, which clears it."""
    settlement = service.lock_open(db, settlement_id)
    expense = _get(db, settlement.id, expense_id)
    if expense.voided_at is not None:
        raise AppError(409, "expense_voided", "El gasto está anulado y no se modifica.")
    load = load_of(db, expense.id)
    is_fuel = db.get(ExpenseCategory, expense.category_id).code == FUEL  # type: ignore[union-attr]
    before = _snapshot(expense, load)
    fields = body.model_fields_set
    if "category_id" in fields:
        if body.category_id is None:
            raise AppError(422, "invalid_expense_category", "La categoría es obligatoria.")
        if body.category_id != expense.category_id:
            target = _active_category(db, body.category_id)
            if is_fuel or target.code == FUEL:
                raise AppError(
                    422,
                    "fuel_category_change",
                    "Un gasto no pasa de combustible a otra categoría ni al revés: anulalo y cargá uno nuevo.",
                )
        expense.category_id = body.category_id
    if is_fuel:
        if "amount" in fields:
            if load is None and body.fuel is None:
                raise _load_required()
            raise _amount_is_computed()
        if body.fuel is not None:
            load = _apply_fuel(db, actor, settlement.employee_id, expense, load, body.fuel)
            expense.amount = fuel.amount(load.liters, load.price_per_liter)
            expense.legacy_fuel_without_detail = False
    else:
        if body.fuel is not None:
            raise _detail_not_allowed()
        if "amount" in fields:
            if body.amount is None:
                raise AppError(422, "invalid_amount", "El importe es obligatorio.")
            expense.amount = body.amount
    if "description" in fields:
        expense.description = (body.description or "").strip() or None
    after = _snapshot(expense, load)
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


def _apply_fuel(
    db: Session, actor: User, driver_id: int | None, expense: Expense, load: FuelLoad | None, patch: s.FuelPatch
) -> FuelLoad:
    fields = patch.model_fields_set
    if load is None:  # a legacy expense getting its detail: everything but employee and odometer is needed
        missing = [f for f in ("vehicle_id", "liters", "price_per_liter") if getattr(patch, f) is None]
        if missing:
            raise _load_required(missing)
        load = FuelLoad(expense_id=expense.id, created_by=actor.id, updated_by=actor.id)
        fields = fields | {"vehicle_id", "employee_id", "liters", "price_per_liter"}
    old = fuel.snapshot(load) if load.id is not None else None
    if "vehicle_id" in fields:
        if patch.vehicle_id is None:
            raise AppError(422, "invalid_vehicle", "El vehículo es obligatorio.")
        if patch.vehicle_id != load.vehicle_id:
            fuel.require_vehicle(db, patch.vehicle_id)
            load.vehicle_id = patch.vehicle_id
    if "employee_id" in fields:
        employee_id = patch.employee_id or driver_id
        if employee_id != load.employee_id:
            load.employee_id = fuel.require_employee(db, employee_id)
    for name in ("liters", "price_per_liter"):
        if name in fields:
            value = getattr(patch, name)
            if value is None:
                raise AppError(422, f"invalid_{name}", "Litros y precio por litro son obligatorios.")
            setattr(load, name, value)
    if "odometer_km" in fields:
        load.odometer_km = patch.odometer_km
    if old is None:
        db.add(load)  # only now: every field is set before the insert
    elif fuel.snapshot(load) != old:
        load.updated_by = actor.id
    return load


def load_of(db: Session, expense_id: int) -> FuelLoad | None:
    return db.scalars(select(FuelLoad).where(FuelLoad.expense_id == expense_id)).one_or_none()


def publish_fuel(db: Session, settlement: Settlement, close_operation_id: int) -> None:
    """Freeze what the new close publishes per fuel load (R5-4): the loads of the expenses in effect, with
    their liters, price, amount, vehicle and employee. Until the next close this is the fuel in effect."""
    rows = db.execute(
        select(FuelLoad, Expense.amount)
        .join(Expense, Expense.id == FuelLoad.expense_id)
        .where(Expense.settlement_id == settlement.id, Expense.voided_at.is_(None))
        .order_by(FuelLoad.id)
    ).all()
    db.add_all(
        SettlementClosingFuelLoad(
            close_operation_id=close_operation_id,
            settlement_id=settlement.id,
            settlement_version=settlement.version,
            fuel_load_id=load.id,
            expense_id=load.expense_id,
            vehicle_id=load.vehicle_id,
            employee_id=load.employee_id,
            liters=load.liters,
            price_per_liter=load.price_per_liter,
            amount=amount,
        )
        for load, amount in rows
    )


def loads_of(db: Session, expense_ids: list[int]) -> dict[int, FuelLoad]:
    if not expense_ids:
        return {}
    return {row.expense_id: row for row in db.scalars(select(FuelLoad).where(FuelLoad.expense_id.in_(expense_ids)))}


def _fuel_request(body: s.FuelIn) -> dict[str, Any]:
    return {
        "vehicle_id": body.vehicle_id,
        "employee_id": body.employee_id,
        "liters": str(body.liters.quantize(fuel.LITER)),
        "price_per_liter": str(body.price_per_liter.quantize(fuel.PRICE)),
        "odometer_km": body.odometer_km,
    }


def _amount_is_computed() -> AppError:
    return AppError(
        422,
        "fuel_amount_is_computed",
        "El importe del combustible no se escribe: es litros por precio por litro y lo calcula el sistema.",
    )


def _detail_not_allowed() -> AppError:
    return AppError(422, "fuel_detail_not_allowed", "Solo un gasto de combustible lleva datos de carga.")


def _load_required(missing: list[str] | None = None) -> AppError:
    return AppError(
        422,
        "fuel_load_required",
        "Este gasto de combustible se cargó sin detalle: para cambiar su importe cargá vehículo, litros y "
        "precio por litro.",
        {"missing": missing} if missing else None,
    )


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


def _snapshot(expense: Expense, load: FuelLoad | None = None) -> dict[str, Any]:
    snapshot: dict[str, Any] = {
        "category_id": expense.category_id,
        "amount": service.money(expense.amount),
        "description": expense.description,
    }
    if load is not None:
        snapshot["fuel"] = fuel.snapshot(load)
    if expense.legacy_fuel_without_detail:
        snapshot["legacy_fuel_without_detail"] = True
    return snapshot


def _by_request_id(db: Session, body: s.ExpenseIn) -> Expense | None:
    return db.scalars(select(Expense).where(Expense.client_request_id == body.client_request_id)).first()
