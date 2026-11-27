from datetime import date

from pydantic import BaseModel


class ZoneRefOut(BaseModel):
    id: int
    name: str  # the zone's display name


class SellerRefOut(BaseModel):
    """A customer's seller as the STORE SYS master names it, and the employee its mapping resolved to (if any)."""

    salesperson_id: int | None
    salesperson_name: str | None
    source_label: str | None
    mapping_id: int | None
    valid_from: date


class CustomerMappingOut(BaseModel):
    id: int
    source_system: str
    namespace: str
    kind_label: str | None
    external_id: str
    status: str
    method: str | None


class ZoneAssignmentOut(BaseModel):
    id: int
    zone: ZoneRefOut
    valid_from: date
    valid_to: date | None
    voided: bool
    source: str
    import_job_id: int | None


class SellerAssignmentOut(BaseModel):
    id: int
    salesperson_id: int | None
    salesperson_name: str | None
    source_label: str | None
    mapping_id: int | None
    valid_from: date
    valid_to: date | None
    voided: bool
    source: str
    import_job_id: int | None


class CustomerHistoryOut(BaseModel):
    """Codes and the assignment history of a customer, read only: the STORE SYS master owns them (F1.7)."""

    customer_id: int
    mappings: list[CustomerMappingOut]
    zones: list[ZoneAssignmentOut]
    salespeople: list[SellerAssignmentOut]


class EmployeeMappingOut(BaseModel):
    id: int
    source_system: str
    namespace: str
    kind_label: str | None
    external_id: str
    external_label: str | None
    status: str
    customers: int  # current customer assignments that came through this code (STORE SYS sellers)


class SalespersonOut(BaseModel):
    """An employee as Comercial sees it: never phone nor hire date. Created and edited only in Empleados."""

    id: int
    name: str
    active: bool
    is_salesperson: bool  # has the Preventista function today
    mappings: list[EmployeeMappingOut]
    customers: int  # customers currently assigned to them


class SalespeopleOut(BaseModel):
    items: list[SalespersonOut]
    # Seller and AXUM salesperson codes not linked to anyone yet (pending, ambiguous, rejected) or deliberately
    # excluded: what a person resolves in Mapeos.
    unlinked: list[EmployeeMappingOut]
