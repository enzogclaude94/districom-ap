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
- «Banco en descubierto» (R4-2): the leg on a bank of an external debit (`rules.EXTERNAL_BANK_DEBITS`: the
  rejection of a cheque already credited) is booked even into an overdraft; the account is still locked.
  Any other debit of the bank keeps every end-of-day balance from its date on >= 0, so it can neither
  create an overdraft nor deepen one; incomes are never limited and reduce it.
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
    CHEQUE_KINDS,
    REASON_REQUIRED_TYPES,
    Account,
    FinancialOperation,
    LedgerEntry,
)
from app.modules.settlements.ledger.rules import (
    DOCUMENT_TYPES,
    EXTERNAL_BANK_DEBITS,
    REVERSAL_MIN_ROLE,
    REVERSIBLE_DOCUMENT_TYPES,
    RULES,
    leg_shape_error,
)
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
    # The cheque this leg moves: required on the cheque accounts (R4), forbidden elsewhere. An operation
    # may have several legs on the same cheque account, one per cheque.
    cheque_id: int | None = None


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
class Replaced:
    """Result of `replace_operation`: the reversal of the original and its replacement."""

    reversal: FinancialOperation
    replacement: FinancialOperation
    # False when the request was a retry and the existing pair was returned.
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

    @property
    def overdrawn(self) -> bool:
        """«Banco en descubierto»: only a bank can be below zero, and only through an external debit."""
        return self.kind == "BANK" and self.balance < 0


@dataclass(frozen=True)
class Movement:
    entry_id: int
    operation_id: int
    operation_type: str
    business_date: date
    description: str | None
    amount: Decimal
    created_by: int
    created_by_name: str
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
    if type in DOCUMENT_TYPES and source is None:
        raise AppError(422, "source_required", "Esta operación se registra desde su documento.")
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
    if original.type in DOCUMENT_TYPES:
        raise AppError(
            409,
            "operation_owned_by_document",
            "Esta operación pertenece a un documento (una rendición o un pendiente de cobro): se corrige desde él.",
            {"source_type": original.source_type, "source_id": original.source_id},
        )
    return _post(
        db,
        actor=actor,
        type="REVERSAL",
        legs=[Leg(entry.account_id, -entry.amount, entry.cheque_id) for entry in original.entries],
        client_request_id=client_request_id,
        business_date=business_date,
        description=reason,
        source=None,
        reverses=original,
    )


def reverse_document_operation(
    db: Session,
    *,
    actor: User,
    operation_id: int,
    source: SourceRef,
    reason: str,
    client_request_id: UUID,
) -> Posted:
    """Undo an operation of a document (a pending's receipt or resolution, R3-2) from that document's flow.

    A reversal says "that operation was wrong", never a new economic fact: it carries the ORIGINAL
    business date, and `created_at` records when the correction happened. Funds are checked on that date
    and every later end of day, so reversing a receipt whose cash already left the box is
    `insufficient_funds`. The caller must mark its domain row reversed in the same transaction; the
    database refuses the commit otherwise (migration 0010). A retry with the same request returns it.
    """
    _require_role(actor, REVERSAL_MIN_ROLE)
    original = db.get(FinancialOperation, operation_id)
    if original is None:
        raise AppError(404, "operation_not_found", "La operación no existe.")
    if original.type not in REVERSIBLE_DOCUMENT_TYPES or (original.source_type, original.source_id) != (
        source.type,
        source.id,
    ):
        raise AppError(
            422, "invalid_document_reversal", "La operación no pertenece a ese documento o no se revierte así."
        )
    return _post(
        db,
        actor=actor,
        type="REVERSAL",
        legs=[Leg(entry.account_id, -entry.amount, entry.cheque_id) for entry in original.entries],
        client_request_id=client_request_id,
        business_date=original.business_date,
        description=reason,
        source=None,
        reverses=original,
    )


