"""Close and reopen of a delivery settlement towards the ledger (R2-2, Enzo's R2-P1 and R2-P2).

`status` is the editing state (OPEN/CLOSED); `current_close_operation_id` is the SETTLEMENT_CLOSE in
effect in the ledger. Their combination is the financial state:

- NOT_CLOSED: OPEN and never closed;
- CLOSED: CLOSED, its close in effect;
- BEING_CORRECTED: reopened (OPEN) while the previous close is still in effect in Cajas.

Close (CERRAR RENDICIÓN, always explicit and confirmed, even at $0), one transaction: lock the
settlement, recompute every total from the database, validate who absorbs the difference, publish
the SETTLEMENT_CLOSE (or, after a reopening, the reversal of the previous close plus the new one with
`replace_operation`, checked on their net effect), materialize the difference and its allocations,
freeze the settlement and audit. Legs:

    Cobranzas de reparto       -expected
    each cash box              +cash counted and added for that box
    each bank                  +VERIFIED transfers to that bank
    Gastos operativos          +valid expenses
    employee internal account  +its part of a shortage / -its part of a surplus
    Diferencias de rendición   the company's part, same sign

Idempotency: the ledger request ids derive from (settlement, version), and the close carries a
fingerprint of everything it means (totals per account, difference, allocations, legs). Repeating a
close of the same version with the same content returns it; with other content is a conflict.

Reopen (admin, with a reason) only unfreezes the load and increments the version: the ledger is not
touched and the previous close stays in effect until the new close replaces it.

Lock order, always: the settlement row, then (inside the ledger) the accounts that lose value, in id
order. Load changes lock the settlement row too, so they never interleave with a close.
"""

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid5

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery import service, summary
from app.modules.settlements.delivery.models import (
    DifferenceAllocation,
    Settlement,
    SettlementClosing,
    SettlementDifference,
    SettlementTransfer,
)
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import employee_account_id
from app.platform.audit.models import AuditEvent
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User
from app.platform.parties.models import Employee

MODULE_KEY = "settlements"
CLOSE_TYPE = "SETTLEMENT_CLOSE"
SOURCE_TYPE = "settlement"
REOPENED_EVENT = "settlements.settlement.reopened"
# Namespace of the deterministic ledger request ids of closes; never change it.
CLOSE_NAMESPACE = UUID("5d0f6c1e-8a43-4a52-9a43-1d2b8d7f0c21")
ZERO = Decimal("0.00")

# A leg before its account exists: ("account", account_id) or ("employee", employee_id).
LegKey = tuple[str, int]


@dataclass(frozen=True)
class Allocation:
    target: str
    employee_id: int | None
    amount: Decimal
    reason: str | None


@dataclass
class Plan:
    """What closing the settlement now means, computed from the database only."""

    settlement: Settlement
    totals: summary.Totals
    verified_by_bank: dict[int, Decimal]
    legs: dict[LegKey, Decimal] = field(default_factory=dict)

    @property
    def difference(self) -> Decimal:
        return self.totals.difference

    @property
    def kind(self) -> str:
        return self.totals.difference_kind


# --- financial state ------------------------------------------------------------------------------


def financial_state(settlement: Settlement) -> str:
    if settlement.status == "CLOSED":
        return "CLOSED"
    return "BEING_CORRECTED" if settlement.current_close_operation_id is not None else "NOT_CLOSED"


def financial_state_detail(settlement: Settlement) -> str:
    state = financial_state(settlement)
    if state == "CLOSED":
        return "Rendición cerrada: su cierre está vigente en Cajas."
    if state == "BEING_CORRECTED":
        return (
            "Rendición reabierta para corrección: el último cierre sigue vigente en Cajas "
            "hasta que se confirme el nuevo cierre."
        )
    return "Rendición abierta: todavía no movió dinero en Cajas."


def close_request_id(settlement_id: int, version: int) -> UUID:
    return uuid5(CLOSE_NAMESPACE, f"settlement:{settlement_id}:v{version}:close")


def reversal_request_id(settlement_id: int, version: int) -> UUID:
    return uuid5(CLOSE_NAMESPACE, f"settlement:{settlement_id}:v{version}:reversal")


