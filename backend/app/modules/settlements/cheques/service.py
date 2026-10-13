"""Cheques of R4-1: the load in a settlement, an ECHEQ's verification, what each close publishes, and the
administrative entries (opening balance, partner contribution).

Load (operator, settlement OPEN, including a correction):
- creation is idempotent by `client_request_id`, like every load of a settlement;
- a physical cheque is received only when the operator confirms having it in hand (who and when are kept);
  an ECHEQ is DECLARED until someone verifies it, and an unverified one never justifies the settlement and
  blocks its close (`settlement_has_unverified_echeqs`, Enzo's R4 decision D-K);
- every field is corrected (with a CORRECTED event) until the cheque had a financial effect; from then on
  its amount, identity, payable dates and who delivered it never change (`cheque_published_immutable`, and
  the database refuses it too). Descriptive data is corrected by an admin with a reason;
- identity: `identity.py`. A duplicate is refused; a possible duplicate needs a confirmation with a reason.

Close (`delivery.closing`): with the settlement and its cheques locked, `publish` freezes in
`settlement_closing_cheques` the cheques the new close puts in the portfolio and records PUBLISHED /
WITHDRAWN. A cheque marked for removal stays in the portfolio until that close, and only a cheque still
in the portfolio can be removed (`cheque_has_activity`).

Lock order, always: the settlement row, its pendings, its cheques by id (then, in the ledger, the accounts).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.core.errors import AppError
from app.modules.settlements.cheques import identity, terms
from app.modules.settlements.cheques import schemas as s
from app.modules.settlements.cheques.models import Cheque, ChequeEvent, SettlementClosingCheque
from app.modules.settlements.cheques.state import justifies
from app.modules.settlements.delivery import idempotency, service
from app.modules.settlements.delivery.models import Settlement
from app.modules.settlements.ledger import service as ledger
from app.modules.settlements.pending.service import active_employee
from app.platform.audit.service import record_change
from app.platform.auth.access import has_module_access
from app.platform.auth.models import User
from app.platform.banks import service as banks

MODULE_KEY = "settlements"
SOURCE_TYPE = "cheque"
CLOSE_TYPE = "SETTLEMENT_CLOSE"
ENTRY_TYPES = {"OPENING_BALANCE": "CHEQUE_OPENING_BALANCE", "OWNER_CONTRIBUTION": "CHEQUE_OWNER_CONTRIBUTION"}
ZERO = Decimal("0.00")
_IDENTITY_INDEXES = ("uq_cheques_instrument_key", "uq_cheques_echeq_external_id")

# Fields of the instrument that never change once it had a financial effect (Enzo's R4 decisions).
FINANCIAL_FIELDS = (
    "payment_type",
    "amount",
    "issuer_bank_id",
    "branch_code",
    "account_ref",
    "cheque_number",
    "echeq_external_id",
    "issue_date",
    "payment_date",
    "delivered_by_name",
)


@dataclass(frozen=True)
class Instrument:
    """The cleaned data of an instrument, checked for its kind and type, with its identity."""

    kind: str
    payment_type: str
    amount: Decimal
    issuer_bank_id: int
    branch_code: str | None
    account_ref: str | None
    cheque_number: str
    echeq_external_id: str | None
    issue_date: date
    payment_date: date | None
    drawer_name: str
    drawer_tax_id: str | None
    delivered_by_name: str
    notes: str | None
    identity: identity.Identity

    def snapshot(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "payment_type": self.payment_type,
            "amount": service.money(self.amount),
            "issuer_bank_id": self.issuer_bank_id,
            "branch_code": self.branch_code,
            "account_ref": self.account_ref,
            "cheque_number": self.cheque_number,
            "echeq_external_id": self.echeq_external_id,
            "issue_date": self.issue_date.isoformat(),
            "payment_date": self.payment_date.isoformat() if self.payment_date else None,
            "drawer_name": self.drawer_name,
            "drawer_tax_id": self.drawer_tax_id,
            "delivered_by_name": self.delivered_by_name,
            "notes": self.notes,
        }


# --- load in a settlement -------------------------------------------------------------------------


def create(db: Session, actor: User, settlement_id: int, body: s.ChequeIn) -> service.Created:
    data = _clean(body.model_dump())
    fingerprint = idempotency.fingerprint(
        actor.id,
        "cheque",
        {
            "settlement_id": settlement_id,
            **data,
            "received_by_employee_id": body.received_by_employee_id,
            "physical_receipt_confirmed": body.physical_receipt_confirmed,
        },
    )
    existing = _by_request_id(db, body.client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "cheque_id", existing.id)
        return service.Created(existing.id, created=False)

    settlement = service.lock_open(db, settlement_id)
    _check_receipt_confirmation(body.kind, body.physical_receipt_confirmed)
    instrument = _instrument(db, data, received_on=settlement.business_date)
    receiver = settlement.employee_id
    if body.received_by_employee_id is not None and body.received_by_employee_id != settlement.employee_id:
        receiver = active_employee(db, body.received_by_employee_id).id
    confirmation = _check_identity(db, instrument, None, body)
    cheque = _new_cheque(
        actor,
        instrument,
        origin_type="SETTLEMENT",
        settlement_id=settlement.id,
        received_on=settlement.business_date,
        received_by=receiver,
        verification="DECLARED",
        request=(body.client_request_id, fingerprint),
    )
    winner = _insert(db, cheque, body.client_request_id, fingerprint)
    if winner is not None:
        return winner
    after = {**_snapshot(cheque), **({"duplicate_confirmation": confirmation} if confirmation else {})}
    record_event(db, actor, cheque, "RECEIVED", None, after, settlement=settlement)
    _audit(db, actor, cheque, "settlements.cheque.received", "create", None, after)
    service.commit(db)
    return service.Created(cheque.id, created=True)


def update(db: Session, actor: User, settlement_id: int, cheque_id: int, body: s.ChequePatch) -> None:
    settlement = service.lock_open(db, settlement_id)
    cheque = _lock_of_settlement(db, settlement.id, cheque_id)
    _require_live(cheque)
    _require_unpublished(db, cheque)
    if cheque.kind == "ECHEQ" and cheque.verification_status != "DECLARED":
        raise AppError(
            409,
            "echeq_already_decided",
            "El ECHEQ ya fue verificado o rechazado: para corregir sus datos, primero corregí la verificación.",
        )
    fields = body.model_fields_set - {"confirm_not_duplicate", "duplicate_reason"}
    for name in ("payment_type", "amount", "issuer_bank_id", "cheque_number", "issue_date", "drawer_name"):
        if name in fields and getattr(body, name) is None:
            raise AppError(422, "field_required", "Ese dato del cheque es obligatorio.", {"field": name})
    if "delivered_by_name" in fields and body.delivered_by_name is None:
        raise AppError(422, "field_required", "Ese dato del cheque es obligatorio.", {"field": "delivered_by_name"})
    before = _snapshot(cheque)
    current = {name: getattr(cheque, name) for name in _INSTRUMENT_FIELDS} | {"kind": cheque.kind}
    proposed = current | {name: getattr(body, name) for name in fields if name in _INSTRUMENT_FIELDS}
    instrument = _instrument(db, _clean(proposed), received_on=settlement.business_date)
    receiver = cheque.received_by_employee_id
    if "received_by_employee_id" in fields and body.received_by_employee_id != receiver:
        receiver = (
            active_employee(db, body.received_by_employee_id).id if body.received_by_employee_id is not None else None
        )
    same_instrument = (
        instrument.issuer_bank_id == cheque.issuer_bank_id
        and instrument.identity.instrument_key == cheque.instrument_key
        and instrument.echeq_external_id == cheque.echeq_external_id
        and instrument.cheque_number == cheque.cheque_number
    )
    # Before touching the row, so a duplicate is the friendly error and not the unique index at autoflush.
    confirmation = None if same_instrument else _check_identity(db, instrument, cheque.id, body)
    _apply(cheque, instrument)
    cheque.received_by_employee_id = receiver
    after = _snapshot(cheque)
    if before == after:
        db.commit()
        return
    cheque.updated_by = actor.id
    changed_before = {k: v for k, v in before.items() if after[k] != v}
    changed_after = {k: after[k] for k in changed_before} | (
        {"duplicate_confirmation": confirmation} if confirmation else {}
    )
    record_event(db, actor, cheque, "CORRECTED", changed_before, changed_after, settlement=settlement)
    _audit(db, actor, cheque, "settlements.cheque.corrected", "update", changed_before, changed_after)
    _commit_identity(db)


def void(db: Session, actor: User, settlement_id: int, cheque_id: int, reason: str) -> None:
    """Void the load. A cheque in the portfolio stays there until the next close (marked for removal)."""
    settlement = service.lock_open(db, settlement_id)
    cheque = _lock_of_settlement(db, settlement.id, cheque_id)
    if cheque.voided_at is not None:
        raise AppError(409, "cheque_already_voided", "El cheque ya estaba anulado.")
    reason = reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la anulación.")
    _check_no_activity(db, [cheque.id])
    cheque.voided_by = actor.id
    cheque.voided_at = datetime.now(UTC)
    cheque.void_reason = reason
    record_event(db, actor, cheque, "VOIDED", {"voided": False}, {"voided": True}, reason=reason, settlement=settlement)
    _audit(
        db, actor, cheque, "settlements.cheque.voided", "void", {"voided": False}, {"voided": True, "reason": reason}
    )
    service.commit(db)


def decide_verification(db: Session, actor: User, settlement_id: int, cheque_id: int, body: s.VerificationIn) -> None:
    """Verify an ECHEQ or reject its verification; an admin corrects a decision (with a reason) while the
    cheque is not published. Every decision is an event; none is overwritten silently."""
    settlement = service.lock_open(db, settlement_id)
    cheque = _lock_of_settlement(db, settlement.id, cheque_id)
    if cheque.kind != "ECHEQ":
        raise AppError(422, "not_an_echeq", "Solo un ECHEQ se verifica: un cheque físico se recibe en mano.")
    _require_live(cheque)
    _require_unpublished(db, cheque)
    target = body.status
    if cheque.verification_status == target:
        service.commit(db)  # nothing changed: just release the locks
        return
    reason = (body.reason or "").strip() or None
    is_correction = cheque.verification_status != "DECLARED"
    if is_correction:
        if not has_module_access(actor, MODULE_KEY, "admin"):
            raise AppError(
                403, "forbidden", "Solo un administrador puede corregir la verificación de un ECHEQ ya decidida."
            )
        if reason is None:
            raise AppError(422, "reason_required", "Indicá el motivo de la corrección.")
    elif target == "VERIFICATION_REJECTED" and reason is None:
        raise AppError(422, "reason_required", "Indicá por qué no se pudo verificar el ECHEQ.")
    if target == "VERIFIED":
        ident = _identity_of(cheque)
        if not identity.is_complete("ECHEQ", ident):
            raise AppError(
                422,
                "echeq_identity_required",
                "Para verificar el ECHEQ cargá su identificador electrónico o, si el banco no lo muestra, la "
                "cuenta girada (CBU) y el CUIT del librador.",
            )
    previous = cheque.verification_status
    cheque.verification_status = target
    cheque.verification_changed_by = actor.id
    cheque.verification_changed_at = datetime.now(UTC)
    cheque.verification_reason = reason
    record_event(
        db,
        actor,
        cheque,
        target,
        {"verification_status": previous},
        {"verification_status": target, "correction": is_correction},
        reason=reason,
        settlement=settlement,
    )
    _audit(
        db,
        actor,
        cheque,
        "settlements.cheque.verification",
        "correct" if is_correction else "decide",
        {"verification_status": previous},
        {"verification_status": target, "reason": reason},
    )
    _commit_identity(db)


# --- close ----------------------------------------------------------------------------------------


def lock_for_close(db: Session, settlement: Settlement) -> list[Cheque]:
    """Every cheque of the (already locked) settlement, locked in id order."""
    return list(
        db.scalars(
            select(Cheque)
            .where(Cheque.settlement_id == settlement.id)
            .order_by(Cheque.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    )


def justifying(cheques: Sequence[Cheque]) -> list[Cheque]:
    """What a close of the settlement puts in the portfolio: live cheques received or verified."""
    return [
        c
        for c in cheques
        if justifies(kind=c.kind, voided=c.voided_at is not None, verification_status=c.verification_status)
    ]


def check_close(db: Session, cheques: Sequence[Cheque]) -> None:
    """A correction close never pretends a cheque that already moved on was never received."""
    _check_no_activity(db, [c.id for c in cheques if c.voided_at is not None])


def publish(
    db: Session,
    actor: User,
    settlement: Settlement,
    cheques: Sequence[Cheque],
    close_operation_id: int,
    previous_close_id: int | None,
) -> None:
    """Freeze which cheques the new close puts in the portfolio and record it in each cheque's history."""
    previous = in_effect(db, previous_close_id)
    live = justifying(cheques)
    db.add_all(
        SettlementClosingCheque(
            close_operation_id=close_operation_id,
            settlement_id=settlement.id,
            settlement_version=settlement.version,
            cheque_id=c.id,
            amount=c.amount,
        )
        for c in live
    )
    by_id = {c.id: c for c in cheques}
    for c in live:
        before = previous.get(c.id)
        record_event(
            db,
            actor,
            c,
            "PUBLISHED",
            {"amount": service.money(before)} if before is not None else None,
            {"amount": service.money(c.amount)},
            settlement=settlement,
            close_operation_id=close_operation_id,
        )
    published = {c.id for c in live}
    for cheque_id, amount in sorted(previous.items()):
        if cheque_id not in published:
            record_event(
                db,
                actor,
                by_id[cheque_id],
                "WITHDRAWN",
                {"amount": service.money(amount)},
                None,
                settlement=settlement,
                close_operation_id=close_operation_id,
            )


