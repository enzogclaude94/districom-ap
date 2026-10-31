"""Settlements while they are loaded (R2-1): creation, reading, corrections and the cash count.

Delivery settlements are created here; boca de expendio settlements (OUTLET, R7-2) in
`outlet_settlements.service`. Everything else (count, additions, transfers, expenses, cheques, close) is
the same domain for both, and what differs by type is its `TypeProfile`.

Every write locks the settlement row and requires it to be OPEN; the database refuses the same changes
on a CLOSED settlement whoever writes (migration 0007). Each public function commits its own
transaction and audits what changed.

Sequence: the n-th settlement of a driver on a day gets `sequence_number = n`. It is generated under
a transaction-level advisory lock on (type, driver, day), so two concurrent creations wait for each
other and get 1 and 2; the unique constraint on (type, employee_id, business_date, sequence_number)
stays as the last guard.
"""

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError, database_timeout
from app.core.logging import db_error_fields
from app.modules.settlements.delivery import denominations, idempotency
from app.modules.settlements.delivery import schemas as s
from app.modules.settlements.delivery.models import CashCountLine, Settlement
from app.modules.settlements.expected import service as expected
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.parties.models import Employee, EmployeeFunction
from app.platform.parties.service import has_function

logger = logging.getLogger(__name__)

DRIVER_FUNCTION = "settlements.delivery_driver"
DELIVERY = "DELIVERY"
OUTLET = "OUTLET"


@dataclass(frozen=True)
class TypeProfile:
    """What a settlement type allows, and where its close takes the expected amount from (R7-2, ADR 009)."""

    # System account the close takes the expected amount out of.
    collections_account: str
    # Pending collections (R3) and fuel expenses (R5) are loaded only where the type allows them (Enzo's D4).
    pending_collections: bool
    fuel: bool


PROFILES = {
    DELIVERY: TypeProfile("delivery_collections", pending_collections=True, fuel=True),
    OUTLET: TypeProfile("outlet_collections", pending_collections=False, fuel=False),
}


def profile(settlement: Settlement) -> TypeProfile:
    return PROFILES[settlement.type]


@dataclass(frozen=True)
class Created:
    """A row created by a user action, or the earlier one when the request was a retry."""

    id: int
    created: bool


# --- shared ---------------------------------------------------------------------------------------


def lock_open(db: Session, settlement_id: int) -> Settlement:
    """The settlement, locked until commit; 409 unless it is OPEN."""
    settlement = db.scalars(select(Settlement).where(Settlement.id == settlement_id).with_for_update()).one_or_none()
    if settlement is None:
        raise not_found()
    if settlement.status != "OPEN":
        raise AppError(
            409,
            "settlement_closed",
            "La rendición está cerrada: no admite cambios en su carga.",
            {"settlement_id": settlement.id, "status": settlement.status},
        )
    return settlement


def get(db: Session, settlement_id: int) -> Settlement:
    settlement = db.get(Settlement, settlement_id)
    if settlement is None:
        raise not_found()
    return settlement


def require_type(db: Session, settlement_id: int, type: str) -> int:
    """The id, when that settlement exists and is of that type: a boca settlement is never reached through
    the delivery endpoints, nor the other way round (404, as if it did not exist there)."""
    found = db.scalar(select(Settlement.type).where(Settlement.id == settlement_id))
    if found != type:
        raise not_found()
    return settlement_id


def not_found() -> AppError:
    return AppError(404, "settlement_not_found", "La rendición no existe.")


def commit(db: Session) -> None:
    """Commit; a rule only the database saw (a concurrent close, for example) is 409, never a raw error."""
    try:
        db.commit()
    except DBAPIError as exc:
        db.rollback()
        if (timeout := database_timeout(exc)) is not None:
            raise timeout from exc
        logger.warning("settlement_commit_rejected", extra=db_error_fields(exc))
        raise AppError(
            409,
            "invalid_settlement_change",
            "El cambio no cumple las reglas de la rendición y no se registró.",
        ) from exc


def money(amount: Decimal) -> str:
    return str(amount.quantize(Decimal("0.01")))


def require_cash_box(db: Session, account_id: int) -> None:
    if account_id not in {box.account_id for box in denominations.cash_boxes(db).values()}:
        raise AppError(422, "invalid_destination", "El destino del efectivo tiene que ser una caja activa.")


# --- drivers --------------------------------------------------------------------------------------


def drivers(db: Session, on: date) -> list[Employee]:
    """Active employees who hold the driver function on that day: the only ones a settlement can be for."""
    holding = select(EmployeeFunction.employee_id).where(
        EmployeeFunction.function_code == DRIVER_FUNCTION,
        EmployeeFunction.valid_from <= on,
        or_(EmployeeFunction.valid_to.is_(None), EmployeeFunction.valid_to >= on),
    )
    return list(
        db.scalars(
            select(Employee).where(Employee.active.is_(True), Employee.id.in_(holding)).order_by(Employee.full_name)
        )
    )


