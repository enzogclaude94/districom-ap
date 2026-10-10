"""Third parties: reading for every module, the master for platform admins (R6-1).

- A platform admin creates, corrects, deactivates and reactivates (`/api/v1/third-parties`).
- A module may let its users create one quickly while they record something (Rendiciones: an operator
  during a payment) through `create`, saying where it came from; the row is the same master row, audited
  the same way. Correcting or (de)activating stays with platform admins.
- Duplicates: the same CUIT is refused (`third_party_tax_id_taken`); the same name where the CUIT cannot
  tell them apart (one of them has none) is a *possible* duplicate: never merged, the caller must confirm
  it is another third party and say why, and that confirmation stays in its history.

Every change is audited with who, before and after (`platform.third_party.*`); nothing is deleted.
"""

from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.models import AuditEvent
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.third_parties import tax_ids
from app.platform.third_parties.models import ThirdParty, name_key
from app.platform.third_parties.schemas import DuplicateConfirmation, ThirdPartyIn, ThirdPartyPatch

ENTITY = "third_party"
PLATFORM = "platform"


def list_all(db: Session, *, include_inactive: bool = False, q: str | None = None) -> list[ThirdParty]:
    query = select(ThirdParty).order_by(ThirdParty.name_key, ThirdParty.id)
    if not include_inactive:
        query = query.where(ThirdParty.active.is_(True))
    if q is not None and (needle := q.strip()):
        digits = "".join(c for c in needle if c.isdigit())
        key = name_key(needle)
        condition = ThirdParty.name_key.contains(key) if key else None
        if digits and len(digits) >= 3:
            by_tax = ThirdParty.tax_id.contains(digits)
            condition = by_tax if condition is None else (condition | by_tax)
        if condition is not None:
            query = query.where(condition)
    return list(db.scalars(query))


def get(db: Session, third_party_id: int) -> ThirdParty:
    row = db.get(ThirdParty, third_party_id)
    if row is None:
        raise AppError(404, "third_party_not_found", "El tercero no existe.")
    return row


def lock_active(db: Session, third_party_id: int) -> ThirdParty:
    """A third party a new record may name, locked FOR SHARE until the caller's commit: a concurrent
    deactivation waits for it, or wins and this is refused. 422 when unknown or deactivated."""
    row = db.scalars(
        select(ThirdParty)
        .where(ThirdParty.id == third_party_id)
        .with_for_update(read=True)
        .execution_options(populate_existing=True)
    ).one_or_none()
    if row is None:
        raise AppError(422, "invalid_third_party", "El tercero no existe.", {"third_party_id": third_party_id})
    if not row.active:
        raise AppError(
            422,
            "third_party_inactive",
            f"{row.name} está desactivado: no admite pagos nuevos.",
            {"third_party_id": third_party_id},
        )
    return row


def history(db: Session, third_party_id: int) -> list[AuditEvent]:
    return list(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.entity_type == ENTITY, AuditEvent.entity_id == str(third_party_id))
            .order_by(AuditEvent.occurred_at, AuditEvent.id)
        )
    )


def create(db: Session, actor: User, body: ThirdPartyIn, *, origin: str = PLATFORM, commit: bool = True) -> ThirdParty:
    """Register a third party. `origin` says which flow created it (audited); the caller checked who may."""
    name = _name(body.name)
    tax_id = tax_ids.normalize(body.tax_id)
    notes = _notes(body.notes)
    confirmation = _check_duplicates(db, name, tax_id, None, body)
    row = ThirdParty(name=name, tax_id=tax_id, notes=notes, created_by=actor.id, updated_by=actor.id)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:  # another user registered the same CUIT at the same time
        raise _conflict(db, tax_id) from exc
    after: dict[str, Any] = {"name": name, "tax_id": tax_id, "notes": notes, "active": True, "origin": origin}
    if confirmation is not None:
        after["duplicate_confirmation"] = confirmation
    _audit(db, actor, row, "created", None, after)
    if commit:
        db.commit()
    return row