def withdraw_published(
    db: Session,
    actor: User,
    settlement: Settlement,
    cheques: Sequence[Cheque],
    previous_close_id: int,
    reversal_operation_id: int,
) -> None:
    """Record that the cheques of the previous close left the portfolio with its reversal, when the settlement
    is closed again without a ledger operation (an OUTLET settlement corrected to $0, R7-2)."""
    by_id = {c.id: c for c in cheques}
    for cheque_id, amount in sorted(in_effect(db, previous_close_id).items()):
        record_event(
            db,
            actor,
            by_id[cheque_id],
            "WITHDRAWN",
            {"amount": service.money(amount)},
            None,
            settlement=settlement,
            operation_id=reversal_operation_id,
        )


def preview_rows(db: Session, settlement_id: int) -> list[list[Any]]:
    """What of the cheques a closing preview depends on, sorted by id: any material change makes it stale."""
    rows = of(db, settlement_id)
    return [
        [
            c.id,
            c.kind,
            c.payment_type,
            service.money(c.amount),
            c.issuer_bank_id,
            c.instrument_key,
            c.echeq_external_id,
            c.cheque_number,
            c.issue_date.isoformat(),
            c.payment_date.isoformat() if c.payment_date else None,
            c.delivered_by_name,
            c.verification_status,
            c.voided_at is not None,
        ]
        for c in rows
    ]


