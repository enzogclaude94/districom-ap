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
class ProtectedAreaSpec:
    """A part of a module with its own authorization (ADR 010): its own roles, granted apart from the module's.

    A user reaches an area with a role in its module **and** a grant on the area key (`<module>.<area>`).
    `explicit_grant_required`: the platform admin, who is admin in every module, does not inherit the area;
    they administer the grants and need one of their own to read it (sensitive data, Enzo's D12).
    """

    key: str
    name: str
    explicit_grant_required: bool = True


@dataclass(frozen=True)
class ModuleSpec:
    key: str  # API prefix, settings scope and database schema
    name: str  # name shown to users
    router: APIRouter
    settings: SettingsScope | None = None
    # Employee functions owned by the module; codes are `<key>.<function>` (DATA_MODEL.md §4.1).
    employee_functions: tuple[EmployeeFunctionSpec, ...] = ()
    # Areas of the module with their own authorization; keys are `<key>.<area>` (ADR 010).
    protected_areas: tuple[ProtectedAreaSpec, ...] = ()


_installed: dict[str, ModuleSpec] = {}


def install(module: ModuleSpec) -> None:
    for function in module.employee_functions:
        if not function.code.startswith(f"{module.key}."):
            raise ValueError(f"Employee function {function.code} must use the {module.key}. prefix")
    for area in module.protected_areas:
        if not area.key.startswith(f"{module.key}."):
            raise ValueError(f"Protected area {area.key} must use the {module.key}. prefix")
    _installed[module.key] = module
    if module.settings is not None:
        settings_registry.register(module.settings)
    for function in module.employee_functions:
        function_registry.register(function)


def installed_modules() -> list[ModuleSpec]:
    return list(_installed.values())


def installed_area(key: str) -> tuple[ModuleSpec, ProtectedAreaSpec] | None:
    """The protected area with this key and its module, if installed."""
    for module in _installed.values():
        for area in module.protected_areas:
            if area.key == key:
                return module, area
    return None


def installed_areas() -> list[tuple[ModuleSpec, ProtectedAreaSpec]]:
    return [(module, area) for module in _installed.values() for area in module.protected_areas]
