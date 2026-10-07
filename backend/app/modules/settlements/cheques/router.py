"""Cheques API (R4-1, R4-2), mounted at /api/v1/settlements.

The load is written from its settlement (`/deliveries/{id}/cheques`, operator, settlement OPEN) and every
write there answers with the settlement, like the rest of its load. Here too: viewer reads the cheques,
each one's history and the portfolio summary; admin corrects descriptive data and registers the cheques
that enter the portfolio outside a settlement (opening balance, partner contribution). Creations are
idempotent by `client_request_id` (201 created, 200 repeated, 409 `idempotency_conflict`).

R4-2, the financial cycle: operator deposits cheques of the portfolio (one slip, one operation), registers
creditings and rejections, and collects a pending with a cheque; admin returns a cheque from the portfolio
and reverses any document of the cycle (with a reason, on the original's date). Each answers with its
document.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.modules.settlements.cheques import collections, cycle, service, views
from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery import views as delivery_views
from app.modules.settlements.delivery.service import Created
from app.modules.settlements.pending import views as pending_views
from app.modules.settlements.pending.schemas import PendingDetailOut
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
    origin_type: Literal["SETTLEMENT", "OPENING_BALANCE", "OWNER_CONTRIBUTION", "PENDING_RECEIPT"] | None = None,
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


# --- the financial cycle (R4-2) -------------------------------------------------------------------


def _status(created: Created, response: Response) -> None:
    response.status_code = 201 if created.created else 200


@router.post(
    "/cheque-deposits",
    response_model=cs.DepositOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Depositar cheques de la cartera en un banco (una boleta, una operación)",
)
def deposit_cheques(body: cs.DepositIn, db: DbSession, user: Operator, response: Response) -> cs.DepositOut:
    created = cycle.deposit(db, user, body)
    _status(created, response)
    return views.deposit(db, created.id)


@router.get("/cheque-deposits/{deposit_id}", response_model=cs.DepositOut, summary="Depósito de cheques")
def get_cheque_deposit(deposit_id: int, db: DbSession, _: Viewer) -> cs.DepositOut:
    return views.deposit(db, deposit_id)


@router.post(
    "/cheque-deposits/{deposit_id}/reverse",
    response_model=cs.DepositOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Revertir un depósito: los cheques vuelven a la cartera (admin, con motivo)",
)
def reverse_cheque_deposit(
    deposit_id: int, body: cs.CycleReversalIn, db: DbSession, user: Admin, response: Response
) -> cs.DepositOut:
    _status(cycle.reverse_deposit(db, user, deposit_id, body), response)
    return views.deposit(db, deposit_id)


@router.post(
    "/cheque-credits",
    response_model=cs.CreditOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Acreditación: el banco acreditó cheques depositados (uno o varios)",
)
def credit_cheques(body: cs.CreditIn, db: DbSession, user: Operator, response: Response) -> cs.CreditOut:
    created = cycle.credit(db, user, body)
    _status(created, response)
    return views.credit(db, created.id)


@router.get("/cheque-credits/{credit_id}", response_model=cs.CreditOut, summary="Acreditación de cheques")
def get_cheque_credit(credit_id: int, db: DbSession, _: Viewer) -> cs.CreditOut:
    return views.credit(db, credit_id)


@router.post(
    "/cheque-credits/{credit_id}/reverse",
    response_model=cs.CreditOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Revertir una acreditación: los cheques vuelven a depositados (admin, con motivo)",
)
def reverse_cheque_credit(
    credit_id: int, body: cs.CycleReversalIn, db: DbSession, user: Admin, response: Response
) -> cs.CreditOut:
    _status(cycle.reverse_credit(db, user, credit_id, body), response)
    return views.credit(db, credit_id)


@router.post(
    "/cheques/{cheque_id}/rejection",
    response_model=cs.RejectionOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Rechazo del banco (en tránsito o ya acreditado): genera un pendiente nuevo",
)
def reject_cheque(
    cheque_id: int, body: cs.RejectionIn, db: DbSession, user: Operator, response: Response
) -> cs.RejectionOut:
    created = cycle.reject(db, user, cheque_id, body)
    _status(created, response)
    return views.rejection(db, created.id)


@router.get("/cheque-rejections/{rejection_id}", response_model=cs.RejectionOut, summary="Rechazo de un cheque")
def get_cheque_rejection(rejection_id: int, db: DbSession, _: Viewer) -> cs.RejectionOut:
    return views.rejection(db, rejection_id)


@router.post(
    "/cheque-rejections/{rejection_id}/reverse",
    response_model=cs.RejectionOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Revertir un rechazo: anula el pendiente que generó (admin, con motivo)",
)
def reverse_cheque_rejection(
    rejection_id: int, body: cs.CycleReversalIn, db: DbSession, user: Admin, response: Response
) -> cs.RejectionOut:
    _status(cycle.reverse_rejection(db, user, rejection_id, body), response)
    return views.rejection(db, rejection_id)


@router.post(
    "/cheques/{cheque_id}/return",
    response_model=cs.ReturnOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Devolver un cheque de la cartera a quien lo entregó: genera un pendiente nuevo (admin, con motivo)",
)
def return_cheque(cheque_id: int, body: cs.ReturnIn, db: DbSession, user: Admin, response: Response) -> cs.ReturnOut:
    created = cycle.give_back(db, user, cheque_id, body)
    _status(created, response)
    return views.given_back(db, created.id)


@router.get("/cheque-returns/{return_id}", response_model=cs.ReturnOut, summary="Devolución de un cheque")
def get_cheque_return(return_id: int, db: DbSession, _: Viewer) -> cs.ReturnOut:
    return views.given_back(db, return_id)


@router.post(
    "/cheque-returns/{return_id}/reverse",
    response_model=cs.ReturnOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Revertir una devolución: el cheque vuelve a la cartera y se anula el pendiente (admin, con motivo)",
)
def reverse_cheque_return(
    return_id: int, body: cs.CycleReversalIn, db: DbSession, user: Admin, response: Response
) -> cs.ReturnOut:
    _status(cycle.reverse_return(db, user, return_id, body), response)
    return views.given_back(db, return_id)


# --- a pending collected with a cheque (R4-2) -----------------------------------------------------


@router.post(
    "/pending-collections/{pending_id}/cheque-receipts",
    response_model=PendingDetailOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Cobrar un pendiente con un cheque (físico: entra a cartera; ECHEQ: queda declarado)",
)
def receive_cheque_for_pending(
    pending_id: int, body: cs.ChequeReceiptIn, db: DbSession, user: Operator, response: Response
) -> PendingDetailOut:
    _status(collections.receive(db, user, pending_id, body), response)
    return pending_views.detail(db, pending_id)


@router.post(
    "/pending-collections/{pending_id}/cheque-receipts/{cheque_id}/verification",
    response_model=PendingDetailOut,
    summary="Verificar el ECHEQ declarado para un pendiente (lo cobra) o rechazar su verificación",
)
def decide_pending_echeq(
    pending_id: int, cheque_id: int, body: cs.PendingEcheqVerificationIn, db: DbSession, user: Operator
) -> PendingDetailOut:
    collections.decide_echeq(db, user, pending_id, cheque_id, body)
    return pending_views.detail(db, pending_id)
