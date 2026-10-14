"""Boca de expendio settlements API (R7-2), mounted at /api/v1/settlements.

The same roles and the same loads as a delivery settlement (`delivery/router.py`), on the same services:
viewer reads; operator opens a settlement, loads it (count, additions, transfers and their verification,
expenses, cheques, remittances to a cash box) and closes it, allocating the difference to employees; admin also
assigns a difference to the company, reopens a closed settlement, reverses a remittance and records one while the
settlement is being corrected (R7-3). There are no pending collections and no fuel. Each path
reaches only OUTLET settlements (`routing.OutletSettlementId`). Creations are idempotent by
`client_request_id`: 201 when created, 200 for a repeated request.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.modules.settlements.cheques import schemas as cs
from app.modules.settlements.cheques import service as cheques
from app.modules.settlements.delivery import additions, closing, expenses, service, transfers
from app.modules.settlements.delivery import schemas as ds
from app.modules.settlements.delivery import views as delivery_views
from app.modules.settlements.delivery.routing import OutletSettlementId
from app.modules.settlements.outlet_settlements import remittances, views
from app.modules.settlements.outlet_settlements import schemas as s
from app.modules.settlements.outlet_settlements import service as outlet_settlements
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: rendiciones de boca de expendio"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]

CREATED_RESPONSES: dict[int | str, dict[str, str]] = {
    200: {"description": "Repetición de una solicitud ya registrada: devuelve lo creado la primera vez."},
    201: {"description": "Creado."},
}
PATH = "/outlet-settlements/{settlement_id}"


def _detail(
    db: DbSession, settlement_id: int, created: service.Created | None, response: Response
) -> s.OutletSettlementOut:
    if created is not None:
        response.status_code = 201 if created.created else 200
    return views.settlement(db, settlement_id)


# --- settlements ----------------------------------------------------------------------------------


@router.get("/outlet-settlements", response_model=s.OutletSettlementPage, summary="Rendiciones de boca")
def list_outlet_settlements(
    db: DbSession,
    _: Viewer,
    date_from: date | None = None,
    date_to: date | None = None,
    outlet_id: int | None = None,
    status: Literal["OPEN", "CLOSED"] | None = None,
    financial_state: ds.FinancialState | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.OutletSettlementPage:
    rows, total = outlet_settlements.list_settlements(
        db,
        date_from=date_from,
        date_to=date_to,
        outlet_id=outlet_id,
        status=status,
        financial_state=financial_state,
        limit=limit,
        offset=offset,
    )
    return s.OutletSettlementPage(items=views.items(db, rows), total=total)


@router.post(
    "/outlet-settlements",
    response_model=s.OutletSettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Nueva rendición de boca (operator; la Rendición 2 del día requiere confirmación)",
)
def create_outlet_settlement(
    body: s.OutletSettlementIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    created = outlet_settlements.create(db, user, body)
    return _detail(db, created.id, created, response)


@router.get(
    "/outlets/{outlet_id}/settlements-summary",
    response_model=s.OutletSettlementsSummary,
    summary="Rendiciones abiertas de una boca (para su ficha)",
)
def outlet_settlements_summary(outlet_id: int, db: DbSession, _: Viewer) -> s.OutletSettlementsSummary:
    return s.OutletSettlementsSummary(outlet_id=outlet_id, open_count=outlet_settlements.open_count(db, outlet_id))


@router.get(PATH, response_model=s.OutletSettlementOut, summary="Rendición de boca con su tablero")
def get_outlet_settlement(settlement_id: OutletSettlementId, db: DbSession, _: Viewer) -> s.OutletSettlementOut:
    return views.settlement(db, settlement_id)


@router.patch(
    PATH, response_model=s.OutletSettlementOut, summary="Cargar o corregir el esperado, la referencia o notas"
)
def update_outlet_settlement(
    settlement_id: OutletSettlementId, body: s.OutletSettlementPatch, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    service.update(db, user, settlement_id, body)
    return _detail(db, settlement_id, None, response)


@router.put(f"{PATH}/count", response_model=s.OutletSettlementOut, summary="Conteo final de efectivo")
def replace_outlet_count(
    settlement_id: OutletSettlementId, body: ds.CountIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    service.replace_count(db, user, settlement_id, body)
    return _detail(db, settlement_id, None, response)


# --- additions ------------------------------------------------------------------------------------


@router.post(
    f"{PATH}/additions",
    response_model=s.OutletSettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Agregado de efectivo",
)
def create_outlet_addition(
    settlement_id: OutletSettlementId, body: ds.AdditionIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    return _detail(db, settlement_id, additions.create(db, user, settlement_id, body), response)


@router.post(
    f"{PATH}/additions/{{addition_id}}/void", response_model=s.OutletSettlementOut, summary="Anular un agregado"
)
def void_outlet_addition(
    settlement_id: OutletSettlementId,
    addition_id: int,
    body: ds.VoidIn,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    additions.void(db, user, settlement_id, addition_id, body.reason)
    return _detail(db, settlement_id, None, response)


# --- transfers ------------------------------------------------------------------------------------


@router.post(
    f"{PATH}/transfers",
    response_model=s.OutletSettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Transferencia declarada",
)
def create_outlet_transfer(
    settlement_id: OutletSettlementId, body: ds.TransferIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    return _detail(db, settlement_id, transfers.create(db, user, settlement_id, body), response)


@router.patch(
    f"{PATH}/transfers/{{transfer_id}}",
    response_model=s.OutletSettlementOut,
    summary="Corregir los datos de una transferencia declarada",
)
def update_outlet_transfer(
    settlement_id: OutletSettlementId,
    transfer_id: int,
    body: ds.TransferPatch,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    transfers.update(db, user, settlement_id, transfer_id, body)
    return _detail(db, settlement_id, None, response)


@router.post(
    f"{PATH}/transfers/{{transfer_id}}/status",
    response_model=s.OutletSettlementOut,
    summary="Verificar o rechazar (operator); corregir una decisión (admin, con motivo)",
)
def change_outlet_transfer_status(
    settlement_id: OutletSettlementId,
    transfer_id: int,
    body: ds.TransferStatusIn,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    transfers.change_status(db, user, settlement_id, transfer_id, body)
    return _detail(db, settlement_id, None, response)


# --- expenses -------------------------------------------------------------------------------------


@router.post(
    f"{PATH}/expenses",
    response_model=s.OutletSettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Gasto (sin combustible)",
)
def create_outlet_expense(
    settlement_id: OutletSettlementId, body: ds.ExpenseIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    return _detail(db, settlement_id, expenses.create(db, user, settlement_id, body), response)


@router.patch(f"{PATH}/expenses/{{expense_id}}", response_model=s.OutletSettlementOut, summary="Corregir un gasto")
def update_outlet_expense(
    settlement_id: OutletSettlementId,
    expense_id: int,
    body: ds.ExpensePatch,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    expenses.update(db, user, settlement_id, expense_id, body)
    return _detail(db, settlement_id, None, response)


@router.post(f"{PATH}/expenses/{{expense_id}}/void", response_model=s.OutletSettlementOut, summary="Anular un gasto")
def void_outlet_expense(
    settlement_id: OutletSettlementId,
    expense_id: int,
    body: ds.VoidIn,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    expenses.void(db, user, settlement_id, expense_id, body.reason)
    return _detail(db, settlement_id, None, response)


# --- cheques (R4: they enter «Cartera de cheques» only with the close) ---------------------------


@router.post(
    f"{PATH}/cheques",
    response_model=s.OutletSettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Cheque recibido en la boca (físico en mano, o ECHEQ declarado)",
)
def create_outlet_cheque(
    settlement_id: OutletSettlementId, body: cs.ChequeIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    return _detail(db, settlement_id, cheques.create(db, user, settlement_id, body), response)


@router.patch(
    f"{PATH}/cheques/{{cheque_id}}",
    response_model=s.OutletSettlementOut,
    summary="Corregir un cheque que todavía no tuvo efecto financiero",
)
def update_outlet_cheque(
    settlement_id: OutletSettlementId,
    cheque_id: int,
    body: cs.ChequePatch,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    cheques.update(db, user, settlement_id, cheque_id, body)
    return _detail(db, settlement_id, None, response)


@router.post(
    f"{PATH}/cheques/{{cheque_id}}/void",
    response_model=s.OutletSettlementOut,
    summary="Anular un cheque (en una corrección, sale de cartera con el nuevo cierre)",
)
def void_outlet_cheque(
    settlement_id: OutletSettlementId,
    cheque_id: int,
    body: ds.VoidIn,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    cheques.void(db, user, settlement_id, cheque_id, body.reason)
    return _detail(db, settlement_id, None, response)


@router.post(
    f"{PATH}/cheques/{{cheque_id}}/verification",
    response_model=s.OutletSettlementOut,
    summary="Verificar un ECHEQ o rechazar su verificación",
)
def decide_outlet_echeq_verification(
    settlement_id: OutletSettlementId,
    cheque_id: int,
    body: cs.VerificationIn,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.OutletSettlementOut:
    cheques.decide_verification(db, user, settlement_id, cheque_id, body)
    return _detail(db, settlement_id, None, response)


# --- remittances (R7-3) ---------------------------------------------------------------------------


@router.post(
    f"{PATH}/remittances/preview",
    response_model=s.RemittancePreviewOut,
    summary="Qué publicaría «Entregar efectivo» (para confirmar antes de registrarla)",
)
def preview_outlet_remittance(
    settlement_id: OutletSettlementId, body: s.RemittancePreviewIn, db: DbSession, user: Operator
) -> s.RemittancePreviewOut:
    settlement = service.get(db, settlement_id)
    plan = remittances.plan(db, user, settlement, body)
    assert settlement.outlet_id is not None
    return views.remittance_preview(plan, views.advances_account(db, settlement.outlet_id))


@router.post(
    f"{PATH}/remittances",
    response_model=s.OutletSettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Entregar efectivo a una caja (operator; en corrección, solo admin)",
)
def create_outlet_remittance(
    settlement_id: OutletSettlementId, body: s.RemittanceIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    return _detail(db, settlement_id, remittances.create(db, user, settlement_id, body), response)


@router.post(
    f"{PATH}/remittances/{{remittance_id}}/reverse",
    response_model=s.OutletSettlementOut,
    summary="Revertir una entrega, con motivo (admin)",
)
def reverse_outlet_remittance(
    settlement_id: OutletSettlementId,
    remittance_id: int,
    body: s.RemittanceReversalIn,
    db: DbSession,
    user: Admin,
) -> s.OutletSettlementOut:
    remittances.reverse(db, user, settlement_id, remittance_id, body)
    return views.settlement(db, settlement_id)


# --- close and reopen -----------------------------------------------------------------------------


@router.get(
    f"{PATH}/closing-preview",
    response_model=s.OutletClosingPreviewOut,
    summary="Qué publicaría el cierre ahora, qué lo impide y los responsables del día",
)
def outlet_closing_preview(settlement_id: OutletSettlementId, db: DbSession, _: Viewer) -> s.OutletClosingPreviewOut:
    return views.closing_preview(db, settlement_id)


@router.post(
    f"{PATH}/close",
    response_model=s.OutletSettlementOut,
    status_code=201,
    responses={
        200: {"description": "Repetición del mismo cierre: devuelve la rendición ya cerrada."},
        201: {"description": "Rendición cerrada (en el ledger, o administrativamente si es de $0 sin movimientos)."},
    },
    summary="Cerrar la rendición de boca (con la asignación de la diferencia)",
)
def close_outlet_settlement(
    settlement_id: OutletSettlementId, body: ds.CloseIn, db: DbSession, user: Operator, response: Response
) -> s.OutletSettlementOut:
    return _detail(db, settlement_id, closing.close(db, user, settlement_id, body), response)


@router.post(
    f"{PATH}/reopen",
    response_model=s.OutletSettlementOut,
    summary="Reabrir para corregir (no mueve Cajas hasta el nuevo cierre)",
)
def reopen_outlet_settlement(
    settlement_id: OutletSettlementId, body: ds.ReopenIn, db: DbSession, user: Admin
) -> s.OutletSettlementOut:
    closing.reopen(db, user, settlement_id, body)
    return views.settlement(db, settlement_id)


@router.get(
    f"{PATH}/history",
    response_model=ds.HistoryOut,
    summary="Cierres (financieros y administrativos), reaperturas y diferencias",
)
def outlet_settlement_history(settlement_id: OutletSettlementId, db: DbSession, _: Viewer) -> ds.HistoryOut:
    return delivery_views.history(db, settlement_id)
