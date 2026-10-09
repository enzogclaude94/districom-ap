"""Payments API (R6-1), mounted at /api/v1/settlements. Roles: viewer reads every payment; operator records
a payment of merchandise, an expense or a refund, and creates a third party quickly while doing it; admin
also records OTHER, reverses a payment and corrects its descriptive data. The ledger service checks the
roles again. The Terceros master itself (correcting, deactivating) belongs to platform admins
(`/api/v1/third-parties`).

Creation and reversal are idempotent by `client_request_id`: 201 when created, 200 with the same payment
when the request repeats an earlier one, 409 `idempotency_conflict` when the id was used for different
content.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response

from app.modules.settlements.payments import schemas as s
from app.modules.settlements.payments import service, views
from app.platform.auth.access import require_module_access
from app.platform.auth.deps import DbSession
from app.platform.auth.models import User
from app.platform.third_parties import router as third_party_views
from app.platform.third_parties import service as third_parties
from app.platform.third_parties.schemas import ThirdPartyIn, ThirdPartyOut

router = APIRouter(tags=["settlements: pagos"])

Viewer = Annotated[User, Depends(require_module_access("settlements", "viewer"))]
Operator = Annotated[User, Depends(require_module_access("settlements", "operator"))]
Admin = Annotated[User, Depends(require_module_access("settlements", "admin"))]

# Recorded in the third party's history as where it was created.
QUICK_CREATE_ORIGIN = "settlements.payment"


@router.get("/payments", response_model=s.PaymentPage, summary="Pagos a terceros")
def list_payments(db: DbSession, _: Viewer, q: Annotated[s.PaymentListQuery, Query()]) -> s.PaymentPage:
    return views.list_payments(db, q)


@router.get("/payments/{payment_id}", response_model=s.PaymentDetail, summary="Un pago con su operación e historia")
def get_payment(payment_id: int, db: DbSession, _: Viewer) -> s.PaymentDetail:
    return views.detail(db, payment_id)


@router.post("/payments", response_model=s.PaymentDetail, status_code=201, summary="Registrar un pago a un tercero")
def create_payment(body: s.PaymentIn, db: DbSession, user: Operator, response: Response) -> s.PaymentDetail:
    created = service.create(db, user, body)
    response.status_code = 201 if created.created else 200
    return views.detail(db, created.id)


@router.post(
    "/payments/{payment_id}/reverse",
    response_model=s.PaymentDetail,
    status_code=201,
    summary="Revertir un pago completo, con motivo (admin)",
)
def reverse_payment(
    payment_id: int, body: s.PaymentReversalIn, db: DbSession, user: Admin, response: Response
) -> s.PaymentDetail:
    created = service.reverse(db, user, payment_id, body)
    response.status_code = 201 if created.created else 200
    return views.detail(db, payment_id)


@router.post(
    "/payments/{payment_id}/descriptive",
    response_model=s.PaymentDetail,
    summary="Corregir referencia o descripción de un pago, con motivo (admin)",
)
def correct_payment(payment_id: int, body: s.PaymentCorrectionIn, db: DbSession, user: Admin) -> s.PaymentDetail:
    service.correct(db, user, payment_id, body)
    return views.detail(db, payment_id)


@router.post(
    "/third-parties",
    response_model=ThirdPartyOut,
    status_code=201,
    summary="Alta rápida de un tercero durante un pago (operator)",
)
def quick_create_third_party(body: ThirdPartyIn, db: DbSession, user: Operator) -> ThirdPartyOut:
    return third_party_views.one(db, third_parties.create(db, user, body, origin=QUICK_CREATE_ORIGIN))
