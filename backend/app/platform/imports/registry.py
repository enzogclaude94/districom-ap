"""Import types and the contract of their adapters (ADR 014 §2.1, §2.4).

A module declares its types in `ModuleSpec.import_types`; keys carry the module's prefix
(`commercial.store_sys.customers`). The platform runs the pipeline and never knows a report's columns.

An adapter has three steps:

- `score`: how well a workbook matches the report (0..1), for detection by header signature;
- `analyze`: every row of the file → a staged row with its status and messages. It runs in a **read-only
  transaction**: it may look up existing data to resolve entities, but it cannot write (ADR 014 §2.2);
- `write`: on confirmation, inside the job's single transaction and the type's advisory lock, the staged rows →
  canonical data, with one outcome per row it wrote.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Protocol

from sqlalchemy.orm import Session

from app.platform.imports.workbook import RawRow, RawSheet, Workbook

# A workbook "is" a report when the share of its required columns found in one header row reaches this.
DETECTION_THRESHOLD = 0.8

# What the operator declares when uploading, because the file does not say it (docs/IMPORTS.md §3.2, §5.3).
SCOPES = ("FULL", "FILTERED")
# What an admin decides when a confirmation stops because the system cannot prove what it would replace.
DECISIONS = ("replace", "additional")

ROW_STATUSES = ("valid", "warning", "rejected", "duplicate", "pending_resolution", "skipped")
ROW_OUTCOMES = ("inserted", "updated", "unchanged", "superseded")
SEVERITIES = ("error", "warning", "info")


class StructuralError(Exception):
    """The file cannot be imported as this report at all (missing columns, no data…): the job fails, nothing is
    written. `reason` is the job's `failure_reason`; `details` must not carry row values."""

    def __init__(self, reason: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class RowMessage:
    code: str
    severity: str  # error, warning or info
    text: str  # shown to users: never a row value (no names, CUIT, phones, addresses)
    column: str | None = None


@dataclass(frozen=True)
class AnalyzedRow:
    sheet: RawSheet
    row: RawRow
    status: str
    normalized: dict[str, Any] | None = None  # the canonical record the row proposes, JSON-safe
    business_key: str | None = None
    entity_type: str | None = None
    messages: tuple[RowMessage, ...] = ()


@dataclass
class Analysis:
    rows: list[AnalyzedRow]
    snapshot_date: date | None = None
    period_from: date | None = None
    period_to: date | None = None
    scope: dict[str, Any] | None = None
    # What the confirmation would do beyond the counters (replacements…), shown in the preview. JSON-safe, no PII.
    preview: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


class ConfirmationBlockedError(Exception):
    """The adapter cannot prove what the confirmation would replace and needs an admin decision (docs/IMPORTS.md
    §5.3): nothing is written and the job stays `pending`. `details` must not carry row values."""

    def __init__(self, code: str, message: str, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


@dataclass(frozen=True)
class Declared:
    """What the operator declared at upload: the date of a master snapshot, and the scope of an export (FULL, or
    FILTERED with a note on the filters). None when not declared: each adapter decides its conservative default."""

    snapshot_date: date | None = None
    scope: str | None = None
    scope_note: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "snapshot_date": self.snapshot_date.isoformat() if self.snapshot_date else None,
            "scope": self.scope,
            "scope_note": self.scope_note,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> "Declared":
        data = data or {}
        day = data.get("snapshot_date")
        return cls(date.fromisoformat(day) if day else None, data.get("scope"), data.get("scope_note"))


@dataclass(frozen=True)
class Decision:
    """An admin's explicit decision on a blocked confirmation: `replace` the overlapping data, or load the file as
    `additional`, with the reason, audited."""

    action: str
    reason: str


@dataclass(frozen=True)
class AnalyzeContext:
    job_id: int
    import_type: str
    source_system_id: int
    declared: Declared = field(default_factory=Declared)


@dataclass(frozen=True)
class StagedRow:
    id: int
    sheet: str
    row_number: int
    status: str
    normalized: dict[str, Any] | None
    business_key: str | None
    entity_type: str | None


@dataclass(frozen=True)
class ConfirmContext:
    job_id: int
    import_type: str
    source_system_id: int
    user_id: int
    declared: Declared = field(default_factory=Declared)
    decision: Decision | None = None


@dataclass(frozen=True)
class RowOutcome:
    row_id: int
    outcome: str  # inserted, updated, unchanged or superseded
    entity_type: str | None = None
    entity_id: int | None = None


class ImportAdapter(Protocol):
    version: str

    def score(self, workbook: Workbook) -> float: ...

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis: ...

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        """Write `rows` (every staged row whose status is valid, warning or pending_resolution) and return
        **exactly one** outcome per row: no row may be left without one, none may get two, and no other row may
        appear. A row the adapter decides not to change is `unchanged`, never omitted. The platform fails the whole
        confirmation (`adapter_contract_violation`, full rollback) when the outcomes break this contract."""
        ...


@dataclass(frozen=True)
class ImportTypeSpec:
    key: str  # `<module>.<source>.<report>`
    name: str  # shown to users
    source_system: str  # code in `source_systems` (STORE_SYS, AXUM)
    adapter: ImportAdapter


@dataclass(frozen=True)
class RegisteredType:
    module_key: str
    spec: ImportTypeSpec


class ImportTypeRegistry:
    def __init__(self) -> None:
        self._types: dict[str, RegisteredType] = {}

    def register(self, module_key: str, spec: ImportTypeSpec) -> None:
        """Idempotent per key, so re-creating the app (tests) does not fail."""
        if not spec.key.startswith(f"{module_key}."):
            raise ValueError(f"Import type {spec.key} must use the {module_key}. prefix")
        self._types[spec.key] = RegisteredType(module_key, spec)

    def unregister(self, key: str) -> None:
        self._types.pop(key, None)

    def get(self, key: str) -> RegisteredType | None:
        return self._types.get(key)

    def all(self) -> list[RegisteredType]:
        return list(self._types.values())


registry = ImportTypeRegistry()