def update(db: Session, actor: User, third_party_id: int, body: ThirdPartyPatch) -> ThirdParty:
    row = db.scalars(select(ThirdParty).where(ThirdParty.id == third_party_id).with_for_update()).first()
    if row is None:
        raise AppError(404, "third_party_not_found", "El tercero no existe.")
    fields = body.model_fields_set - {"confirm_not_duplicate", "duplicate_reason"}
    changes: list[tuple[str, dict[str, Any], dict[str, Any]]] = []
    name, tax_id = row.name, row.tax_id
    if "name" in fields and body.name is not None:
        name = _name(body.name)
    if "tax_id" in fields:
        tax_id = tax_ids.normalize(body.tax_id)
    confirmation = None
    if name_key(name) != row.name_key or tax_id != row.tax_id:
        confirmation = _check_duplicates(db, name, tax_id, row.id, body)
    if name != row.name:
        changes.append(("name_changed", {"name": row.name}, {"name": name}))
        row.name = name
    if tax_id != row.tax_id:
        changes.append(("tax_id_changed", {"tax_id": row.tax_id}, {"tax_id": tax_id}))
        row.tax_id = tax_id
    if "notes" in fields:
        notes = _notes(body.notes)
        if notes != row.notes:
            changes.append(("notes_changed", {"notes": row.notes}, {"notes": notes}))
            row.notes = notes
    if body.active is not None and body.active != row.active:
        changes.append(("activated" if body.active else "deactivated", {"active": row.active}, {"active": body.active}))
        row.active = body.active
    if changes:
        row.updated_by = actor.id
        try:
            with db.begin_nested():
                db.flush()
        except IntegrityError as exc:  # the CUIT was taken meanwhile
            raise _conflict(db, row.tax_id) from exc
        for index, (kind, before, after) in enumerate(changes):
            if confirmation is not None and index == 0:
                after = after | {"duplicate_confirmation": confirmation}
            _audit(db, actor, row, kind, before, after)
    db.commit()
    return row


def _name(raw: str) -> str:
    name = " ".join(raw.split())
    if not name_key(name):
        raise AppError(422, "name_required", "Indicá el nombre del tercero.")
    return name


def _notes(raw: str | None) -> str | None:
    return (raw or "").strip() or None


def _check_duplicates(
    db: Session, name: str, tax_id: str | None, own_id: int | None, confirmation: DuplicateConfirmation
) -> dict[str, Any] | None:
    """Refuse a taken CUIT; with a possible homonym, require the caller's confirmation (returned for the
    history). Candidates are read under a transaction-level advisory lock on the name, so two concurrent
    registrations of the same name see each other; the unique CUIT index stays as the last guard."""
    key = name_key(name)
    db.execute(text("SELECT pg_advisory_xact_lock(hashtext(:k))"), {"k": f"platform.third_party:{key}"})
    if tax_id is not None:
        other = db.scalars(
            select(ThirdParty).where(ThirdParty.tax_id == tax_id, ThirdParty.id != (own_id or 0))
        ).first()
        if other is not None:
            raise _tax_id_taken(other)
    homonyms = list(
        db.scalars(
            select(ThirdParty).where(ThirdParty.name_key == key, ThirdParty.id != (own_id or 0)).order_by(ThirdParty.id)
        )
    )
    # Two CUITs that differ are two different third parties: only a missing CUIT leaves the doubt.
    candidates = [h for h in homonyms if h.tax_id is None or tax_id is None]
    if not candidates:
        return None
    if not confirmation.confirm_not_duplicate:
        raise AppError(
            409,
            "third_party_possible_duplicate",
            "Ya existe un tercero con ese nombre. Si es otro, confirmalo e indicá por qué; nunca se fusionan.",
            {
                "candidates": [
                    {
                        "third_party_id": h.id,
                        "name": h.name,
                        "tax_id_display": tax_ids.display(h.tax_id) if h.tax_id else None,
                        "active": h.active,
                    }
                    for h in candidates
                ]
            },
        )
    reason = " ".join((confirmation.duplicate_reason or "").split())
    if not reason:
        raise AppError(422, "reason_required", "Indicá por qué es otro tercero y no el mismo.")
    return {"candidates": [h.id for h in candidates], "reason": reason}


def _conflict(db: Session, tax_id: str | None) -> AppError:
    db.expire_all()
    winner = db.scalars(select(ThirdParty).where(ThirdParty.tax_id == tax_id)).first() if tax_id else None
    if winner is None:
        return AppError(409, "third_party_conflict", "No se pudo guardar el tercero. Volvé a intentar.")
    return _tax_id_taken(winner)


def _tax_id_taken(other: ThirdParty) -> AppError:
    state = "" if other.active else " (desactivado)"
    return AppError(
        409,
        "third_party_tax_id_taken",
        f"Ese CUIT ya es de {other.name}{state}.",
        {"third_party_id": other.id, "active": other.active},
    )


def _audit(
    db: Session, actor: User, row: ThirdParty, kind: str, before: dict[str, Any] | None, after: dict[str, Any]
) -> None:
    record_change(
        db,
        f"platform.third_party.{kind}",
        action="create" if kind == "created" else "update",
        user_id=actor.id,
        entity_type=ENTITY,
        entity_id=row.id,
        before=before,
        after=after,
    )
