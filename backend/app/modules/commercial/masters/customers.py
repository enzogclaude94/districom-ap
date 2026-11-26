"""Customers as Maestros shows them (F1.7): the platform's customer plus what Comercial knows of it (its STORE SYS
code, current zone and current seller) and, in the detail, its codes and assignment history.

The customer stays the platform's (`public.customers`, PII and identity rules there); this only composes. Nothing
here writes: zone and seller history come from the STORE SYS master (`assignments`), codes from Mapeos.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import ColumnElement, Select, select
from sqlalchemy.orm import Session

from app.modules.commercial.assignments.models import CustomerSalespersonAssignment, CustomerZoneAssignment
from app.modules.commercial.config.models import Zone
from app.modules.commercial.masters.schemas import (
    CustomerHistoryOut,
    CustomerMappingOut,
    SellerAssignmentOut,
    SellerRefOut,
    ZoneAssignmentOut,
    ZoneRefOut,
)
from app.platform.customers.models import Customer
from app.platform.mappings import resolvers
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.mappings.service import code_key, source_system_id
from app.platform.parties.models import Employee

CURRENT_ZONE = (CustomerZoneAssignment.voided_by_job_id.is_(None), CustomerZoneAssignment.valid_to.is_(None))
CURRENT_SELLER = (
    CustomerSalespersonAssignment.voided_by_job_id.is_(None),
    CustomerSalespersonAssignment.valid_to.is_(None),
)


def _store_sys_customer(db: Session) -> tuple[ColumnElement[bool], ...]:
    return (
        ExternalEntityMapping.entity_type == "customer",
        ExternalEntityMapping.source_system_id == source_system_id(db, "STORE_SYS"),
        ExternalEntityMapping.namespace == "customer",
        ExternalEntityMapping.status == "confirmed",
    )


def by_code(db: Session, q: str | None) -> Select[Any] | None:
    """The customer a text names as its STORE SYS code (exact, through its confirmed mapping)."""
    key = code_key(q or "")
    if not key:
        return None
    return select(ExternalEntityMapping.entity_id).where(
        *_store_sys_customer(db),
        ExternalEntityMapping.external_id == key,
    )


def in_zone(zone_id: int) -> Select[Any]:
    return select(CustomerZoneAssignment.customer_id).where(*CURRENT_ZONE, CustomerZoneAssignment.zone_id == zone_id)


def with_salesperson(salesperson_id: int) -> Select[Any]:
    return select(CustomerSalespersonAssignment.customer_id).where(
        *CURRENT_SELLER, CustomerSalespersonAssignment.salesperson_id == salesperson_id
    )


@dataclass(frozen=True)
class Current:
    store_sys_code: str | None = None
    zone: ZoneRefOut | None = None
    salesperson: SellerRefOut | None = None


def _names(db: Session, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    return dict(db.execute(select(Employee.id, Employee.full_name).where(Employee.id.in_(ids))).all())


def current(db: Session, customers: Sequence[Customer]) -> dict[int, Current]:
    """Code, zone and seller in force for a page of customers: one query per kind, never one per customer."""
    ids = [c.id for c in customers]
    if not ids:
        return {}
    codes: dict[int, str] = {}
    for entity_id, external_id in db.execute(
        select(ExternalEntityMapping.entity_id, ExternalEntityMapping.external_id)
        .where(*_store_sys_customer(db), ExternalEntityMapping.entity_id.in_(ids))
        .order_by(ExternalEntityMapping.id)
    ):
        if entity_id is not None:
            codes.setdefault(entity_id, external_id)
    zones = {
        customer_id: ZoneRefOut(id=zone_id, name=name)
        for customer_id, zone_id, name in db.execute(
            select(CustomerZoneAssignment.customer_id, Zone.id, Zone.display_name)
            .join(Zone, Zone.id == CustomerZoneAssignment.zone_id)
            .where(*CURRENT_ZONE, CustomerZoneAssignment.customer_id.in_(ids))
        )
    }
    sellers = db.scalars(
        select(CustomerSalespersonAssignment).where(*CURRENT_SELLER, CustomerSalespersonAssignment.customer_id.in_(ids))
    ).all()
    names = _names(db, {s.salesperson_id for s in sellers if s.salesperson_id is not None})
    by_customer = {
        s.customer_id: SellerRefOut(
            salesperson_id=s.salesperson_id,
            salesperson_name=names.get(s.salesperson_id) if s.salesperson_id is not None else None,
            source_label=s.source_seller_label,
            mapping_id=s.seller_mapping_id,
            valid_from=s.valid_from,
        )
        for s in sellers
    }
    return {i: Current(codes.get(i), zones.get(i), by_customer.get(i)) for i in ids}


def history(db: Session, customer_id: int) -> CustomerHistoryOut:
    """Every code linked to the customer and its zone and seller history, newest first (voided rows included and
    marked: history is never hidden)."""
    systems = mappings.source_codes(db)
    linked = [
        CustomerMappingOut(
            id=m.id,
            source_system=systems[m.source_system_id],
            namespace=m.namespace,
            kind_label=resolvers.registry.label((m.entity_type, systems[m.source_system_id], m.namespace)),
            external_id=m.external_id,
            status=m.status,
            method=m.method,
        )
        for m in mappings.mappings_of(db, "customer", customer_id)
    ]
    zones = [
        ZoneAssignmentOut(
            id=row.id,
            zone=ZoneRefOut(id=zone.id, name=zone.display_name),
            valid_from=row.valid_from,
            valid_to=row.valid_to,
            voided=row.voided_by_job_id is not None,
            source=row.source,
            import_job_id=row.import_job_id,
        )
        for row, zone in db.execute(
            select(CustomerZoneAssignment, Zone)
            .join(Zone, Zone.id == CustomerZoneAssignment.zone_id)
            .where(CustomerZoneAssignment.customer_id == customer_id)
            .order_by(CustomerZoneAssignment.valid_from.desc(), CustomerZoneAssignment.id.desc())
        )
    ]
    sellers = db.scalars(
        select(CustomerSalespersonAssignment)
        .where(CustomerSalespersonAssignment.customer_id == customer_id)
        .order_by(CustomerSalespersonAssignment.valid_from.desc(), CustomerSalespersonAssignment.id.desc())
    ).all()
    names = _names(db, {s.salesperson_id for s in sellers if s.salesperson_id is not None})
    return CustomerHistoryOut(
        customer_id=customer_id,
        mappings=linked,
        zones=zones,
        salespeople=[
            SellerAssignmentOut(
                id=s.id,
                salesperson_id=s.salesperson_id,
                salesperson_name=names.get(s.salesperson_id) if s.salesperson_id is not None else None,
                source_label=s.source_seller_label,
                mapping_id=s.seller_mapping_id,
                valid_from=s.valid_from,
                valid_to=s.valid_to,
                voided=s.voided_by_job_id is not None,
                source=s.source,
                import_job_id=s.import_job_id,
            )
            for s in sellers
        ],
    )
