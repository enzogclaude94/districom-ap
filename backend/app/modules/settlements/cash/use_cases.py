"""Cash and bank operations of R1: each one builds its legs and publishes them through the ledger service.

Each use case commits its own transaction. A violation that only the database can see at commit (for
example, a concurrent writer outside the service) becomes `409 invalid_ledger_operation`, never a raw
PostgreSQL message.
"""

import logging
from dataclasses import dataclass
from datetime import date

from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.core.errors import AppError, database_timeout
from app.core.logging import db_error_fields
from app.modules.settlements.cash import schemas
from app.modules.settlements.ledger import accounts as ledger_accounts
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.service import Leg
from app.platform.auth.models import User

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Result:
    operation: ledger.OperationDetail
    # False when the request repeated an earlier one (same client_request_id and content).
    created: bool


def cash_transfer(db: Session, actor: User, body: schemas.CashTransferIn) -> Result:
    return _publish(
        db,
        actor,
        "CASH_TRANSFER",
        body,
        [Leg(body.from_account_id, -body.amount), Leg(body.to_account_id, body.amount)],
    )


def bank_deposit(db: Session, actor: User, body: schemas.BankDepositIn) -> Result:
    return _publish(
        db,
        actor,
        "BANK_DEPOSIT",
        body,
        [Leg(body.cash_account_id, -body.amount), Leg(body.bank_account_id, body.amount)],
    )


def bank_withdrawal(db: Session, actor: User, body: schemas.BankWithdrawalIn) -> Result:
    legs = [Leg(body.bank_account_id, -body.amount), Leg(body.cash_account_id, body.amount)]
    return _publish(db, actor, "BANK_WITHDRAWAL", body, legs)


def opening_balance(db: Session, actor: User, body: schemas.OpeningBalanceIn) -> Result:
    counterpart = ledger.system_account_id(db, "opening_balance")
    return _publish(
        db, actor, "OPENING_BALANCE", body, [Leg(body.account_id, body.amount), Leg(counterpart, -body.amount)]
    )


def owner_contribution(db: Session, actor: User, body: schemas.OwnerMovementIn) -> Result:
    legs = [Leg(body.account_id, body.amount), Leg(body.owner_account_id, -body.amount)]
    return _publish(db, actor, "OWNER_CONTRIBUTION", body, legs)


def owner_withdrawal(db: Session, actor: User, body: schemas.OwnerMovementIn) -> Result:
    legs = [Leg(body.account_id, -body.amount), Leg(body.owner_account_id, body.amount)]
    return _publish(db, actor, "OWNER_WITHDRAWAL", body, legs)


def adjustment(db: Session, actor: User, body: schemas.AdjustmentIn) -> Result:
    counterpart = ledger.system_account_id(db, "adjustment")
    return _publish(db, actor, "ADJUSTMENT", body, [Leg(body.account_id, body.amount), Leg(counterpart, -body.amount)])


def reverse(db: Session, actor: User, operation_id: int, body: schemas.ReversalIn) -> Result:
    posted = ledger.reverse_operation(
        db,
        actor=actor,
        operation_id=operation_id,
        reason=body.reason,
        client_request_id=body.client_request_id,
        business_date=body.business_date,
    )
    return _finish(db, posted)


def create_owner_account(db: Session, actor: User, body: schemas.OwnerAccountIn) -> ledger.AccountBalance:
    account_id = ledger_accounts.create_owner_account(db, actor=actor, name=body.name)
    _commit(db)
    return ledger.account_balance(db, account_id)


def _publish(db: Session, actor: User, type: str, body: schemas.OperationIn, legs: list[Leg]) -> Result:
    posted = ledger.post_operation(
        db,
        actor=actor,
        type=type,
        legs=legs,
        client_request_id=body.client_request_id,
        business_date=body.business_date,
        description=body.description,
    )
    return _finish(db, posted)


def _finish(db: Session, posted: ledger.Posted) -> Result:
    operation_id = posted.operation.id
    if posted.created:
        _commit(db)
    return Result(ledger.operation_detail(db, operation_id), posted.created)


def _commit(db: Session) -> None:
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        logger.warning("ledger_commit_rejected", extra=db_error_fields(exc))
        raise AppError(
            409,
            "invalid_ledger_operation",
            "La operación no cumple las reglas del libro de movimientos y no se registró.",
        ) from exc


def balances(db: Session, on: date | None, include_inactive: bool) -> list[ledger.AccountBalance]:
    return ledger.account_balances(db, on=on, include_inactive=include_inactive)


def movements(
    db: Session, account_id: int, date_from: date | None, date_to: date | None, limit: int, offset: int
) -> tuple[list[ledger.Movement], int]:
    if date_from and date_to and date_from > date_to:
        raise AppError(422, "invalid_date_range", "La fecha desde no puede ser posterior a la fecha hasta.")
    return ledger.account_movements(db, account_id, date_from=date_from, date_to=date_to, limit=limit, offset=offset)


def account(db: Session, account_id: int, on: date | None) -> ledger.AccountBalance:
    return ledger.account_balance(db, account_id, on=on)


def operation(db: Session, operation_id: int) -> ledger.OperationDetail:
    return ledger.operation_detail(db, operation_id)
