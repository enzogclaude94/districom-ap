"""Direct expenses (R5-3): an expense paid from one cash box or the bank, outside any settlement.

The expense is the document of its own ledger operation: in ONE transaction the service writes the
expense, its fuel load (FUEL), the `DIRECT_EXPENSE` operation (the paying account -X, «Gastos operativos»
+X, source `expense:<id>`, on the expense's business date) and the audit; the database checks at commit
that the expense and its operation are exactly each other (migration 0016).

- Funds: an ordinary debit, through `ledger.service`: a cash box is never negative, today or on any day
  from the business date on; the bank never goes into an overdraft for it, nor deeper into one (it is not
  an external debit like a cheque rejection).
- Money is immutable once published: amount, category, paying account, date, and the load's vehicle,
  employee, liters and price. A mistake in any of them is a reversal plus a new expense.
- Reversal (admin, with a reason): the REVERSAL of its operation, on the ORIGINAL business date
  (`created_at` says when it was corrected), with the expense marked reversed in the same transaction.
  Owned by the expense: Cajas never reverses a `DIRECT_EXPENSE` (`operation_owned_by_document`).
- Descriptive correction (admin, with a reason): only the description and the fuel odometer.

Idempotent by `client_request_id`: the ledger request ids derive from the client's.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.delivery import fuel, idempotency
from app.modules.settlements.delivery.models import FUEL, Expense, ExpenseCategory, FuelLoad
from app.modules.settlements.delivery.service import money
from app.modules.settlements.expenses import schemas as s
from app.modules.settlements.ledger import service as ledger
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
SOURCE = "expense"
PAYING_KINDS = ("CASH_BOX", "BANK")
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("8d3c2f4e-6b1a-4e7d-9f20-5a7c1e3b9d46")


@dataclass(frozen=True)
class Created:
    id: int
    # False when the request repeated an earlier one (same client_request_id and content).
    created: bool


def create(db: Session, actor: User, body: s.DirectExpenseIn) -> Created:
    description = (body.description or "").strip() or None
    request: dict[str, Any] = {
        "business_date": body.business_date.isoformat(),
        "category_id": body.category_id,
        "paid_from_account_id": body.paid_from_account_id,
        "amount": money(body.amount) if body.amount is not None else None,
        "description": description,
        "fuel": _fuel_request(body.fuel) if body.fuel is not None else None,
    }
    fingerprint = idempotency.fingerprint(actor.id, "direct_expense", request)
    existing = _by_request_id(db, body.client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "expense_id", existing.id)
        return Created(existing.id, created=False)

    if body.business_date > business_today():
        raise AppError(422, "future_business_date", "La fecha del gasto no puede ser futura.")
    category = db.get(ExpenseCategory, body.category_id)
    if category is None or not category.active:
        raise AppError(422, "invalid_expense_category", "La categoría no existe o está desactivada.")
    account = _paying_account(db, body.paid_from_account_id)
    load: FuelLoad | None = None
    if category.code == FUEL:
        if body.amount is not None:
            raise AppError(
                422,
                "fuel_amount_is_computed",
                "El importe del combustible lo calcula el sistema con litros y precio por litro: no se escribe.",
            )
        if body.fuel is None:
            raise AppError(
                422,
                "fuel_detail_required",
                "Un gasto de combustible lleva vehículo, empleado, litros y precio por litro.",
            )
        if body.fuel.employee_id is None:
            raise AppError(
                422,
                "fuel_employee_required",
                "Elegí el empleado que cargó el combustible: desde una caja no hay repartidor por defecto.",
            )
        fuel.require_vehicle(db, body.fuel.vehicle_id)
        fuel.require_employee(db, body.fuel.employee_id)
        amount = fuel.amount(body.fuel.liters, body.fuel.price_per_liter)
        load = FuelLoad(
            vehicle_id=body.fuel.vehicle_id,
            employee_id=body.fuel.employee_id,
            liters=body.fuel.liters,
            price_per_liter=body.fuel.price_per_liter,
            odometer_km=body.fuel.odometer_km,
            created_by=actor.id,
            updated_by=actor.id,
        )
    else:
        if body.fuel is not None:
            raise AppError(
                422,
                "fuel_detail_not_allowed",
                "Solo un gasto de la categoría de combustible lleva carga de combustible.",
            )
        if body.amount is None:
            raise AppError(422, "invalid_amount", "El importe es obligatorio.")
        amount = body.amount

    expense = Expense(
        origin="DIRECT",
        settlement_id=None,
        business_date=body.business_date,
        category_id=category.id,
        paid_from_account_id=account.account_id,
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
    except IntegrityError:
        winner = _by_request_id(db, body.client_request_id)
        if winner is None:
            raise
        db.rollback()
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "expense_id", winner.id)
        return Created(winner.id, created=False)
    if load is not None:
        load.expense_id = expense.id
        db.add(load)
        db.flush()

    operation = ledger.post_operation(
        db,
        actor=actor,
        type="DIRECT_EXPENSE",
        legs=[
            ledger.Leg(account.account_id, -amount),
            ledger.Leg(ledger.system_account_id(db, "expense"), amount),
        ],
        client_request_id=uuid5(NAMESPACE, f"direct-expense:{body.client_request_id}"),
        business_date=body.business_date,
        # Fixed with the operation: the expense's own description may still be corrected.
        description=f"Gasto directo n.º {expense.id} · {category.name}",
        source=ledger.SourceRef(SOURCE, expense.id),
    ).operation
    expense.operation_id = operation.id
    db.flush()
    record_change(
        db,
        "settlements.expense.created",
        action="create",
        user_id=actor.id,
        entity_type="expense",
        entity_id=expense.id,
        before=None,
        after=snapshot(expense, load) | {"operation_id": operation.id},
    )
    commit(db)
    return Created(expense.id, created=True)


def reverse(db: Session, actor: User, expense_id: int, body: s.ExpenseReversalIn) -> Created:
    _require_admin(actor, "Solo un administrador revierte un gasto directo.")
    reason = _reason(body.reason)
    expense = _locked_direct(db, expense_id)
    request_id = uuid5(NAMESPACE, f"direct-expense-reversal:{body.client_request_id}")
    if expense.reversal_operation_id is not None:
        reversal = ledger.get_operation(db, expense.reversal_operation_id)
        if reversal.client_request_id == request_id:
            if (expense.voided_by, expense.void_reason) != (actor.id, reason):
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Ese identificador de solicitud ya se usó para un reverso distinto.",
                    {"operation_id": reversal.id},
                )
            db.commit()
            return Created(expense.id, created=False)
        raise AppError(
            409, "expense_already_reversed", "El gasto ya fue revertido.", {"reversal_operation_id": reversal.id}
        )
    assert expense.operation_id is not None  # a published direct expense always has it (0016)
    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=expense.operation_id,
        source=ledger.SourceRef(SOURCE, expense.id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    expense.reversal_operation_id = reversal.id
    expense.voided_by = actor.id
    expense.voided_at = datetime.now(UTC)
    expense.void_reason = reason
    db.flush()
    record_change(
        db,
        "settlements.expense.reversed",
        action="reverse",
        user_id=actor.id,
        entity_type="expense",
        entity_id=expense.id,
        before={"operation_id": expense.operation_id},
        after={
            "reversal_operation_id": reversal.id,
            "business_date": reversal.business_date.isoformat(),
            "reason": reason,
        },
    )
    commit(db)
    return Created(expense.id, created=True)


def correct(db: Session, actor: User, expense_id: int, body: s.ExpenseCorrectionIn) -> None:
    """Change the description and/or the odometer of a direct expense in effect; nothing else, ever."""
    _require_admin(actor, "Solo un administrador corrige un gasto directo.")
    reason = _reason(body.reason)
    expense = _locked_direct(db, expense_id)
    if expense.reversal_operation_id is not None:
        raise AppError(409, "expense_reversed", "El gasto fue revertido y ya no se corrige.")
    fields = body.model_fields_set - {"reason"}
    if not fields:
        raise AppError(422, "nothing_to_correct", "Indicá la descripción o el odómetro a corregir.")
    load = db.scalars(select(FuelLoad).where(FuelLoad.expense_id == expense.id).with_for_update()).one_or_none()
    if "odometer_km" in fields and load is None:
        raise AppError(422, "fuel_detail_not_allowed", "Solo un gasto de combustible tiene odómetro.")

    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    if "description" in fields:
        description = (body.description or "").strip() or None
        if description != expense.description:
            before["description"], after["description"] = expense.description, description
            expense.description = description
    if "odometer_km" in fields and load is not None and body.odometer_km != load.odometer_km:
        before["odometer_km"], after["odometer_km"] = load.odometer_km, body.odometer_km
        load.odometer_km = body.odometer_km
        load.updated_by = actor.id
    if not after:
        db.rollback()  # nothing changed (a repeated correction): release the locks
        return
    db.flush()
    record_change(
        db,
        "settlements.expense.corrected",
        action="update",
        user_id=actor.id,
        entity_type="expense",
        entity_id=expense.id,
        before=before,
        after=after | {"reason": reason},
    )
    commit(db)


# --- shared ---------------------------------------------------------------------------------------


def snapshot(expense: Expense, load: FuelLoad | None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "origin": expense.origin,
        "business_date": expense.business_date.isoformat() if expense.business_date else None,
        "category_id": expense.category_id,
        "paid_from_account_id": expense.paid_from_account_id,
        "amount": money(expense.amount),
        "description": expense.description,
    }
    if load is not None:
        result["fuel"] = fuel.snapshot(load)
    return result


def commit(db: Session) -> None:
    """Commit; a rule only the database saw is 409, never a raw error."""
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        logger.warning("direct_expense_commit_rejected", extra={"sqlstate": getattr(exc.orig, "sqlstate", None)})
        raise AppError(
            409, "invalid_direct_expense", "El gasto no cumple las reglas de los gastos directos y no se registró."
        ) from exc


def _paying_account(db: Session, account_id: int) -> ledger.AccountBalance:
    try:
        account = ledger.account_balance(db, account_id)
    except AppError:
        account = None
    if account is None or not account.active or account.kind not in PAYING_KINDS:
        raise AppError(
            422,
            "invalid_payment_account",
            "Un gasto directo se paga desde una caja o un banco activos.",
            {"paid_from_account_id": account_id},
        )
    return account


def _locked_direct(db: Session, expense_id: int) -> Expense:
    expense = db.scalars(
        select(Expense).where(Expense.id == expense_id).with_for_update().execution_options(populate_existing=True)
    ).one_or_none()
    if expense is None:
        raise AppError(404, "expense_not_found", "El gasto no existe.")
    if expense.origin != "DIRECT":
        raise AppError(
            422,
            "not_a_direct_expense",
            "Ese gasto es de una rendición: se corrige o anula desde la rendición mientras está abierta.",
            {"settlement_id": expense.settlement_id},
        )
    return expense


def _by_request_id(db: Session, client_request_id: UUID) -> Expense | None:
    return db.scalars(select(Expense).where(Expense.client_request_id == client_request_id)).one_or_none()


def _fuel_request(body: s.DirectFuelIn) -> dict[str, Any]:
    return {
        "vehicle_id": body.vehicle_id,
        "employee_id": body.employee_id,
        "liters": str(body.liters.quantize(fuel.LITER)),
        "price_per_liter": str(body.price_per_liter.quantize(fuel.PRICE)),
        "odometer_km": body.odometer_km,
    }


def _require_admin(actor: User, message: str) -> None:
    if not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(403, "forbidden", message)


def _reason(reason: str) -> str:
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo.")
    return reason
