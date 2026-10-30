"""The expected amount of a settlement and where it comes from (R9-1, ADR 011, Enzo's R9-1 adjustments).

Every change of the expected amount goes through here and is one event of `expected_amount_events`: a manual load
or correction (PATCH of a delivery or a boca, the amount given when opening it), a Store Sys record of the canonical
contract, and the decision on a pending Store Sys update. The event and the settlement change in one transaction;
`audit_events` keeps its cross-cutting trail.

Authority of a Store Sys record over the settlement it reaches:

| Settlement | Expected in effect | Outcome |
|---|---|---|
| never closed | none | APPLIED |
| never closed | Store Sys, same reference (a later revision) | APPLIED |
| never closed | manual (or before R9-1), same amount | MATCHED: the origin becomes Store Sys |
| never closed | manual, other amount; or Store Sys of another reference | PENDING_CONFIRMATION |
| being corrected | any | PENDING_CONFIRMATION |
| closed | any | REJECTED_CLOSED: nothing changes, never reopened |

A revision older than the newest one received for its reference is stored SUPERSEDED and changes nothing. A pending
update leaves the expected amount as it is until someone accepts it (CONFIRMED) or keeps the amount in effect with a
reason (KEPT); a later Store Sys record for the settlement decides it SUPERSEDED. While a pending update waits, the
expected amount does not change by hand and the settlement does not close.

Idempotency is per record: (STORE_SYS, external_reference, revision) with the same fingerprint returns the event it
already created, as it stands; with another fingerprint it is `external_reference_conflict` and nothing is written.
A refused record stores nothing, so it is evaluated again when it is sent again (a settlement opened since). Each
record is its own transaction.

Lock order: the settlement row (as every load), then its events.
"""

import hashlib
import json
import logging
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError, database_timeout
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.expected import schemas as s
from app.modules.settlements.expected.models import (
    APPLIED,
    CONFIRMED,
    KEPT,
    MANUAL,
    MATCHED,
    PENDING_CONFIRMATION,
    REJECTED_CLOSED,
    SETTING_OUTCOMES,
    STORE_SYS,
    SUPERSEDED,
    ExpectedAmountEvent,
)
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User
from app.platform.outlets.models import Outlet
from app.platform.parties.models import Employee

logger = logging.getLogger(__name__)

MODULE_KEY = "settlements"
DELIVERY = "DELIVERY"
OUTLET = "OUTLET"
ZERO = Decimal("0.00")

PENDING_CODE = "expected_update_pending"
PENDING_MESSAGE = "Store Sys informó otro esperado: aceptalo o mantené el cargado antes de seguir."


def money(amount: Decimal | None) -> str | None:
    return None if amount is None else str(amount.quantize(Decimal("0.01")))


def being_corrected(settlement: Settlement) -> bool:
    return settlement.status == "OPEN" and settlement.current_closing_id is not None


def pending_of(db: Session, settlement_id: int) -> ExpectedAmountEvent | None:
    return db.scalars(
        select(ExpectedAmountEvent).where(
            ExpectedAmountEvent.settlement_id == settlement_id, ExpectedAmountEvent.outcome == PENDING_CONFIRMATION
        )
    ).one_or_none()


def pending_settlements(db: Session, settlement_ids: Sequence[int]) -> set[int]:
    """Which of these settlements have a Store Sys update waiting for a decision (the board's mark, P6)."""
    if not settlement_ids:
        return set()
    return set(
        db.scalars(
            select(ExpectedAmountEvent.settlement_id).where(
                ExpectedAmountEvent.settlement_id.in_(settlement_ids),
                ExpectedAmountEvent.outcome == PENDING_CONFIRMATION,
            )
        )
    )


def require_no_pending(db: Session, settlement: Settlement) -> None:
    if pending_of(db, settlement.id) is not None:
        raise AppError(409, PENDING_CODE, PENDING_MESSAGE, {"settlement_id": settlement.id})


def _event_snapshot(event: ExpectedAmountEvent) -> dict[str, Any]:
    return {
        "event_id": event.id,
        "source": event.source,
        "outcome": event.outcome,
        "external_reference": event.external_reference,
        "revision": event.revision,
        "expected_amount": money(event.expected_amount),
        "previous_amount": money(event.previous_amount),
        "previous_source": event.previous_source,
        "settlement_version": event.settlement_version,
    }


def _new_event(db: Session, actor: User, settlement: Settlement, **fields: Any) -> ExpectedAmountEvent:
    event = ExpectedAmountEvent(
        settlement_id=settlement.id,
        settlement_version=settlement.version,
        previous_amount=settlement.expected_amount,
        previous_source=settlement.expected_source,
        previous_event_id=settlement.expected_event_id,
        received_by=actor.id,
        **fields,
    )
    db.add(event)
    db.flush()
    return event


