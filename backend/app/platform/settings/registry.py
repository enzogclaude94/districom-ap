"""Registry of editable business parameters, grouped by scope (ARCHITECTURE.md §4, ADR 006).

The platform registers its own scope; each module registers one scope named after its key.
Full keys are `<scope>.<key>` (e.g. `platform.currency`, `commercial.active_window_days`).
`app_settings` only stores overrides, so every registered key always resolves to a value.
"""

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class SettingSpec:
    key: str
    default: Any
    label: str
    description: str = ""


@dataclass(frozen=True)
class SettingsScope:
    key: str
    label: str
    specs: tuple[SettingSpec, ...]

    def full_key(self, spec: SettingSpec) -> str:
        return f"{self.key}.{spec.key}"


class SettingsRegistry:
    def __init__(self) -> None:
        self._scopes: dict[str, SettingsScope] = {}

    def register(self, scope: SettingsScope) -> None:
        """Idempotent per scope key, so re-creating the app (tests) does not duplicate scopes."""
        self._scopes[scope.key] = scope

    def scopes(self) -> list[SettingsScope]:
        return list(self._scopes.values())

    def spec(self, full_key: str) -> SettingSpec:
        scope_key, _, key = full_key.partition(".")
        scope = self._scopes.get(scope_key)
        spec = next((s for s in scope.specs if s.key == key), None) if scope else None
        if spec is None:
            raise KeyError(f"Unknown setting: {full_key}")
        return spec


PLATFORM_SCOPE = SettingsScope(
    key="platform",
    label="General",
    specs=(
        SettingSpec("currency", "ARS", "Moneda"),
        SettingSpec("week_start", "MONDAY", "Inicio de semana"),
    ),
)

registry = SettingsRegistry()
registry.register(PLATFORM_SCOPE)
