"""Pending collections API (R3-1, R3-2), mounted at /api/v1/settlements.

The load is written from its settlement (`/deliveries/{id}/pending-collections`). Here, once a close put the
pending in effect: viewer reads the global list, its alert summary and each pending; operator records a
receipt; admin resolves, reverses receipts and resolutions and changes the responsible after the close.
Creations are idempotent by `client_request_id` (201 created, 200 repeated, 409 `idempotency_conflict`),
and so are reversals. Every write answers with the pending's detail.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.modules.settlements.delivery.service import Created
from app.modules.settlements.pending import applications, listing, views
from app.modules.settlements.pending import schemas as s
from app.modules.settlements.pending.state import State
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User

router = APIRouter(tags=["settlements: pendientes de cobro"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]

CREATED_RESPONSES: dict[int | str, dict[str, str]] = {
    200: {"description": "Repetición de una solicitud ya registrada: devuelve lo registrado la primera vez."},
    201: {"description": "Registrado."},
}


def _detail(db: DbSession, pending_id: int, created: Created | None, response: Response) -> s.PendingDetailOut:
    if created is not None:
        response.status_code = 201 if created.created else 200
    return views.detail(db, pending_id)


@router.get(
    "/pending-collections", response_model=s.PendingPage, summary="Pendientes de cobro (por defecto, con saldo)"
)
def list_pending_collections(
    db: DbSession,
    _: Viewer,
    state: Annotated[list[State] | None, Query(description="Por defecto OPEN y PARTIAL")] = None,
    customer: Annotated[str | None, Query(max_length=200)] = None,
    driver_id: int | None = None,
    responsible_employee_id: int | None = None,
    settlement_id: int | None = None,
    min_age_hours: Annotated[int | None, Query(ge=0)] = None,
    alert: bool = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> s.PendingPage:
    filters = listing.Filters(
        states=state or listing.OWED_STATES,
        customer=customer.strip() if customer else None,
        driver_id=driver_id,
        responsible_employee_id=responsible_employee_id,
        settlement_id=settlement_id,
        min_age_hours=min_age_hours,
        alert_only=alert,
    )
    rows, total = listing.page(db, filters, limit=limit, offset=offset)
    return s.PendingPage(items=views.list_items(db, rows), total=total)


@router.get(
    "/pending-collections/summary",
    response_model=s.PendingAlertSummary,
    summary="Pendientes en alerta: cantidad y saldo",
)
def pending_collections_summary(db: DbSession, _: Viewer) -> s.PendingAlertSummary:
    hours, count, balance = listing.summary(db)
    return s.PendingAlertSummary(alert_hours=hours, count=count, balance=balance)


@router.get(
    "/pending-collections/{pending_id}",
    response_model=s.PendingDetailOut,
    summary="Pendiente: carga, importe vigente, cobros, resoluciones, lo publicado por cada cierre e historial",
)
def get_pending_collection(pending_id: int, db: DbSession, _: Viewer) -> s.PendingDetailOut:
    return views.detail(db, pending_id)


@router.post(
    "/pending-collections/{pending_id}/receipts",
    response_model=s.PendingDetailOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Registrar un cobro (efectivo a una caja, transferencia a un banco)",
)
def record_receipt(
    pending_id: int, body: s.ReceiptIn, db: DbSession, user: Operator, response: Response
) -> s.PendingDetailOut:
    return _detail(db, pending_id, applications.record_receipt(db, user, pending_id, body), response)


@router.post(
    "/pending-collections/{pending_id}/receipts/{receipt_id}/reverse",
    response_model=s.PendingDetailOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Revertir un cobro mal registrado (con la fecha del cobro original)",
)
def reverse_receipt(
    pending_id: int, receipt_id: int, body: s.DocumentReversalIn, db: DbSession, user: Admin, response: Response
) -> s.PendingDetailOut:
    return _detail(db, pending_id, applications.reverse_receipt(db, user, pending_id, receipt_id, body), response)


@router.post(
    "/pending-collections/{pending_id}/resolutions",
    response_model=s.PendingDetailOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Resolver un pendiente: incobrable de la empresa y/o a cargo de empleados",
)
def resolve(
    pending_id: int, body: s.ResolutionIn, db: DbSession, user: Admin, response: Response
) -> s.PendingDetailOut:
    return _detail(db, pending_id, applications.resolve(db, user, pending_id, body), response)


@router.post(
    "/pending-collections/{pending_id}/resolutions/{resolution_id}/reverse",
    response_model=s.PendingDetailOut,
    status_code=201,
    responses=CREATED_RESPONSES,
    summary="Revertir una resolución (con la fecha de la resolución original)",
)
def reverse_resolution(
    pending_id: int, resolution_id: int, body: s.DocumentReversalIn, db: DbSession, user: Admin, response: Response
) -> s.PendingDetailOut:
    return _detail(db, pending_id, applications.reverse_resolution(db, user, pending_id, resolution_id, body), response)


@router.patch(
    "/pending-collections/{pending_id}/responsible",
    response_model=s.PendingDetailOut,
    summary="Cambiar el responsable de cobrarlo (también con la rendición cerrada)",
)
def change_responsible(
    pending_id: int, body: s.ResponsibleIn, db: DbSession, user: Admin, response: Response
) -> s.PendingDetailOut:
    applications.change_responsible(db, user, pending_id, body)
    return _detail(db, pending_id, None, response)
