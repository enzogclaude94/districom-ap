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