# --- preview --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Preview:
    plan: Plan
    blockers: list[s.Blocker]


def preview(db: Session, settlement_id: int) -> Preview:
    """What a close would publish now and what stops it, without writing anything."""
    plan = _plan(db, service.get(db, settlement_id))
    blockers = []
    if plan.settlement.status != "OPEN":
        blockers.append(s.Blocker(code="settlement_already_closed", message="La rendición ya está cerrada."))
    if plan.totals.declared_transfers_count:
        blockers.append(
            s.Blocker(
                code="declared_transfers_pending",
                message=f"Hay {plan.totals.declared_transfers_count} transferencias sin verificar ni rechazar.",
            )
        )
    return Preview(plan, blockers)


# --- close ----------------------------------------------------------------------------------------


def close(db: Session, actor: User, settlement_id: int, body: s.CloseIn) -> service.Created:
    settlement = _lock(db, settlement_id)
    plan = _plan(db, settlement)
    allocations = _normalize(body.allocations)
    fingerprint = _fingerprint(plan, body.version, allocations)

    existing = db.scalars(
        select(SettlementClosing).where(
            SettlementClosing.settlement_id == settlement.id, SettlementClosing.settlement_version == body.version
        )
    ).one_or_none()
    if existing is not None:
        if existing.request_fingerprint != fingerprint:
            raise AppError(
                409,
                "idempotency_conflict",
                "Esta versión de la rendición ya se cerró con otro contenido.",
                {"settlement_id": settlement.id, "version": body.version, "operation_id": existing.operation_id},
            )
        db.commit()  # a retry of the same close: release the lock and return it
        return service.Created(settlement.id, created=False)
    if settlement.status != "OPEN":
        raise AppError(
            409,
            "settlement_already_closed",
            "La rendición ya está cerrada.",
            {"settlement_id": settlement.id, "version": settlement.version},
        )
    if body.version != settlement.version:
        raise _concurrent_change(settlement)
    if plan.totals.declared_transfers_count:
        raise AppError(
            409,
            "declared_transfers_pending",
            "Hay transferencias declaradas sin verificar ni rechazar: resolvelas antes de cerrar.",
            {"declared_transfers_count": plan.totals.declared_transfers_count},
        )
    _check_allocations(db, actor, plan, allocations)
    if not body.confirm:
        raise AppError(422, "confirmation_required", "Confirmá el cierre de la rendición.")

    legs = _ledger_legs(db, actor, plan, allocations)
    description = f"Cierre de rendición #{settlement.id} (versión {settlement.version})"
    source = ledger.SourceRef(SOURCE_TYPE, settlement.id)
    reversal_id: int | None = None
    if settlement.current_close_operation_id is None:
        operation = ledger.post_operation(
            db,
            actor=actor,
            type=CLOSE_TYPE,
            legs=legs,
            client_request_id=close_request_id(settlement.id, settlement.version),
            business_date=settlement.business_date,
            description=description,
            source=source,
        ).operation
    else:
        replaced = ledger.replace_operation(
            db,
            actor=actor,
            original_id=settlement.current_close_operation_id,
            type=CLOSE_TYPE,
            legs=legs,
            client_request_id=close_request_id(settlement.id, settlement.version),
            reversal_request_id=reversal_request_id(settlement.id, settlement.version),
            business_date=settlement.business_date,
            description=description,
            reason=f"Corrección de la rendición #{settlement.id}: {settlement.reopen_reason or ''}",
            source=source,
        )
        operation, reversal_id = replaced.replacement, replaced.reversal.id

    now = datetime.now(UTC)
    superseded = db.execute(
        update(SettlementDifference)
        .where(SettlementDifference.settlement_id == settlement.id, SettlementDifference.superseded_at.is_(None))
        .values(superseded_at=now, superseded_by=actor.id)
        .returning(SettlementDifference.id)
    ).scalar_one_or_none()
    difference_id = None
    if allocations:
        difference = SettlementDifference(
            settlement_id=settlement.id,
            settlement_version=settlement.version,
            kind=plan.kind,
            amount=abs(plan.difference),
            close_operation_id=operation.id,
            created_by=actor.id,
        )
        db.add(difference)
        db.flush()
        difference_id = difference.id
        db.add_all(
            DifferenceAllocation(
                difference_id=difference.id,
                target=a.target,
                employee_id=a.employee_id,
                amount=a.amount,
                reason=a.reason,
                decided_by=actor.id,
                decided_at=now,
            )
            for a in allocations
        )
    db.add(
        SettlementClosing(
            settlement_id=settlement.id,
            settlement_version=settlement.version,
            operation_id=operation.id,
            reversal_operation_id=reversal_id,
            request_fingerprint=fingerprint,
            closed_by=actor.id,
            closed_at=now,
        )
    )
    previous_close = settlement.current_close_operation_id
    settlement.status = "CLOSED"
    settlement.current_close_operation_id = operation.id
    settlement.closed_at = now
    settlement.closed_by = actor.id
    record_change(
        db,
        "settlements.settlement.closed",
        action="close",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before={"status": "OPEN", "version": settlement.version, "close_in_effect": previous_close},
        after={
            "status": "CLOSED",
            "version": settlement.version,
            "close_operation_id": operation.id,
            "reversal_operation_id": reversal_id,
            "difference": service.money(plan.difference),
            "difference_kind": plan.kind,
            "difference_id": difference_id,
            "superseded_difference_id": superseded,
            "allocations": [
                {
                    "target": a.target,
                    "employee_id": a.employee_id,
                    "amount": service.money(a.amount),
                    "reason": a.reason,
                }
                for a in allocations
            ],
        },
    )
    service.commit(db)
    return service.Created(settlement.id, created=True)