def in_effect(db: Session, close_operation_id: int | None) -> dict[int, Decimal]:
    """What a close put in the portfolio per cheque."""
    if close_operation_id is None:
        return {}
    rows = db.execute(
        select(SettlementClosingCheque.cheque_id, SettlementClosingCheque.amount).where(
            SettlementClosingCheque.close_operation_id == close_operation_id
        )
    )
    return dict(rows.all())


# --- administrative entries (admin) ---------------------------------------------------------------


def opening_balance(db: Session, actor: User, body: s.AdministrativeEntryIn) -> service.Created:
    """A cheque already in the company's hands when Rendiciones starts (Enzo's R4 decision D-B)."""
    return _administrative_entry(db, actor, body, "OPENING_BALANCE", ledger.system_account_id(db, "opening_balance"))


def owner_contribution(db: Session, actor: User, body: s.OwnerContributionIn) -> service.Created:
    """A partner contributes a cheque: it enters the portfolio against the partner's account."""
    accounts = {a.account_id: a for a in ledger.account_balances(db)}
    owner = accounts.get(body.owner_account_id)
    if owner is None or owner.kind != "OWNER_EQUITY":
        raise AppError(422, "invalid_owner_account", "El aporte tiene que ser de la cuenta de un socio activa.")
    return _administrative_entry(db, actor, body, "OWNER_CONTRIBUTION", owner.account_id)