def replace_operation(
    db: Session,
    *,
    actor: User,
    original_id: int,
    type: str,
    legs: Sequence[Leg],
    client_request_id: UUID,
    reversal_request_id: UUID,
    business_date: date,
    description: str | None,
    reason: str,
    source: SourceRef,
) -> Replaced:
    """Replace a document's operation: publish its REVERSAL and the new operation as one economic change.

    Used when a corrected document is closed again (a reopened settlement, R2-2). Both operations carry
    `business_date` (the document's date, not the day of the correction; `created_at` records when it
    happened) and are written in the caller's transaction. Funds are checked on the NET effect of both
    per account, today and at every end of day since that date: correcting a close that brought
    $1.000.000 to $1.000.500 only needs room for +$500, even if the cash was deposited long ago. The
    database checks the same at commit, when both operations are in.

    The replacement must be of the same type and source as the original, so this is never a way to
    reverse something else. A retry with the same `client_request_id` and content returns the pair.
    """
    rule = RULES.get(type)
    if rule is None or type not in DOCUMENT_TYPES:
        raise AppError(422, "unknown_operation_type", "El tipo de operación no se puede reemplazar.")
    _require_role(actor, rule.min_role)
    description = (description or "").strip() or None
    new_legs = [Leg(leg.account_id, _money(leg.amount), leg.cheque_id) for leg in legs]
    fingerprint = command_fingerprint(
        actor_id=actor.id,
        type=type,
        business_date=business_date,
        description=description,
        legs=new_legs,
        source=source,
        reverses_operation_id=None,
    )
    existing = _find_by_request_id(db, client_request_id)
    if existing is not None:
        replayed = _replay(existing, fingerprint).operation
        undone = db.scalars(
            select(FinancialOperation).where(FinancialOperation.reverses_operation_id == original_id)
        ).one()
        return Replaced(undone, replayed, created=False)

    original = db.get(FinancialOperation, original_id)
    if original is None:
        raise AppError(404, "operation_not_found", "La operación no existe.")
    if (original.type, original.source_type, original.source_id) != (type, source.type, source.id):
        raise AppError(422, "invalid_replacement", "Solo se reemplaza una operación del mismo tipo y documento.")
    if _reversed_by(db, original.id) is not None:
        raise AppError(409, "operation_already_reversed", "La operación ya fue revertida.")
    reason = reason.strip()
    reversal_legs = [Leg(entry.account_id, -entry.amount, entry.cheque_id) for entry in original.entries]
    _validate_command("REVERSAL", business_date, reason or None, reversal_legs)
    _validate_command(type, business_date, description, new_legs)
    accounts = _load_accounts(db, new_legs + reversal_legs)
    _check_cheque_legs(new_legs, accounts)
    shape_error = leg_shape_error(
        rule, [accounts[leg.account_id].kind for leg in new_legs], [1 if leg.amount > 0 else -1 for leg in new_legs]
    )
    if shape_error:
        raise AppError(422, "invalid_legs", shape_error)

    net: dict[int, Decimal] = {}
    for leg in reversal_legs + new_legs:
        net[leg.account_id] = net.get(leg.account_id, Decimal(0)) + leg.amount
    net_legs = [Leg(account_id, amount) for account_id, amount in net.items() if amount != 0]
    _check_funds(db, net_legs, accounts, business_date)

    reversal = _post(
        db,
        actor=actor,
        business_date=business_date,
        check_funds=False,
        type="REVERSAL",
        legs=reversal_legs,
        client_request_id=reversal_request_id,
        description=reason,
        source=None,
        reverses=original,
    )
    replacement = _post(
        db,
        actor=actor,
        business_date=business_date,
        check_funds=False,
        type=type,
        legs=new_legs,
        client_request_id=client_request_id,
        description=description,
        source=source,
        reverses=None,
    )
    return Replaced(reversal.operation, replacement.operation, created=True)


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
    check_funds: bool = True,
) -> Posted:
    business_date = business_date or business_today()
    description = (description or "").strip() or None
    legs = [Leg(leg.account_id, _money(leg.amount), leg.cheque_id) for leg in legs]
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
    _check_cheque_legs(legs, accounts)
    if reverses is None:
        rule = RULES[type]
        shape_error = leg_shape_error(
            rule, [accounts[leg.account_id].kind for leg in legs], [1 if leg.amount > 0 else -1 for leg in legs]
        )
        if shape_error:
            raise AppError(422, "invalid_legs", shape_error)
    elif _reversed_by(db, reverses.id) is not None:
        raise AppError(409, "operation_already_reversed", "La operación ya fue revertida.")
    if check_funds:  # replace_operation checks the net effect of the pair instead
        _check_funds(db, legs, accounts, business_date, operation_type=type)

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
    operation.entries = [
        LedgerEntry(account_id=leg.account_id, amount=leg.amount, cheque_id=leg.cheque_id) for leg in legs
    ]
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
        # A cheque is part of the leg only when there is one, so fingerprints of operations without
        # cheques (everything before R4) stay what they were.
        "legs": sorted(
            [leg.account_id, str(_money(leg.amount))] + ([leg.cheque_id] if leg.cheque_id is not None else [])
            for leg in legs
        ),
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
    if len({(leg.account_id, leg.cheque_id) for leg in legs}) != len(legs):
        raise AppError(422, "invalid_legs", "Una cuenta aparece más de una vez en la operación.")
    if any(leg.amount == 0 or abs(leg.amount) > MAX_AMOUNT for leg in legs):
        raise AppError(422, "invalid_amount", "Cada movimiento debe tener un importe distinto de cero.")
    if sum((leg.amount for leg in legs), Decimal(0)) != 0:
        raise AppError(422, "unbalanced_operation", "Los movimientos de la operación no suman cero.")