# --- reopen ---------------------------------------------------------------------------------------


def reopen(db: Session, actor: User, settlement_id: int, body: s.ReopenIn) -> service.Created:
    """Unfreeze a closed settlement for correction. The ledger is not touched (BEING_CORRECTED)."""
    if not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(403, "forbidden", "Solo un administrador puede reabrir una rendición.")
    reason = body.reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la reapertura.")
    settlement = _lock(db, settlement_id)
    if settlement.status != "CLOSED":
        if (settlement.version, settlement.reopened_by, settlement.reopen_reason) == (
            body.version + 1,
            actor.id,
            reason,
        ):
            db.commit()  # a retry of the same reopening
            return service.Created(settlement.id, created=False)
        raise AppError(
            409,
            "settlement_not_closed",
            "La rendición no está cerrada.",
            {"settlement_id": settlement.id, "version": settlement.version},
        )
    if body.version != settlement.version:
        raise _concurrent_change(settlement)
    previous = settlement.version
    settlement.status = "OPEN"
    settlement.version = previous + 1
    settlement.reopened_at = datetime.now(UTC)
    settlement.reopened_by = actor.id
    settlement.reopen_reason = reason
    record_change(
        db,
        REOPENED_EVENT,
        action="reopen",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before={"status": "CLOSED", "version": previous},
        after={
            "status": "OPEN",
            "version": settlement.version,
            "reason": reason,
            "close_in_effect": settlement.current_close_operation_id,
        },
    )
    service.commit(db)
    return service.Created(settlement.id, created=True)


# --- reading --------------------------------------------------------------------------------------


def current_difference(db: Session, settlement: Settlement) -> SettlementDifference | None:
    """Difference of the close in effect (a reopened settlement still shows the one in Cajas)."""
    if settlement.current_close_operation_id is None:
        return None
    return db.scalars(
        select(SettlementDifference).where(
            SettlementDifference.close_operation_id == settlement.current_close_operation_id
        )
    ).one_or_none()


def differences(db: Session, settlement_id: int) -> list[SettlementDifference]:
    return list(
        db.scalars(
            select(SettlementDifference)
            .where(SettlementDifference.settlement_id == settlement_id)
            .order_by(SettlementDifference.settlement_version)
        )
    )


