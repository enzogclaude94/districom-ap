"""Employee administration API (platform admins). Modules use the service, not this API."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.clock import business_today
from app.platform.auth.deps import DbSession, require_role
from app.platform.auth.models import User
from app.platform.parties import service
from app.platform.parties.functions import registry
from app.platform.parties.models import Employee, EmployeeFunction
from app.platform.parties.schemas import (
    DeactivateIn,
    EmployeeFunctionOut,
    EmployeeIn,
    EmployeeOut,
    EmployeePage,
    FunctionAssignIn,
    FunctionCodeOut,
    FunctionEndIn,
)

router = APIRouter(prefix="/employees", tags=["employees"])

PlatformAdmin = Annotated[User, Depends(require_role("admin"))]


def _function_out(row: EmployeeFunction, today: date) -> EmployeeFunctionOut:
    spec = registry.get(row.function_code)
    return EmployeeFunctionOut(
        id=row.id,
        function_code=row.function_code,
        label=spec.label if spec else row.function_code,
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        source=row.source,
        current=row.valid_from <= today and (row.valid_to is None or row.valid_to >= today),
    )


def _employee_out(employee: Employee, functions: list[EmployeeFunction]) -> EmployeeOut:
    today = business_today()
    return EmployeeOut(
        id=employee.id,
        full_name=employee.full_name,
        active=employee.active,
        hire_date=employee.hire_date,
        phone=employee.phone,
        external_axum_id=employee.external_axum_id,
        external_store_id=employee.external_store_id,
        functions=[_function_out(row, today) for row in functions],
    )


def _detail(db: DbSession, employee: Employee) -> EmployeeOut:
    return _employee_out(employee, service.functions_of(db, [employee.id])[employee.id])


@router.get("/function-codes", response_model=list[FunctionCodeOut])
def function_codes(_: PlatformAdmin) -> list[FunctionCodeOut]:
    return [FunctionCodeOut(code=spec.code, label=spec.label) for spec in registry.all()]


@router.get("", response_model=EmployeePage)
def list_employees(
    db: DbSession,
    _: PlatformAdmin,
    active: bool | None = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    function_code: Annotated[str | None, Query(max_length=60)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EmployeePage:
    rows, total = service.list_employees(
        db, active=active, search=q, function_code=function_code, limit=limit, offset=offset
    )
    functions = service.functions_of(db, [row.id for row in rows])
    return EmployeePage(items=[_employee_out(row, functions[row.id]) for row in rows], total=total)


@router.post("", response_model=EmployeeOut, status_code=201)
def create_employee(body: EmployeeIn, db: DbSession, user: PlatformAdmin) -> EmployeeOut:
    return _detail(db, service.create_employee(db, body, user_id=user.id))


@router.get("/{employee_id}", response_model=EmployeeOut)
def get_employee(employee_id: int, db: DbSession, _: PlatformAdmin) -> EmployeeOut:
    return _detail(db, service.get_employee(db, employee_id))


@router.put("/{employee_id}", response_model=EmployeeOut)
def update_employee(employee_id: int, body: EmployeeIn, db: DbSession, user: PlatformAdmin) -> EmployeeOut:
    return _detail(db, service.update_employee(db, employee_id, body, user_id=user.id))


@router.post("/{employee_id}/deactivate", response_model=EmployeeOut)
def deactivate_employee(employee_id: int, body: DeactivateIn, db: DbSession, user: PlatformAdmin) -> EmployeeOut:
    return _detail(db, service.deactivate_employee(db, employee_id, body.end_date, user_id=user.id))


@router.post("/{employee_id}/reactivate", response_model=EmployeeOut)
def reactivate_employee(employee_id: int, db: DbSession, user: PlatformAdmin) -> EmployeeOut:
    return _detail(db, service.reactivate_employee(db, employee_id, user_id=user.id))


@router.post("/{employee_id}/functions", response_model=EmployeeOut, status_code=201)
def assign_function(employee_id: int, body: FunctionAssignIn, db: DbSession, user: PlatformAdmin) -> EmployeeOut:
    service.assign_function(db, employee_id, body.function_code, body.valid_from, user_id=user.id)
    return _detail(db, service.get_employee(db, employee_id))


@router.post("/{employee_id}/functions/{function_id}/end", response_model=EmployeeOut)
def end_function(
    employee_id: int, function_id: int, body: FunctionEndIn, db: DbSession, user: PlatformAdmin
) -> EmployeeOut:
    service.end_function(db, employee_id, function_id, body.valid_to, user_id=user.id)
    return _detail(db, service.get_employee(db, employee_id))
