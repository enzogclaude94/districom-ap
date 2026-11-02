"""A synthetic import type that exercises the whole pipeline without any business entity (F1.1b).

`commercial.test.items` reads a report of items (code, name, quantity, optional date) and writes them into
`test_import_items`, a table only the tests create. Its knobs let a test make analysis try to write, make the
confirmation fail half way, or hold the confirmation inside the type's lock.
"""

import threading
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.imports.headers import Column, find_header
from app.platform.imports.registry import (
    Analysis,
    AnalyzeContext,
    AnalyzedRow,
    ConfirmContext,
    ImportTypeSpec,
    RowMessage,
    RowOutcome,
    StagedRow,
    StructuralError,
)
from app.platform.imports.workbook import Cell, Workbook

TYPE_KEY = "commercial.test.items"
OTHER_TYPE_KEY = "commercial.test.other"

COLUMNS = (
    Column("code", ("codigo", "código")),
    Column("name", ("nombre", "descripcion")),
    Column("quantity", ("cantidad",)),
    Column("day", ("fecha",), required=False),
)
OTHER_COLUMNS = (Column("code", ("codigo",)), Column("reason", ("motivo",)))

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS test_import_items (
    code text PRIMARY KEY,
    name text,
    quantity numeric NOT NULL,
    day date,
    job_id bigint NOT NULL
)
"""


@dataclass
class Knobs:
    write_during_analyze: bool = False
    fail_after_writes: int | None = None
    structural_error_on_write: bool = False
    # Set while a confirmation is inside `write`; it then waits for `release` (lock tests).
    entered: threading.Event = field(default_factory=threading.Event)
    release: threading.Event | None = None


def _text(cell: Cell) -> str | None:
    if cell is None:
        return None
    if isinstance(cell, Decimal):
        return str(cell)
    if isinstance(cell, date | datetime):
        return cell.isoformat()
    return cell.strip() or None


def _quantity(cell: Cell) -> Decimal | None:
    if isinstance(cell, Decimal):
        return cell
    if isinstance(cell, str):
        try:
            return Decimal(cell.strip().replace(".", "").replace(",", ".") if "," in cell else cell.strip())
        except InvalidOperation:
            return None
    return None


class ItemsAdapter:
    version = "test-1"

    def __init__(self) -> None:
        self.knobs = Knobs()

    def score(self, workbook: Workbook) -> float:
        matches = [find_header(sheet, COLUMNS) for sheet in workbook.sheets]
        return max((m.score for m in matches if m is not None), default=0.0)

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis:
        if self.knobs.write_during_analyze:
            db.execute(text("INSERT INTO test_import_items (code, quantity, job_id) VALUES ('X', 1, :j)"), {"j": 0})
        sheet = workbook.sheets[0]
        header = find_header(sheet, COLUMNS)
        if header is None or header.missing:
            raise StructuralError(
                "missing_columns",
                "Faltan columnas requeridas.",
                {"missing": list(header.missing) if header else [c.name for c in COLUMNS if c.required]},
            )
        existing = set(db.scalars(text("SELECT code FROM test_import_items")))
        rows: list[AnalyzedRow] = []
        seen: set[str] = set()
        days: list[date] = []
        for index, row in enumerate(sheet.rows):
            if index <= header.row_index or row.is_blank():
                rows.append(AnalyzedRow(sheet, row, "skipped"))
                continue

            def cell(name: str, cells: tuple[Cell, ...] = row.cells) -> Cell:
                position = header.positions.get(name)
                return cells[position] if position is not None and position < len(cells) else None

            code = _text(cell("code"))
            quantity = _quantity(cell("quantity"))
            name = _text(cell("name"))
            day_cell = cell("day")
            day = day_cell if isinstance(day_cell, date) and not isinstance(day_cell, datetime) else None
            if code is None:
                rows.append(
                    AnalyzedRow(
                        sheet,
                        row,
                        "rejected",
                        messages=(RowMessage("missing_code", "error", "Falta el código.", "code"),),
                    )
                )
                continue
            if quantity is None:
                rows.append(
                    AnalyzedRow(
                        sheet,
                        row,
                        "rejected",
                        business_key=code,
                        messages=(RowMessage("invalid_quantity", "error", "La cantidad no es un número.", "quantity"),),
                    )
                )
                continue
            if code in seen:
                rows.append(
                    AnalyzedRow(
                        sheet,
                        row,
                        "duplicate",
                        business_key=code,
                        messages=(RowMessage("repeated_code", "warning", "Código repetido en el archivo."),),
                    )
                )
                continue
            seen.add(code)
            if day:
                days.append(day)
            normalized = {
                "code": code,
                "name": name,
                "quantity": str(quantity),
                "day": day.isoformat() if day else None,
            }
            status, messages = "valid", ()
            if name is None:
                status, messages = "warning", (RowMessage("missing_name", "warning", "Falta el nombre.", "name"),)
            rows.append(
                AnalyzedRow(
                    sheet,
                    row,
                    status,
                    normalized={**normalized, "exists": code in existing},
                    business_key=code,
                    entity_type="test_item",
                    messages=messages,
                )
            )
        return Analysis(
            rows=rows,
            period_from=min(days) if days else None,
            period_to=max(days) if days else None,
            preview={"new_codes": sum(1 for r in rows if r.normalized and not r.normalized["exists"])},
            metadata={"header_row": header.row_number},
        )

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        self.knobs.entered.set()
        if self.knobs.release is not None:
            self.knobs.release.wait(timeout=30)
        if self.knobs.structural_error_on_write:
            raise StructuralError("source_conflict", "El archivo contradice datos ya importados.")
        outcomes = []
        for count, row in enumerate(rows):
            if self.knobs.fail_after_writes is not None and count >= self.knobs.fail_after_writes:
                raise RuntimeError("synthetic failure in the middle of the confirmation")
            assert row.normalized is not None
            current = db.execute(
                text("SELECT name, quantity, day FROM test_import_items WHERE code = :c"), {"c": row.normalized["code"]}
            ).first()
            values = {
                "c": row.normalized["code"],
                "n": row.normalized["name"],
                "q": Decimal(row.normalized["quantity"]),
                "d": date.fromisoformat(row.normalized["day"]) if row.normalized["day"] else None,
                "j": context.job_id,
            }
            if current is None:
                db.execute(
                    text(
                        "INSERT INTO test_import_items (code, name, quantity, day, job_id) VALUES (:c, :n, :q, :d, :j)"
                    ),
                    values,
                )
                outcomes.append(RowOutcome(row.id, "inserted", "test_item"))
            elif (current.name, current.quantity, current.day) == (values["n"], values["q"], values["d"]):
                outcomes.append(RowOutcome(row.id, "unchanged", "test_item"))
            else:
                db.execute(
                    text(
                        "UPDATE test_import_items SET name = :n, quantity = :q, day = :d, job_id = :j WHERE code = :c"
                    ),
                    values,
                )
                outcomes.append(RowOutcome(row.id, "updated", "test_item"))
        return outcomes


class OtherAdapter:
    """A second type, to test detection between types and that the lock is per type."""

    version = "test-1"

    def score(self, workbook: Workbook) -> float:
        matches = [find_header(sheet, OTHER_COLUMNS) for sheet in workbook.sheets]
        return max((m.score for m in matches if m is not None), default=0.0)

    def analyze(self, db: Session, workbook: Workbook, context: AnalyzeContext) -> Analysis:
        return Analysis(rows=[AnalyzedRow(workbook.sheets[0], row, "skipped") for row in workbook.sheets[0].rows])

    def write(self, db: Session, context: ConfirmContext, rows: Sequence[StagedRow]) -> Sequence[RowOutcome]:
        return []


def specs(items: ItemsAdapter, other: OtherAdapter) -> tuple[ImportTypeSpec, ImportTypeSpec]:
    return (
        ImportTypeSpec(TYPE_KEY, "Ítems de prueba", "STORE_SYS", items),
        ImportTypeSpec(OTHER_TYPE_KEY, "Otro reporte de prueba", "AXUM", other),
    )
