"""Partial deliveries of a boca de expendio (R7-3, ADR 009, Enzo's R7-3 decisions).

A remittance is cash the boca hands over to a cash box during the day, before its settlement closes. In ONE
transaction the service writes the remittance, its `OUTLET_REMITTANCE` operation («Entregas a cuenta — {Boca}»
-X, the destination cash box +X, source `outlet_remittance:<id>`, on the settlement's business date) and the
audit; the database checks at commit that both are exactly each other and that the boca's account still matches
its remittances (migration 0024).

- Who: on a settlement never closed, operator or admin; on one reopened for correction (BEING_CORRECTED), only
  an admin, who documents a remittance the system missed; never on a CLOSED one (reopen it first).
- Destination: any active cash box (default Caja principal in the screen); never a bank nor a technical account.
- `delivered_at`: when it happened, not in the future, on the business date in Buenos Aires. `created_at` is
  when it was recorded, which may be later (a correction).
- Above the expected amount: never blocked; with the total of the remittances in effect after this one above
  the expected amount, it asks for an explicit confirmation («Con esta entrega lo entregado supera el importe
  esperado en $X.»). Without an expected amount it is allowed as is.
- Money is immutable: a mistake is a reversal plus a new remittance. Reversal (admin, with a reason, OPEN or
  BEING_CORRECTED): the REVERSAL of its operation on the ORIGINAL business date, which the cash box must be
  able to cover from that day on (never an invented balance). Cajas never reverses it.
- A boca deactivated after the settlement opened does not stop its remittances nor their reversals.

The close of the settlement consolidates the remittances in effect (`delivery.closing`): «Entregas a cuenta»
+their total, without moving the cash boxes again; `settlement_closing_remittances` freezes which ones.

Lock order, always: the settlement row, its remittances by id, the boca's «Entregas a cuenta», then (inside the
ledger) the accounts that lose value, in id order. The close takes the same order after its pendings and
cheques. Idempotent by `client_request_id`: the ledger request ids derive from the client's.
"""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.errors import AppError, database_timeout
from app.core.logging import db_error_fields
from app.modules.settlements.delivery import idempotency, service
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import (
    CashBox,
    active_cash_box,
    advances_account_of,
    lock_account,
    outlet_advances_account_id,
)
from app.modules.settlements.outlet_settlements import schemas as s
from app.modules.settlements.outlet_settlements.models import OutletRemittance, SettlementClosingRemittance
from app.modules.settlements.outlets.models import OutletResponsibility
from app.modules.settlements.outlets.service import in_effect_on
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User
from app.platform.outlets import service as outlets
from app.platform.parties.models import Employee

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
SOURCE = "outlet_remittance"
OPERATION_TYPE = "OUTLET_REMITTANCE"
# Namespace of the ledger request ids derived from the client's; never change it.
NAMESPACE = UUID("3b7e5a92-41c8-4f0d-a6e3-9c2d8f15b704")
ZERO = Decimal("0.00")


@dataclass(frozen=True)
class Plan:
    """What recording a remittance now means, validated: its legs and whether it goes above the expected."""

    settlement: Settlement
    amount: Decimal
    destination: CashBox
    advances_name: str
    delivered_at: datetime
    employee_id: int | None
    reference: str | None
    remitted_before: Decimal
    # How much the remittances in effect would go above the expected amount (None without an expected amount).
    excess: Decimal | None

    @property
    def remitted_after(self) -> Decimal:
        return self.remitted_before + self.amount


# --- writing --------------------------------------------------------------------------------------


