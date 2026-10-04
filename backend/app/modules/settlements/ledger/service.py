"""The only door to the ledger of Rendiciones (ADR 007 §8, ARCHITECTURE.md §4.6).

Every movement of value is published with `post_operation` (or undone with `reverse_operation`) inside
the caller's transaction; the caller commits. Nothing else may insert into `financial_operations` or
`ledger_entries`.

Guarantees, in the service and again in the database (migration 0005):
- balanced: the legs of an operation sum zero;
- append-only: no UPDATE or DELETE; corrections are reversals;
- idempotent: a `client_request_id` publishes once. Repeating it with the same command returns the
  original operation; repeating it with a different command is `409 idempotency_conflict`;
- no negative cash: accounts with `allow_negative = false` are locked (`FOR NO KEY UPDATE`, in id
  order) before their balance is checked, so concurrent operations cannot overdraw them, and a
  retroactive operation cannot leave any past end-of-day balance negative. Requires the default
  READ COMMITTED isolation.
"""

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.ledger.models import (
    REASON_REQUIRED_TYPES,
    Account,
    FinancialOperation,
    LedgerEntry,
)
from app.modules.settlements.ledger.rules import REVERSAL_MIN_ROLE, RULES, leg_shape_error
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User

MODULE_KEY = "settlements"
CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("999999999999.99")  # numeric(14,2)


@dataclass(frozen=True)
class Leg:
    account_id: int
    amount: Decimal


@dataclass(frozen=True)
class SourceRef:
    """Document that originated an operation (settlement, payment, pending...), from R2 on."""

    type: str
    id: int


@dataclass(frozen=True)
class Posted:
    operation: FinancialOperation
    # False when the request was a retry and the existing operation was returned.
    created: bool


@dataclass(frozen=True)
class AccountBalance:
    account_id: int
    code: str | None
    name: str
    kind: str
    allow_negative: bool
    active: bool
    balance: Decimal


@dataclass(frozen=True)
class Movement:
    entry_id: int
    operation_id: int
    operation_type: str
    business_date: date
    description: str | None
    amount: Decimal
    created_by: int
    created_at: datetime
    source_type: str | None
    source_id: int | None
    reverses_operation_id: int | None
    reversed_by_operation_id: int | None


# --- writing -------------------------------------------------------------------------------------


def post_operation(
    db: Session,
    *,
    actor: User,
    type: str,
    legs: Sequence[Leg],
    client_request_id: UUID,
    business_date: date | None = None,
    description: str | None = None,
    source: SourceRef | None = None,
) -> Posted:
    """Publish an operation of `type` with its legs. See the module docstring for the guarantees."""
    rule = RULES.get(type)
    if rule is None:
        raise AppError(422, "unknown_operation_type", "El tipo de operación no existe.")
    _require_role(actor, rule.min_role)
    return _post(
        db,
        actor=actor,
        type=type,
        legs=legs,
        client_request_id=client_request_id,
        business_date=business_date,
        description=description,
        source=source,
        reverses=None,
    )


def reverse_operation(
    db: Session,
    *,
    actor: User,
    operation_id: int,
    reason: str,
    client_request_id: UUID,
    business_date: date | None = None,
) -> Posted:
    """Undo an operation with a REVERSAL carrying its opposite legs. The original stays untouched."""
    _require_role(actor, REVERSAL_MIN_ROLE)
    original = db.get(FinancialOperation, operation_id)
    if original is None:
        raise AppError(404, "operation_not_found", "La operación no existe.")
    if original.type == "REVERSAL":
        raise AppError(
            409,
            "cannot_reverse_reversal",
            "Un reverso no se revierte: registrá una operación nueva con el movimiento correcto.",
        )
    return _post(
        db,
        actor=actor,
        type="REVERSAL",
        legs=[Leg(entry.account_id, -entry.amount) for entry in original.entries],
        client_request_id=client_request_id,
        business_date=business_date,
        description=reason,
        source=None,
        reverses=original,
    )


