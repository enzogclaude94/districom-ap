"""Entity types a mapping may point to, each with how to check that an id names one that can be linked.

Only platform identities today (DATA_MODEL §7.4): `customer` and `employee`. Products and zones of STORE SYS are
identified in their own tables (DATA_MODEL §1, exception 2); a module would register its own type here only when
another source brings codes for them.
"""

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.customers.models import Customer
from app.platform.parties.models import Employee


@dataclass(frozen=True)
class EntityTypeSpec:
    key: str
    label: str
    # True when `entity_id` names an entity a mapping may be confirmed to (it exists and is active).
    linkable: Callable[[Session, int], bool]


def _customer(db: Session, entity_id: int) -> bool:
    return db.scalar(select(Customer.active).where(Customer.id == entity_id)) is True


def _employee(db: Session, entity_id: int) -> bool:
    return db.scalar(select(Employee.active).where(Employee.id == entity_id)) is True


ENTITY_TYPES: dict[str, EntityTypeSpec] = {
    spec.key: spec
    for spec in (EntityTypeSpec("customer", "Cliente", _customer), EntityTypeSpec("employee", "Empleado", _employee))
}