def _check_cheque_legs(legs: list[Leg], accounts: dict[int, Account]) -> None:
    """A leg on a cheque account names its cheque, and no other leg does (the database checks it too)."""
    for leg in legs:
        if (accounts[leg.account_id].kind in CHEQUE_KINDS) != (leg.cheque_id is not None):
            raise AppError(422, "invalid_legs", "Cada movimiento de la cartera de cheques corresponde a un cheque.")


def _load_accounts(db: Session, legs: list[Leg]) -> dict[int, Account]:
    ids = {leg.account_id for leg in legs}
    accounts = {account.id: account for account in db.scalars(select(Account).where(Account.id.in_(ids)))}
    if accounts.keys() != ids:
        raise AppError(422, "account_not_found", "Una de las cuentas no existe.")
    if not all(account.active for account in accounts.values()):
        raise AppError(409, "account_inactive", "Una de las cuentas está dada de baja.")
    return accounts


def _check_funds(
    db: Session,
    legs: list[Leg],
    accounts: dict[int, Account],
    business_date: date,
    *,
    operation_type: str | None = None,
) -> None:
    """Lock every account that cannot go negative and loses value here, then check its balances.

    The leg changes the end-of-day balance of `business_date` and of every later day, so the lowest of
    those balances must cover it: a retroactive withdrawal cannot leave a past day negative even if
    today's balance stays positive. Locks are taken in id order so two operations never wait on each
    other in a cycle; `NO KEY UPDATE` does not block the foreign-key checks of concurrent inserts into
    other accounts. An external debit of a bank (`EXTERNAL_BANK_DEBITS`) locks it too, but is not limited.
    """
    # Several legs may leave the same account (one per cheque): what is checked is their sum.
    debits: dict[int, Decimal] = {}
    for leg in legs:
        if leg.amount < 0 and not accounts[leg.account_id].allow_negative:
            debits[leg.account_id] = debits.get(leg.account_id, Decimal(0)) + leg.amount
    for account_id, amount in sorted(debits.items()):
        leg = Leg(account_id, amount)
        db.execute(select(Account.id).where(Account.id == leg.account_id).with_for_update(key_share=True))
        if operation_type in EXTERNAL_BANK_DEBITS and accounts[account_id].kind == "BANK":
            continue
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
        "legs": [
            {"account_id": e.account_id, "amount": str(e.amount)} | ({"cheque_id": e.cheque_id} if e.cheque_id else {})
            for e in operation.entries
        ],
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
        select(LedgerEntry, FinancialOperation, reversal.id, User.full_name)
        .join(FinancialOperation, LedgerEntry.operation_id == FinancialOperation.id)
        .join(User, User.id == FinancialOperation.created_by)
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
            created_by_name=author,
            created_at=op.created_at,
            source_type=op.source_type,
            source_id=op.source_id,
            reverses_operation_id=op.reverses_operation_id,
            reversed_by_operation_id=reversed_by,
        )
        for entry, op, reversed_by, author in rows
    ]
    return movements, total


