"""Router of Rendiciones, mounted at /api/v1/settlements.

Every endpoint added here must depend on `require_module_access("settlements", ...)`. Cajas (R1)
lives in `cash/router.py`, delivery settlements (R2) in `delivery/router.py` and pending collections
(R3) in `pending/router.py`, payments to third parties (R6) in `payments/router.py`; later phases add
their own routers (ROADMAP.md).
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.modules.settlements.cash.router import router as cash_router
from app.modules.settlements.cheques.router import router as cheques_router
from app.modules.settlements.delivery.router import router as delivery_router
from app.modules.settlements.expenses.router import router as expenses_router
from app.modules.settlements.fuel_history.router import router as fuel_history_router
from app.modules.settlements.payments.router import router as payments_router
from app.modules.settlements.pending.router import router as pending_router
from app.platform.auth.access import module_role, require_module_access
from app.platform.auth.models import User

router = APIRouter(tags=["settlements"])

ModuleViewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]


class ModuleStatusOut(BaseModel):
    module: str
    role: str


@router.get("/status", response_model=ModuleStatusOut)
def status(user: ModuleViewer) -> ModuleStatusOut:
    """The caller's role in Rendiciones; 403 without access."""
    role = module_role(user, "settlements")
    assert role is not None  # guaranteed by require_module_access
    return ModuleStatusOut(module="settlements", role=role)


router.include_router(cash_router)
router.include_router(delivery_router)
router.include_router(pending_router)
router.include_router(cheques_router)
router.include_router(expenses_router)
router.include_router(fuel_history_router)
router.include_router(payments_router)