def _post(
    db: Session,
    *,
    actor: User,
    type: str,
    legs: Sequence[Leg],
    client_request_id: UUID,
    business_date: date | None,
    description: str | None,
    source: SourceRef | None,
    reverses: FinancialOperation | None,
) -> Posted:
    business_date = business_date or business_today()
    description = (description or "").strip() or None
    legs = [Leg(leg.account_id, _money(leg.amount)) for leg in legs]
    fingerprint = command_fingerprint(
        actor_id=actor.id,
        type=type,
        business_date=business_date,
        description=description,
        legs=legs,
        source=source,
        reverses_operation_id=reverses.id if reverses else None,
    )

    # A retry is answered before anything else is validated: it must get the same result as the first time.
    existing = _find_by_request_id(db, client_request_id)
    if existing is not None:
        return _replay(existing, fingerprint)

    _validate_command(type, business_date, description, legs)
    accounts = _load_accounts(db, legs)
    if reverses is None:
        rule = RULES[type]
        shape_error = leg_shape_error(
            rule, [accounts[leg.account_id].kind for leg in legs], [1 if leg.amount > 0 else -1 for leg in legs]
        )
        if shape_error:
            raise AppError(422, "invalid_legs", shape_error)
    elif _reversed_by(db, reverses.id) is not None:
        raise AppError(409, "operation_already_reversed", "La operación ya fue revertida.")
    _check_funds(db, legs, accounts, business_date)

    operation = FinancialOperation(
        type=type,
        business_date=business_date,
        description=description,
        client_request_id=client_request_id,
        request_fingerprint=fingerprint,
        source_type=source.type if source else None,
        source_id=source.id if source else None,
        reverses_operation_id=reverses.id if reverses else None,
        created_by=actor.id,
    )
    operation.entries = [LedgerEntry(account_id=leg.account_id, amount=leg.amount) for leg in legs]
    try:
        with db.begin_nested():
            db.add(operation)
            db.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_financial_operations_client_request_id":
            # A concurrent request with the same key committed first.
            winner = _find_by_request_id(db, client_request_id)
            if winner is not None:
                return _replay(winner, fingerprint)
        if constraint == "uq_financial_operations_reverses_operation_id":
            raise AppError(409, "operation_already_reversed", "La operación ya fue revertida.") from exc
        raise

    _audit(db, actor, operation, reverses)
    return Posted(operation, created=True)


