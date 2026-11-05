from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


class ImportTypeOut(BaseModel):
    key: str
    name: str
    module_key: str
    source_system: str
    can_import: bool  # the user is operator of the module


class ImportFileOut(BaseModel):
    id: int
    original_filename: str
    size_bytes: int
    detected_format: str | None
    sha256: str
    uploaded_at: datetime


class RowCounts(BaseModel):
    detected: int
    processed: int
    valid: int
    rejected: int
    duplicate: int
    skipped: int
    pending_resolution: int
    inserted: int
    updated: int
    unchanged: int
    superseded: int
    warnings: int
    errors: int


class ImportJobItem(BaseModel):
    id: int
    import_type: str | None
    import_type_name: str | None
    status: str
    original_filename: str
    created_by_name: str
    created_at: datetime
    finished_at: datetime | None
    rows_detected: int
    rows_rejected: int
    failure_reason: str | None


class ImportJobPage(BaseModel):
    items: list[ImportJobItem]
    total: int


class ImportJobDetail(BaseModel):
    id: int
    import_type: str | None
    import_type_name: str | None
    source_system: str | None
    status: str
    parser_version: str | None
    file: ImportFileOut
    created_by_name: str
    confirmed_by_name: str | None
    created_at: datetime
    analyzed_at: datetime | None
    confirmed_at: datetime | None
    finished_at: datetime | None
    counts: RowCounts
    snapshot_date: date | None
    period_from: date | None
    period_to: date | None
    scope: dict[str, Any] | None
    # Reader, detection scores, candidates and the adapter's preview; never row values.
    metadata: dict[str, Any]
    failure_reason: str | None
    failure_detail: str | None
    reprocess_of_job_id: int | None
    duplicate_of_job_id: int | None
    can_confirm: bool
    can_decide: bool  # an admin of the type's module, who may answer a blocked confirmation
    can_reprocess: bool


class ConfirmRequest(BaseModel):
    """Only to answer a confirmation the importer stopped (409 `import_decision_required`): admin, with a reason."""

    decision: Literal["replace", "additional"] | None = None
    reason: str | None = Field(default=None, max_length=500)


class ImportRowOut(BaseModel):
    id: int
    sheet: str
    row_number: int
    status: str
    outcome: str | None
    raw: list[Any]
    normalized: dict[str, Any] | None
    business_key: str | None
    entity_type: str | None
    entity_id: int | None
    messages: list[dict[str, Any]]


class ImportRowPage(BaseModel):
    items: list[ImportRowOut]
    total: int


class QualityIssueOut(BaseModel):
    id: int
    code: str
    severity: str
    status: str
    entity_type: str | None
    entity_id: int | None
    row_id: int | None
    details: dict[str, Any]
    occurrences: int
    first_seen_at: datetime
    last_seen_at: datetime
