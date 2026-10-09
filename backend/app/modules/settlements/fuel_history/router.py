"""Fuel history API (R5-4), mounted at /api/v1/settlements. Read-only; every settlements role reads it."""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.settlements.fuel_history import schemas as s
from app.modules.settlements.fuel_history import views
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: combustible"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]


@router.get(
    "/fuel-loads",
    response_model=s.FuelLoadPage,
    summary="Historial de combustible: una fila por carga y el resumen del combustible vigente",
)
def list_fuel_loads(db: DbSession, _: Viewer, q: Annotated[s.FuelLoadQuery, Query()]) -> s.FuelLoadPage:
    return views.list_loads(db, q)


@router.get(
    "/fuel-loads/employees",
    response_model=list[s.FuelEmployee],
    summary="Empleados que cargaron combustible, activos o no",
)
def fuel_employees(db: DbSession, _: Viewer) -> list[s.FuelEmployee]:
    return views.employees(db)
