"""Maestros reads under /api/v1/commercial (F1.7). Only what the existing endpoints did not cover: a customer's codes
and assignment history, and the sellers. `viewer` reads; nothing here writes (customers, products, zones, lists and
the rest are edited through their own existing endpoints)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.commercial.masters import customers, salespeople
from app.modules.commercial.masters.schemas import CustomerHistoryOut, SalespeopleOut
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User
from app.platform.customers import service as platform_customers

router = APIRouter()

Viewer = Annotated[User, Depends(require_module_access("commercial", "viewer"))]


@router.get(
    "/customers/{customer_id}/history",
    response_model=CustomerHistoryOut,
    summary="Códigos e historia de zona y vendedor de un cliente",
)
def customer_history(db: DbSession, _: Viewer, customer_id: int) -> CustomerHistoryOut:
    platform_customers.get(db, customer_id)  # 404 for a customer that does not exist
    return customers.history(db, customer_id)


@router.get("/salespeople", response_model=SalespeopleOut, summary="Vendedores y preventistas con sus códigos")
def list_salespeople(db: DbSession, _: Viewer) -> SalespeopleOut:
    return salespeople.overview(db)
