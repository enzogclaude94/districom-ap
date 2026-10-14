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
    Pendientes de cobro        +pending collections not voided (R3-1)
    Cartera de cheques         +each cheque that justifies, one leg per cheque with its cheque_id (R4-1)
    employee internal account  +its part of a shortage / -its part of a surplus
    Diferencias de rendición   the company's part, same sign

Idempotency: the ledger request ids derive from (settlement, version), and the close carries a
fingerprint of everything it means (totals per account, difference, allocations, legs). Repeating a
close of the same version with the same content returns it; with other content is a conflict.

Reopen (admin, with a reason) records a `settlement_reopenings` row, unfreezes the load and increments
the version: the ledger is not touched and the previous close stays in effect until the new close
replaces it. The same holds for pending collections (R3-1): each close freezes what it published per
pending in `settlement_closing_pendings`, and that row of the close in effect is the amount in effect,
whatever the load of a correction says until it is closed. Cheques (R4-1) work the same way with
`settlement_closing_cheques`: a cheque added in a correction is not in the portfolio until the new close,
and one voided in it stays there until then; a DECLARED ECHEQ blocks the close.

The close carries the `preview_token` of the preview the user confirmed: if anything that could alter
the close changed since, it is refused (`settlement_preview_stale`) before any financial write.

