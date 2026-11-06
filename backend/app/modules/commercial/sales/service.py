"""Replacing earlier sale lines by period and scope (docs/IMPORTS.md §5.3, D-G).

The report has no document number and two identical lines can be two sales, so lines are never deduplicated by
content. A new file replaces the active lines of the days it covers only when the system can prove it: the new
export **and** every earlier one with active lines on those days were declared FULL (no filters), and the new file
has rows on each of those days. Then the earlier lines of those days become SUPERSEDED (never deleted) in the same
transaction. In any other case the confirmation stops for an admin's explicit decision.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.modules.commercial.sales.models import SaleLine
from app.platform.imports.models import ImportJob

INSERT, REPLACE, BLOCKED = "insert", "replace", "blocked"


@dataclass(frozen=True)
class Replaced:
    job_id: int
    scope: str | None
    days: tuple[date, ...]
    lines: int


@dataclass(frozen=True)
class Plan:
    action: str  # insert (nothing overlaps), replace (provably safe) or blocked (an admin decides)
    overlap_days: tuple[date, ...] = ()
    replaces: tuple[Replaced, ...] = ()
    reasons: tuple[str, ...] = ()
    uncovered_days: tuple[date, ...] = field(default_factory=tuple)

    def as_json(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "overlap_days": [d.isoformat() for d in self.overlap_days],
            "uncovered_days": [d.isoformat() for d in self.uncovered_days],
            "reasons": list(self.reasons),
            "replaces": [
                {"job_id": r.job_id, "scope": r.scope, "days": [d.isoformat() for d in r.days], "lines": r.lines}
                for r in self.replaces
            ],
            "lines": sum(r.lines for r in self.replaces),
        }


def declared_scope(job_scope: dict[str, Any] | None) -> str | None:
    return (job_scope or {}).get("declared")


def plan(db: Session, *, days: set[date], period_from: date, period_to: date, scope: str | None) -> Plan:
    """What confirming a file with rows on `days` (between `period_from` and `period_to`) would do to the active
    lines already loaded in that period."""
    counts = db.execute(
        select(SaleLine.import_job_id, SaleLine.business_date, func.count())
        .where(
            SaleLine.status == "ACTIVE",
            SaleLine.business_date >= period_from,
            SaleLine.business_date <= period_to,
        )
        .group_by(SaleLine.import_job_id, SaleLine.business_date)
    ).all()
    if not counts:
        return Plan(INSERT)
    per_job: dict[int, dict[date, int]] = defaultdict(dict)
    for job_id, day, lines in counts:
        per_job[job_id][day] = lines
    scopes = {
        job_id: declared_scope(job_scope)
        for job_id, job_scope in db.execute(select(ImportJob.id, ImportJob.scope).where(ImportJob.id.in_(per_job)))
    }
    overlap = {day for job_days in per_job.values() for day in job_days}
    uncovered = tuple(sorted(overlap - days))
    reasons = []
    if scope != "FULL":
        reasons.append("new_scope_not_full")
    if any(scopes.get(job_id) != "FULL" for job_id in per_job):
        reasons.append("previous_scope_not_full")
    if uncovered:
        reasons.append("days_without_rows")
    replaces = tuple(
        Replaced(job_id, scopes.get(job_id), tuple(sorted(job_days)), sum(job_days.values()))
        for job_id, job_days in sorted(per_job.items())
    )
    return Plan(BLOCKED if reasons else REPLACE, tuple(sorted(overlap)), replaces, tuple(reasons), uncovered)


def supersede(db: Session, days: tuple[date, ...], *, job_id: int) -> int:
    """Mark the active lines of `days` as replaced by `job_id`; returns how many."""
    if not days:
        return 0
    result = db.execute(
        update(SaleLine)
        .where(SaleLine.status == "ACTIVE", SaleLine.business_date.in_(days), SaleLine.import_job_id != job_id)
        .values(status="SUPERSEDED", superseded_by_job_id=job_id, updated_at=func.now())
    )
    return int(result.rowcount or 0)  # type: ignore[attr-defined]