def _administrative_entry(
    db: Session, actor: User, body: s.AdministrativeEntryIn, origin: str, counter_account_id: int
) -> service.Created:
    if not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(403, "forbidden", "Solo un administrador registra el ingreso de un cheque a la cartera.")
    received_on = body.business_date or business_today()
    data = _clean(body.model_dump())
    description = (body.description or "").strip() or None
    fingerprint = idempotency.fingerprint(
        actor.id,
        f"cheque_{origin.lower()}",
        {
            **data,
            "received_on": received_on.isoformat(),
            "counter_account_id": counter_account_id,
            "description": description,
            "physical_receipt_confirmed": body.physical_receipt_confirmed,
        },
    )
    existing = _by_request_id(db, body.client_request_id)
    if existing is not None:
        idempotency.check_replay(existing.request_fingerprint, fingerprint, "cheque_id", existing.id)
        return service.Created(existing.id, created=False)
    if received_on > business_today():
        raise AppError(422, "future_business_date", "La fecha de ingreso no puede ser futura.")
    _check_receipt_confirmation(body.kind, body.physical_receipt_confirmed)
    instrument = _instrument(db, data, received_on=received_on)
    if body.kind == "ECHEQ" and not identity.is_complete("ECHEQ", instrument.identity):
        raise AppError(
            422,
            "echeq_identity_required",
            "Un ECHEQ entra a la cartera verificado: cargá su identificador electrónico o la cuenta girada (CBU) "
            "y el CUIT del librador.",
        )
    confirmation = _check_identity(db, instrument, None, body)
    cheque = _new_cheque(
        actor,
        instrument,
        origin_type=origin,
        settlement_id=None,
        received_on=received_on,
        received_by=None,
        verification="VERIFIED",
        request=(body.client_request_id, fingerprint),
    )
    winner = _insert(db, cheque, body.client_request_id, fingerprint)
    if winner is not None:
        return winner
    after = {**_snapshot(cheque), **({"duplicate_confirmation": confirmation} if confirmation else {})}
    record_event(db, actor, cheque, "RECEIVED", None, after)
    if cheque.kind == "ECHEQ":
        record_event(db, actor, cheque, "VERIFIED", None, {"verification_status": "VERIFIED"})
    label = "Saldo inicial" if origin == "OPENING_BALANCE" else "Aporte de socio"
    operation = ledger.post_operation(
        db,
        actor=actor,
        type=ENTRY_TYPES[origin],
        legs=[
            ledger.Leg(counter_account_id, -cheque.amount),
            ledger.Leg(ledger.system_account_id(db, "cheque_portfolio"), cheque.amount, cheque.id),
        ],
        client_request_id=body.client_request_id,
        business_date=received_on,
        description=description or f"{label}: cheque #{cheque.id}",
        source=ledger.SourceRef(SOURCE_TYPE, cheque.id),
    ).operation
    record_event(
        db, actor, cheque, "PUBLISHED", None, {"amount": service.money(cheque.amount)}, operation_id=operation.id
    )
    _audit(
        db,
        actor,
        cheque,
        f"settlements.cheque.{origin.lower()}",
        "create",
        None,
        {**after, "operation_id": operation.id},
    )
    _commit_identity(db)
    return service.Created(cheque.id, created=True)