Boca de expendio (OUTLET, R7-2): the same close, taking the expected amount out of «Cobranzas de boca» (no
leg when it is 0); its expected amount must be loaded to close. An OUTLET settlement of $0 without any movement
closes with an administrative zero close (Enzo's R7-2-Q1): a `settlement_closings` row of kind
ADMINISTRATIVE_ZERO with its snapshot, no ledger operation, no zero legs. `current_closing_id` is the
documentary close in effect (financial or administrative); `current_close_operation_id` its operation, NULL
only after an administrative zero close. Crossings:

- administrative -> reopen -> activity -> close: a normal SETTLEMENT_CLOSE (nothing to reverse);
- financial -> reopen -> corrected to $0 -> close: the REVERSAL of the previous close
  (`ledger.withdraw_document_operation`) and an administrative close, without a new operation;
- administrative -> reopen -> $0 again -> close: a new administrative close.

Reopening a zero close never invents a reversal of an operation that never existed.

Remittances (OUTLET, R7-3): the cash a boca handed over during the day already entered its cash boxes with each
`OUTLET_REMITTANCE`. They justify like cash, and the close consolidates the ones in effect: «Entregas a cuenta —
{Boca}» +their total, the cash boxes untouched by them (only the final count moves them). Example: expected
1.000.000, remittances 400.000 + 300.000, final count 200.000, a transfer of 100.000 → «Cobranzas de boca»
-1.000.000, «Entregas a cuenta» +700.000, Caja +200.000, Banco +100.000. Each close freezes in
`settlement_closing_remittances` the remittances it consolidated; a reopening does not touch them, and a
remittance reversed during a correction stays consolidated by the old close until the new one replaces it. An
administrative zero close needs no remittance in effect.

Lock order, always: the settlement row, its pending collections by id, its cheques by id, its remittances by id
and the boca's «Entregas a cuenta», then (inside the ledger) the accounts that lose value, in id order. Load changes
lock the settlement row too, so they never interleave with a close.
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
from app.modules.settlements.cheques import service as cheques
from app.modules.settlements.cheques.models import Cheque
from app.modules.settlements.delivery import expenses, service, summary
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import (
    CashAddition,
    CashCountLine,
    DifferenceAllocation,
    Expense,
    FuelLoad,
    Settlement,
    SettlementClosing,
    SettlementDifference,
    SettlementReopening,
    SettlementTransfer,
)
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.ledger.accounts import advances_account_of, employee_account_id
from app.modules.settlements.outlet_settlements import remittances
from app.modules.settlements.outlet_settlements.models import OutletRemittance
from app.modules.settlements.pending import service as pending_collections
from app.modules.settlements.pending.models import PendingCollection
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
ADMINISTRATIVE_ZERO = "ADMINISTRATIVE_ZERO"
EXPECTED_REQUIRED = "expected_amount_required"
EXPECTED_REQUIRED_MESSAGE = "Cargá el importe esperado de Store Sys para cerrar la rendición."

# A leg before its account exists: ("account", account_id), ("employee", employee_id) or
# ("cheque", cheque_id), the latter on "Cartera de cheques" with the cheque named on the leg.
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

    @property
    def administrative_zero(self) -> bool:
        """An OUTLET settlement of $0 without any movement: it closes without a ledger operation (R7-2-Q1)."""
        return without_movements(self.settlement, self.totals) and not self.legs


def without_movements(settlement: Settlement, totals: summary.Totals) -> bool:
    """An OUTLET settlement whose expected amount is $0 and that has nothing loaded: what an administrative zero
    close certifies (the close also checks it publishes no leg)."""
    return (
        settlement.type == service.OUTLET
        and not totals.expected_missing
        and totals.expected == 0
        and totals.cash_total == 0
        and totals.transfers["VERIFIED"] == 0
        and totals.declared_transfers_count == 0
        and totals.cheques == 0
        and totals.unverified_echeqs_count == 0
        and totals.expenses == 0
        and totals.pending == 0
        and totals.remitted == 0
    )


# --- financial state ------------------------------------------------------------------------------


def financial_state(settlement: Settlement) -> str:
    """From the documentary close in effect, never from the ledger operation alone: an administrative zero
    close (R7-2) is a close without one."""
    if settlement.status == "CLOSED":
        return "CLOSED"
    return "BEING_CORRECTED" if settlement.current_closing_id is not None else "NOT_CLOSED"


def administrative_close_in_effect(settlement: Settlement) -> bool:
    return settlement.current_closing_id is not None and settlement.current_close_operation_id is None


def financial_state_detail(settlement: Settlement) -> str:
    state = financial_state(settlement)
    zero = administrative_close_in_effect(settlement)
    if state == "CLOSED":
        if zero:
            return "Rendición cerrada en cero: cierre administrativo, sin movimientos en Cajas."
        return "Rendición cerrada: su cierre está vigente en Cajas."
    if state == "BEING_CORRECTED":
        if zero:
            return (
                "Rendición reabierta para corrección: su cierre anterior fue administrativo en cero y no movió "
                "dinero en Cajas."
            )
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
    token: str


def preview(db: Session, settlement_id: int) -> Preview:
    """What a close would publish now and what stops it, without writing anything."""
    plan = _plan(db, service.get(db, settlement_id))
    blockers = []
    if plan.settlement.status != "OPEN":
        blockers.append(s.Blocker(code="settlement_already_closed", message="La rendición ya está cerrada."))
    if plan.totals.expected_missing:
        blockers.append(s.Blocker(code=EXPECTED_REQUIRED, message=EXPECTED_REQUIRED_MESSAGE))
    if plan.totals.declared_transfers_count:
        blockers.append(
            s.Blocker(
                code="declared_transfers_pending",
                message=f"Hay {plan.totals.declared_transfers_count} transferencias sin verificar ni rechazar.",
            )
        )
    if plan.totals.unverified_echeqs_count:
        blockers.append(
            s.Blocker(
                code="settlement_has_unverified_echeqs",
                message=f"Hay {plan.totals.unverified_echeqs_count} ECHEQ declarados sin verificar.",
            )
        )
    return Preview(plan, blockers, preview_token(db, plan))


def preview_token(db: Session, plan: Plan) -> str:
    """SHA-256 of the settlement state a preview shows: what the user confirms when closing (Enzo's R2-2
    adjustment 1). Any change that could alter the close changes it, even one that keeps the difference
    (moving cash from one box to another, recounting with other notes, a decision on a transfer).

    Built from the rows themselves, sorted by stable keys, never from the order the database returns.
    """
    settlement = plan.settlement
    sid = settlement.id
    lines = db.execute(
        select(
            CashCountLine.denomination_code,
            CashCountLine.denomination_value,
            CashCountLine.quantity,
            CashCountLine.destination_account_id,
        ).where(CashCountLine.settlement_id == sid)
    )
    added = db.execute(
        select(CashAddition.id, CashAddition.amount, CashAddition.destination_account_id).where(
            CashAddition.settlement_id == sid, CashAddition.voided_at.is_(None)
        )
    )
    sent = db.execute(
        select(
            SettlementTransfer.id,
            SettlementTransfer.amount,
            SettlementTransfer.bank_account_id,
            SettlementTransfer.status,
        ).where(SettlementTransfer.settlement_id == sid)
    )
    spent = db.execute(
        select(Expense.id, Expense.category_id, Expense.amount).where(
            Expense.settlement_id == sid, Expense.voided_at.is_(None)
        )
    )
    fueled = db.execute(
        select(
            FuelLoad.expense_id,
            FuelLoad.vehicle_id,
            FuelLoad.employee_id,
            FuelLoad.liters,
            FuelLoad.price_per_liter,
            FuelLoad.odometer_km,
            Expense.amount,
        )
        .join(Expense, Expense.id == FuelLoad.expense_id)
        .where(Expense.settlement_id == sid, Expense.voided_at.is_(None))
    )
    fuel_rows = sorted(
        [eid, vehicle, employee, str(liters), str(price), odometer, service.money(amount)]
        for eid, vehicle, employee, liters, price, odometer, amount in fueled
    )
    totals = plan.totals
    payload: dict[str, Any] = {
        "settlement": sid,
        "type": settlement.type,
        "version": settlement.version,
        "status": settlement.status,
        "close_in_effect": settlement.current_close_operation_id,
        "closing_in_effect": settlement.current_closing_id,
        "business_date": settlement.business_date.isoformat(),
        "employee": settlement.employee_id,
        "outlet": settlement.outlet_id,
        "expected": None if totals.expected_missing else service.money(totals.expected),
        "count": sorted([code, service.money(value), qty, box] for code, value, qty, box in lines),
        "additions": sorted([aid, service.money(amount), box] for aid, amount, box in added),
        "transfers": sorted([tid, service.money(amount), bank, status] for tid, amount, bank, status in sent),
        "expenses": sorted([eid, category, service.money(amount)] for eid, category, amount in spent),
        "pendings": pending_collections.preview_rows(db, sid),
        "cheques": cheques.preview_rows(db, sid),
        "remittances": remittances.preview_rows(db, sid),
        "cash_by_box": sorted([box, service.money(t.total)] for box, t in totals.by_box.items()),
        "verified_by_bank": sorted([bank, service.money(amount)] for bank, amount in plan.verified_by_bank.items()),
        "difference": service.money(plan.difference),
        "legs": sorted([kind, ref, service.money(amount)] for (kind, ref), amount in plan.legs.items()),
    }
    if fuel_rows:  # R5-2: who loaded which vehicle, how many liters at what price; absent when none
        payload["fuel"] = fuel_rows
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- close ----------------------------------------------------------------------------------------


def close(db: Session, actor: User, settlement_id: int, body: s.CloseIn) -> service.Created:
    settlement = _lock(db, settlement_id)
    pendings = pending_collections.lock_for_close(db, settlement)
    locked_cheques = cheques.lock_for_close(db, settlement)
    in_effect = remittances.lock_for_close(db, settlement)
    plan = _plan(db, settlement)
    allocations = _normalize(body.allocations)
    fingerprint = _fingerprint(plan, body.version, allocations, pendings, locked_cheques, in_effect)

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
    if body.preview_token != preview_token(db, plan):
        raise AppError(
            409,
            "settlement_preview_stale",
            "La rendición cambió desde la última vista previa. Actualizá los importes y revisala nuevamente "
            "antes de cerrar.",
            {"settlement_id": settlement.id, "version": settlement.version},
        )
    if plan.totals.expected_missing:
        raise AppError(422, EXPECTED_REQUIRED, EXPECTED_REQUIRED_MESSAGE, {"settlement_id": settlement.id})
    if plan.totals.declared_transfers_count:
        raise AppError(
            409,
            "declared_transfers_pending",
            "Hay transferencias declaradas sin verificar ni rechazar: resolvelas antes de cerrar.",
            {"declared_transfers_count": plan.totals.declared_transfers_count},
        )
    cheques.require_settlement_cheques_verified(plan.totals.unverified_echeqs_count)
    pending_collections.check_against_applied(db, pendings)
    cheques.check_close(db, locked_cheques)
    _check_allocations(db, actor, plan, allocations)
    if not body.confirm:
        raise AppError(422, "confirmation_required", "Confirmá el cierre de la rendición.")

    if plan.administrative_zero:
        return _close_administratively(db, actor, plan, locked_cheques, fingerprint)

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

    pending_collections.publish(db, actor, settlement, pendings, operation.id, settlement.current_close_operation_id)
    cheques.publish(db, actor, settlement, locked_cheques, operation.id, settlement.current_close_operation_id)
    remittances.publish(db, settlement, in_effect, operation.id)
    expenses.publish_fuel(db, settlement, operation.id)
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
    closing_row = SettlementClosing(
        settlement_id=settlement.id,
        settlement_version=settlement.version,
        kind="FINANCIAL",
        operation_id=operation.id,
        reversal_operation_id=reversal_id,
        request_fingerprint=fingerprint,
        closed_by=actor.id,
        closed_at=now,
    )
    db.add(closing_row)
    db.flush()
    previous_close = settlement.current_close_operation_id
    settlement.status = "CLOSED"
    settlement.current_close_operation_id = operation.id
    settlement.current_closing_id = closing_row.id
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


def _close_administratively(
    db: Session, actor: User, plan: Plan, locked_cheques: list[Cheque], fingerprint: str
) -> service.Created:
    """CIERRE ADMINISTRATIVO CERO (Enzo's R7-2-Q1): an OUTLET settlement of $0 without any movement closes
    without a ledger operation and without zero legs; the close, its snapshot, who and when, the version, the
    audit and the history are kept. After a financial close (a correction to $0), that close is reversed and
    nothing replaces it. The database checks the whole load against it at commit (0023)."""
    settlement = plan.settlement
    previous_close = settlement.current_close_operation_id
    reversal_id: int | None = None
    if previous_close is not None:
        reversal = ledger.withdraw_document_operation(
            db,
            actor=actor,
            original_id=previous_close,
            type=CLOSE_TYPE,
            source=ledger.SourceRef(SOURCE_TYPE, settlement.id),
            reason=f"Corrección de la rendición #{settlement.id} a cero: {settlement.reopen_reason or ''}",
            reversal_request_id=reversal_request_id(settlement.id, settlement.version),
        )
        reversal_id = reversal.operation.id
        cheques.withdraw_published(db, actor, settlement, locked_cheques, previous_close, reversal_id)
    now = datetime.now(UTC)
    superseded = db.execute(
        update(SettlementDifference)
        .where(SettlementDifference.settlement_id == settlement.id, SettlementDifference.superseded_at.is_(None))
        .values(superseded_at=now, superseded_by=actor.id)
        .returning(SettlementDifference.id)
    ).scalar_one_or_none()
    snapshot = zero_snapshot(settlement)
    closing_row = SettlementClosing(
        settlement_id=settlement.id,
        settlement_version=settlement.version,
        kind=ADMINISTRATIVE_ZERO,
        operation_id=None,
        reversal_operation_id=reversal_id,
        request_fingerprint=fingerprint,
        snapshot=snapshot,
        closed_by=actor.id,
        closed_at=now,
    )
    db.add(closing_row)
    db.flush()
    settlement.status = "CLOSED"
    settlement.current_close_operation_id = None
    settlement.current_closing_id = closing_row.id
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
            "closing_kind": ADMINISTRATIVE_ZERO,
            "closing_id": closing_row.id,
            "close_operation_id": None,
            "reversal_operation_id": reversal_id,
            "superseded_difference_id": superseded,
            "snapshot": snapshot,
        },
    )
    service.commit(db)
    return service.Created(settlement.id, created=True)


