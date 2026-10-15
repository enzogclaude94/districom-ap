"""Rendiciones > Empleados API (R8-1, ADR 010), mounted at /api/v1/settlements/employee-finance.

Every endpoint requires the protected area `settlements.employee_finance` (404 without it, so nothing is
discoverable); a role in Rendiciones or being platform admin is not enough. Reading takes viewer; creating
or ending a salary term takes admin of the area. R8-1 writes nothing to the ledger.
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.settlements.employee_finance import compensation, views
from app.modules.settlements.employee_finance import schemas as s
from app.modules.settlements.employee_finance.access import AreaAdmin, AreaViewer
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
