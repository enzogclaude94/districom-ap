"""Applying a master snapshot to a customer's zone and salesperson history (DATA_MODEL §5, docs/IMPORTS.md §3.2).

The same assignment changes nothing. A different one closes the open row the day before the snapshot and opens
another; an empty one only closes. When the open row was opened by a snapshot of that same day it is voided instead
of closed (it would end before it started). A snapshot older than the history is refused: the past is never
rewritten in place.
"""

from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.commercial.assignments.models import CustomerSalespersonAssignment, CustomerZoneAssignment

# What applying a snapshot did to one assignment.
UNCHANGED, OPENED, CHANGED, CLOSED, NONE = "unchanged", "opened", "changed", "closed", "none"


class AssignmentOutOfOrderError(ValueError):
    """The snapshot is older than the customer's history."""


def _open_row[Row: (CustomerZoneAssignment, CustomerSalespersonAssignment)](
    db: Session, model: type[Row], customer_id: int
) -> Row | None:
    return db.scalars(
        select(model)
        .where(model.customer_id == customer_id, model.voided_by_job_id.is_(None), model.valid_to.is_(None))
        .with_for_update()
    ).first()


def _check_order[Row: (CustomerZoneAssignment, CustomerSalespersonAssignment)](
    db: Session, model: type[Row], customer_id: int, snapshot: date
) -> None:
    latest = db.scalar(
        select(func.max(func.greatest(model.valid_from, model.valid_to + 1))).where(
            model.customer_id == customer_id, model.voided_by_job_id.is_(None)
        )
    )
    if latest is not None and latest > snapshot:
        raise AssignmentOutOfOrderError(f"Snapshot {snapshot} is older than the history of customer {customer_id}")


def _end[Row: (CustomerZoneAssignment, CustomerSalespersonAssignment)](row: Row, snapshot: date, job_id: int) -> None:
    if row.valid_from >= snapshot:
        row.voided_by_job_id = job_id
        row.voided_at = func.now()
    else:
        row.valid_to = snapshot - timedelta(days=1)


def _apply[Row: (CustomerZoneAssignment, CustomerSalespersonAssignment)](
    db: Session,
    model: type[Row],
    customer_id: int,
    values: dict[str, Any] | None,
    same: tuple[str, ...],
    *,
    snapshot: date,
    job_id: int,
    user_id: int | None,
) -> str:
    _check_order(db, model, customer_id, snapshot)
    current = _open_row(db, model, customer_id)
    if values is None:
        if current is None:
            return NONE
        _end(current, snapshot, job_id)
        db.flush()
        return CLOSED
    if current is not None and all(getattr(current, k) == values[k] for k in same):
        return UNCHANGED
    if current is not None:
        _end(current, snapshot, job_id)
        db.flush()
    db.add(
        model(
            customer_id=customer_id,
            valid_from=snapshot,
            source="STORE_SYS",
            import_job_id=job_id,
            created_by=user_id,
            **values,
        )
    )
    db.flush()
    return OPENED if current is None else CHANGED


def apply_zone(
    db: Session, customer_id: int, zone_id: int | None, *, snapshot: date, job_id: int, user_id: int | None
) -> str:
    values = None if zone_id is None else {"zone_id": zone_id}
    return _apply(
        db, CustomerZoneAssignment, customer_id, values, ("zone_id",), snapshot=snapshot, job_id=job_id, user_id=user_id
    )


def apply_seller(
    db: Session,
    customer_id: int,
    *,
    label: str | None,
    key: str | None,
    salesperson_id: int | None,
    mapping_id: int | None,
    snapshot: date,
    job_id: int,
    user_id: int | None,
) -> str:
    """The seller is compared by its label's key: a mapping confirmed later does not make it a «change»."""
    values = (
        None
        if key is None
        else {
            "source_seller_label": label,
            "source_seller_key": key,
            "salesperson_id": salesperson_id,
            "seller_mapping_id": mapping_id,
        }
    )
    return _apply(
        db,
        CustomerSalespersonAssignment,
        customer_id,
        values,
        ("source_seller_key",),
        snapshot=snapshot,
        job_id=job_id,
        user_id=user_id,
    )


def current_zone_ids(db: Session, customer_ids: list[int]) -> dict[int, int]:
    if not customer_ids:
        return {}
    rows = db.execute(
        select(CustomerZoneAssignment.customer_id, CustomerZoneAssignment.zone_id).where(
            CustomerZoneAssignment.customer_id.in_(customer_ids),
            CustomerZoneAssignment.voided_by_job_id.is_(None),
            CustomerZoneAssignment.valid_to.is_(None),
        )
    )
    return dict(rows.all())


def current_seller_keys(db: Session, customer_ids: list[int]) -> dict[int, str | None]:
    if not customer_ids:
        return {}
    rows = db.execute(
        select(CustomerSalespersonAssignment.customer_id, CustomerSalespersonAssignment.source_seller_key).where(
            CustomerSalespersonAssignment.customer_id.in_(customer_ids),
            CustomerSalespersonAssignment.voided_by_job_id.is_(None),
            CustomerSalespersonAssignment.valid_to.is_(None),
        )
    )
    return dict(rows.all())


def zone_on(db: Session, customer_id: int, on: date) -> int | None:
    """The customer's zone on a day, for historical attribution."""
    return db.scalar(
        select(CustomerZoneAssignment.zone_id).where(
            CustomerZoneAssignment.customer_id == customer_id,
            CustomerZoneAssignment.voided_by_job_id.is_(None),
            CustomerZoneAssignment.valid_from <= on,
            (CustomerZoneAssignment.valid_to.is_(None)) | (CustomerZoneAssignment.valid_to >= on),
        )
    )


def seller_on(db: Session, customer_id: int, on: date) -> CustomerSalespersonAssignment | None:
    return db.scalars(
        select(CustomerSalespersonAssignment).where(
            CustomerSalespersonAssignment.customer_id == customer_id,
            CustomerSalespersonAssignment.voided_by_job_id.is_(None),
            CustomerSalespersonAssignment.valid_from <= on,
            (CustomerSalespersonAssignment.valid_to.is_(None)) | (CustomerSalespersonAssignment.valid_to >= on),
        )
    ).first()