def _set_in_effect(db: Session, settlement: Settlement, event: ExpectedAmountEvent) -> None:
    settlement.expected_amount = event.expected_amount
    settlement.expected_source = event.source
    settlement.expected_event_id = event.id
    db.flush()


# --- manual ---------------------------------------------------------------------------------------


def set_manual(
    db: Session, actor: User, settlement: Settlement, amount: Decimal | None, reason: str | None
) -> ExpectedAmountEvent | None:
    """Load or correct the expected amount by hand, on a settlement the caller holds locked and OPEN. Nothing happens
    when the amount is the one in effect. Correcting an amount that came from Store Sys takes a reason; from then on
    the manual amount rules and the next Store Sys revision waits for confirmation. Never while an update is pending.
    The caller commits."""
    if amount is not None:
        amount = amount.quantize(Decimal("0.01"))
    if amount == settlement.expected_amount and (amount is None or settlement.expected_source == MANUAL):
        return None
    if amount is None and settlement.type == DELIVERY:
        raise AppError(422, "invalid_amount", "El importe esperado de una rendición de reparto no se puede borrar.")
    require_no_pending(db, settlement)
    reason = (reason or "").strip() or None
    if settlement.expected_source == STORE_SYS and reason is None:
        raise AppError(
            422,
            "expected_reason_required",
            "Indicá el motivo para corregir el esperado informado por Store Sys.",
            {"settlement_id": settlement.id},
        )
    event = _new_event(db, actor, settlement, source=MANUAL, outcome=MANUAL, expected_amount=amount, reason=reason)
    _set_in_effect(db, settlement, event)
    record_change(
        db,
        "settlements.expected.manual",
        action="update",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before={"expected_amount": money(event.previous_amount), "expected_source": event.previous_source},
        after={**_event_snapshot(event), "has_reason": reason is not None},
    )
    return event


# --- Store Sys records ----------------------------------------------------------------------------


@dataclass(frozen=True)
class RecordError(Exception):
    """A record that stores nothing: its code and message go back in its result."""

    code: str
    message: str


def _validate(raw: dict[str, Any]) -> s.StoreSysRecordIn:
    try:
        record = s.StoreSysRecordIn.model_validate(raw)
    except ValidationError as exc:
        if all(error["loc"][:1] == ("expected_amount",) for error in exc.errors()):
            raise RecordError("invalid_expected_amount", "El importe esperado no es válido.") from exc
        raise RecordError("invalid_record", "El registro no cumple el contrato de Store Sys.") from exc
    if record.settlement_type == DELIVERY:
        if record.employee_store_code is None or record.outlet_code is not None:
            raise RecordError("invalid_record", "Un registro de reparto lleva el código Store Sys del empleado.")
        if record.expected_amount <= 0:
            raise RecordError(
                "invalid_expected_amount", "El esperado de una rendición de reparto tiene que ser mayor a 0."
            )
    else:
        if record.outlet_code is None or record.employee_store_code is not None:
            raise RecordError("invalid_record", "Un registro de boca lleva el código de la boca.")
        if record.expected_amount < 0:
            raise RecordError("invalid_expected_amount", "El esperado de una rendición de boca no puede ser negativo.")
    if record.business_date > business_today():
        raise RecordError("invalid_record", "La fecha del registro no puede ser futura.")
    return record


def fingerprint(record: s.StoreSysRecordIn) -> str:
    """What the record means: type, date, who, number of the day and amount (never its informative instant)."""
    canonical = {
        "settlement_type": record.settlement_type,
        "business_date": record.business_date.isoformat(),
        "employee_store_code": record.employee_store_code,
        "outlet_code": record.outlet_code,
        "sequence_number": record.sequence_number,
        "expected_amount": money(record.expected_amount),
    }
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _stored(db: Session, record: s.StoreSysRecordIn, print_: str) -> ExpectedAmountEvent | None:
    """The event of this key, when the record was received before; another content for the same key is a conflict."""
    event = db.scalars(
        select(ExpectedAmountEvent).where(
            ExpectedAmountEvent.source == STORE_SYS,
            ExpectedAmountEvent.external_reference == record.external_reference,
            ExpectedAmountEvent.revision == record.revision,
        )
    ).one_or_none()
    if event is not None and event.request_fingerprint != print_:
        raise RecordError(
            "external_reference_conflict",
            "Store Sys ya informó esta referencia y revisión con otro contenido: no se registró.",
        )
    return event