def employees(db: Session) -> list[Employee]:
    """Active employees, whatever their function: who can absorb part of a difference at close."""
    return list(db.scalars(select(Employee).where(Employee.active.is_(True)).order_by(Employee.full_name)))


def _require_driver(db: Session, employee_id: int, on: date) -> Employee:
    employee = db.get(Employee, employee_id)
    if employee is None:
        raise AppError(422, "employee_not_found", "El empleado no existe.")
    if not employee.active or not has_function(db, employee_id, DRIVER_FUNCTION, on):
        raise AppError(
            422,
            "employee_not_driver",
            f"{employee.full_name} no figura como repartidor el {on:%d/%m/%Y}.",
            {"employee_id": employee_id},
        )
    return employee


# --- settlements ----------------------------------------------------------------------------------


def create(db: Session, actor: User, body: s.SettlementIn) -> Created:
    notes = (body.notes or "").strip() or None
    reference = (body.external_reference or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        "settlement",
        {
            "type": DELIVERY,
            "business_date": body.business_date.isoformat(),
            "employee_id": body.employee_id,
            "expected_amount": money(body.expected_amount) if body.expected_amount is not None else None,
            "external_reference": reference,
            "notes": notes,
        },
    )
    # A retry is answered before anything else is validated: it must get the same result as the first time.
    existing = db.scalars(select(Settlement).where(Settlement.client_request_id == body.client_request_id)).first()
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "settlement_id", existing.id)
        return Created(existing.id, created=False)

    if body.business_date > business_today():
        raise AppError(422, "future_business_date", "La fecha de la rendición no puede ser futura.")
    _require_driver(db, body.employee_id, body.business_date)

    lock_key = f"settlements.sequence:{DELIVERY}:{body.employee_id}:{body.business_date.isoformat()}"
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(lock_key, 0))))
    # After the lock: READ COMMITTED sees every settlement committed by whoever held it before.
    last = db.scalar(
        select(func.max(Settlement.sequence_number)).where(
            Settlement.type == DELIVERY,
            Settlement.employee_id == body.employee_id,
            Settlement.business_date == body.business_date,
        )
    )
    settlement = Settlement(
        type=DELIVERY,
        business_date=body.business_date,
        employee_id=body.employee_id,
        sequence_number=(last or 0) + 1,
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
            return Created(winner.id, created=False)
        raise
    # A delivery may open without its expected amount (Enzo's R9-1 adjustment 1); one given is its first event.
    expected.set_manual(db, actor, settlement, body.expected_amount, None)

    record_change(
        db,
        "settlements.settlement.created",
        action="create",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before=None,
        after=snapshot(settlement),
    )
    commit(db)
    return Created(settlement.id, created=True)


def update(db: Session, actor: User, settlement_id: int, body: s.SettlementPatch) -> None:
    settlement = lock_open(db, settlement_id)
    before = snapshot(settlement)
    fields = body.model_fields_set
    if "expected_amount" in fields:
        # Every change is an event (Enzo's R9-1 adjustment 5); a boca's may be cleared (D2), a delivery's not.
        expected.set_manual(db, actor, settlement, body.expected_amount, body.expected_reason)
    if "external_reference" in fields:
        settlement.external_reference = (body.external_reference or "").strip() or None
    if "notes" in fields:
        settlement.notes = (body.notes or "").strip() or None
    after = snapshot(settlement)
    if before != after:
        record_change(
            db,
            "settlements.settlement.updated",
            action="update",
            user_id=actor.id,
            entity_type="settlement",
            entity_id=settlement.id,
            before=before,
            after=after,
        )
    commit(db)


def list_settlements(
    db: Session,
    *,
    date_from: date | None,
    date_to: date | None,
    employee_id: int | None,
    status: str | None,
    limit: int,
    offset: int,
    financial_state: str | None = None,
) -> tuple[Sequence[tuple[Settlement, str]], int]:
    if date_from and date_to and date_from > date_to:
        raise AppError(422, "invalid_date_range", "La fecha desde no puede ser posterior a la fecha hasta.")
    query = (
        select(Settlement, Employee.full_name)
        .join(Employee, Employee.id == Settlement.employee_id)
        .where(Settlement.type == DELIVERY)
    )
    if date_from:
        query = query.where(Settlement.business_date >= date_from)
    if date_to:
        query = query.where(Settlement.business_date <= date_to)
    if employee_id is not None:
        query = query.where(Settlement.employee_id == employee_id)
    if status is not None:
        query = query.where(Settlement.status == status)
    query = query.where(*financial_state_filter(financial_state))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.execute(
        query.order_by(Settlement.business_date.desc(), Employee.full_name, Settlement.sequence_number, Settlement.id)
        .limit(limit)
        .offset(offset)
    )
    return list(rows), total


def financial_state_filter(financial_state: str | None) -> list[ColumnElement[bool]]:
    """The financial state is read from the documentary close in effect (R7-2): an administrative zero
    close has none in the ledger and is still a close."""
    if financial_state == "CLOSED":
        return [Settlement.status == "CLOSED"]
    if financial_state == "NOT_CLOSED":
        return [Settlement.status == "OPEN", Settlement.current_closing_id.is_(None)]
    if financial_state == "BEING_CORRECTED":
        return [Settlement.status == "OPEN", Settlement.current_closing_id.is_not(None)]
    return []


def same_day(db: Session, settlement: Settlement) -> list[int]:
    """Other settlements of the same driver (or boca) and day."""
    return list(
        db.scalars(
            select(Settlement.id)
            .where(
                Settlement.type == settlement.type,
                Settlement.employee_id.is_not_distinct_from(settlement.employee_id),
                Settlement.outlet_id.is_not_distinct_from(settlement.outlet_id),
                Settlement.business_date == settlement.business_date,
                Settlement.id != settlement.id,
            )
            .order_by(Settlement.sequence_number)
        )
    )


def snapshot(settlement: Settlement) -> dict[str, Any]:
    result = {
        "type": settlement.type,
        "business_date": settlement.business_date.isoformat(),
        "employee_id": settlement.employee_id,
        "sequence_number": settlement.sequence_number,
        "expected_amount": money(settlement.expected_amount) if settlement.expected_amount is not None else None,
        "external_reference": settlement.external_reference,
        "has_notes": settlement.notes is not None,
        "status": settlement.status,
    }
    if settlement.outlet_id is not None:
        result["outlet_id"] = settlement.outlet_id
    return result


# --- cash count -----------------------------------------------------------------------------------


def replace_count(db: Session, actor: User, settlement_id: int, body: s.CountIn) -> None:
    """Replace the whole count of an OPEN settlement; the previous one stays in the audit trail.

    A line keeps the code and value of its denomination. A denomination already in this count keeps
    its stored value even if the configuration changed; a new line needs an active denomination.
    """
    settlement = lock_open(db, settlement_id)
    current = list(
        db.scalars(select(CashCountLine).where(CashCountLine.settlement_id == settlement.id).order_by(CashCountLine.id))
    )
    previous = {line.denomination_code: line.denomination_value for line in current}
    config = denominations.by_code(db)
    lines = [line for line in body.lines if line.quantity > 0]
    codes = [line.denomination_code for line in lines]
    if len(set(codes)) != len(codes):
        raise AppError(422, "duplicate_denomination", "Una denominación aparece más de una vez en el conteo.")
    new_lines = []
    for line in lines:
        value = previous.get(line.denomination_code)
        if value is None:
            denomination = config.get(line.denomination_code)
            if denomination is None or not denomination.active:
                raise AppError(
                    422,
                    "invalid_denomination",
                    f"La denominación {line.denomination_code} no existe o está desactivada.",
                    {"denomination_code": line.denomination_code},
                )
            value = denomination.value
        require_cash_box(db, line.destination_account_id)
        new_lines.append(
            CashCountLine(
                settlement_id=settlement.id,
                denomination_code=line.denomination_code,
                denomination_value=value,
                quantity=line.quantity,
                destination_account_id=line.destination_account_id,
                created_by=actor.id,
            )
        )
    before = sorted((_line_snapshot(line) for line in current), key=lambda line: line["denomination_code"])
    after = sorted((_line_snapshot(line) for line in new_lines), key=lambda line: line["denomination_code"])
    if before == after:
        commit(db)  # nothing changed: just release the lock
        return
    for old in current:
        db.delete(old)
    db.flush()
    db.add_all(new_lines)
    record_change(
        db,
        "settlements.cash_count.replaced",
        action="update",
        user_id=actor.id,
        entity_type="settlement",
        entity_id=settlement.id,
        before={"lines": before},
        after={"lines": after},
    )
    settlement.updated_at = datetime.now(UTC)
    commit(db)


def count_lines(db: Session, settlement_id: int) -> list[CashCountLine]:
    return list(
        db.scalars(
            select(CashCountLine)
            .where(CashCountLine.settlement_id == settlement_id)
            .order_by(CashCountLine.denomination_value.desc(), CashCountLine.denomination_code)
        )
    )


def _line_snapshot(line: CashCountLine) -> dict[str, Any]:
    return {
        "denomination_code": line.denomination_code,
        "denomination_value": money(line.denomination_value),
        "quantity": line.quantity,
        "destination_account_id": line.destination_account_id,
    }