# --- descriptive data (admin) ---------------------------------------------------------------------


def correct_descriptive(db: Session, actor: User, cheque_id: int, body: s.DescriptiveIn) -> None:
    """Admin, with a reason, at any time: data that neither identifies the instrument nor says who owes it."""
    if not has_module_access(actor, MODULE_KEY, "admin"):
        raise AppError(403, "forbidden", "Solo un administrador corrige los datos descriptivos de un cheque.")
    cheque = _lock(db, cheque_id)
    _require_live(cheque)
    fields = body.model_fields_set - {"reason"}
    if "drawer_name" in fields and body.drawer_name is None:
        raise AppError(422, "field_required", "Ese dato del cheque es obligatorio.", {"field": "drawer_name"})
    if "drawer_tax_id" in fields and cheque.kind == "ECHEQ":
        raise AppError(
            409,
            "cheque_published_immutable",
            "El CUIT del librador de un ECHEQ es parte de su identidad: no se corrige.",
        )
    before = {name: getattr(cheque, name) for name in ("drawer_name", "drawer_tax_id", "notes")}
    if "drawer_name" in fields and body.drawer_name is not None:
        cheque.drawer_name = _clean_name(body.drawer_name, "drawer_name")
    if "drawer_tax_id" in fields:
        cheque.drawer_tax_id = identity.tax_id(body.drawer_tax_id)
    if "notes" in fields:
        cheque.notes = (body.notes or "").strip() or None
    after = {name: getattr(cheque, name) for name in before}
    if before == after:
        db.commit()
        return
    reason = body.reason.strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá el motivo de la corrección.")
    cheque.updated_by = actor.id
    changed_before = {k: v for k, v in before.items() if after[k] != v}
    changed_after = {k: after[k] for k in changed_before}
    record_event(db, actor, cheque, "DESCRIPTIVE_CORRECTED", changed_before, changed_after, reason=reason)
    _audit(db, actor, cheque, "settlements.cheque.descriptive_corrected", "update", changed_before, changed_after)
    service.commit(db)


# --- reading --------------------------------------------------------------------------------------


def of(db: Session, settlement_id: int) -> list[Cheque]:
    return list(db.scalars(select(Cheque).where(Cheque.settlement_id == settlement_id).order_by(Cheque.id)))


def get(db: Session, cheque_id: int) -> Cheque:
    cheque = db.get(Cheque, cheque_id)
    if cheque is None:
        raise AppError(404, "cheque_not_found", "El cheque no existe.")
    return cheque


def events_of(db: Session, cheque_id: int) -> list[ChequeEvent]:
    return list(db.scalars(select(ChequeEvent).where(ChequeEvent.cheque_id == cheque_id).order_by(ChequeEvent.id)))


def closings_of(db: Session, cheque_id: int) -> list[SettlementClosingCheque]:
    return list(
        db.scalars(
            select(SettlementClosingCheque)
            .where(SettlementClosingCheque.cheque_id == cheque_id)
            .order_by(SettlementClosingCheque.settlement_version)
        )
    )


def entry_operation_id(db: Session, cheque_id: int) -> int | None:
    """The operation that put it in the portfolio outside a settlement (an administrative entry, or the receipt
    of a pending it paid), if any."""
    return db.scalar(
        select(ChequeEvent.operation_id)
        .where(
            ChequeEvent.cheque_id == cheque_id,
            ChequeEvent.event_type.in_(["PUBLISHED", "COLLECTED"]),
            ChequeEvent.operation_id.is_not(None),
        )
        .order_by(ChequeEvent.id)
        .limit(1)
    )


# --- internals ------------------------------------------------------------------------------------


