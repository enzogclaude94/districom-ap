from typing import Any

from sqlalchemy.orm import Session

from app.core.logging import request_id_var
from app.platform.audit.models import AuditEvent


def record_event(
    db: Session,
    event_type: str,
    *,
    user_id: int | None = None,
    entity_type: str | None = None,
    entity_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """Add an audit event to the current transaction (caller commits)."""
    db.add(
        AuditEvent(
            event_type=event_type,
            user_id=user_id,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload or {},
            request_id=request_id_var.get(),
        )
    )


def record_change(
    db: Session,
    event_type: str,
    *,
    action: str,
    user_id: int | None,
    entity_type: str,
    entity_id: int | str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
) -> None:
    """Audit a change to business data with the `{action, before, after}` convention (ADR 007 §10).

    `before` / `after` hold only the fields that matter for the change, already free of PII.
    """
    record_event(
        db,
        event_type,
        user_id=user_id,
        entity_type=entity_type,
        entity_id=str(entity_id),
        payload={"action": action, "before": before, "after": after},
    )
