"""Rendiciones > Integración Store Sys (R9-1, ADR 011), mounted at /api/v1/settlements.

The protected area `settlements.store_sys_import` (`store_sys/access.py`): without it, 404. The import receives
records of the canonical contract only; a file, a folder or a connection to Store Sys is never read here (the adapter
is a later phase and calls the same service).
"""

from typing import Annotated

from fastapi import APIRouter, Query

from app.modules.settlements.expected import schemas as es
from app.modules.settlements.expected import service as expected
from app.modules.settlements.store_sys import schemas as s
from app.modules.settlements.store_sys import service
from app.modules.settlements.store_sys.access import AreaAdmin, AreaViewer, Importer
from app.platform.auth.deps import DbSession

router = APIRouter(tags=["settlements: integración Store Sys"])


@router.post(
    "/imports/store-sys/expected-amounts",
    response_model=es.StoreSysImportOut,
    summary="Recibir importes esperados de Store Sys (contrato canónico, 1 a 200 registros)",
)
def import_expected_amounts(body: es.StoreSysImportIn, db: DbSession, user: Importer) -> es.StoreSysImportOut:
    return expected.import_records(db, user, body)


@router.get("/store-sys/employee-codes", response_model=s.EmployeeCodePage, summary="Códigos Store Sys de empleados")
def list_employee_codes(
    db: DbSession,
    _: AreaViewer,
    q: Annotated[str | None, Query(max_length=100)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.EmployeeCodePage:
    return service.list_codes(db, q, limit, offset)


@router.get("/store-sys/employee-codes/{employee_id}", response_model=s.EmployeeCodeOut, summary="Código Store Sys")
def get_employee_code(employee_id: int, db: DbSession, _: AreaViewer) -> s.EmployeeCodeOut:
    return service.get_code(db, employee_id)


@router.put(
    "/store-sys/employee-codes/{employee_id}",
    response_model=s.EmployeeCodeOut,
    summary="Cargar, corregir o quitar el código Store Sys de un empleado",
)
def set_employee_code(employee_id: int, body: s.EmployeeCodeIn, db: DbSession, user: AreaAdmin) -> s.EmployeeCodeOut:
    return service.set_code(db, user, employee_id, body)


@router.get(
    "/store-sys/employee-codes/{employee_id}/history",
    response_model=list[s.EmployeeCodeChangeOut],
    summary="Historia del código Store Sys de un empleado",
)
def employee_code_history(employee_id: int, db: DbSession, _: AreaViewer) -> list[s.EmployeeCodeChangeOut]:
    return service.history(db, employee_id)