def _owner_scope(db: Session, record: s.StoreSysRecordIn) -> tuple[Any, ...]:
    """The settlements of the record's person or boca, type and day; never resolved by name."""
    if record.settlement_type == DELIVERY:
        employee_id = db.scalar(
            select(Employee.id).where(Employee.external_store_id == record.employee_store_code, Employee.active)
        )
        if employee_id is None:
            raise RecordError("invalid_external_identity", "No hay un empleado activo con ese código Store Sys.")
        owner = Settlement.employee_id == employee_id
    else:
        code = (record.outlet_code or "").strip().upper()
        outlet_id = db.scalar(select(Outlet.id).where(Outlet.code == code, Outlet.active))
        if outlet_id is None:
            raise RecordError("invalid_external_identity", "No hay una boca activa con ese código.")
        owner = Settlement.outlet_id == outlet_id
    return (Settlement.type == record.settlement_type, Settlement.business_date == record.business_date, owner)


def _target(db: Session, record: s.StoreSysRecordIn) -> tuple[int, int | None]:
    """The settlement the record reaches and the newest revision received for its reference. A reference stays with
    the settlement its first revision reached."""
    scope = _owner_scope(db, record)
    bound = db.execute(
        select(ExpectedAmountEvent.settlement_id, ExpectedAmountEvent.revision)
        .where(
            ExpectedAmountEvent.source == STORE_SYS,
            ExpectedAmountEvent.external_reference == record.external_reference,
        )
        .order_by(ExpectedAmountEvent.revision.desc())
        .limit(1)
    ).first()
    query = select(Settlement.id).where(*scope)
    if record.sequence_number is not None:
        query = query.where(Settlement.sequence_number == record.sequence_number)
    candidates = list(db.scalars(query.order_by(Settlement.sequence_number)))
    if bound is not None:
        if bound[0] not in candidates:
            raise RecordError(
                "external_reference_conflict",
                "Esta referencia de Store Sys ya corresponde a otra rendición: no se registró.",
            )
        return bound[0], bound[1]
    if not candidates:
        raise RecordError(
            "settlement_not_found", "No hay una rendición de ese día para ese código: reenvialo cuando exista."
        )
    if len(candidates) > 1:
        raise RecordError(
            "ambiguous_settlement",
            f"Hay {len(candidates)} rendiciones ese día: indicá el número de rendición.",
        )
    return candidates[0], None


def _authority(settlement: Settlement, record: s.StoreSysRecordIn, current_reference: str | None) -> str:
    if settlement.status == "CLOSED":
        return REJECTED_CLOSED
    if being_corrected(settlement):
        return PENDING_CONFIRMATION
    if settlement.expected_amount is None:
        return APPLIED
    if settlement.expected_source == STORE_SYS:
        return APPLIED if current_reference == record.external_reference else PENDING_CONFIRMATION
    return MATCHED if settlement.expected_amount == record.expected_amount else PENDING_CONFIRMATION


def _receive(
    db: Session, actor: User, record: s.StoreSysRecordIn, client_request_id: UUID
) -> tuple[ExpectedAmountEvent, Settlement]:
    print_ = fingerprint(record)
    stored = _stored(db, record, print_)
    if stored is not None:
        return stored, db.get_one(Settlement, stored.settlement_id)
    settlement_id, newest = _target(db, record)
    settlement = db.scalars(select(Settlement).where(Settlement.id == settlement_id).with_for_update()).one()
    # After the lock: a concurrent copy of this record may have just been stored.
    stored = _stored(db, record, print_)
    if stored is not None:
        return stored, settlement
    current = db.get(ExpectedAmountEvent, settlement.expected_event_id) if settlement.expected_event_id else None
    if newest is not None and record.revision < newest:
        outcome = SUPERSEDED
    else:
        outcome = _authority(settlement, record, current.external_reference if current else None)
    fields: dict[str, Any] = {
        "source": STORE_SYS,
        "outcome": outcome,
        "external_reference": record.external_reference,
        "revision": record.revision,
        "request_fingerprint": print_,
        "reported_at": record.reported_at,
        "client_request_id": client_request_id,
        "expected_amount": record.expected_amount,
    }
    if outcome not in (SUPERSEDED, REJECTED_CLOSED):
        # A newer Store Sys record replaces the update nobody decided yet.
        waiting = pending_of(db, settlement.id)
        if waiting is not None:
            waiting.outcome = SUPERSEDED
            waiting.decided_by = actor.id
            waiting.decided_at = datetime.now(UTC)
            db.flush()
            fields["supersedes_event_id"] = waiting.id
    event = _new_event(db, actor, settlement, **fields)
    if outcome in SETTING_OUTCOMES:
        _set_in_effect(db, settlement, event)
    record_change(
        db,
        "settlements.expected.imported",
        action="import",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before={"expected_amount": money(event.previous_amount), "expected_source": event.previous_source},
        after={**_event_snapshot(event), "supersedes_event_id": event.supersedes_event_id},
    )
    return event, settlement


