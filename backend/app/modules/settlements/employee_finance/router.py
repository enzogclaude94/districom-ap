"""Rendiciones > Empleados API (R8-1, ADR 010), mounted at /api/v1/settlements/employee-finance.

Every endpoint requires the protected area `settlements.employee_finance` (404 without it, so nothing is
discoverable); a role in Rendiciones or being platform admin is not enough. Reading takes viewer; creating
or ending a salary term takes admin of the area. R8-2: a consumption takes operator of the area; an advance, an
adjustment and any reversal take admin of the area. R8-3: giving or reversing a loan takes admin of the area plus
operator in Rendiciones (it moves a cash box or the bank); deferring an installment takes admin of the area. Each
movement and loan is the document of its own ledger operation (`movements.py`, `loans.py`); writes are idempotent
by `client_request_id` (201 created, 200 a repeated request).
"""

from typing import Annotated

from fastapi import APIRouter, Query, Response

from app.modules.settlements.employee_finance import compensation, loans, movements, views
from app.modules.settlements.employee_finance import schemas as s
from app.modules.settlements.employee_finance.access import AreaAdmin, AreaOperator, AreaViewer
from app.platform.auth.deps import DbSession

router = APIRouter(prefix="/employee-finance", tags=["settlements: empleados"])


@router.get("/employees", response_model=s.EmployeeFinancePage)
def list_employees(
    db: DbSession,
    _: AreaViewer,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.EmployeeFinancePage:
    return views.employees(db, q, limit, offset)


@router.get("/employees/{employee_id}", response_model=s.EmployeeFinanceDetail)
def get_employee(employee_id: int, db: DbSession, _: AreaViewer) -> s.EmployeeFinanceDetail:
    return views.detail(db, employee_id)


@router.get("/employees/{employee_id}/movements", response_model=s.AccountMovementPage)
def list_movements(
    employee_id: int,
    db: DbSession,
    _: AreaViewer,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.AccountMovementPage:
    return views.movements(db, employee_id, limit, offset)


@router.get("/employees/{employee_id}/compensation-terms", response_model=list[s.CompensationTermOut])
def list_terms(employee_id: int, db: DbSession, _: AreaViewer) -> list[s.CompensationTermOut]:
    return views.terms(db, employee_id)


@router.post("/employees/{employee_id}/compensation-terms", response_model=s.CompensationTermOut, status_code=201)
def create_term(employee_id: int, body: s.CompensationTermIn, db: DbSession, user: AreaAdmin) -> s.CompensationTermOut:
    row = compensation.create(db, user, employee_id, body.valid_from, body.base_salary, body.notes)
    return views.term(db, row)


@router.post("/compensation-terms/{term_id}/end", response_model=s.CompensationTermOut)
def end_term(term_id: int, body: s.CompensationTermEndIn, db: DbSession, user: AreaAdmin) -> s.CompensationTermOut:
    row = compensation.end(db, user, term_id, body.valid_to, body.reason)
    return views.term(db, row)


# --- consumptions, advances and adjustments (R8-2) ---------------------------------------------------

MOVEMENT_RESPONSES: dict[int | str, dict[str, str]] = {
    200: {"description": "Repetición de una solicitud ya registrada: devuelve el movimiento original."},
    201: {"description": "Movimiento registrado."},
}


def _respond(db: DbSession, result: movements.Created, response: Response) -> s.EmployeeMovementOut:
    response.status_code = 201 if result.created else 200
    return views.movement(db, result.id)


@router.post("/employees/{employee_id}/movements/preview", response_model=s.MovementPreviewOut)
def preview_movement(
    employee_id: int, body: s.MovementPreviewIn, db: DbSession, user: AreaOperator
) -> s.MovementPreviewOut:
    """Validate a consumption, advance or adjustment and show its legs, without registering it."""
    return views.preview(db, user, employee_id, body)


@router.post(
    "/employees/{employee_id}/consumptions",
    response_model=s.EmployeeMovementOut,
    status_code=201,
    responses=MOVEMENT_RESPONSES,
)
def create_consumption(
    employee_id: int, body: s.ConsumptionIn, db: DbSession, user: AreaOperator, response: Response
) -> s.EmployeeMovementOut:
    cmd = movements.command(
        "CONSUMPTION", employee_id, body.business_date, body.amount, body.description, body.reference
    )
    return _respond(db, movements.create(db, user, cmd, body.client_request_id), response)


@router.post(
    "/employees/{employee_id}/advances",
    response_model=s.EmployeeMovementOut,
    status_code=201,
    responses=MOVEMENT_RESPONSES,
)
def create_advance(
    employee_id: int, body: s.AdvanceIn, db: DbSession, user: AreaAdmin, response: Response
) -> s.EmployeeMovementOut:
    cmd = movements.command(
        "ADVANCE",
        employee_id,
        body.business_date,
        body.amount,
        body.description,
        body.reference,
        funding_account_id=body.funding_account_id,
    )
    return _respond(db, movements.create(db, user, cmd, body.client_request_id), response)


@router.post(
    "/employees/{employee_id}/adjustments",
    response_model=s.EmployeeMovementOut,
    status_code=201,
    responses=MOVEMENT_RESPONSES,
)
def create_adjustment(
    employee_id: int, body: s.AdjustmentIn, db: DbSession, user: AreaAdmin, response: Response
) -> s.EmployeeMovementOut:
    cmd = movements.command(
        "ADJUSTMENT",
        employee_id,
        body.business_date,
        body.amount,
        body.description,
        body.reference,
        direction=body.direction,
    )
    return _respond(db, movements.create(db, user, cmd, body.client_request_id), response)


@router.get("/employee-movements/{movement_id}", response_model=s.EmployeeMovementOut)
def get_movement(movement_id: int, db: DbSession, _: AreaViewer) -> s.EmployeeMovementOut:
    return views.movement(db, movement_id)


@router.post(
    "/employee-movements/{movement_id}/reverse", response_model=s.EmployeeMovementOut, responses=MOVEMENT_RESPONSES
)
def reverse_movement(
    movement_id: int, body: s.MovementReversalIn, db: DbSession, user: AreaAdmin, response: Response
) -> s.EmployeeMovementOut:
    result = movements.reverse(db, user, movement_id, body.reason, body.client_request_id)
    response.status_code = 200
    return views.movement(db, result.id)


# --- loans, installments and deferrals (R8-3) --------------------------------------------------------

LOAN_RESPONSES: dict[int | str, dict[str, str]] = {
    200: {"description": "Repetición de una solicitud ya registrada: devuelve el préstamo como quedó."},
    201: {"description": "Registrado."},
}


def _loan(db: DbSession, result: loans.Created, response: Response) -> s.EmployeeLoanOut:
    response.status_code = 201 if result.created else 200
    return views.loan(db, result.id)


@router.post("/employees/{employee_id}/loans/preview", response_model=s.LoanPreviewOut)
def preview_loan(employee_id: int, body: s.LoanPreviewIn, db: DbSession, user: AreaAdmin) -> s.LoanPreviewOut:
    """Validate a loan and show its legs and schedule (the default one unless `installments`), without giving it."""
    return views.preview_loan(db, user, employee_id, body)


@router.post(
    "/employees/{employee_id}/loans", response_model=s.EmployeeLoanOut, status_code=201, responses=LOAN_RESPONSES
)
def create_loan(
    employee_id: int, body: s.LoanIn, db: DbSession, user: AreaAdmin, response: Response
) -> s.EmployeeLoanOut:
    cmd = views.loan_command(employee_id, body)
    return _loan(db, loans.create(db, user, cmd, body.client_request_id), response)


@router.get("/employees/{employee_id}/loans", response_model=list[s.EmployeeLoanRow])
def list_loans(employee_id: int, db: DbSession, _: AreaViewer) -> list[s.EmployeeLoanRow]:
    return views.loans(db, employee_id)


@router.get("/loans/{loan_id}", response_model=s.EmployeeLoanOut)
def get_loan(loan_id: int, db: DbSession, _: AreaViewer) -> s.EmployeeLoanOut:
    return views.loan(db, loan_id)


@router.post("/loans/{loan_id}/reverse", response_model=s.EmployeeLoanOut, responses=LOAN_RESPONSES)
def reverse_loan(
    loan_id: int, body: s.MovementReversalIn, db: DbSession, user: AreaAdmin, response: Response
) -> s.EmployeeLoanOut:
    result = loans.reverse(db, user, loan_id, body.reason, body.client_request_id)
    response.status_code = 200
    return views.loan(db, result.id)


@router.post("/loan-installments/{installment_id}/defer", response_model=s.EmployeeLoanOut, responses=LOAN_RESPONSES)
def defer_installment(
    installment_id: int, body: s.LoanDeferralIn, db: DbSession, user: AreaAdmin, response: Response
) -> s.EmployeeLoanOut:
    """Replace a scheduled installment by one or more new ones adding up to its amount. Moves no money."""
    replacements = [loans.Installment(r.due_on, r.amount) for r in body.replacements]
    result = loans.defer(db, user, installment_id, body.reason, replacements, body.client_request_id)
    response.status_code = 200
    return views.loan(db, result.id)