_INSTRUMENT_FIELDS = (
    "payment_type",
    "amount",
    "issuer_bank_id",
    "branch_code",
    "account_ref",
    "cheque_number",
    "echeq_external_id",
    "issue_date",
    "payment_date",
    "drawer_name",
    "drawer_tax_id",
    "delivered_by_name",
    "notes",
)


def record_event(
    db: Session,
    actor: User,
    cheque: Cheque,
    event_type: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    *,
    reason: str | None = None,
    settlement: Settlement | None = None,
    close_operation_id: int | None = None,
    operation_id: int | None = None,
    payment_id: int | None = None,
) -> None:
    db.add(
        ChequeEvent(
            cheque_id=cheque.id,
            event_type=event_type,
            before=before,
            after=after,
            reason=reason,
            settlement_id=settlement.id if settlement else None,
            settlement_version=settlement.version if settlement else None,
            close_operation_id=close_operation_id,
            operation_id=operation_id,
            payment_id=payment_id,
            created_by=actor.id,
        )
    )


def _clean(raw: dict[str, Any]) -> dict[str, Any]:
    """The instrument fields of a request, cleaned and JSON-ready (they also make the request fingerprint)."""
    return {
        "kind": raw["kind"],
        "payment_type": raw["payment_type"],
        "amount": service.money(Decimal(raw["amount"])),
        "issuer_bank_id": raw["issuer_bank_id"],
        "branch_code": identity.digits(raw.get("branch_code"), "branch_code", max_length=10),
        "account_ref": identity.account(raw.get("account_ref")),
        "cheque_number": identity.digits(raw.get("cheque_number"), "cheque_number", max_length=20),
        "echeq_external_id": identity.external_id(raw.get("echeq_external_id")),
        "issue_date": _iso(raw["issue_date"]),
        "payment_date": _iso(raw.get("payment_date")),
        "drawer_name": _clean_name(raw["drawer_name"], "drawer_name"),
        "drawer_tax_id": identity.tax_id(raw.get("drawer_tax_id")),
        "delivered_by_name": _clean_name(raw["delivered_by_name"], "delivered_by_name"),
        "notes": (raw.get("notes") or "").strip() or None,
    }


def _instrument(db: Session, data: dict[str, Any], *, received_on: date) -> Instrument:
    kind = data["kind"]
    if data["cheque_number"] is None:
        raise AppError(422, "invalid_cheque_data", "Indicá el número del cheque.", {"field": "cheque_number"})
    if kind == "PHYSICAL" and data["echeq_external_id"] is not None:
        raise AppError(
            422,
            "invalid_cheque_data",
            "Un cheque físico no tiene identificador de ECHEQ.",
            {"field": "echeq_external_id"},
        )
    if kind == "ECHEQ" and data["drawer_tax_id"] is None:
        raise AppError(
            422, "drawer_tax_id_required", "Un ECHEQ siempre informa el CUIT del librador.", {"field": "drawer_tax_id"}
        )
    banks.active(db, data["issuer_bank_id"])
    issue_date = date.fromisoformat(data["issue_date"])
    payment_date = date.fromisoformat(data["payment_date"]) if data["payment_date"] else None
    terms.check_dates(
        payment_type=data["payment_type"],
        issue_date=issue_date,
        payment_date=payment_date,
        received_on=received_on,
    )
    ident = identity.compute(
        kind=kind,
        bank_id=data["issuer_bank_id"],
        number=data["cheque_number"],
        branch=data["branch_code"],
        account_ref=data["account_ref"],
        echeq_id=data["echeq_external_id"],
        drawer_tax_id=data["drawer_tax_id"],
    )
    return Instrument(
        kind=kind,
        payment_type=data["payment_type"],
        amount=Decimal(data["amount"]),
        issuer_bank_id=data["issuer_bank_id"],
        branch_code=data["branch_code"],
        account_ref=data["account_ref"],
        cheque_number=data["cheque_number"],
        echeq_external_id=data["echeq_external_id"],
        issue_date=issue_date,
        payment_date=payment_date,
        drawer_name=data["drawer_name"],
        drawer_tax_id=data["drawer_tax_id"],
        delivered_by_name=data["delivered_by_name"],
        notes=data["notes"],
        identity=ident,
    )


def _check_receipt_confirmation(kind: str, confirmed: bool) -> None:
    if kind == "PHYSICAL" and not confirmed:
        raise AppError(
            422,
            "physical_receipt_required",
            "Un cheque físico se registra solo si lo recibiste en mano: confirmá «Recibí el cheque físicamente».",
        )
    if kind == "ECHEQ" and confirmed:
        raise AppError(
            422,
            "invalid_cheque_data",
            "Un ECHEQ no se recibe en mano: se verifica.",
            {"field": "physical_receipt_confirmed"},
        )


