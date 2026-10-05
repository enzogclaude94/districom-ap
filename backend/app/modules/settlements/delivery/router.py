"""Delivery settlements API (R2-1, R2-2), mounted at /api/v1/settlements.

Roles: viewer reads; operator creates settlements, loads them (count, additions, transfers and
their verification, expenses) and closes them, allocating the difference to employees; admin also
assigns a difference to the company, reopens a closed settlement, corrects a transfer decision and
edits expense categories and cash denominations. Only the close writes the ledger, through
`ledger.service`. Creations are idempotent by `client_request_id`: 201 when created, 200 with
the same row when the request repeats an earlier one, 409 `idempotency_conflict` when the id was used
for different content. Writes on a CLOSED settlement are 409 `settlement_closed`.
"""

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response

from app.core.clock import business_today
from app.modules.settlements.delivery import additions, closing, denominations, expenses, service, transfers, views
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.denominations import Denomination
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: rendiciones de reparto"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]

CREATED_RESPONSES: dict[int | str, dict[str, str]] = {
    200: {"description": "Repetición de una solicitud ya registrada: devuelve lo creado la primera vez."},
    201: {"description": "Creado."},
}


def _detail(db: DbSession, settlement_id: int, created: service.Created | None, response: Response) -> s.SettlementOut:
    if created is not None:
        response.status_code = 201 if created.created else 200
    return views.settlement(db, settlement_id)


# --- drivers and settlements ----------------------------------------------------------------------


@router.get("/drivers", response_model=list[s.Driver], summary="Repartidores que pueden rendir en una fecha")
def list_drivers(
    db: DbSession, _: Viewer, on: Annotated[date | None, Query(description="Fecha de negocio")] = None
) -> list[s.Driver]:
    return [s.Driver(id=e.id, full_name=e.full_name) for e in service.drivers(db, on or business_today())]