def allocations_of(db: Session, difference_ids: list[int]) -> dict[int, list[DifferenceAllocation]]:
    result: dict[int, list[DifferenceAllocation]] = {did: [] for did in difference_ids}
    if difference_ids:
        rows = db.scalars(
            select(DifferenceAllocation)
            .where(DifferenceAllocation.difference_id.in_(difference_ids))
            .order_by(DifferenceAllocation.id)
        )
        for row in rows:
            result[row.difference_id].append(row)
    return result


def closings(db: Session, settlement_id: int) -> list[SettlementClosing]:
    return list(
        db.scalars(
            select(SettlementClosing)
            .where(SettlementClosing.settlement_id == settlement_id)
            .order_by(SettlementClosing.settlement_version)
        )
    )


def close_of(db: Session, operation_id: int) -> SettlementClosing | None:
    return db.scalars(select(SettlementClosing).where(SettlementClosing.operation_id == operation_id)).one_or_none()


def reopenings(db: Session, settlement_id: int) -> list[AuditEvent]:
    """Reopenings come from the audit trail, which is append-only and records who, when and why."""
    return list(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.event_type == REOPENED_EVENT,
                AuditEvent.entity_type == "settlement",
                AuditEvent.entity_id == str(settlement_id),
            )
            .order_by(AuditEvent.id)
        )
    )


# --- internals ------------------------------------------------------------------------------------


