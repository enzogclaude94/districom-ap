"""Expenses API (R5-3), mounted at /api/v1/settlements. Roles: viewer reads every expense; operator records a
direct expense from a cash box or the bank; admin also reverses one and corrects its description or
odometer. The ledger service checks the roles again.

Creation and reversal are idempotent by `client_request_id`: 201 when created, 200 with the same expense
when the request repeats an earlier one, 409 `idempotency_conflict` when the id was used for different
content.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.modules.settlements.expenses import direct, views
from app.modules.settlements.expenses import schemas as s
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: gastos"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]


@router.get("/expenses", response_model=s.ExpensePage, summary="Gastos: directos y de rendiciones")
def list_expenses(db: DbSession, _: Viewer, q: Annotated[s.ExpenseListQuery, Query()]) -> s.ExpensePage:
    return views.list_expenses(db, q)


@router.get("/expenses/{expense_id}", response_model=s.ExpenseDetail, summary="Un gasto con su operación e historia")
def get_expense(expense_id: int, db: DbSession, _: Viewer) -> s.ExpenseDetail:
    return views.detail(db, expense_id)


@router.post("/expenses", response_model=s.ExpenseDetail, status_code=201, summary="Registrar un gasto directo")
def create_expense(body: s.DirectExpenseIn, db: DbSession, user: Operator, response: Response) -> s.ExpenseDetail:
    created = direct.create(db, user, body)
    response.status_code = 201 if created.created else 200
    return views.detail(db, created.id)


@router.post(
    "/expenses/{expense_id}/reverse",
    response_model=s.ExpenseDetail,
    status_code=201,
    summary="Revertir un gasto directo, con motivo (admin)",
)
def reverse_expense(
    expense_id: int, body: s.ExpenseReversalIn, db: DbSession, user: Admin, response: Response
) -> s.ExpenseDetail:
    created = direct.reverse(db, user, expense_id, body)
    response.status_code = 201 if created.created else 200
    return views.detail(db, expense_id)


@router.post(
    "/expenses/{expense_id}/descriptive",
    response_model=s.ExpenseDetail,
    summary="Corregir descripción u odómetro de un gasto directo, con motivo (admin)",
)
def correct_expense(expense_id: int, body: s.ExpenseCorrectionIn, db: DbSession, user: Admin) -> s.ExpenseDetail:
    direct.correct(db, user, expense_id, body)
    return views.detail(db, expense_id)
