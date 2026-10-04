from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.errors import AppError
from app.platform.auth.access import has_module_access
from app.platform.auth.deps import CurrentUser, DbSession
from app.platform.settings.registry import PLATFORM_SCOPE, registry
from app.platform.settings.service import get_setting

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingOut(BaseModel):
    key: str
    label: str
    description: str
    value: Any
    is_default: bool


class SettingsScopeOut(BaseModel):
    scope: str
    label: str
    settings: list[SettingOut]


@router.get("", response_model=list[SettingsScopeOut])
def list_settings(db: DbSession, user: CurrentUser) -> list[SettingsScopeOut]:
    """Scopes the user administers: `platform` for platform admins, each module for its admins."""
    result = []
    for scope in registry.scopes():
        allowed = (
            user.role == "admin" if scope.key == PLATFORM_SCOPE.key else has_module_access(user, scope.key, "admin")
        )
        if not allowed:
            continue
        settings = []
        for spec in scope.specs:
            value = get_setting(db, scope.full_key(spec))
            settings.append(
                SettingOut(
                    key=scope.full_key(spec),
                    label=spec.label,
                    description=spec.description,
                    value=value,
                    is_default=value == spec.default,
                )
            )
        result.append(SettingsScopeOut(scope=scope.key, label=scope.label, settings=settings))
    if not result:
        raise AppError(403, "forbidden", "No tenés permisos para esta acción.")
    return result
