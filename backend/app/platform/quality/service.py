from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.platform.quality.models import DataQualityIssue
from app.platform.quality.registry import registry


def record_issue(
    db: Session,
    code: str,
    dedupe_key: str,
    *,
    job_id: int | None = None,
    row_id: int | None = None,
    entity_type: str | None = None,
    entity_id: int | None = None,
    external_ref: dict[str, Any] | None = None,
    details: dict[str, Any] | None = None,
) -> int:
    """Open the issue, or add an occurrence to the open one with the same `(code, dedupe_key)`, in the caller's
    transaction. The severity is the code's; an unregistered code is a bug. Returns the issue id."""
    spec = registry.get(code)
    if spec is None:
        raise ValueError(f"Unregistered quality code: {code}")
    statement = (
        insert(DataQualityIssue)
        .values(
            code=code,
            severity=spec.severity,
            dedupe_key=dedupe_key,
            job_id=job_id,
            row_id=row_id,
            entity_type=entity_type,
            entity_id=entity_id,
            external_ref=external_ref,
            details=details or {},
        )
        .on_conflict_do_update(
            index_elements=["code", "dedupe_key"],
            index_where=DataQualityIssue.status == "open",
            set_={
                "occurrences": DataQualityIssue.occurrences + 1,
                "last_seen_at": func.now(),
                "job_id": job_id,
                "row_id": row_id,
                "details": details or {},
            },
        )
        .returning(DataQualityIssue.id)
    )
    return int(db.execute(statement).scalar_one())


def issues_of_job(db: Session, job_id: int) -> list[DataQualityIssue]:
    return list(
        db.scalars(select(DataQualityIssue).where(DataQualityIssue.job_id == job_id).order_by(DataQualityIssue.id))
    )


def resolve_open(db: Session, code: str, dedupe_key: str, *, user_id: int | None, note: str) -> bool:
    """Close the open issue `(code, dedupe_key)` because its condition went away (a zone configured, a document type
    classified), in the caller's transaction. True when one was open."""
    issue = db.scalars(
        select(DataQualityIssue).where(
            DataQualityIssue.code == code, DataQualityIssue.dedupe_key == dedupe_key, DataQualityIssue.status == "open"
        )
    ).first()
    if issue is None:
        return False
    issue.status = "resolved"
    issue.resolved_by = user_id
    issue.resolved_at = func.now()
    issue.resolution_note = note[:500]
    return True