def _check_identity(
    db: Session, instrument: Instrument, exclude_id: int | None, confirmation: s.DuplicateConfirmation
) -> dict[str, Any] | None:
    """Refuse a duplicate; with a possible one, require the caller's confirmation (returned for the history).

    Candidates are taken under a transaction-level advisory lock on (bank, number), so two concurrent loads
    of the same instrument see each other; the unique indexes stay as the last guard.
    """
    ident = instrument.identity
    db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"settlements.cheque:{instrument.issuer_bank_id}:{ident.number}"},
    )
    rows = list(
        db.scalars(
            select(Cheque).where(
                Cheque.kind == instrument.kind,
                Cheque.issuer_bank_id == instrument.issuer_bank_id,
                func.coalesce(func.nullif(func.ltrim(Cheque.cheque_number, "0"), ""), "0") == ident.number,
                Cheque.id != (exclude_id or 0),
            )
        )
    )
    positions = ledger.cheque_positions(db, [c.id for c in rows])
    holding = [
        c for c in rows if (c.voided_at is None and c.verification_status != "VERIFICATION_REJECTED") or positions[c.id]
    ]
    for c in holding:
        same_external = ident.external_id is not None and c.echeq_external_id == ident.external_id
        same_key = ident.instrument_key is not None and c.instrument_key == ident.instrument_key
        if same_external or same_key:
            raise _duplicate(c)
    candidates = [
        c for c in holding if identity.may_be_same(instrument.kind, ident, c.instrument_key, c.echeq_external_id)
    ]
    if not candidates:
        return None
    if not confirmation.confirm_not_duplicate:
        raise AppError(
            409,
            "cheque_possible_duplicate",
            "Ya hay un cheque del mismo banco y número sin datos suficientes para distinguirlos. Si es otro "
            "cheque, confirmalo e indicá por qué.",
            {
                "candidates": [
                    {
                        "cheque_id": c.id,
                        "amount": service.money(c.amount),
                        "delivered_by_name": c.delivered_by_name,
                        "settlement_id": c.settlement_id,
                    }
                    for c in candidates
                ]
            },
        )
    reason = (confirmation.duplicate_reason or "").strip()
    if not reason:
        raise AppError(422, "reason_required", "Indicá por qué es otro cheque y no el mismo.")
    return {"candidates": [c.id for c in candidates], "reason": reason}


def _duplicate(existing: Cheque) -> AppError:
    return AppError(
        409,
        "cheque_duplicate",
        "Ese cheque ya está registrado: es el mismo instrumento.",
        {"cheque_id": existing.id, "settlement_id": existing.settlement_id},
    )


def _new_cheque(
    actor: User,
    instrument: Instrument,
    *,
    origin_type: str,
    settlement_id: int | None,
    received_on: date,
    received_by: int | None,
    verification: str,
    request: tuple[Any, str],
) -> Cheque:
    now = datetime.now(UTC)
    physical = instrument.kind == "PHYSICAL"
    cheque = Cheque(
        kind=instrument.kind,
        origin_type=origin_type,
        settlement_id=settlement_id,
        received_business_date=received_on,
        received_by_employee_id=received_by,
        physical_receipt_confirmed_by=actor.id if physical else None,
        physical_receipt_confirmed_at=now if physical else None,
        verification_status=None if physical else verification,
        verification_changed_by=None if physical else actor.id,
        verification_changed_at=None if physical else now,
        client_request_id=request[0],
        request_fingerprint=request[1],
        created_by=actor.id,
    )
    _apply(cheque, instrument)
    return cheque


def _apply(cheque: Cheque, instrument: Instrument) -> None:
    for name in _INSTRUMENT_FIELDS:
        setattr(cheque, name, getattr(instrument, name))
    cheque.instrument_key = instrument.identity.instrument_key


def _insert(db: Session, cheque: Cheque, request_id: Any, fingerprint: str) -> service.Created | None:
    """Insert the new cheque; None when it was inserted, or the earlier one when a concurrent retry won."""
    try:
        with db.begin_nested():
            db.add(cheque)
            db.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint in _IDENTITY_INDEXES:
            db.rollback()
            raise AppError(409, "cheque_duplicate", "Ese cheque ya está registrado: es el mismo instrumento.") from exc
        winner = _by_request_id(db, request_id)
        if winner is None:
            raise
        db.rollback()  # release the locks taken for the insert that lost
        idempotency.check_replay(winner.request_fingerprint, fingerprint, "cheque_id", winner.id)
        return service.Created(winner.id, created=False)
    return None


def _commit_identity(db: Session) -> None:
    """Commit; a duplicate only the unique indexes saw (a concurrent load) is `cheque_duplicate`."""
    try:
        db.flush()
    except IntegrityError as exc:
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        db.rollback()
        if constraint in _IDENTITY_INDEXES:
            raise AppError(409, "cheque_duplicate", "Ese cheque ya está registrado: es el mismo instrumento.") from exc
        raise
    service.commit(db)


