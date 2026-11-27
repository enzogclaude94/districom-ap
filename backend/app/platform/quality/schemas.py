"""Calidad API (F1.6). Built field by field from the issue and its code: `details` and `external_ref` never leave
as they are stored."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class QualityGroupOut(BaseModel):
    key: str
    label: str


class QualityCodeOut(BaseModel):
    code: str
    label: str
    severity: str
    group: QualityGroupOut
    description: str
    action: str
    closes_when: str  # empty: only a person closes it
    fix: str | None  # mapping, import, masters or None (no screen yet)


class IssueSourceOut(BaseModel):
    job_id: int | None
    import_type: str | None
    import_label: str | None
    row_id: int | None
    row_number: int | None
    sheet: str | None


class IssueOut(BaseModel):
    id: int
    code: str
    label: str
    severity: str
    status: str
    group: QualityGroupOut
    subject: str | None  # what it affects, in business words
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
    source: IssueSourceOut
    mapping_id: int | None
    fix: str | None
    # With fix `masters`: the path of the exact record to correct, built by the owner module (F1.7); else null.
    fix_href: str | None = None
    action: str
    resolved_at: datetime | None
    resolved_by_name: str | None


class IssueDetailOut(IssueOut):
    description: str
    closes_when: str
    facts: dict[str, Any]  # only counts, row numbers, statuses and ids (`review.SAFE_DETAILS`)
    resolution_note: str | None
    resolved_automatically: bool  # its condition went away (import, mapping, configuration), not closed in Calidad
    # Secondary technical reference, for whoever decides; never PII.
    dedupe_key: str | None = None
    entity_type: str | None = None
    entity_id: int | None = None


class IssuePage(BaseModel):
    items: list[IssueOut]
    total: int
    limit: int
    offset: int


class CountedOut(BaseModel):
    issues: int  # distinct problems
    occurrences: int  # times they were seen


class IssueSummaryOut(BaseModel):
    open: dict[str, CountedOut]  # per severity: error, warning, info
    open_total: CountedOut
    resolved: CountedOut
    ignored: CountedOut


class IssueDecisionIn(BaseModel):
    note: str = Field(max_length=2000)
