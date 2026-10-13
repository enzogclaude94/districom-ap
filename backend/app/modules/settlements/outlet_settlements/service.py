"""Boca de expendio settlements (R7-2, ADR 009): opening, listing and the responsables of the day.

Everything after the opening is the settlement domain of `delivery` (count, additions, transfers,
expenses, cheques, close, reopen) under the OUTLET profile: no pending collections, no fuel, the close
against «Cobranzas de boca», and the administrative zero close.

Opening (operator): the boca must be active (the database checks it again, locking the boca); creation is
idempotent by `client_request_id`; the n-th settlement of a boca on a day gets `sequence_number = n` under a
transaction-level advisory lock on (OUTLET, boca, day), and a second one needs `confirm_additional`. A boca
deactivated later keeps its open settlements: they are loaded, closed and corrected without asking again.
"""

from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.delivery import idempotency, service
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.outlet_settlements import schemas as s
from app.modules.settlements.outlets.models import OutletResponsibility
from app.modules.settlements.outlets.service import in_effect_on
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.outlets import service as outlets
from app.platform.outlets.models import Outlet
from app.platform.parties.models import Employee

OUTLET = service.OUTLET


def create(db: Session, actor: User, body: s.OutletSettlementIn) -> service.Created:
    notes = (body.notes or "").strip() or None
    reference = (body.external_reference or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        "settlement",
        {
            "type": OUTLET,
            "business_date": body.business_date.isoformat(),
            "outlet_id": body.outlet_id,
            "expected_amount": service.money(body.expected_amount) if body.expected_amount is not None else None,
            "external_reference": reference,
            "notes": notes,
        },
    )
    # A retry is answered before anything else is validated: it must get the same result as the first time.
    existing = db.scalars(select(Settlement).where(Settlement.client_request_id == body.client_request_id)).first()
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "settlement_id", existing.id)
        return service.Created(existing.id, created=False)

    if body.business_date > business_today():
        raise AppError(422, "future_business_date", "La fecha de la rendición no puede ser futura.")
    outlets.active(db, body.outlet_id)

    lock_key = f"settlements.sequence:{OUTLET}:{body.outlet_id}:{body.business_date.isoformat()}"
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))
    # After the lock: READ COMMITTED sees every settlement committed by whoever held it before.
    last = db.scalar(
        select(func.max(Settlement.sequence_number)).where(
            Settlement.type == OUTLET,
            Settlement.outlet_id == body.outlet_id,
            Settlement.business_date == body.business_date,
        )
    )
    if last and not body.confirm_additional:
        db.rollback()  # release the advisory lock
        raise AppError(
            409,
            "additional_settlement_confirmation_required",
            f"La boca ya tiene {last} rendición(es) ese día: confirmá que querés abrir la Rendición {last + 1}.",
            {"existing": last, "next_sequence_number": last + 1},
        )
    settlement = Settlement(
        type=OUTLET,
        business_date=body.business_date,
        outlet_id=body.outlet_id,
        sequence_number=(last or 0) + 1,
        expected_amount=body.expected_amount,
        expected_source="MANUAL",
        external_reference=reference,
        notes=notes,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(settlement)
            db.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint == "uq_settlements_client_request_id":
            # A concurrent request with the same key committed first.
            winner = db.scalars(select(Settlement).where(Settlement.client_request_id == body.client_request_id)).one()
            db.rollback()  # release the locks taken for the insert that lost
            idempotency.check_replay(winner.request_fingerprint, fingerprint, "settlement_id", winner.id)
            return service.Created(winner.id, created=False)
        raise

    record_change(
        db,
        "settlements.settlement.created",
        action="create",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before=None,
        after=service.snapshot(settlement),
    )
    service.commit(db)
    return service.Created(settlement.id, created=True)


def list_settlements(
    db: Session,
    *,
    date_from: date | None,
    date_to: date | None,
    outlet_id: int | None,
    status: str | None,
    financial_state: str | None,
    limit: int,
    offset: int,
) -> tuple[Sequence[tuple[Settlement, Outlet]], int]:
    if date_from and date_to and date_from > date_to:
        raise AppError(422, "invalid_date_range", "La fecha desde no puede ser posterior a la fecha hasta.")
    query = select(Settlement, Outlet).join(Outlet, Outlet.id == Settlement.outlet_id).where(Settlement.type == OUTLET)
    if date_from:
        query = query.where(Settlement.business_date >= date_from)
    if date_to:
        query = query.where(Settlement.business_date <= date_to)
    if outlet_id is not None:
        query = query.where(Settlement.outlet_id == outlet_id)
    if status is not None:
        query = query.where(Settlement.status == status)
    query = query.where(*service.financial_state_filter(financial_state))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(Settlement.business_date.desc(), Outlet.code, Settlement.sequence_number, Settlement.id)
        .limit(limit)
        .offset(offset)
    )
    return [(row[0], row[1]) for row in rows], total


def open_count(db: Session, outlet_id: int) -> int:
    """Settlements of the boca still OPEN: never closed or reopened for correction."""
    outlets.get(db, outlet_id)
    return (
        db.scalar(
            select(func.count())
            .select_from(Settlement)
            .where(Settlement.type == OUTLET, Settlement.outlet_id == outlet_id, Settlement.status == "OPEN")
        )
        or 0
    )


def responsible_suggestions(db: Session, settlement: Settlement) -> list[Employee]:
    """Responsables of the boca on the settlement's business day, as suggestions only: they never block the
    close, never split a difference and never come with an amount (Enzo's D3). A boca without responsables
    that day still renders."""
    return list(
        db.scalars(
            select(Employee)
            .join(OutletResponsibility, OutletResponsibility.employee_id == Employee.id)
            .where(OutletResponsibility.outlet_id == settlement.outlet_id, in_effect_on(settlement.business_date))
            .distinct()
            .order_by(Employee.full_name, Employee.id)
        )
    )