def _result(index: int, raw: dict[str, Any], **fields: Any) -> s.RecordResultOut:
    reference = raw.get("external_reference")
    revision = raw.get("revision")
    return s.RecordResultOut(
        index=index,
        external_reference=reference.strip()[:100] if isinstance(reference, str) else None,
        revision=revision if isinstance(revision, int) and not isinstance(revision, bool) else None,
        **fields,
    )


def import_records(db: Session, actor: User, body: s.StoreSysImportIn) -> s.StoreSysImportOut:
    """One result per record, in order. Each record is atomic and independent of the others."""
    results: list[s.RecordResultOut] = []
    seen: set[tuple[str, int]] = set()
    for index, raw in enumerate(body.records):
        try:
            record = _validate(raw)
            key = (record.external_reference, record.revision)
            if key in seen:
                raise RecordError("duplicate_in_batch", "Esta referencia y revisión ya vino antes en el mismo envío.")
            seen.add(key)
            event, settlement = _receive_committed(db, actor, record, body.client_request_id)
        except RecordError as refused:
            db.rollback()
            results.append(_result(index, raw, outcome=None, code=refused.code, message=refused.message))
            continue
        results.append(
            _result(
                index,
                raw,
                outcome=event.outcome,
                settlement=s.SettlementRefOut(
                    type=settlement.type,
                    business_date=settlement.business_date,
                    sequence_number=settlement.sequence_number,
                ),
            )
        )
    stored = [r for r in results if r.outcome is not None]
    return s.StoreSysImportOut(
        results=results,
        counts=s.ImportCounts(
            received=len(results),
            stored=len(stored),
            refused=len(results) - len(stored),
            by_outcome=dict(Counter(r.outcome for r in stored if r.outcome is not None)),
        ),
    )


def _receive_committed(
    db: Session, actor: User, record: s.StoreSysRecordIn, client_request_id: UUID
) -> tuple[ExpectedAmountEvent, Settlement]:
    """One record in its own transaction. A concurrent copy that won the key is answered as a retry."""
    try:
        try:
            event, settlement = _receive(db, actor, record, client_request_id)
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            if (
                getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
                != "uq_expected_amount_events_store_key"
            ):
                raise
            event, settlement = _receive(db, actor, record, client_request_id)
            db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise RecordError(timeout.code, timeout.message) from exc
        # A rule only the database saw (the settlement closed under a concurrent close, for example).
        logger.warning("store_sys_record_rejected", extra={"sqlstate": getattr(exc.orig, "sqlstate", None)})
        raise RecordError(
            "invalid_settlement_change", "El registro no cumple las reglas de la rendición y no se aplicó."
        ) from exc
    return event, settlement


# --- decisions ------------------------------------------------------------------------------------


def _lock_pending(db: Session, actor: User, event_id: int) -> tuple[ExpectedAmountEvent, Settlement]:
    settlement_id = db.scalar(select(ExpectedAmountEvent.settlement_id).where(ExpectedAmountEvent.id == event_id))
    if settlement_id is None:
        raise AppError(404, "expected_update_not_found", "La actualización de Store Sys no existe.")
    settlement = db.scalars(select(Settlement).where(Settlement.id == settlement_id).with_for_update()).one()
    event = db.get_one(ExpectedAmountEvent, event_id)
    if event.outcome != PENDING_CONFIRMATION:
        raise AppError(
            409,
            "expected_update_already_decided",
            "Esta actualización de Store Sys ya fue resuelta.",
            {"event_id": event.id, "outcome": event.outcome},
        )
    if settlement.status != "OPEN":
        raise AppError(409, "settlement_closed", "La rendición está cerrada: no admite cambios en su carga.")
    if being_corrected(settlement) and not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(
            403, "forbidden", "Resolver una actualización de una rendición en corrección requiere un administrador."
        )
    return event, settlement


