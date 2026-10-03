from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.platform.auth.deps import DbSession, require_role
from app.platform.auth.models import User
from app.platform.settings.defaults import DEFAULTS
from app.platform.settings.service import get_setting

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingOut(BaseModel):
    key: str
    value: Any
    is_default: bool


@router.get("", response_model=list[SettingOut])
def list_settings(db: DbSession, _: Annotated[User, Depends(require_role("admin"))]) -> list[SettingOut]:
    result = []
    for key, default in DEFAULTS.items():
        value = get_setting(db, key)
        result.append(SettingOut(key=key, value=value, is_default=value == default))
    return result