def plan(db: Session, actor: User, settlement: Settlement, body: s.RemittancePreviewIn) -> Plan:
    """Validate a remittance against the settlement (locked or not) and say what it would publish."""
    correcting = settlement.current_closing_id is not None
    if settlement.status != "OPEN":
        raise AppError(
            409,
            "settlement_closed",
            "La rendición está cerrada: reabrila para registrar o revertir una entrega.",
            {"settlement_id": settlement.id, "status": settlement.status},
        )
    if correcting and not _is_admin(actor):
        raise AppError(
            403,
            "remittance_requires_admin",
            "La rendición está en corrección: solo un administrador registra una entrega.",
        )
    amount = body.amount.quantize(Decimal("0.01"))
    delivered_at = _delivered_at(body.delivered_at, settlement)
    destination = active_cash_box(db, body.destination_account_id)
    if destination is None:
        raise AppError(
            422,
            "invalid_remittance_destination",
            "La entrega va a una caja activa: nunca a un banco ni a una cuenta técnica.",
            {"destination_account_id": body.destination_account_id},
        )
    _check_delivered_by(db, settlement, body.delivered_by_employee_id, correcting)
    remitted_before = remitted_total(db, settlement.id)
    expected = settlement.expected_amount
    excess = None
    if expected is not None and remitted_before + amount > expected:
        excess = remitted_before + amount - expected
    assert settlement.outlet_id is not None  # OUTLET (identity_by_type)
    outlet = outlets.get(db, settlement.outlet_id)
    return Plan(
        settlement=settlement,
        amount=amount,
        destination=destination,
        advances_name=f"Entregas a cuenta — {outlet.name}",
        delivered_at=delivered_at,
        employee_id=body.delivered_by_employee_id,
        reference=(body.reference or "").strip() or None,
        remitted_before=remitted_before,
        excess=excess,
    )


def create(db: Session, actor: User, settlement_id: int, body: s.RemittanceIn) -> service.Created:
    reference = (body.reference or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        SOURCE,
        {
            "settlement_id": settlement_id,
            "amount": service.money(body.amount),
            "destination_account_id": body.destination_account_id,
            "delivered_at": body.delivered_at.astimezone(UTC).isoformat(),
            "delivered_by_employee_id": body.delivered_by_employee_id,
            "reference": reference,
        },
    )
    # A retry is answered before anything else is validated: it must get the same result as the first time.
    existing = _by_request_id(db, body.client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "remittance_id", existing.id)
        return service.Created(existing.id, created=False)

    settlement = _lock_settlement(db, settlement_id)
    planned = plan(db, actor, settlement, body)
    if planned.excess is not None and not body.confirm_above_expected:
        raise excess_error(planned)
    assert settlement.outlet_id is not None
    outlet = outlets.get(db, settlement.outlet_id)
    advances = outlet_advances_account_id(db, outlet_id=outlet.id, outlet_name=outlet.name, user_id=actor.id)
    _lock_account(db, advances)

    remittance = OutletRemittance(
        settlement_id=settlement.id,
        outlet_id=settlement.outlet_id,
        business_date=settlement.business_date,
        delivered_at=planned.delivered_at,
        amount=planned.amount,
        destination_account_id=planned.destination.id,
        delivered_by_employee_id=planned.employee_id,
        reference=planned.reference,
        client_request_id=body.client_request_id,
        request_fingerprint=fingerprint,
        created_by=actor.id,
    )
    try:
        with db.begin_nested():
            db.add(remittance)
            db.flush()
    except IntegrityError as exc:
        winner = _by_request_id(db, body.client_request_id)
        if winner is None:
            # A rule the database saw at insert time (e.g. the cash box was deactivated meanwhile).
            db.rollback()
            raise _rejected(exc) from exc
        db.rollback()  # release the locks taken for the insert that lost
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "remittance_id", winner.id)
        return service.Created(winner.id, created=False)

    operation = ledger.post_operation(
        db,
        actor=actor,
        type=OPERATION_TYPE,
        legs=[ledger.Leg(advances, -planned.amount), ledger.Leg(planned.destination.id, planned.amount)],
        client_request_id=uuid5(NAMESPACE, f"outlet-remittance:{body.client_request_id}"),
        business_date=settlement.business_date,
        description=f"Entrega de {outlet.name} · rendición n.º {settlement.sequence_number} (#{settlement.id})",
        source=ledger.SourceRef(SOURCE, remittance.id),
    ).operation
    remittance.operation_id = operation.id
    db.flush()
    record_change(
        db,
        "settlements.outlet_remittance.created",
        action="create",
        user_id=actor.id,
        entity_type="outlet_remittance",
        entity_id=remittance.id,
        before=None,
        after=snapshot(remittance)
        | {
            "operation_id": operation.id,
            "remitted_total": service.money(planned.remitted_after),
            "above_expected": service.money(planned.excess) if planned.excess is not None else None,
        },
    )
    commit(db)
    return service.Created(remittance.id, created=True)


