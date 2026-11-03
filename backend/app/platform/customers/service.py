"""Customers: what the STORE SYS master import (F1.3) will call, the exact-name candidates a sales line may
propose, and the two flags an admin sets (DATA_MODEL §4.1, docs/IMPORTS.md §3.2).

Name, legal name, CUIT, address and phone are PII: never in logs nor audit payloads.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.platform.audit.service import record_change
from app.platform.auth.models import User
from app.platform.customers.models import Customer
from app.platform.mappings.service import label_key
from app.platform.third_parties.tax_ids import check_digit

_NOT_DIGITS = re.compile(r"\D+")


def name_key(display_name: str) -> str:
    """The key a sales line's customer name is compared with: the same `label_key` its mapping uses."""
    return label_key(display_name)


@dataclass(frozen=True)
class MasterRecord:
    """A customer as the master brings it, already trimmed; the CUIT and phone as typed."""

    display_name: str
    legal_name: str | None = None
    tax_id: str | None = None
    address: str | None = None
    phone: str | None = None


@dataclass(frozen=True)
class NormalizedRecord:
    display_name: str
    legal_name: str | None
    tax_id: str | None
    address: str | None
    phone: str | None
    warnings: tuple[str, ...] = field(default_factory=tuple)  # codes, never values


def _blank(value: str | None) -> str | None:
    clean = " ".join(value.split()) if value else ""
    return clean or None


def normalize_tax_id(raw: str | None) -> tuple[str | None, str | None]:
    """(CUIT, warning code). STORE SYS writes `"  -        -"` for no CUIT: that is no CUIT, not an error. Anything
    else that is not 11 digits with a valid check digit is kept out (`None`) with the warning `invalid_tax_id`."""
    digits = _NOT_DIGITS.sub("", raw or "")
    if not digits:
        return None, None
    if len(digits) != 11 or check_digit(digits[:10]) != int(digits[10]):
        return None, "invalid_tax_id"
    return digits, None


def normalize(record: MasterRecord) -> NormalizedRecord:
    display_name = _blank(record.display_name)
    if display_name is None:
        raise ValueError("A customer needs a display name")
    tax_id, warning = normalize_tax_id(record.tax_id)
    phone = _NOT_DIGITS.sub("", record.phone or "") or None
    return NormalizedRecord(
        display_name=display_name[:200],
        legal_name=(_blank(record.legal_name) or "")[:200] or None,
        tax_id=tax_id,
        address=_blank(record.address),
        phone=phone[:60] if phone else None,
        warnings=(warning,) if warning else (),
    )


def create_from_master(db: Session, record: NormalizedRecord, *, job_id: int, seen_on: date) -> Customer:
    """A customer the master brings for the first time. Only the master import calls this (ADR 014 §2.6)."""
    customer = Customer(
        display_name=record.display_name,
        display_name_key=name_key(record.display_name),
        legal_name=record.legal_name,
        tax_id=record.tax_id,
        address=record.address,
        phone=record.phone,
        first_seen_job_id=job_id,
        last_seen_job_id=job_id,
        last_seen_on=seen_on,
    )
    db.add(customer)
    db.flush()
    return customer


def update_from_master(
    db: Session, customer: Customer, record: NormalizedRecord, *, job_id: int, seen_on: date
) -> bool:
    """Refresh what the master owns (name, legal name, CUIT, address, phone); True when any of them changed. The
    flags DistriCom decides (`is_generic`, `active`) are never touched by an import."""
    values = {
        "display_name": record.display_name,
        "display_name_key": name_key(record.display_name),
        "legal_name": record.legal_name,
        "tax_id": record.tax_id,
        "address": record.address,
        "phone": record.phone,
    }
    changed = any(getattr(customer, k) != v for k, v in values.items())
    for k, v in values.items():
        setattr(customer, k, v)
    customer.last_seen_job_id = job_id
    customer.last_seen_on = seen_on
    db.flush()
    return changed


def exact_name_candidates(db: Session, name: str) -> list[int]:
    """Active customers whose normalized display name is exactly this one: what a sales line, which brings only a
    name, may *propose* (pending with one, ambiguous with more). Never a confirmation, never similarity."""
    key = name_key(name)
    if not key:
        return []
    return list(
        db.scalars(
            select(Customer.id).where(Customer.display_name_key == key, Customer.active.is_(True)).order_by(Customer.id)
        )
    )


def get(db: Session, customer_id: int) -> Customer:
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise AppError(404, "customer_not_found", "Ese cliente no existe.")
    return customer


def search(
    db: Session, *, q: str | None, include_inactive: bool, limit: int, offset: int
) -> tuple[Sequence[Customer], int]:
    query = select(Customer)
    if not include_inactive:
        query = query.where(Customer.active.is_(True))
    if q and (key := name_key(q)):
        query = query.where(Customer.display_name_key.contains(key, autoescape=True))
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Customer.display_name_key, Customer.id).offset(offset).limit(limit)).all()
    return rows, total


def set_flags(
    db: Session, actor: User, customer_id: int, *, is_generic: bool | None, active: bool | None, reason: str | None
) -> Customer:
    """What DistriCom decides about a customer: generic (Consumidor Final…, excluded from customer universes) and
    active. Audited with a reason; the master never changes them."""
    customer = db.scalars(select(Customer).where(Customer.id == customer_id).with_for_update()).first()
    if customer is None:
        raise AppError(404, "customer_not_found", "Ese cliente no existe.")
    clean = " ".join(reason.split()) if reason else ""
    if not clean:
        raise AppError(422, "reason_required", "Indicá el motivo del cambio.")
    before = {"is_generic": customer.is_generic, "active": customer.active}
    if is_generic is not None:
        customer.is_generic = is_generic
    if active is not None:
        customer.active = active
    after = {"is_generic": customer.is_generic, "active": customer.active}
    if after == before:
        db.rollback()
        return get(db, customer_id)
    record_change(
        db,
        "platform.customer.updated",
        action="update",
        user_id=actor.id,
        entity_type="customer",
        entity_id=customer.id,
        before=before,
        after={**after, "reason": clean[:500]},
    )
    db.commit()
    db.refresh(customer)
    return customer