def get_operation(db: Session, operation_id: int) -> FinancialOperation:
    """An operation with its legs (traceability: entry → operation → source document)."""
    operation = db.get(FinancialOperation, operation_id)
    if operation is None:
        raise AppError(404, "operation_not_found", "La operación no existe.")
    return operation


@dataclass(frozen=True)
class EntryDetail:
    account_id: int
    account_name: str
    account_kind: str
    amount: Decimal
    cheque_id: int | None = None


@dataclass(frozen=True)
class OperationDetail:
    id: int
    type: str
    business_date: date
    description: str | None
    created_by: int
    created_by_name: str
    created_at: datetime
    source_type: str | None
    source_id: int | None
    reverses_operation_id: int | None
    reversed_by_operation_id: int | None
    entries: list[EntryDetail]


def operation_detail(db: Session, operation_id: int) -> OperationDetail:
    """An operation as the API shows it: legs with their accounts, author and reversal links."""
    operation = get_operation(db, operation_id)
    accounts = {
        a.id: a for a in db.scalars(select(Account).where(Account.id.in_([e.account_id for e in operation.entries])))
    }
    author = db.get(User, operation.created_by)
    return OperationDetail(
        id=operation.id,
        type=operation.type,
        business_date=operation.business_date,
        description=operation.description,
        created_by=operation.created_by,
        created_by_name=author.full_name if author else "",
        created_at=operation.created_at,
        source_type=operation.source_type,
        source_id=operation.source_id,
        reverses_operation_id=operation.reverses_operation_id,
        reversed_by_operation_id=_reversed_by(db, operation.id),
        entries=[
            EntryDetail(e.account_id, accounts[e.account_id].name, accounts[e.account_id].kind, e.amount, e.cheque_id)
            for e in operation.entries
        ],
    )


def account_balance(db: Session, account_id: int, *, on: date | None = None) -> AccountBalance:
    """One account with its balance (inactive accounts included)."""
    for balance in account_balances(db, on=on, include_inactive=True):
        if balance.account_id == account_id:
            return balance
    raise AppError(404, "account_not_found", "La cuenta no existe.")


def system_account_id(db: Session, code: str) -> int:
    """Id of a seeded system account (`opening_balance`, `adjustment`...) by its stable code."""
    account_id = db.scalar(select(Account.id).where(Account.code == code))
    if account_id is None:
        raise AppError(500, "missing_system_account", "Falta una cuenta del sistema.")
    return account_id


def cheque_positions(db: Session, cheque_ids: Sequence[int]) -> dict[int, dict[str, Decimal]]:
    """Where each cheque is: the net of its legs per account kind (R4). A cheque in the portfolio has
    +amount there; one that never had effect, or left the company's hands, has nothing."""
    result: dict[int, dict[str, Decimal]] = {cid: {} for cid in cheque_ids}
    if not cheque_ids:
        return result
    rows = db.execute(
        select(LedgerEntry.cheque_id, Account.kind, func.sum(LedgerEntry.amount))
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(LedgerEntry.cheque_id.in_(cheque_ids))
        .group_by(LedgerEntry.cheque_id, Account.kind)
    )
    for cheque_id, kind, amount in rows:
        if cheque_id is not None and amount != 0:
            result[cheque_id][kind] = amount
    return result