def _lock(db: Session, settlement_id: int) -> Settlement:
    settlement = db.scalars(
        select(Settlement)
        .where(Settlement.id == settlement_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()
    if settlement is None:
        raise service.not_found()
    return settlement


def _concurrent_change(settlement: Settlement) -> AppError:
    return AppError(
        409,
        "settlement_concurrent_change",
        "La rendición cambió mientras la estabas viendo: actualizá y revisá antes de seguir.",
        {"settlement_id": settlement.id, "version": settlement.version, "status": settlement.status},
    )


def _plan(db: Session, settlement: Settlement) -> Plan:
    totals = summary.totals(db, [settlement.id])[settlement.id]
    # Cheques (R4) and pending collections (R3) are always zero in this phase; they get their legs then.
    verified = db.execute(
        select(SettlementTransfer.bank_account_id, func.sum(SettlementTransfer.amount))
        .where(SettlementTransfer.settlement_id == settlement.id, SettlementTransfer.status == "VERIFIED")
        .group_by(SettlementTransfer.bank_account_id)
    )
    plan = Plan(settlement, totals, dict(verified.all()))
    legs: dict[LegKey, Decimal] = {}

    def add(key: LegKey, amount: Decimal) -> None:
        legs[key] = legs.get(key, ZERO) + amount

    add(("account", ledger.system_account_id(db, "delivery_collections")), -totals.expected)
    for box_id, box in totals.by_box.items():
        add(("account", box_id), box.total)
    for bank_id, amount in plan.verified_by_bank.items():
        add(("account", bank_id), amount)
    if totals.expenses:
        add(("account", ledger.system_account_id(db, "expense")), totals.expenses)
    plan.legs = {key: amount for key, amount in legs.items() if amount != 0}
    return plan


def _normalize(allocations: list[s.AllocationIn]) -> list[Allocation]:
    result = []
    for a in allocations:
        if (a.target == "EMPLOYEE") != (a.employee_id is not None):
            raise AppError(
                422,
                "invalid_allocation",
                "Cada parte de la diferencia es de un empleado (con su id) o de la empresa (sin empleado).",
            )
        result.append(Allocation(a.target, a.employee_id, a.amount, (a.reason or "").strip() or None))
    return result


def _check_allocations(db: Session, actor: User, plan: Plan, allocations: list[Allocation]) -> None:
    """The difference is split exactly, among distinct employees and at most one company part."""
    owed = abs(plan.difference)
    if owed == 0:
        if allocations:
            raise AppError(
                422,
                "difference_allocation_mismatch",
                "La rendición no tiene diferencia: no hay nada que asignar.",
                {"difference": "0.00"},
            )
        return
    allocated = sum((a.amount for a in allocations), ZERO)
    details = {"difference": service.money(owed), "difference_kind": plan.kind, "allocated": service.money(allocated)}
    if not allocations or allocated < owed:
        raise AppError(
            422,
            "difference_not_fully_allocated",
            f"Falta asignar ${owed - allocated} de la diferencia.",
            details,
        )
    if allocated > owed:
        raise AppError(
            422,
            "difference_allocation_mismatch",
            "Lo asignado supera la diferencia de la rendición.",
            details,
        )
    employees = [a.employee_id for a in allocations if a.employee_id is not None]
    company = [a for a in allocations if a.target == "COMPANY"]
    if len(set(employees)) != len(employees) or len(company) > 1:
        raise AppError(
            422,
            "difference_allocation_mismatch",
            "Cada empleado y la empresa pueden aparecer una sola vez en la asignación.",
            details,
        )
    if company:
        if not has_module_access(actor, MODULE_KEY, "admin"):
            raise AppError(
                403,
                "company_allocation_requires_admin",
                "Solo un administrador puede asignar una diferencia a la empresa.",
            )
        if company[0].reason is None:
            raise AppError(422, "reason_required", "Indicá el motivo por el que la empresa absorbe la diferencia.")
    found = set(db.scalars(select(Employee.id).where(Employee.id.in_(employees))))
    missing = [e for e in employees if e not in found]
    if missing:
        raise AppError(422, "employee_not_found", "El empleado no existe.", {"employee_id": missing[0]})


def _difference_legs(plan: Plan, allocations: list[Allocation], difference_account: int) -> dict[LegKey, Decimal]:
    # Shortage: the employee owes it (+) or the company loses it (+); surplus: the opposite sign.
    sign = 1 if plan.kind == "SHORTAGE" else -1
    legs: dict[LegKey, Decimal] = {}
    for a in allocations:
        key: LegKey = ("employee", a.employee_id) if a.employee_id is not None else ("account", difference_account)
        legs[key] = sign * a.amount
    return legs


def _all_legs(db: Session, plan: Plan, allocations: list[Allocation]) -> dict[LegKey, Decimal]:
    legs = dict(plan.legs)
    for key, amount in _difference_legs(
        plan, allocations, ledger.system_account_id(db, "settlement_difference")
    ).items():
        legs[key] = legs.get(key, ZERO) + amount
    return {key: amount for key, amount in legs.items() if amount != 0}


def _ledger_legs(db: Session, actor: User, plan: Plan, allocations: list[Allocation]) -> list[ledger.Leg]:
    legs = []
    names = dict(
        db.execute(
            select(Employee.id, Employee.full_name).where(
                Employee.id.in_([a.employee_id for a in allocations if a.employee_id is not None])
            )
        ).all()
    )
    for (kind, ref), amount in sorted(_all_legs(db, plan, allocations).items()):
        if kind == "account":
            account_id = ref
        else:
            account_id = employee_account_id(db, employee_id=ref, employee_name=names[ref], user_id=actor.id)
        legs.append(ledger.Leg(account_id, amount))
    return legs


def _fingerprint(plan: Plan, version: int, allocations: list[Allocation]) -> str:
    """SHA-256 of everything the close means; who sends it does not change what it is."""
    totals = plan.totals
    payload: dict[str, Any] = {
        "source": [SOURCE_TYPE, plan.settlement.id],
        "version": version,
        "business_date": plan.settlement.business_date.isoformat(),
        "expected": service.money(totals.expected),
        "cash": sorted([box_id, service.money(box.total)] for box_id, box in totals.by_box.items()),
        "transfers": sorted([bank, service.money(amount)] for bank, amount in plan.verified_by_bank.items()),
        "expenses": service.money(totals.expenses),
        "difference": service.money(plan.difference),
        "allocations": sorted(
            [a.target, a.employee_id or 0, service.money(a.amount), a.reason or ""] for a in allocations
        ),
        "legs": sorted([kind, ref, service.money(amount)] for (kind, ref), amount in plan.legs.items()),
        # The company's leg is keyed 0 here: its account is fixed, only who absorbs what matters.
        "difference_legs": sorted(
            [kind, ref, service.money(amount)] for (kind, ref), amount in _difference_legs(plan, allocations, 0).items()
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
