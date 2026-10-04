"""Contract for business modules and the list of installed ones (ADR 006).

The platform never imports a module: the composition root (`app/api/v1/router.py`) installs them.
"""

from dataclasses import dataclass

from fastapi import APIRouter

from app.platform.settings.registry import SettingsScope
from app.platform.settings.registry import registry as settings_registry


@dataclass(frozen=True)
class ModuleSpec:
    key: str  # API prefix, settings scope and database schema
    name: str  # name shown to users
    router: APIRouter
    settings: SettingsScope | None = None


_installed: dict[str, ModuleSpec] = {}


def install(module: ModuleSpec) -> None:
    _installed[module.key] = module
    if module.settings is not None:
        settings_registry.register(module.settings)


def installed_modules() -> list[ModuleSpec]:
    return list(_installed.values())
