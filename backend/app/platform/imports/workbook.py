"""What a reader hands to an adapter: sheets of typed cells with the file's real row numbers (docs/IMPORTS.md §4).

An adapter only sees these types; it never imports `xlrd`, `openpyxl` or `csv` (an import-linter contract checks it).
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

# A spreadsheet number is a Decimal (an integral one has no decimals), a date without time is a `date`.
# CSV has no types: every non-empty cell is a str.
Cell = str | Decimal | date | datetime | None


@dataclass(frozen=True)
class RawRow:
    number: int  # 1-based row number in the file, as a person sees it in Excel
    cells: tuple[Cell, ...]

    def is_blank(self) -> bool:
        return all(cell is None or (isinstance(cell, str) and not cell.strip()) for cell in self.cells)


@dataclass(frozen=True)
class RawSheet:
    name: str
    rows: tuple[RawRow, ...]


@dataclass(frozen=True)
class Workbook:
    format: str  # XLS, XLSX or CSV
    sheets: tuple[RawSheet, ...]
    # How the file was read (encoding, separator, BIFF version, codepage…), stored in the job's metadata.
    metadata: dict[str, str | int] = field(default_factory=dict)


def encode_cell(cell: Cell) -> object:
    """A cell as JSON for `import_rows.raw`, keeping its type: text stays text, a number is `{"n": "12.5"}`, a date
    `{"d": "2026-10-23"}` and a date with time `{"dt": "2026-10-23T08:15:00"}`, so nothing is lost or reinterpreted."""
    if cell is None or isinstance(cell, str):
        return cell
    if isinstance(cell, Decimal):
        return {"n": str(cell)}
    if isinstance(cell, datetime):
        return {"dt": cell.isoformat()}
    return {"d": cell.isoformat()}


def encode_cells(cells: tuple[Cell, ...]) -> list[object]:
    return [encode_cell(cell) for cell in cells]