def command_fingerprint(
    *,
    actor_id: int,
    type: str,
    business_date: date,
    description: str | None,
    legs: Sequence[Leg],
    source: SourceRef | None,
    reverses_operation_id: int | None,
) -> str:
    """Stable SHA-256 of what an operation request means; leg order and amount formatting do not matter."""
    payload = {
        "actor": actor_id,
        "type": type,
        "business_date": business_date.isoformat(),
        "description": description,
        "legs": sorted([leg.account_id, str(_money(leg.amount))] for leg in legs),
        "source": [source.type, source.id] if source else None,
        "reverses": reverses_operation_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _replay(existing: FinancialOperation, fingerprint: str) -> Posted:
    if existing.request_fingerprint != fingerprint:
        raise AppError(
            409,
            "idempotency_conflict",
            "Ese identificador de solicitud ya se usó para una operación distinta.",
            {"operation_id": existing.id},
        )
    return Posted(existing, created=False)


def _require_role(actor: User, min_role: str) -> None:
    if not has_module_access(actor, MODULE_KEY, min_role):
        raise AppError(403, "forbidden", "No tenés permiso para registrar esta operación.")


def _money(amount: Decimal) -> Decimal:
    if not isinstance(amount, Decimal) or not amount.is_finite():
        raise AppError(422, "invalid_amount", "El importe no es válido.")
    if amount != amount.quantize(CENT):
        raise AppError(422, "invalid_amount", "El importe admite como máximo dos decimales.")
    return amount.quantize(CENT)


def _validate_command(type: str, business_date: date, description: str | None, legs: list[Leg]) -> None:
    if business_date > business_today():
        raise AppError(422, "future_business_date", "La fecha de la operación no puede ser futura.")
    if type in REASON_REQUIRED_TYPES and description is None:
        raise AppError(422, "reason_required", "Esta operación requiere un motivo.")
    if len(legs) < 2:
        raise AppError(422, "invalid_legs", "Una operación tiene al menos dos movimientos.")
    if len({leg.account_id for leg in legs}) != len(legs):
        raise AppError(422, "invalid_legs", "Una cuenta aparece más de una vez en la operación.")
    if any(leg.amount == 0 or abs(leg.amount) > MAX_AMOUNT for leg in legs):
        raise AppError(422, "invalid_amount", "Cada movimiento debe tener un importe distinto de cero.")
    if sum((leg.amount for leg in legs), Decimal(0)) != 0:
        raise AppError(422, "unbalanced_operation", "Los movimientos de la operación no suman cero.")


def _load_accounts(db: Session, legs: list[Leg]) -> dict[int, Account]:
    ids = {leg.account_id for leg in legs}
    accounts = {account.id: account for account in db.scalars(select(Account).where(Account.id.in_(ids)))}
    if accounts.keys() != ids:
        raise AppError(422, "account_not_found", "Una de las cuentas no existe.")
    if not all(account.active for account in accounts.values()):
        raise AppError(409, "account_inactive", "Una de las cuentas está dada de baja.")
    return accounts


def _check_funds(db: Session, legs: list[Leg], accounts: dict[int, Account], business_date: date) -> None:
    """Lock every account that cannot go negative and loses value here, then check its balances.

    The leg changes the end-of-day balance of `business_date` and of every later day, so the lowest of
    those balances must cover it: a retroactive withdrawal cannot leave a past day negative even if
    today's balance stays positive. Locks are taken in id order so two operations never wait on each
    other in a cycle; `NO KEY UPDATE` does not block the foreign-key checks of concurrent inserts into
    other accounts.
    """
    debits = sorted(
        (leg for leg in legs if leg.amount < 0 and not accounts[leg.account_id].allow_negative),
        key=lambda leg: leg.account_id,
    )
    for leg in debits:
        db.execute(select(Account.id).where(Account.id == leg.account_id).with_for_update(key_share=True))
        lowest, on = _lowest_balance_since(db, leg.account_id, business_date)
        if lowest + leg.amount < 0:
            account = accounts[leg.account_id]
            raise AppError(
                409,
                "insufficient_funds",
                f"{account.name} quedaría con saldo negativo el {on.strftime('%d/%m/%Y')}.",
                {
                    "account_id": account.id,
                    "business_date": on.isoformat(),
                    "balance": str(lowest),
                    "amount": str(leg.amount),
                },
            )


def _lowest_balance_since(db: Session, account_id: int, since: date) -> tuple[Decimal, date]:
    """Lowest end-of-day balance of the account on `since` or any later business date, and that date.

    Balances only change on days with entries, so the candidates are `since` itself and those days.
    One indexed pass over the account's entries: a few hundred a day at most for a distributor.
    """
    daily = (
        select(FinancialOperation.business_date.label("day"), func.sum(LedgerEntry.amount).label("amount"))
        .join(FinancialOperation, LedgerEntry.operation_id == FinancialOperation.id)
        .where(LedgerEntry.account_id == account_id)
        .group_by(FinancialOperation.business_date)
        .subquery()
    )
    running = select(daily.c.day, func.sum(daily.c.amount).over(order_by=daily.c.day).label("balance")).subquery()
    lowest = Decimal(db.scalar(select(func.coalesce(func.sum(daily.c.amount), 0)).where(daily.c.day <= since)) or 0)
    on = since
    later = db.execute(
        select(running.c.day, running.c.balance)
        .where(running.c.day > since)
        .order_by(running.c.balance, running.c.day)
        .limit(1)
    ).first()
    if later is not None and later.balance < lowest:
        lowest, on = Decimal(later.balance), later.day
    return lowest, on


def _find_by_request_id(db: Session, client_request_id: UUID) -> FinancialOperation | None:
    return db.scalars(
        select(FinancialOperation).where(FinancialOperation.client_request_id == client_request_id)
    ).one_or_none()


def _reversed_by(db: Session, operation_id: int) -> int | None:
    return db.scalars(
        select(FinancialOperation.id).where(FinancialOperation.reverses_operation_id == operation_id)
    ).one_or_none()


def _audit(db: Session, actor: User, operation: FinancialOperation, reverses: FinancialOperation | None) -> None:
    after: dict[str, Any] = {
        "type": operation.type,
        "business_date": operation.business_date.isoformat(),
        "description": operation.description,
        "legs": [{"account_id": e.account_id, "amount": str(e.amount)} for e in operation.entries],
    }
    if reverses is not None:
        after["reverses_operation_id"] = reverses.id
    record_change(
        db,
        "settlements.operation.reversed" if reverses else "settlements.operation.posted",
        action="reverse" if reverses else "post",
        user_id=actor.id,
        entity_type="financial_operation",
        entity_id=operation.id,
        before=None,
        after=after,
    )


# --- reading -------------------------------------------------------------------------------------


def account_balances(db: Session, *, on: date | None = None, include_inactive: bool = False) -> list[AccountBalance]:
    """Balance of every account, at the end of `on` (business date) or today. Always summed from entries."""
    sums_query = (
        select(LedgerEntry.account_id, func.sum(LedgerEntry.amount).label("balance"))
        .join(FinancialOperation)
        .group_by(LedgerEntry.account_id)
    )
    if on is not None:
        sums_query = sums_query.where(FinancialOperation.business_date <= on)
    sums = sums_query.subquery()
    query = (
        select(Account, func.coalesce(sums.c.balance, 0))
        .outerjoin(sums, sums.c.account_id == Account.id)
        .order_by(Account.id)
    )
    if not include_inactive:
        query = query.where(Account.active)
    return [
        AccountBalance(a.id, a.code, a.name, a.kind, a.allow_negative, a.active, Decimal(balance))
        for a, balance in db.execute(query)
    ]


def account_movements(
    db: Session,
    account_id: int,
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[Movement], int]:
    """Entries of an account with their operation, newest first, and the total count."""
    if db.get(Account, account_id) is None:
        raise AppError(404, "account_not_found", "La cuenta no existe.")
    reversal = aliased(FinancialOperation)
    query = (
        select(LedgerEntry, FinancialOperation, reversal.id)
        .join(FinancialOperation, LedgerEntry.operation_id == FinancialOperation.id)
        .outerjoin(reversal, reversal.reverses_operation_id == FinancialOperation.id)
        .where(LedgerEntry.account_id == account_id)
    )
    if date_from is not None:
        query = query.where(FinancialOperation.business_date >= date_from)
    if date_to is not None:
        query = query.where(FinancialOperation.business_date <= date_to)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(FinancialOperation.business_date.desc(), FinancialOperation.id.desc())
        .limit(limit)
        .offset(offset)
    )
    movements = [
        Movement(
            entry_id=entry.id,
            operation_id=op.id,
            operation_type=op.type,
            business_date=op.business_date,
            description=op.description,
            amount=entry.amount,
            created_by=op.created_by,
            created_at=op.created_at,
            source_type=op.source_type,
            source_id=op.source_id,
            reverses_operation_id=op.reverses_operation_id,
            reversed_by_operation_id=reversed_by,
        )
        for entry, op, reversed_by in rows
    ]
    return movements, total


def get_operation(db: Session, operation_id: int) -> FinancialOperation:
    """An operation with its legs (traceability: entry → operation → source document)."""
    operation = db.get(FinancialOperation, operation_id)
    if operation is None:
        raise AppError(404, "operation_not_found", "La operación no existe.")
    return operation
