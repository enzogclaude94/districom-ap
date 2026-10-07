"""Financial institutions: reading, controlled additions and activation (DATA_MODEL.md §4.1, financial_institutions).

An official entry is never edited: its code and name are the BCRA's. The database refuses any change
other than `active` (migration 0011), so an admin cannot silently turn one entity into another.
"""

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.banks.models import FinancialInstitution


def list_all(db: Session, *, include_inactive: bool = False) -> list[FinancialInstitution]:
    query = select(FinancialInstitution).order_by(FinancialInstitution.bcra_code)
    if not include_inactive:
        query = query.where(FinancialInstitution.active.is_(True))
    return list(db.scalars(query))


def get(db: Session, institution_id: int) -> FinancialInstitution:
    row = db.get(FinancialInstitution, institution_id)
    if row is None:
        raise AppError(404, "bank_not_found", "La entidad bancaria no existe.")
    return row


def active(db: Session, institution_id: int) -> FinancialInstitution:
    """An entity a new record may name; 422 when unknown or deactivated."""
    row = db.get(FinancialInstitution, institution_id)
    if row is None or not row.active:
        raise AppError(
            422, "invalid_bank", "El banco tiene que ser una entidad activa del catálogo.", {"bank_id": institution_id}
        )
    return row


def add_manual(db: Session, actor: User, bcra_code: int, name: str, reason: str) -> FinancialInstitution:
    name, reason = " ".join(name.split()), reason.strip()
    if not name or not reason:
        raise AppError(422, "reason_required", "Indicá el nombre de la entidad y por qué se agrega.")
    existing = db.scalars(select(FinancialInstitution).where(FinancialInstitution.bcra_code == bcra_code)).first()
    if existing is not None:
        raise _code_taken(existing)
    row = FinancialInstitution(
        bcra_code=bcra_code, name=name, source="MANUAL", created_by=actor.id, manual_reason=reason
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        winner = db.scalars(select(FinancialInstitution).where(FinancialInstitution.bcra_code == bcra_code)).one()
        raise _code_taken(winner) from exc
    record_change(
        db,
        "platform.financial_institution.added",
        action="create",
        user_id=actor.id,
        entity_type="financial_institution",
        entity_id=row.id,
        before=None,
        after={"bcra_code": bcra_code, "name": name, "source": "MANUAL", "reason": reason},
    )
    db.commit()
    return row


def set_active(db: Session, actor: User, institution_id: int, value: bool) -> FinancialInstitution:
    row = get(db, institution_id)
    if row.active != value:
        row.active = value
        record_change(
            db,
            "platform.financial_institution.activated" if value else "platform.financial_institution.deactivated",
            action="update",
            user_id=actor.id,
            entity_type="financial_institution",
            entity_id=row.id,
            before={"active": not value},
            after={"active": value},
        )
    db.commit()
    return row


def _code_taken(existing: FinancialInstitution) -> AppError:
    return AppError(
        409,
        "bank_code_taken",
        f"El código BCRA {existing.bcra_code} ya es de {existing.name}: no se reemplaza una entidad.",
        {"bank_id": existing.id},
    )
