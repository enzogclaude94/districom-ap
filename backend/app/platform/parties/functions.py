"""Registry of employee function codes (DATA_MODEL.md §4.1, ADR 007).

Each module declares the functions it uses (`ModuleSpec.employee_functions`); the platform validates
assignments against this registry. Codes carry the owner's prefix (`commercial.salesperson`).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class EmployeeFunctionSpec:
    code: str
    label: str  # shown to users


class EmployeeFunctionRegistry:
    def __init__(self) -> None:
        self._functions: dict[str, EmployeeFunctionSpec] = {}

    def register(self, spec: EmployeeFunctionSpec) -> None:
        """Idempotent per code, so re-creating the app (tests) does not fail."""
        self._functions[spec.code] = spec

    def get(self, code: str) -> EmployeeFunctionSpec | None:
        return self._functions.get(code)

    def all(self) -> list[EmployeeFunctionSpec]:
        return list(self._functions.values())


registry = EmployeeFunctionRegistry()
