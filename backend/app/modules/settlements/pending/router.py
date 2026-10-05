"""Pending collections API (R3-1), mounted at /api/v1/settlements.

Reading only: the load is written from its settlement (`/deliveries/{id}/pending-collections`). The list
holds the pendings in effect in Cajas, with the amount of the close in effect; R3-2 adds receipts,
resolutions, filters and alerts.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.modules.settlements.pending import schemas as s
from app.modules.settlements.pending import service, views
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: pendientes de cobro"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]


@router.get("/pending-collections", response_model=s.PendingPage, summary="Pendientes vigentes en Cajas")
def list_pending_collections(
    db: DbSession,
    _: Viewer,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.PendingPage:
    rows, total = service.in_effect_page(db, limit=limit, offset=offset)
    return s.PendingPage(items=views.items(db, rows), total=total)


@router.get(
    "/pending-collections/{pending_id}",
    response_model=s.PendingDetailOut,
    summary="Pendiente: carga, importe vigente, lo publicado por cada cierre e historial",
)
def get_pending_collection(pending_id: int, db: DbSession, _: Viewer) -> s.PendingDetailOut:
    return views.detail(db, pending_id)
