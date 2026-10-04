from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.platform.settings.models import AppSetting
from app.platform.settings.registry import registry


def get_setting(db: Session, full_key: str) -> Any:
    """Value of a registered `<scope>.<key>` parameter: the stored override or the code default."""
    spec = registry.spec(full_key)
    override = db.scalar(select(AppSetting.value).where(AppSetting.key == full_key))
    return spec.default if override is None else override


def set_setting(db: Session, full_key: str, value: Any, *, user_id: int) -> None:
    """Store an override of a registered parameter (the owner validates the value first). The caller commits."""
    registry.spec(full_key)
    row = db.get(AppSetting, full_key)
    if row is None:
        db.add(AppSetting(key=full_key, value=value, updated_by=user_id))
    else:
        row.value = value
        row.updated_by = user_id
        row.updated_at = datetime.now(UTC)
