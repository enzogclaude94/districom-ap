"""Contract for business modules and the list of installed ones (ADR 006).

The platform never imports a module: the composition root (`app/api/v1/router.py`) installs them.
"""

from dataclasses import dataclass

from fastapi import APIRouter

from app.platform.parties.functions import EmployeeFunctionSpec
from app.platform.parties.functions import registry as function_registry
from app.platform.settings.registry import SettingsScope
from app.platform.settings.registry import registry as settings_registry


@dataclass(frozen=True)
class ModuleSpec:
    key: str  # API prefix, settings scope and database schema
    name: str  # name shown to users
    router: APIRouter
    settings: SettingsScope | None = None
    # Employee functions owned by the module; codes are `<key>.<function>` (DATA_MODEL.md §4.1).
    employee_functions: tuple[EmployeeFunctionSpec, ...] = ()


_installed: dict[str, ModuleSpec] = {}


def install(module: ModuleSpec) -> None:
    for function in module.employee_functions:
        if not function.code.startswith(f"{module.key}."):
            raise ValueError(f"Employee function {function.code} must use the {module.key}. prefix")
    _installed[module.key] = module
    if module.settings is not None:
        settings_registry.register(module.settings)
    for function in module.employee_functions:
        function_registry.register(function)


def installed_modules() -> list[ModuleSpec]:
    return list(_installed.values())
