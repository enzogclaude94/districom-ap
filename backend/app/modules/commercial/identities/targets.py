"""Entities a mapping may be linked to, as Comercial shows them in Datos > Mapeos (F1.5).

Customers: by name, legal name or STORE SYS code (through its confirmed mapping), with their current zone; never
CUIT, phone nor address. Employees: only active ones (never created here), with their current functions, the
module's salespeople first without hiding the others. A viewer gets only the name (and functions for a person).
"""

from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.commercial.assignments.service import current_zone_ids
from app.modules.commercial.config.models import Zone
from app.platform.customers.models import Customer
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.mappings.service import code_key, label_key, source_system_id
from app.platform.parties.functions import registry as functions
from app.platform.parties.models import Employee, EmployeeFunction

SALESPERSON = "commercial.salesperson"


@dataclass(frozen=True)
class Target:
    id: int
    name: str
    active: bool
    legal_name: str | None = None
    store_sys_code: str | None = None
    zone: str | None = None
    functions: tuple[str, ...] = ()
    is_salesperson: bool = False


def _store_sys_codes(db: Session, customer_ids: Sequence[int]) -> dict[int, str]:
    if not customer_ids:
        return {}
    rows = db.execute(
        select(ExternalEntityMapping.entity_id, ExternalEntityMapping.external_id)
        .where(
            ExternalEntityMapping.entity_type == "customer",
            ExternalEntityMapping.source_system_id == source_system_id(db, "STORE_SYS"),
            ExternalEntityMapping.namespace == "customer",
            ExternalEntityMapping.status == "confirmed",
            ExternalEntityMapping.entity_id.in_(customer_ids),
        )
        .order_by(ExternalEntityMapping.id)
    ).all()
    found: dict[int, str] = {}
    for entity_id, external_id in rows:
        if entity_id is not None:
            found.setdefault(entity_id, external_id)
    return found


def _customers(db: Session, rows: Sequence[Customer], full: bool) -> list[Target]:
    if not full:
        return [Target(c.id, c.display_name, c.active) for c in rows]
    ids = [c.id for c in rows]
    codes = _store_sys_codes(db, ids)
    zone_ids = current_zone_ids(db, ids)
    zones = (
        dict(db.execute(select(Zone.id, Zone.display_name).where(Zone.id.in_(set(zone_ids.values())))).all())
        if zone_ids
        else {}
    )
    return [
        Target(
            c.id,
            c.display_name,
            c.active,
            legal_name=c.legal_name,
            store_sys_code=codes.get(c.id),
            zone=zones.get(zone_ids[c.id]) if c.id in zone_ids else None,
        )
        for c in rows
    ]


def _current_functions(db: Session, ids: Sequence[int]) -> dict[int, list[str]]:
    today = business_today()
    found: dict[int, list[str]] = {i: [] for i in ids}
    if not ids:
        return found
    rows = db.execute(
        select(EmployeeFunction.employee_id, EmployeeFunction.function_code)
        .where(
            EmployeeFunction.employee_id.in_(ids),
            EmployeeFunction.valid_from <= today,
            or_(EmployeeFunction.valid_to.is_(None), EmployeeFunction.valid_to >= today),
        )
        .order_by(EmployeeFunction.function_code)
    ).all()
    for employee_id, code in rows:
        found[int(employee_id)].append(code)
    return found


def _employees(db: Session, rows: Sequence[Employee]) -> list[Target]:
    current = _current_functions(db, [e.id for e in rows])
    result = []
    for e in rows:
        codes = current[e.id]
        labels = tuple((spec.label if (spec := functions.get(code)) else code) for code in codes)
        result.append(Target(e.id, e.full_name, e.active, functions=labels, is_salesperson=SALESPERSON in codes))
    return result


def describe(db: Session, entity_type: str, ids: Sequence[int], *, full: bool) -> dict[int, Target]:
    """The linked entity and the candidates of a mapping, as the detail shows them."""
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    if entity_type == "customer":
        found = _customers(db, db.scalars(select(Customer).where(Customer.id.in_(wanted))).all(), full)
    elif entity_type == "employee":
        found = _employees(db, db.scalars(select(Employee).where(Employee.id.in_(wanted))).all())
    else:
        return {}
    return {t.id: t for t in found}


def search(db: Session, entity_type: str, q: str | None, *, limit: int) -> list[Target]:
    """Linkable entities (active) for a person deciding a mapping."""
    text = " ".join((q or "").split())
    if entity_type == "customer":
        query = select(Customer).where(Customer.active.is_(True))
        if text:
            pattern = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            by_code = select(ExternalEntityMapping.entity_id).where(
                ExternalEntityMapping.entity_type == "customer",
                ExternalEntityMapping.source_system_id == source_system_id(db, "STORE_SYS"),
                ExternalEntityMapping.namespace == "customer",
                ExternalEntityMapping.status == "confirmed",
                ExternalEntityMapping.external_id == code_key(text),
            )
            query = query.where(
                or_(
                    Customer.display_name_key.contains(label_key(text), autoescape=True),
                    Customer.legal_name.ilike(f"%{pattern}%", escape="\\"),
                    Customer.id.in_(by_code),
                )
            )
        rows = db.scalars(query.order_by(Customer.display_name_key, Customer.id).limit(limit)).all()
        return _customers(db, rows, full=True)
    if entity_type == "employee":
        today = business_today()
        salesperson = select(EmployeeFunction.employee_id).where(
            EmployeeFunction.function_code == SALESPERSON,
            EmployeeFunction.valid_from <= today,
            or_(EmployeeFunction.valid_to.is_(None), EmployeeFunction.valid_to >= today),
        )
        people = select(Employee).where(Employee.active.is_(True))
        if text:
            pattern = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            people = people.where(Employee.full_name.ilike(f"%{pattern}%", escape="\\"))
        first = case((Employee.id.in_(salesperson), 0), else_=1)
        found = db.scalars(people.order_by(first, func.lower(Employee.full_name), Employee.id).limit(limit)).all()
        return _employees(db, found)
    return []
