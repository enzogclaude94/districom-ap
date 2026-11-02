"""Registry of data quality codes. Codes carry their owner's prefix (`platform.import.invalid_row`,
`commercial.customer.without_zone`); the database does not list them in a CHECK."""

from dataclasses import dataclass


@dataclass(frozen=True)
class QualityCodeSpec:
    code: str
    severity: str  # error, warning or info
    label: str  # shown to users


class QualityCodeRegistry:
    def __init__(self) -> None:
        self._codes: dict[str, QualityCodeSpec] = {}

    def register(self, spec: QualityCodeSpec) -> None:
        """Idempotent per code, so re-creating the app (tests) does not fail."""
        self._codes[spec.code] = spec

    def get(self, code: str) -> QualityCodeSpec | None:
        return self._codes.get(code)

    def all(self) -> list[QualityCodeSpec]:
        return list(self._codes.values())


PLATFORM_CODES = (
    QualityCodeSpec("platform.import.duplicate_file", "info", "Archivo ya importado"),
    QualityCodeSpec("platform.import.invalid_row", "error", "Fila rechazada en una importación"),
    QualityCodeSpec("platform.import.missing_columns", "error", "Archivo sin las columnas requeridas"),
)

registry = QualityCodeRegistry()
for _spec in PLATFORM_CODES:
    registry.register(_spec)