def _identity_of(cheque: Cheque) -> identity.Identity:
    return identity.compute(
        kind=cheque.kind,
        bank_id=cheque.issuer_bank_id,
        number=cheque.cheque_number,
        branch=cheque.branch_code,
        account_ref=cheque.account_ref,
        echeq_id=cheque.echeq_external_id,
        drawer_tax_id=cheque.drawer_tax_id,
    )


def _check_no_activity(db: Session, cheque_ids: list[int]) -> None:
    """Only a cheque still in the portfolio by its settlement's close may leave the settlement."""
    moved = ledger.cheque_activity(db, cheque_ids, except_types=[CLOSE_TYPE])
    if moved:
        raise AppError(
            409,
            "cheque_has_activity",
            "El cheque ya tuvo movimientos después del cierre (depósito, acreditación, rechazo, devolución o "
            "entrega en un pago): "
            "la rendición no puede quitarlo como si nunca se hubiera recibido.",
            {"cheque_id": min(moved)},
        )


def _require_live(cheque: Cheque) -> None:
    if cheque.voided_at is not None:
        raise AppError(409, "cheque_voided", "El cheque está anulado y no se modifica.")


def _require_unpublished(db: Session, cheque: Cheque) -> None:
    if ledger.cheques_with_effect(db, [cheque.id]):
        raise AppError(
            409,
            "cheque_published_immutable",
            "El cheque ya tuvo efecto financiero: su importe, su identidad y sus fechas no cambian. Solo un "
            "administrador corrige sus datos descriptivos, con motivo.",
            {"cheque_id": cheque.id},
        )


def _lock(db: Session, cheque_id: int) -> Cheque:
    cheque = db.scalars(
        select(Cheque).where(Cheque.id == cheque_id).with_for_update().execution_options(populate_existing=True)
    ).one_or_none()
    if cheque is None:
        raise AppError(404, "cheque_not_found", "El cheque no existe.")
    return cheque


def _lock_of_settlement(db: Session, settlement_id: int, cheque_id: int) -> Cheque:
    cheque = _lock(db, cheque_id)
    if cheque.settlement_id != settlement_id:
        raise AppError(404, "cheque_not_found", "El cheque no existe en esta rendición.")
    return cheque


def _snapshot(cheque: Cheque) -> dict[str, Any]:
    return {
        "kind": cheque.kind,
        "payment_type": cheque.payment_type,
        "amount": service.money(cheque.amount),
        "issuer_bank_id": cheque.issuer_bank_id,
        "branch_code": cheque.branch_code,
        "account_ref": cheque.account_ref,
        "cheque_number": cheque.cheque_number,
        "echeq_external_id": cheque.echeq_external_id,
        "issue_date": cheque.issue_date.isoformat(),
        "payment_date": cheque.payment_date.isoformat() if cheque.payment_date else None,
        "drawer_name": cheque.drawer_name,
        "drawer_tax_id": cheque.drawer_tax_id,
        "delivered_by_name": cheque.delivered_by_name,
        "received_by_employee_id": cheque.received_by_employee_id,
        "received_business_date": cheque.received_business_date.isoformat(),
        "notes": cheque.notes,
    }


def _audit(
    db: Session,
    actor: User,
    cheque: Cheque,
    event: str,
    action: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    # The drawer's CUIT is not audited, only whether there is one (no PII in the cross-cutting audit).
    def scrub(values: dict[str, Any] | None) -> dict[str, Any] | None:
        if values is None:
            return None
        return {k: (v is not None if k == "drawer_tax_id" else v) for k, v in values.items()}

    record_change(
        db,
        event,
        action=action,
        user_id=actor.id,
        entity_type="cheque",
        entity_id=cheque.id,
        before=scrub(before),
        after=scrub(after),
    )


def _clean_name(value: str, field: str) -> str:
    name = " ".join(value.split())
    if not name:
        raise AppError(422, "field_required", "Ese dato del cheque es obligatorio.", {"field": field})
    return name


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, date) else str(value)


def _by_request_id(db: Session, request_id: Any) -> Cheque | None:
    return db.scalars(select(Cheque).where(Cheque.client_request_id == request_id)).first()


def require_settlement_cheques_verified(unverified: int) -> None:
    """An ECHEQ declared and not verified blocks the close (Enzo's R4 decision D-K)."""
    if unverified:
        raise AppError(
            409,
            "settlement_has_unverified_echeqs",
            "Hay ECHEQ declarados sin verificar: verificalos o rechazá su verificación antes de cerrar.",
            {"unverified_echeqs_count": unverified},
        )