def accept(db: Session, actor: User, event_id: int) -> int:
    """The Store Sys amount becomes the expected (CONFIRMED). Returns the settlement."""
    event, settlement = _lock_pending(db, actor, event_id)
    previous = {"expected_amount": money(settlement.expected_amount), "expected_source": settlement.expected_source}
    event.outcome = CONFIRMED
    event.decided_by = actor.id
    event.decided_at = datetime.now(UTC)
    db.flush()
    _set_in_effect(db, settlement, event)
    record_change(
        db,
        "settlements.expected.confirmed",
        action="confirm",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before=previous,
        after=_event_snapshot(event),
    )
    _commit(db)
    return settlement.id


def keep(db: Session, actor: User, event_id: int, reason: str) -> int:
    """The expected amount in effect stays (KEPT), with a reason. Returns the settlement."""
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo para mantener el esperado cargado.")
    event, settlement = _lock_pending(db, actor, event_id)
    event.outcome = KEPT
    event.decided_by = actor.id
    event.decided_at = datetime.now(UTC)
    event.decision_reason = reason
    db.flush()
    record_change(
        db,
        "settlements.expected.kept",
        action="keep",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before={"expected_amount": money(settlement.expected_amount), "expected_source": settlement.expected_source},
        after=_event_snapshot(event),
    )
    _commit(db)
    return settlement.id


def _commit(db: Session) -> None:
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        raise AppError(409, "invalid_settlement_change", "El cambio no cumple las reglas de la rendición.") from exc


# --- reading --------------------------------------------------------------------------------------


def _names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    rows = db.execute(select(User.id, User.full_name).where(User.id.in_(ids)))
    return dict(rows.tuples().all())


def origin(db: Session, settlement: Settlement) -> s.ExpectedOriginOut | None:
    if settlement.expected_amount is None and settlement.expected_event_id is None:
        return None
    if settlement.expected_event_id is None:
        return s.ExpectedOriginOut(source=MANUAL, legacy=True)
    event = db.get_one(ExpectedAmountEvent, settlement.expected_event_id)
    actor = event.decided_by if event.outcome == CONFIRMED else event.received_by
    return s.ExpectedOriginOut(
        source=event.source,
        legacy=False,
        external_reference=event.external_reference,
        revision=event.revision,
        set_at=event.decided_at if event.outcome == CONFIRMED else event.received_at,
        set_by_name=_names(db, {actor} if actor else set()).get(actor) if actor else None,
    )


def pending_out(db: Session, settlement: Settlement) -> s.ExpectedPendingOut | None:
    event = pending_of(db, settlement.id)
    if event is None:
        return None
    # A Store Sys event always carries them (fields_by_source).
    assert event.expected_amount is not None
    assert event.external_reference is not None
    assert event.revision is not None
    return s.ExpectedPendingOut(
        event_id=event.id,
        expected_amount=event.expected_amount,
        current_amount=settlement.expected_amount,
        external_reference=event.external_reference,
        revision=event.revision,
        received_at=event.received_at,
        requires_admin=being_corrected(settlement),
    )


def history(db: Session, settlement: Settlement) -> s.ExpectedHistoryOut:
    events = list(
        db.scalars(
            select(ExpectedAmountEvent)
            .where(ExpectedAmountEvent.settlement_id == settlement.id)
            .order_by(ExpectedAmountEvent.received_at, ExpectedAmountEvent.id)
        )
    )
    names = _names(db, {e.received_by for e in events} | {e.decided_by for e in events if e.decided_by})
    return s.ExpectedHistoryOut(
        settlement_id=settlement.id,
        events=[
            s.ExpectedEventOut(
                id=e.id,
                source=e.source,
                outcome=e.outcome,
                expected_amount=e.expected_amount,
                previous_amount=e.previous_amount,
                previous_source=e.previous_source,
                external_reference=e.external_reference,
                revision=e.revision,
                reported_at=e.reported_at,
                settlement_version=e.settlement_version,
                reason=e.reason,
                received_by_name=names.get(e.received_by),
                received_at=e.received_at,
                decided_by_name=names.get(e.decided_by) if e.decided_by else None,
                decided_at=e.decided_at,
                decision_reason=e.decision_reason,
                in_effect=e.id == settlement.expected_event_id,
            )
            for e in events
        ],
        origin=origin(db, settlement),
        pending=pending_out(db, settlement),
    )


def closing_fields(db: Session, settlement: Settlement) -> dict[str, Any]:
    """What a close freezes of the expected amount it was made against (R9-1)."""
    event = db.get(ExpectedAmountEvent, settlement.expected_event_id) if settlement.expected_event_id else None
    return {
        "expected_amount": settlement.expected_amount,
        "expected_source": settlement.expected_source,
        "expected_event_id": settlement.expected_event_id,
        "expected_reference": event.external_reference if event else None,
        "expected_revision": event.revision if event else None,
    }