def reverse(db: Session, actor: User, settlement_id: int, remittance_id: int, body: s.RemittanceReversalIn) -> int:
    """Undo a remittance (admin, with a reason) while its settlement is OPEN or BEING_CORRECTED."""
    if not _is_admin(actor):
        raise AppError(403, "remittance_requires_admin", "Solo un administrador revierte una entrega.")
    reason = body.reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo del reverso.")
    settlement = _lock_settlement(db, settlement_id)
    remittance = db.scalars(
        select(OutletRemittance)
        .where(OutletRemittance.id == remittance_id, OutletRemittance.settlement_id == settlement.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if remittance is None:
        raise AppError(404, "remittance_not_found", "La entrega no existe en esta rendición.")
    request_id = uuid5(NAMESPACE, f"outlet-remittance-reversal:{body.client_request_id}")
    if remittance.reversal_operation_id is not None:
        reversal = ledger.get_operation(db, remittance.reversal_operation_id)
        if reversal.client_request_id == request_id:
            if (remittance.reversed_by, remittance.reversal_reason) != (actor.id, reason):
                raise AppError(
                    409,
                    "idempotency_conflict",
                    "Ese identificador de solicitud ya se usó para un reverso distinto.",
                    {"operation_id": reversal.id},
                )
            db.commit()  # a retry of the same reversal: release the locks
            return remittance.id
        raise AppError(
            409,
            "remittance_already_reversed",
            "La entrega ya fue revertida.",
            {"reversal_operation_id": reversal.id},
        )
    if settlement.status != "OPEN":
        raise AppError(
            409,
            "settlement_closed",
            "La rendición está cerrada: reabrila para revertir una entrega.",
            {"settlement_id": settlement.id, "status": settlement.status},
        )
    advances = advances_account_of(db, remittance.outlet_id)
    # A published remittance has both (0024).
    assert advances is not None
    assert remittance.operation_id is not None
    _lock_account(db, advances)
    reversal = ledger.reverse_document_operation(
        db,
        actor=actor,
        operation_id=remittance.operation_id,
        source=ledger.SourceRef(SOURCE, remittance.id),
        reason=reason,
        client_request_id=request_id,
    ).operation
    remittance.reversal_operation_id = reversal.id
    remittance.reversed_by = actor.id
    remittance.reversed_at = datetime.now(UTC)
    remittance.reversal_reason = reason
    db.flush()
    record_change(
        db,
        "settlements.outlet_remittance.reversed",
        action="reverse",
        user_id=actor.id,
        entity_type="outlet_remittance",
        entity_id=remittance.id,
        before={"operation_id": remittance.operation_id},
        after={
            "reversal_operation_id": reversal.id,
            "business_date": reversal.business_date.isoformat(),
            "reason": reason,
            "settlement_being_corrected": settlement.current_closing_id is not None,
        },
    )
    commit(db)
    return remittance.id


def excess_error(planned: Plan) -> AppError:
    assert planned.excess is not None
    return AppError(
        409,
        "remittance_above_expected",
        f"Con esta entrega lo entregado supera el importe esperado en ${service.money(planned.excess)}.",
        {
            "excess": service.money(planned.excess),
            "remitted_total": service.money(planned.remitted_after),
            "expected": service.money(planned.settlement.expected_amount or ZERO),
        },
    )


# --- the close ------------------------------------------------------------------------------------


def lock_for_close(db: Session, settlement: Settlement) -> list[OutletRemittance]:
    """Lock the settlement's remittances (by id) and the boca's «Entregas a cuenta» for a close; return the ones in
    effect, which the close consolidates. The settlement row is already locked."""
    rows = list(
        db.scalars(
            select(OutletRemittance)
            .where(OutletRemittance.settlement_id == settlement.id)
            .order_by(OutletRemittance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )
    if settlement.outlet_id is not None:
        advances = advances_account_of(db, settlement.outlet_id)
        if advances is not None:
            _lock_account(db, advances)
    return [row for row in rows if row.reversal_operation_id is None]


def publish(db: Session, settlement: Settlement, in_effect: list[OutletRemittance], close_operation_id: int) -> None:
    """Freeze what the close consolidated: one row per remittance in effect (the database checks it, 0024)."""
    db.add_all(
        SettlementClosingRemittance(
            close_operation_id=close_operation_id,
            settlement_id=settlement.id,
            settlement_version=settlement.version,
            remittance_id=row.id,
            amount=row.amount,
        )
        for row in in_effect
    )
    db.flush()


def preview_rows(db: Session, settlement_id: int) -> list[list[Any]]:
    """The remittances in effect as a close preview token sees them: any new one, reversal or change alters it."""
    rows = db.scalars(
        select(OutletRemittance).where(
            OutletRemittance.settlement_id == settlement_id, OutletRemittance.reversal_operation_id.is_(None)
        )
    )
    return sorted(
        [
            row.id,
            service.money(row.amount),
            row.destination_account_id,
            row.delivered_at.astimezone(UTC).isoformat(),
            row.delivered_by_employee_id,
            row.reference,
        ]
        for row in rows
    )


# --- reading --------------------------------------------------------------------------------------


def of(db: Session, settlement_id: int) -> list[OutletRemittance]:
    return list(
        db.scalars(
            select(OutletRemittance)
            .where(OutletRemittance.settlement_id == settlement_id)
            .order_by(OutletRemittance.delivered_at, OutletRemittance.id)
        )
    )


def remitted_total(db: Session, settlement_id: int) -> Decimal:
    """Total of the settlement's remittances in effect."""
    total = db.scalar(
        select(func.coalesce(func.sum(OutletRemittance.amount), 0)).where(
            OutletRemittance.settlement_id == settlement_id, OutletRemittance.reversal_operation_id.is_(None)
        )
    )
    return Decimal(total or 0)


def not_consolidated_total(db: Session, outlet_id: int) -> Decimal:
    """Cash of the boca handed over in effect that no close in effect consolidated yet: remittances of OPEN
    settlements, and during a correction those recorded after the close still in effect."""
    consolidated = select(SettlementClosingRemittance.id).where(
        SettlementClosingRemittance.remittance_id == OutletRemittance.id,
        SettlementClosingRemittance.close_operation_id == Settlement.current_close_operation_id,
    )
    total = db.scalar(
        select(func.coalesce(func.sum(OutletRemittance.amount), 0))
        .join(Settlement, Settlement.id == OutletRemittance.settlement_id)
        .where(
            OutletRemittance.outlet_id == outlet_id,
            OutletRemittance.reversal_operation_id.is_(None),
            ~consolidated.exists(),
        )
    )
    return Decimal(total or 0)


def consolidated_ids(db: Session, close_operation_id: int | None) -> set[int]:
    """Remittances the close in effect consolidated (none without a financial close in effect)."""
    if close_operation_id is None:
        return set()
    return set(
        db.scalars(
            select(SettlementClosingRemittance.remittance_id).where(
                SettlementClosingRemittance.close_operation_id == close_operation_id
            )
        )
    )


def snapshot(row: OutletRemittance) -> dict[str, Any]:
    return {
        "settlement_id": row.settlement_id,
        "outlet_id": row.outlet_id,
        "business_date": row.business_date.isoformat(),
        "delivered_at": row.delivered_at.astimezone(UTC).isoformat(),
        "amount": service.money(row.amount),
        "destination_account_id": row.destination_account_id,
        "delivered_by_employee_id": row.delivered_by_employee_id,
        "reference": row.reference,
    }


def commit(db: Session) -> None:
    """Commit; a rule only the database saw is 409, never a raw error."""
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        raise _rejected(exc) from exc


def _rejected(exc: DBAPIError) -> AppError:
    logger.warning("outlet_remittance_rejected", extra=db_error_fields(exc))
    return AppError(
        409,
        "invalid_outlet_remittance",
        "La entrega no cumple las reglas de las entregas de boca y no se registró.",
    )


# --- internals ------------------------------------------------------------------------------------


def _lock_settlement(db: Session, settlement_id: int) -> Settlement:
    settlement = db.scalars(
        select(Settlement)
        .where(Settlement.id == settlement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if settlement is None or settlement.type != service.OUTLET:
        raise service.not_found()
    return settlement


def _lock_account(db: Session, account_id: int) -> None:
    lock_account(db, account_id)


def _delivered_at(value: datetime, settlement: Settlement) -> datetime:
    if value.tzinfo is None:
        raise AppError(422, "invalid_delivered_at", "Indicá la hora de la entrega con su zona horaria.")
    if value > datetime.now(UTC):
        raise AppError(422, "future_delivered_at", "La hora de la entrega no puede ser futura.")
    local = value.astimezone(ZoneInfo(get_settings().business_timezone)).date()
    if local != settlement.business_date:
        raise AppError(
            422,
            "delivered_at_not_on_business_date",
            f"La entrega tiene que ser del día de la rendición ({settlement.business_date.strftime('%d/%m/%Y')}).",
            {"business_date": settlement.business_date.isoformat(), "delivered_on": local.isoformat()},
        )
    return value


def _check_delivered_by(db: Session, settlement: Settlement, employee_id: int | None, correcting: bool) -> None:
    """Who carried the cash is a fact of the business date, never of today (Enzo's R7-3 point 6).

    The employee must exist and, if their hire date is known, have been hired by then. Employees have no history
    of deactivation, so the current flag decides only when nothing else speaks for the past: an employee
    deactivated since is accepted if they were a responsable of the boca that day, or during a correction (an
    admin documenting a past remittance). Otherwise a new remittance names an active employee. It never changes the
    difference nor who absorbs it.
    """
    if employee_id is None:
        return
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise AppError(422, "employee_not_found", "El empleado no existe.", {"employee_id": employee_id})
    if employee.hire_date is not None and employee.hire_date > settlement.business_date:
        raise AppError(
            422,
            "employee_not_hired_yet",
            "El empleado todavía no trabajaba en la empresa el día de la entrega.",
            {"employee_id": employee_id},
        )
    if employee.active or correcting:
        return
    responsible = db.scalar(
        select(OutletResponsibility.id).where(
            OutletResponsibility.outlet_id == settlement.outlet_id,
            OutletResponsibility.employee_id == employee_id,
            in_effect_on(settlement.business_date),
        )
    )
    if responsible is None:
        raise AppError(
            422,
            "employee_inactive",
            "El empleado está dado de baja: elegí un empleado activo o uno responsable de la boca ese día.",
            {"employee_id": employee_id},
        )


def _by_request_id(db: Session, client_request_id: UUID) -> OutletRemittance | None:
    return db.scalars(
        select(OutletRemittance).where(OutletRemittance.client_request_id == client_request_id)
    ).one_or_none()


def _is_admin(actor: User) -> bool:
    return has_module_access(actor, MODULE_KEY, "admin")
