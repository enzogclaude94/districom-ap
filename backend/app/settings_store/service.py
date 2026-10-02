from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.settings_store.defaults import DEFAULTS
from app.settings_store.models import AppSetting


def get_setting(db: Session, key: str) -> Any:
    if key not in DEFAULTS:
        raise KeyError(f"Unknown setting: {key}")
    override = db.scalar(select(AppSetting.value).where(AppSetting.key == key))
    return DEFAULTS[key] if override is None else override
