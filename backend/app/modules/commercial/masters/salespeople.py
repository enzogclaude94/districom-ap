"""Vendedores / Preventistas as Maestros shows them (F1.7). There is no second people master: the people are
`public.employees` (Empleados, platform), the Preventista function is `commercial.salesperson`, and the codes STORE
SYS and AXUM use for them are mappings (Mapeos). This reads all three and writes nothing; in particular it never
touches `employees.external_store_id`.
"""

from collections import Counter

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.clock import business_today
from app.modules.commercial.assignments.models import CustomerSalespersonAssignment
from app.modules.commercial.masters.schemas import EmployeeMappingOut, SalespeopleOut, SalespersonOut
from app.platform.mappings import resolvers
from app.platform.mappings import service as mappings
from app.platform.mappings.models import ExternalEntityMapping
from app.platform.parties.models import Employee, EmployeeFunction

SALESPERSON = "commercial.salesperson"
# The codes that name a seller: STORE SYS «vendedor» and AXUM «preventista» (docs/IMPORTS.md §6).
KINDS = (("STORE_SYS", "seller"), ("AXUM", "salesperson"))
UNLINKED = ("pending", "ambiguous", "rejected", "excluded")

_CURRENT_SELLER = (
    CustomerSalespersonAssignment.voided_by_job_id.is_(None),
    CustomerSalespersonAssignment.valid_to.is_(None),
)


def _seller_mappings(db: Session) -> list[ExternalEntityMapping]:
    systems = {code: mappings.source_system_id(db, code) for code, _ in KINDS}
    return list(
        db.scalars(
            select(ExternalEntityMapping)
            .where(
                ExternalEntityMapping.entity_type == "employee",
                or_(
                    *(
                        (ExternalEntityMapping.source_system_id == systems[code])
                        & (ExternalEntityMapping.namespace == namespace)
                        for code, namespace in KINDS
                    )
                ),
            )
            .order_by(ExternalEntityMapping.source_system_id, ExternalEntityMapping.external_id)
        ).all()
    )


def overview(db: Session) -> SalespeopleOut:
    """Employees with the Preventista function (ever) or with a seller code linked, and the seller codes not linked
    to anyone: one query per kind, the counts in the database."""
    today = business_today()
    codes = _seller_mappings(db)
    systems = mappings.source_codes(db)
    per_mapping = Counter(
        dict(
            db.execute(
                select(CustomerSalespersonAssignment.seller_mapping_id, func.count())
                .where(*_CURRENT_SELLER, CustomerSalespersonAssignment.seller_mapping_id.is_not(None))
                .group_by(CustomerSalespersonAssignment.seller_mapping_id)
            ).all()
        )
    )
    per_person = dict(
        db.execute(
            select(CustomerSalespersonAssignment.salesperson_id, func.count())
            .where(*_CURRENT_SELLER, CustomerSalespersonAssignment.salesperson_id.is_not(None))
            .group_by(CustomerSalespersonAssignment.salesperson_id)
        ).all()
    )

    def out(m: ExternalEntityMapping) -> EmployeeMappingOut:
        system = systems[m.source_system_id]
        return EmployeeMappingOut(
            id=m.id,
            source_system=system,
            namespace=m.namespace,
            kind_label=resolvers.registry.label((m.entity_type, system, m.namespace)),
            external_id=m.external_id,
            external_label=m.external_label,
            status=m.status,
            customers=per_mapping.get(m.id, 0),
        )

    linked: dict[int, list[EmployeeMappingOut]] = {}
    for m in codes:
        if m.status == "confirmed" and m.entity_id is not None:
            linked.setdefault(m.entity_id, []).append(out(m))
    ever = set(db.scalars(select(EmployeeFunction.employee_id).where(EmployeeFunction.function_code == SALESPERSON)))
    now = set(
        db.scalars(
            select(EmployeeFunction.employee_id).where(
                EmployeeFunction.function_code == SALESPERSON,
                EmployeeFunction.valid_from <= today,
                or_(EmployeeFunction.valid_to.is_(None), EmployeeFunction.valid_to >= today),
            )
        )
    )
    ids = ever | set(linked) | {i for i in per_person if i is not None}
    people = (
        db.execute(
            select(Employee.id, Employee.full_name, Employee.active)
            .where(Employee.id.in_(ids))
            .order_by(func.lower(Employee.full_name), Employee.id)
        ).all()
        if ids
        else []
    )
    return SalespeopleOut(
        items=[
            SalespersonOut(
                id=person_id,
                name=name,
                active=active,
                is_salesperson=person_id in now,
                mappings=linked.get(person_id, []),
                customers=int(per_person.get(person_id, 0)),
            )
            for person_id, name, active in people
        ],
        unlinked=[out(m) for m in codes if m.status in UNLINKED],
    )
