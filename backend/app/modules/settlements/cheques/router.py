"""Cheques API (R4-1), mounted at /api/v1/settlements.

The load is written from its settlement (`/deliveries/{id}/cheques`, operator, settlement OPEN) and every
write there answers with the settlement, like the rest of its load. Here too: viewer reads the cheques,
each one's history and the portfolio summary; admin corrects descriptive data and registers the cheques
that enter the portfolio outside a settlement (opening balance, partner contribution). Creations are
idempotent by `client_request_id` (201 created, 200 repeated, 409 `idempotency_conflict`).
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.cheques import service, views
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery import views as delivery_views
from app.modules.settlements.delivery.service import Created
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: cheques"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]

CREATED_RESPONSES: dict[int | str, dict[str, str]] = {
    200: {"description": "Repetición de una solicitud ya registrada: devuelve lo registrado la primera vez."},
    201: {"description": "Registrado."},
}


def _settlement(db: DbSession, settlement_id: int, created: Created | None, response: Response) -> s.SettlementOut:
    if created is not None:
        response.status_code = 201 if created.created else 200
    return delivery_views.settlement(db, settlement_id)


def _cheque(db: DbSession, cheque_id: int, created: Created | None, response: Response) -> cs.ChequeDetailOut:
    if created is not None:
        response.status_code = 201 if created.created else 200
    return views.detail(db, cheque_id)


# --- load in a settlement --------------------------------------------------------------------------


@router.post(
    "/deliveries/{settlement_id}/cheques",
    response_model=s.SettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Cheque recibido en la rendición (físico en mano, o ECHEQ declarado)",
)
def create_cheque(
    settlement_id: int, body: cs.ChequeIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    return _settlement(db, settlement_id, service.create(db, user, settlement_id, body), response)


@router.patch(
    "/deliveries/{settlement_id}/cheques/{cheque_id}",
    response_model=s.SettlementOut,
    summary="Corregir un cheque que todavía no tuvo efecto financiero",
)
def update_cheque(
    settlement_id: int, cheque_id: int, body: cs.ChequePatch, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    service.update(db, user, settlement_id, cheque_id, body)
    return _settlement(db, settlement_id, None, response)


@router.post(
    "/deliveries/{settlement_id}/cheques/{cheque_id}/void",
    response_model=s.SettlementOut,
    summary="Anular un cheque, con motivo (si está en cartera, sigue ahí hasta el nuevo cierre)",
)
def void_cheque(
    settlement_id: int, cheque_id: int, body: s.VoidIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    service.void(db, user, settlement_id, cheque_id, body.reason)
    return _settlement(db, settlement_id, None, response)


@router.post(
    "/deliveries/{settlement_id}/cheques/{cheque_id}/verification",
    response_model=s.SettlementOut,
    summary="Verificar un ECHEQ o rechazar su verificación (corregir una decisión: admin, con motivo)",
)
def decide_echeq_verification(
    settlement_id: int,
    cheque_id: int,
    body: cs.VerificationIn,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.SettlementOut:
    service.decide_verification(db, user, settlement_id, cheque_id, body)
    return _settlement(db, settlement_id, None, response)


# --- cheques ---------------------------------------------------------------------------------------


@router.get("/cheques", response_model=cs.ChequePage, summary="Cheques (los más recientes primero)")
def list_cheques(
    db: DbSession,
    _: Viewer,
    settlement_id: int | None = None,
    kind: Literal["PHYSICAL", "ECHEQ"] | None = None,
    origin_type: Literal["SETTLEMENT", "OPENING_BALANCE", "OWNER_CONTRIBUTION"] | None = None,
    in_portfolio: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> cs.ChequePage:
    filters = views.Filters(settlement_id=settlement_id, kind=kind, origin_type=origin_type, in_portfolio=in_portfolio)
    return views.page(db, filters, limit=limit, offset=offset)


@router.get(
    "/cheques/summary",
    response_model=cs.PortfolioSummaryOut,
    summary="Valores: cartera de cheques y cheques depositados (no son dinero disponible)",
)
def cheques_summary(db: DbSession, _: Viewer) -> cs.PortfolioSummaryOut:
    return views.summary(db)


@router.post(
    "/cheques/opening-balance",
    response_model=cs.ChequeDetailOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Saldo inicial: cheque que ya estaba en la empresa al empezar (admin)",
)
def cheque_opening_balance(
    body: cs.AdministrativeEntryIn, db: DbSession, user: Admin, response: Response
) -> cs.ChequeDetailOut:
    created = service.opening_balance(db, user, body)
    return _cheque(db, created.id, created, response)


@router.post(
    "/cheques/owner-contribution",
    response_model=cs.ChequeDetailOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Aporte de socio con un cheque (admin)",
)
def cheque_owner_contribution(
    body: cs.OwnerContributionIn, db: DbSession, user: Admin, response: Response
) -> cs.ChequeDetailOut:
    created = service.owner_contribution(db, user, body)
    return _cheque(db, created.id, created, response)


@router.get("/cheques/{cheque_id}", response_model=cs.ChequeDetailOut, summary="Cheque, con su historial")
def get_cheque(cheque_id: int, db: DbSession, _: Viewer) -> cs.ChequeDetailOut:
    return views.detail(db, cheque_id)


@router.post(
    "/cheques/{cheque_id}/descriptive",
    response_model=cs.ChequeDetailOut,
    summary="Corregir datos descriptivos (titular/librador, notas), con motivo (admin)",
)
def correct_cheque_descriptive(
    cheque_id: int, body: cs.DescriptiveIn, db: DbSession, user: Admin, response: Response
) -> cs.ChequeDetailOut:
    service.correct_descriptive(db, user, cheque_id, body)
    return _cheque(db, cheque_id, None, response)
