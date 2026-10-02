from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, IdMixin


class AuditEvent(IdMixin, Base):
    """Relevant business/security events. Payload must never contain PII or secrets."""

    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_events_type_time", "event_type", "occurred_at"),)

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    user_id: Mapped[int | None] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="SET NULL"))
    event_type: Mapped[str] = mapped_column(String(60))
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    request_id: Mapped[str | None] = mapped_column(String(64))