@router.get("/deliveries", response_model=s.SettlementPage)
def list_deliveries(
    db: DbSession,
    _: Viewer,
    date_from: date | None = None,
    date_to: date | None = None,
    employee_id: int | None = None,
    status: Literal["OPEN", "CLOSED"] | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.SettlementPage:
    rows, total = service.list_settlements(
        db,
        date_from=date_from,
        date_to=date_to,
        employee_id=employee_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return s.SettlementPage(items=views.items(db, rows), total=total)


@router.post(
    "/deliveries",
    response_model=s.SettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Nueva rendición de reparto (operator)",
)
def create_delivery(body: s.SettlementIn, db: DbSession, user: Operator, response: Response) -> s.SettlementOut:
    created = service.create(db, user, body)
    return _detail(db, created.id, created, response)


@router.get("/deliveries/{settlement_id}", response_model=s.SettlementOut, summary="Rendición con su tablero")
def get_delivery(settlement_id: int, db: DbSession, _: Viewer) -> s.SettlementOut:
    return views.settlement(db, settlement_id)


@router.patch("/deliveries/{settlement_id}", response_model=s.SettlementOut, summary="Corregir esperado o notas")
def update_delivery(
    settlement_id: int, body: s.SettlementPatch, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    service.update(db, user, settlement_id, body)
    return _detail(db, settlement_id, None, response)


@router.put("/deliveries/{settlement_id}/count", response_model=s.SettlementOut, summary="Conteo de efectivo")
def replace_count(
    settlement_id: int, body: s.CountIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    service.replace_count(db, user, settlement_id, body)
    return _detail(db, settlement_id, None, response)


# --- additions ------------------------------------------------------------------------------------


@router.post(
    "/deliveries/{settlement_id}/additions",
    response_model=s.SettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Agregado de efectivo",
)
def create_addition(
    settlement_id: int, body: s.AdditionIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    created = additions.create(db, user, settlement_id, body)
    return _detail(db, settlement_id, created, response)


@router.post(
    "/deliveries/{settlement_id}/additions/{addition_id}/void",
    response_model=s.SettlementOut,
    summary="Anular un agregado, con motivo",
)
def void_addition(
    settlement_id: int, addition_id: int, body: s.VoidIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    additions.void(db, user, settlement_id, addition_id, body.reason)
    return _detail(db, settlement_id, None, response)


# --- transfers ------------------------------------------------------------------------------------


@router.post(
    "/deliveries/{settlement_id}/transfers",
    response_model=s.SettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Transferencia declarada",
)
def create_transfer(
    settlement_id: int, body: s.TransferIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    created = transfers.create(db, user, settlement_id, body)
    return _detail(db, settlement_id, created, response)


@router.patch(
    "/deliveries/{settlement_id}/transfers/{transfer_id}",
    response_model=s.SettlementOut,
    summary="Corregir los datos de una transferencia declarada",
)
def update_transfer(
    settlement_id: int, transfer_id: int, body: s.TransferPatch, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    transfers.update(db, user, settlement_id, transfer_id, body)
    return _detail(db, settlement_id, None, response)


@router.post(
    "/deliveries/{settlement_id}/transfers/{transfer_id}/status",
    response_model=s.SettlementOut,
    summary="Verificar o rechazar (operator); corregir una decisión (admin, con motivo)",
)
def change_transfer_status(
    settlement_id: int,
    transfer_id: int,
    body: s.TransferStatusIn,
    db: DbSession,
    user: Operator,
    response: Response,
) -> s.SettlementOut:
    transfers.change_status(db, user, settlement_id, transfer_id, body)
    return _detail(db, settlement_id, None, response)


# --- expenses -------------------------------------------------------------------------------------


@router.post(
    "/deliveries/{settlement_id}/expenses",
    response_model=s.SettlementOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Gasto",
)
def create_expense(
    settlement_id: int, body: s.ExpenseIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    created = expenses.create(db, user, settlement_id, body)
    return _detail(db, settlement_id, created, response)


@router.patch(
    "/deliveries/{settlement_id}/expenses/{expense_id}", response_model=s.SettlementOut, summary="Corregir un gasto"
)
def update_expense(
    settlement_id: int, expense_id: int, body: s.ExpensePatch, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    expenses.update(db, user, settlement_id, expense_id, body)
    return _detail(db, settlement_id, None, response)


@router.post(
    "/deliveries/{settlement_id}/expenses/{expense_id}/void",
    response_model=s.SettlementOut,
    summary="Anular un gasto, con motivo",
)
def void_expense(
    settlement_id: int, expense_id: int, body: s.VoidIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    expenses.void(db, user, settlement_id, expense_id, body.reason)
    return _detail(db, settlement_id, None, response)


# --- close and reopen (R2-2) -----------------------------------------------------------------------


@router.get(
    "/deliveries/{settlement_id}/closing-preview",
    response_model=s.ClosingPreviewOut,
    summary="Qué publicaría el cierre ahora y qué lo impide",
)
def closing_preview(settlement_id: int, db: DbSession, _: Viewer) -> s.ClosingPreviewOut:
    return views.closing_preview(db, settlement_id)


@router.post(
    "/deliveries/{settlement_id}/close",
    response_model=s.SettlementOut,
    status_code=201,
    responses={
        200: {"description": "Repetición del mismo cierre: devuelve la rendición ya cerrada."},
        201: {"description": "Rendición cerrada y su cierre publicado en el ledger."},
    },
    summary="Cerrar la rendición (con la asignación de la diferencia)",
)
def close_delivery(
    settlement_id: int, body: s.CloseIn, db: DbSession, user: Operator, response: Response
) -> s.SettlementOut:
    return _detail(db, settlement_id, closing.close(db, user, settlement_id, body), response)


@router.post(
    "/deliveries/{settlement_id}/reopen",
    response_model=s.SettlementOut,
    summary="Reabrir para corregir (no mueve Cajas hasta el nuevo cierre)",
)
def reopen_delivery(settlement_id: int, body: s.ReopenIn, db: DbSession, user: Admin) -> s.SettlementOut:
    closing.reopen(db, user, settlement_id, body)
    return views.settlement(db, settlement_id)


@router.get(
    "/deliveries/{settlement_id}/history",
    response_model=s.HistoryOut,
    summary="Cierres, reaperturas y diferencias de la rendición",
)
def delivery_history(settlement_id: int, db: DbSession, _: Viewer) -> s.HistoryOut:
    return views.history(db, settlement_id)


# --- configuration --------------------------------------------------------------------------------


@router.get("/expense-categories", response_model=list[s.ExpenseCategoryOut])
def list_expense_categories(db: DbSession, _: Viewer, include_inactive: bool = False) -> list[s.ExpenseCategoryOut]:
    return [views.category(c) for c in expenses.categories(db, include_inactive)]


@router.post("/expense-categories", response_model=s.ExpenseCategoryOut, status_code=201)
def create_expense_category(body: s.ExpenseCategoryIn, db: DbSession, user: Admin) -> s.ExpenseCategoryOut:
    return views.category(expenses.create_category(db, user, body))


@router.patch("/expense-categories/{category_id}", response_model=s.ExpenseCategoryOut)
def update_expense_category(
    category_id: int, body: s.ExpenseCategoryPatch, db: DbSession, user: Admin
) -> s.ExpenseCategoryOut:
    return views.category(expenses.update_category(db, user, category_id, body))


@router.get("/cash-denominations", response_model=list[s.DenominationOut])
def list_cash_denominations(db: DbSession, _: Viewer, include_inactive: bool = False) -> list[s.DenominationOut]:
    return views.denomination_list(db, include_inactive)


@router.put("/cash-denominations", response_model=list[s.DenominationOut], summary="Configurar denominaciones")
def update_cash_denominations(body: s.DenominationsIn, db: DbSession, user: Admin) -> list[s.DenominationOut]:
    items = [
        Denomination(
            code=d.code.strip(),
            value=d.value,
            active=d.active,
            suggested_destination=(d.suggested_destination or "").strip() or None,
        )
        for d in body.denominations
    ]
    denominations.update(db, user, items)
    service.commit(db)
    return views.denomination_list(db, include_inactive=True)
