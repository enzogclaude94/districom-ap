"""Readers by format: the only code that imports `xlrd`, `openpyxl` or `csv` for an import (import-linter contract).

Every reader returns a `Workbook` of typed cells (`workbook.py`) and refuses what it cannot read safely with
`UnsupportedFormatError`. Shared conventions, the same for every spreadsheet format:

- a number is a `Decimal`; an integral one has no decimals (`1234.0` → `Decimal("1234")`);
- a date-formatted cell is a `date` when it has no time of day, otherwise a `datetime`;
- an empty cell or an empty string is `None`;
- a boolean or another value without a canonical type becomes its text.
"""

from datetime import date, datetime, time
from decimal import Decimal
from pathlib import Path

from app.platform.imports.formats import UnsupportedFormatError
from app.platform.imports.workbook import Cell, Workbook

# Ceilings for a single file, far above any real STORE SYS or AXUM export, so a hostile file cannot exhaust memory.
MAX_ROWS = 200_000
MAX_COLUMNS = 500


def read_workbook(path: Path, file_format: str) -> Workbook:
    if file_format == "XLS":
        from app.platform.imports.readers.xls import read_xls

        return read_xls(path)
    if file_format == "XLSX":
        from app.platform.imports.readers.xlsx import read_xlsx

        return read_xlsx(path)
    if file_format == "CSV":
        from app.platform.imports.readers.text import read_csv

        return read_csv(path)
    raise UnsupportedFormatError("unsupported_format", "Formato de archivo no soportado.")


def number(value: float | int) -> Decimal:
    if isinstance(value, int):
        return Decimal(value)
    if value.is_integer():
        return Decimal(int(value))
    return Decimal(repr(value))


def moment(value: datetime) -> date | datetime:
    return value.date() if value.time() == time(0, 0) else value


def text_cell(value: str) -> str | None:
    return value if value != "" else None


def check_size(rows: int, columns: int) -> None:
    if rows > MAX_ROWS:
        raise UnsupportedFormatError("too_many_rows", f"El archivo tiene más de {MAX_ROWS:,} filas.".replace(",", "."))
    if columns > MAX_COLUMNS:
        raise UnsupportedFormatError("too_many_columns", f"El archivo tiene más de {MAX_COLUMNS} columnas.")


def trim_trailing(cells: list[Cell]) -> tuple[Cell, ...]:
    """Drop the empty cells at the end of a row, so a row is as long as its last value."""
    end = len(cells)
    while end and cells[end - 1] is None:
        end -= 1
    return tuple(cells[:end])