def cheques_with_effect(db: Session, cheque_ids: Sequence[int]) -> set[int]:
    """Cheques that ever had a financial effect (any leg, even if later reversed): published instruments."""
    if not cheque_ids:
        return set()
    return set(db.scalars(select(LedgerEntry.cheque_id).where(LedgerEntry.cheque_id.in_(cheque_ids)).distinct()))


def cheque_activity(db: Session, cheque_ids: Sequence[int], *, except_types: Sequence[str]) -> set[int]:
    """Cheques moved by an operation of another type than `except_types` (nor its reversal)."""
    if not cheque_ids:
        return set()
    original = aliased(FinancialOperation)
    rows = db.scalars(
        select(LedgerEntry.cheque_id)
        .join(FinancialOperation, FinancialOperation.id == LedgerEntry.operation_id)
        .outerjoin(original, original.id == FinancialOperation.reverses_operation_id)
        .where(
            LedgerEntry.cheque_id.in_(cheque_ids),
            func.coalesce(original.type, FinancialOperation.type).not_in(list(except_types)),
        )
        .distinct()
    )
    return {cid for cid in rows if cid is not None}


def cheque_entry_dates(db: Session, cheque_ids: Sequence[int], kind: str) -> dict[int, date]:
    """Latest business date on which each cheque entered an account of `kind` (a positive leg naming it):
    no later document of the cheque may be dated before it (R4-2)."""
    if not cheque_ids:
        return {}
    rows = db.execute(
        select(LedgerEntry.cheque_id, func.max(FinancialOperation.business_date))
        .join(FinancialOperation, FinancialOperation.id == LedgerEntry.operation_id)
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(LedgerEntry.cheque_id.in_(cheque_ids), Account.kind == kind, LedgerEntry.amount > 0)
        .group_by(LedgerEntry.cheque_id)
    )
    return {cid: day for cid, day in rows if cid is not None}


def document_operation(db: Session, type: str, source: SourceRef) -> FinancialOperation:
    """The one operation of a document of the cheque cycle (unique by type and source)."""
    operation = db.scalars(
        select(FinancialOperation).where(
            FinancialOperation.type == type,
            FinancialOperation.source_type == source.type,
            FinancialOperation.source_id == source.id,
        )
    ).one_or_none()
    if operation is None:
        raise AppError(404, "operation_not_found", "La operación no existe.")
    return operation


def document_operation_ids(db: Session, type: str, source_type: str, source_ids: Sequence[int]) -> dict[int, int]:
    """Operation id per document id, for documents of the cheque cycle."""
    if not source_ids:
        return {}
    rows = db.execute(
        select(FinancialOperation.source_id, FinancialOperation.id).where(
            FinancialOperation.type == type,
            FinancialOperation.source_type == source_type,
            FinancialOperation.source_id.in_(source_ids),
        )
    )
    return {sid: oid for sid, oid in rows if sid is not None}


def cheques_held(db: Session, kind: str) -> dict[int, Decimal]:
    """Cheques whose legs leave a positive balance in an account of `kind` (CHEQUE_PORTFOLIO or
    CHEQUES_IN_TRANSIT): what the portfolio or the deposited cheques are made of, cheque by cheque."""
    rows = db.execute(
        select(LedgerEntry.cheque_id, func.sum(LedgerEntry.amount))
        .join(Account, Account.id == LedgerEntry.account_id)
        .where(Account.kind == kind, LedgerEntry.cheque_id.is_not(None))
        .group_by(LedgerEntry.cheque_id)
        .having(func.sum(LedgerEntry.amount) > 0)
    )
    return {cid: amount for cid, amount in rows if cid is not None}
