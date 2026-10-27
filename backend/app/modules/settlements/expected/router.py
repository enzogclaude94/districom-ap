"""The expected amount of a settlement (R9-1), mounted at /api/v1/settlements: its history and the decision on a
pending Store Sys update. Roles of Rendiciones: viewer reads; operator accepts or keeps on a settlement never closed;
an admin does it on one being corrected (checked by the service)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.modules.settlements.delivery import service as settlements
from app.modules.settlements.expected import schemas as s
from app.modules.settlements.expected import service
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: importe esperado"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]


@router.get(
    "/settlements/{settlement_id}/expected-history",
    response_model=s.ExpectedHistoryOut,
    summary="Historia del importe esperado de una rendición (reparto o boca)",
)
def expected_history(settlement_id: int, db: DbSession, _: Viewer) -> s.ExpectedHistoryOut:
    return service.history(db, settlements.get(db, settlement_id))


@router.post(
    "/expected-updates/{event_id}/accept",
    response_model=s.ExpectedHistoryOut,
    summary="Aceptar el esperado informado por Store Sys",
)
def accept_expected_update(event_id: int, db: DbSession, user: Operator) -> s.ExpectedHistoryOut:
    settlement_id = service.accept(db, user, event_id)
    return service.history(db, settlements.get(db, settlement_id))


@router.post(
    "/expected-updates/{event_id}/keep",
    response_model=s.ExpectedHistoryOut,
    summary="Mantener el esperado cargado (con motivo)",
)
def keep_expected_update(event_id: int, body: s.KeepIn, db: DbSession, user: Operator) -> s.ExpectedHistoryOut:
    settlement_id = service.keep(db, user, event_id, body.reason)
    return service.history(db, settlements.get(db, settlement_id))
