"""Registry of data quality codes. Codes carry their owner's prefix (`platform.import.invalid_row`,
`commercial.customer.without_zone`); the database does not list them in a CHECK.

Besides its severity and label, a code says how the Calidad screen presents it (F1.6, docs/IMPORTS.md §7.1): the
business group it belongs to, what it means, what corrects it, when it closes by itself and where the correction is
made. All of it is declared next to the code by its owner, so the screen never branches on a code.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session

# Where the correction is made, when that screen exists: the frontend turns it into a link with the safe ids the API
# returns (the mapping, the import job). None: there is no screen for it yet, the action text says so.
FIXES = ("mapping", "import")


@dataclass(frozen=True)
class QualityGroup:
    key: str  # stable, for the filter: `customers`
    label: str  # business noun shown to users: «Clientes»
    order: int = 100


IMPORTS_GROUP = QualityGroup("imports", "Importaciones", 900)


@dataclass(frozen=True)
class QualityCodeSpec:
    code: str
    severity: str  # error, warning or info
    label: str  # shown to users
    group: QualityGroup = IMPORTS_GROUP
    description: str = ""  # what happened, in business words
    action: str = ""  # what corrects it
    closes_when: str = ""  # when the issue closes by itself; empty: only a person closes it
    fix: str | None = None  # one of FIXES
    # Entity type of the mapping behind the issue, to find it from `external_ref` when the issue does not carry its id.
    mapping_entity_type: str | None = None

    def __post_init__(self) -> None:
        if self.fix is not None and self.fix not in FIXES:
            raise ValueError(f"Unknown quality fix {self.fix} for {self.code}")


# Names what an issue's entity is, in business words and without PII («Zona RUTA 01»), for a page of issues at once.
Describe = Callable[[Session, Sequence[int]], dict[int, str]]


@dataclass(frozen=True)
class QualitySubjectSpec:
    entity_type: str  # `data_quality_issues.entity_type`
    describe: Describe


class QualityCodeRegistry:
    def __init__(self) -> None:
        self._codes: dict[str, QualityCodeSpec] = {}
        self._subjects: dict[str, QualitySubjectSpec] = {}

    def register(self, spec: QualityCodeSpec) -> None:
        """Idempotent per code, so re-creating the app (tests) does not fail."""
        self._codes[spec.code] = spec

    def register_subject(self, spec: QualitySubjectSpec) -> None:
        self._subjects[spec.entity_type] = spec

    def get(self, code: str) -> QualityCodeSpec | None:
        return self._codes.get(code)

    def all(self) -> list[QualityCodeSpec]:
        return list(self._codes.values())

    def subject(self, entity_type: str) -> QualitySubjectSpec | None:
        return self._subjects.get(entity_type)

    def groups(self) -> list[QualityGroup]:
        found = {spec.group.key: spec.group for spec in self._codes.values()}
        return sorted(found.values(), key=lambda g: (g.order, g.label))


PLATFORM_CODES = (
    QualityCodeSpec(
        "platform.import.duplicate_file",
        "info",
        "Archivo ya importado",
        description="Se subió un archivo idéntico a uno ya importado. No se escribió nada.",
        action="No requiere acción: los datos ya están cargados por la importación original.",
    ),
    QualityCodeSpec(
        "platform.import.invalid_row",
        "error",
        "Fila rechazada en una importación",
        description="Una fila del archivo no se pudo leer o no cumple las reglas del importador. No se cargó.",
        action="Revisá la fila en la importación. Si el dato está mal en el sistema de origen, corregilo allá y "
        "volvé a exportar.",
        fix="import",
    ),
    QualityCodeSpec(
        "platform.import.missing_columns",
        "error",
        "Archivo sin las columnas requeridas",
        description="El archivo no tiene las columnas que el importador necesita. La importación falló entera.",
        action="Exportá el reporte de nuevo con todas las columnas y volvé a importarlo.",
        fix="import",
    ),
)

registry = QualityCodeRegistry()
for _spec in PLATFORM_CODES:
    registry.register(_spec)