def zero_snapshot(settlement: Settlement) -> dict[str, Any]:
    """What an administrative zero close certifies; the database checks it against the settlement."""
    zero = service.money(ZERO)
    return {
        "type": settlement.type,
        "outlet_id": str(settlement.outlet_id),
        "version": str(settlement.version),
        "business_date": settlement.business_date.isoformat(),
        "expected_amount": zero,
        "cash": zero,
        "additions": zero,
        "transfers_verified": zero,
        "cheques": zero,
        "expenses": zero,
        "pending": zero,
        "remittances": zero,
        "difference": zero,
    }


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
    now = datetime.now(UTC)
    if settlement.current_closing_id is None:  # guaranteed by closed_has_close; keeps mypy honest
        raise AppError(409, "settlement_not_closed", "La rendición no está cerrada.")
    # An administrative zero close is reopened the same way: it stays in effect (with no operation) until the
    # new close, and nothing is reversed because nothing was published (R7-2-Q1).
    db.add(
        SettlementReopening(
            settlement_id=settlement.id,
            from_version=previous,
            to_version=previous + 1,
            closing_id=settlement.current_closing_id,
            close_operation_id=settlement.current_close_operation_id,
            reason=reason,
            reopened_by=actor.id,
            reopened_at=now,
        )
    )
    settlement.status = "OPEN"
    settlement.version = previous + 1
    settlement.reopened_at = now
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
            "closing_in_effect": settlement.current_closing_id,
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


