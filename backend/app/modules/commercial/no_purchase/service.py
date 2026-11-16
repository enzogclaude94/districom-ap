"""What a new AXUM report does to the no-purchase events already loaded (ADR 018 decision 8, docs/IMPORTS.md §5.4).

The report shows neither the period it was asked for nor its filters, may lack days and salespeople, and a later
sync can add events to a day already exported: nothing in it proves what it covers. So, unlike STORE SYS sales,
there is no automatic replacement:
- no active event between the report's first and last day → insert;
- any active event in that period → blocked until a module admin decides, with a reason:
  `replace` marks SUPERSEDED (never deletes) the active events of that period **of the salespeople the new report
  brings**; the other salespeople's events stay; `additional` keeps them all.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.commercial.no_purchase.models import NoPurchaseEvent

INSERT, BLOCKED = "insert", "blocked"
OVERLAP_REASON = "no_purchase_overlap"


@dataclass(frozen=True)
class Replaced:
    job_id: int
    days: tuple[date, ...]
    lines: int  # events that `replace` would supersede
    salespeople: tuple[str, ...]


@dataclass(frozen=True)
class Plan:
    action: str  # insert (nothing loaded in the period) or blocked (an admin decides)
    overlap_days: tuple[date, ...] = ()
    replaces: tuple[Replaced, ...] = ()
    kept: int = 0  # active events of the period from salespeople the report does not bring: `replace` keeps them

    def as_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "overlap_days": [d.isoformat() for d in self.overlap_days],
            "uncovered_days": [],
            "reasons": [OVERLAP_REASON] if self.action == BLOCKED else [],
            "replaces": [
                {
                    "job_id": r.job_id,
                    "scope": None,
                    "days": [d.isoformat() for d in r.days],
                    "lines": r.lines,
                    "salespeople": list(r.salespeople),
                }
                for r in self.replaces
            ],
            "lines": sum(r.lines for r in self.replaces),
            "kept_other_salespeople": self.kept,
        }


def plan(db: Session, *, period_from: date, period_to: date, salespeople: set[str]) -> Plan:
    counts = db.execute(
        select(
            NoPurchaseEvent.import_job_id,
            NoPurchaseEvent.business_date,
            NoPurchaseEvent.source_salesperson,
            func.count(),
        )
        .where(
            NoPurchaseEvent.status == "ACTIVE",
            NoPurchaseEvent.business_date >= period_from,
            NoPurchaseEvent.business_date <= period_to,
        )
        .group_by(NoPurchaseEvent.import_job_id, NoPurchaseEvent.business_date, NoPurchaseEvent.source_salesperson)
    ).all()
    if not counts:
        return Plan(INSERT)
    days: dict[int, set[date]] = defaultdict(set)
    replaced: dict[int, int] = defaultdict(int)
    people: dict[int, set[str]] = defaultdict(set)
    kept = 0
    for job_id, day, salesperson, events in counts:
        days[job_id].add(day)
        if salesperson in salespeople:
            replaced[job_id] += events
            people[job_id].add(salesperson)
        else:
            kept += events
    return Plan(
        BLOCKED,
        tuple(sorted({d for job_days in days.values() for d in job_days})),
        tuple(
            Replaced(job_id, tuple(sorted(job_days)), replaced[job_id], tuple(sorted(people[job_id])))
            for job_id, job_days in sorted(days.items())
        ),
        kept,
    )


def supersede(db: Session, *, period_from: date, period_to: date, salespeople: set[str], job_id: int) -> int:
    """Mark as replaced by `job_id` the active events of the period of `salespeople`; returns how many."""
    if not salespeople:
        return 0
    result = db.execute(
        update(NoPurchaseEvent)
        .where(
            NoPurchaseEvent.status == "ACTIVE",
            NoPurchaseEvent.business_date >= period_from,
            NoPurchaseEvent.business_date <= period_to,
            NoPurchaseEvent.source_salesperson.in_(salespeople),
            NoPurchaseEvent.import_job_id != job_id,
        )
        .values(status="SUPERSEDED", superseded_by_job_id=job_id, updated_at=func.now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
