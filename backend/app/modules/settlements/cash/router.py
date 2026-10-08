"""Cajas API, mounted at /api/v1/settlements. Roles: viewer reads; operator moves cash between boxes and
the bank; admin also records opening balances, partner movements, adjustments, reversals and partner
accounts (ADR 007, Enzo's R1 decisions). The ledger service checks the same roles again.

Writes are idempotent by `client_request_id`: 201 when the operation is created, 200 with the same
operation when the request repeats an earlier one, 409 `idempotency_conflict` when the id was used
for different content.
"""

from collections.abc import Callable
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.modules.settlements.cash import schemas, use_cases
from app.modules.settlements.cheques import views as cheque_views
from app.modules.settlements.ledger.service import AccountBalance, Movement, OperationDetail
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: cajas"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]


def _account_out(balance: AccountBalance, causes: list[schemas.OverdraftCauseOut] | None = None) -> schemas.AccountOut:
    return schemas.AccountOut(
        id=balance.account_id,
        code=balance.code,
        name=balance.name,
        kind=balance.kind,
        allow_negative=balance.allow_negative,
        active=balance.active,
        balance=balance.balance,
        overdrawn=balance.overdrawn,
        overdrawn_label="Banco en descubierto" if balance.overdrawn else None,
        overdraft_causes=causes or [],
    )


def _with_causes(db: DbSession, balance: AccountBalance, on: date | None) -> schemas.AccountOut:
    if not balance.overdrawn:
        return _account_out(balance)
    causes = [
        schemas.OverdraftCauseOut(
            rejection_id=r.id,
            cheque_id=r.cheque_id,
            operation_id=r.operation_id,
            business_date=r.business_date,
            amount=r.amount,
        )
        for r in cheque_views.overdraft_causes(db, balance.account_id, on)
    ]
    return _account_out(balance, causes)


def _movement_out(movement: Movement) -> schemas.MovementOut:
    return schemas.MovementOut(**movement.__dict__)


def _operation_out(detail: OperationDetail) -> schemas.OperationOut:
    return schemas.OperationOut(
        **{k: v for k, v in detail.__dict__.items() if k != "entries"},
        entries=[schemas.EntryOut(**entry.__dict__) for entry in detail.entries],
    )


def _respond(result: use_cases.Result, response: Response) -> schemas.OperationOut:
    response.status_code = 201 if result.created else 200
    return _operation_out(result.operation)


# --- reading --------------------------------------------------------------------------------------


@router.get("/accounts", response_model=list[schemas.AccountOut])
def list_accounts(
    db: DbSession,
    _: Viewer,
    on: Annotated[date | None, Query(description="Saldo al cierre de esta fecha de negocio")] = None,
    include_inactive: bool = False,
) -> list[schemas.AccountOut]:
    return [_with_causes(db, b, on) for b in use_cases.balances(db, on, include_inactive)]


@router.get("/accounts/{account_id}", response_model=schemas.AccountOut)
def get_account(account_id: int, db: DbSession, _: Viewer, on: date | None = None) -> schemas.AccountOut:
    return _with_causes(db, use_cases.account(db, account_id, on), on)


@router.get("/accounts/{account_id}/movements", response_model=schemas.MovementPage)
def list_movements(
    account_id: int,
    db: DbSession,
    _: Viewer,
    date_from: date | None = None,
    date_to: date | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> schemas.MovementPage:
    items, total = use_cases.movements(db, account_id, date_from, date_to, limit, offset)
    return schemas.MovementPage(items=[_movement_out(m) for m in items], total=total)


@router.get("/operations/{operation_id}", response_model=schemas.OperationOut)
def get_operation(operation_id: int, db: DbSession, _: Viewer) -> schemas.OperationOut:
    return _operation_out(use_cases.operation(db, operation_id))


# --- partner accounts (admin) ----------------------------------------------------------------------


@router.post("/accounts", response_model=schemas.AccountOut, status_code=201)
def create_owner_account(body: schemas.OwnerAccountIn, db: DbSession, user: Admin) -> schemas.AccountOut:
    return _account_out(use_cases.create_owner_account(db, user, body))


# --- operations -----------------------------------------------------------------------------------

OPERATION_RESPONSES: dict[int | str, dict[str, str]] = {
    200: {"description": "Repetición de una solicitud ya registrada: devuelve la operación original."},
    201: {"description": "Operación registrada."},
}


def _operation_endpoint(path: str, summary: str) -> Callable[..., object]:
    return router.post(
        path, response_model=schemas.OperationOut, status_code=201, responses=OPERATION_RESPONSES, summary=summary
    )


@_operation_endpoint("/operations/cash-transfer", "Movimiento entre cajas (operator)")
def cash_transfer(
    body: schemas.CashTransferIn, db: DbSession, user: Operator, response: Response
) -> schemas.OperationOut:
    return _respond(use_cases.cash_transfer(db, user, body), response)


@_operation_endpoint("/operations/bank-deposit", "Depósito de una caja en el banco (operator)")
def bank_deposit(
    body: schemas.BankDepositIn, db: DbSession, user: Operator, response: Response
) -> schemas.OperationOut:
    return _respond(use_cases.bank_deposit(db, user, body), response)


@_operation_endpoint("/operations/bank-withdrawal", "Extracción del banco a una caja (operator)")
def bank_withdrawal(
    body: schemas.BankWithdrawalIn, db: DbSession, user: Operator, response: Response
) -> schemas.OperationOut:
    return _respond(use_cases.bank_withdrawal(db, user, body), response)


@_operation_endpoint("/operations/opening-balance", "Saldo inicial de una caja o del banco (admin)")
def opening_balance(
    body: schemas.OpeningBalanceIn, db: DbSession, user: Admin, response: Response
) -> schemas.OperationOut:
    return _respond(use_cases.opening_balance(db, user, body), response)


@_operation_endpoint("/operations/owner-contribution", "Aporte de un socio (admin)")
def owner_contribution(
    body: schemas.OwnerMovementIn, db: DbSession, user: Admin, response: Response
) -> schemas.OperationOut:
    return _respond(use_cases.owner_contribution(db, user, body), response)


@_operation_endpoint("/operations/owner-withdrawal", "Devolución de aporte a un socio (admin)")
def owner_withdrawal(
    body: schemas.OwnerMovementIn, db: DbSession, user: Admin, response: Response
) -> schemas.OperationOut:
    return _respond(use_cases.owner_withdrawal(db, user, body), response)


@_operation_endpoint("/operations/adjustment", "Ajuste de una caja o del banco, con motivo (admin)")
def adjustment(body: schemas.AdjustmentIn, db: DbSession, user: Admin, response: Response) -> schemas.OperationOut:
    return _respond(use_cases.adjustment(db, user, body), response)


@_operation_endpoint("/operations/{operation_id}/reverse", "Reverso de una operación, con motivo (admin)")
def reverse(
    operation_id: int, body: schemas.ReversalIn, db: DbSession, user: Admin, response: Response
) -> schemas.OperationOut:
    return _respond(use_cases.reverse(db, user, operation_id, body), response)