def closing_in_effect(db: Session, settlement: Settlement) -> SettlementClosing | None:
    """The documentary close in effect: financial or administrative zero (R7-2)."""
    return db.get(SettlementClosing, settlement.current_closing_id) if settlement.current_closing_id else None


def reopenings(db: Session, settlement_id: int) -> list[SettlementReopening]:
    return list(
        db.scalars(
            select(SettlementReopening)
            .where(SettlementReopening.settlement_id == settlement_id)
            .order_by(SettlementReopening.from_version)
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
    verified = db.execute(
        select(SettlementTransfer.bank_account_id, func.sum(SettlementTransfer.amount))
        .where(SettlementTransfer.settlement_id == settlement.id, SettlementTransfer.status == "VERIFIED")
        .group_by(SettlementTransfer.bank_account_id)
    )
    plan = Plan(settlement, totals, dict(verified.all()))
    legs: dict[LegKey, Decimal] = {}

    def add(key: LegKey, amount: Decimal) -> None:
        legs[key] = legs.get(key, ZERO) + amount

    add(("account", ledger.system_account_id(db, service.profile(settlement).collections_account)), -totals.expected)
    if totals.remitted and settlement.outlet_id is not None:
        # The boca's remittances already entered their cash boxes: the close consolidates them (R7-3).
        advances = advances_account_of(db, settlement.outlet_id)
        if advances is None:  # a remittance creates it; never missing when there is one
            raise AppError(500, "missing_outlet_advances_account", "Falta la cuenta de entregas de la boca.")
        add(("account", advances), totals.remitted)
    for box_id, box in totals.by_box.items():
        add(("account", box_id), box.total)
    for bank_id, amount in plan.verified_by_bank.items():
        add(("account", bank_id), amount)
    if totals.expenses:
        add(("account", ledger.system_account_id(db, "expense")), totals.expenses)
    if totals.pending:
        add(("account", ledger.system_account_id(db, "pending_collections")), totals.pending)
    for cheque in cheques.justifying(cheques.of(db, settlement.id)):
        add(("cheque", cheque.id), cheque.amount)
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
    portfolio = ledger.system_account_id(db, "cheque_portfolio")
    for (kind, ref), amount in sorted(_all_legs(db, plan, allocations).items()):
        if kind == "account":
            legs.append(ledger.Leg(ref, amount))
        elif kind == "cheque":
            legs.append(ledger.Leg(portfolio, amount, ref))
        else:
            account_id = employee_account_id(db, employee_id=ref, employee_name=names[ref], user_id=actor.id)
            legs.append(ledger.Leg(account_id, amount))
    return legs


def _fingerprint(
    plan: Plan,
    version: int,
    allocations: list[Allocation],
    pendings: list[PendingCollection],
    locked_cheques: list[Cheque],
    in_effect: list[OutletRemittance],
) -> str:
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
    published = sorted([p.id, service.money(p.amount), p.customer_name] for p in pendings if p.voided_at is None)
    if published:  # absent when there are none, so a retry of a close made before R3 keeps its fingerprint
        payload["pendings"] = published
    portfolio = sorted([c.id, service.money(c.amount)] for c in cheques.justifying(locked_cheques))
    if portfolio:  # same: absent when there are none, so a close made before R4 keeps its fingerprint
        payload["cheques"] = portfolio
    consolidated = sorted([r.id, service.money(r.amount)] for r in in_effect)
    if consolidated:  # same: absent when there are none (R7-3)
        payload["remittances"] = consolidated
    if plan.settlement.type != service.DELIVERY:  # absent for delivery, so its earlier closes keep their fingerprint
        payload["type"] = plan.settlement.type
        payload["outlet"] = plan.settlement.outlet_id
        payload["kind"] = ADMINISTRATIVE_ZERO if plan.administrative_zero else "FINANCIAL"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
