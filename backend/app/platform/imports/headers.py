"""Finding a report's header row by its column names (ADR 014 §2.4): adapters declare their columns with aliases in
code, never as editable templates. Shared by every adapter and by type detection."""

import unicodedata
from dataclasses import dataclass

from app.platform.imports.workbook import RawSheet

# Exports put a title, the company name or the filters above the header: look that far for it.
HEADER_SCAN_ROWS = 30


def header_key(value: object) -> str:
    """How two headers compare: case, accents, punctuation and spacing do not matter
    (`Razón Social` = `razon_social`)."""
    if value is None:
        return ""
    decomposed = unicodedata.normalize("NFKD", str(value))
    plain = "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()
    return " ".join("".join(ch if ch.isalnum() else " " for ch in plain).split())


@dataclass(frozen=True)
class Column:
    name: str  # the adapter's own name for the column
    aliases: tuple[str, ...]  # the headers that mean it, as the source writes them
    required: bool = True

    def keys(self) -> set[str]:
        return {header_key(alias) for alias in (self.name, *self.aliases)}


@dataclass(frozen=True)
class HeaderMatch:
    row_index: int  # index in `sheet.rows`
    row_number: int  # the file's row number
    positions: dict[str, int]  # column name → cell index
    missing: tuple[str, ...]  # required columns not found
    score: float  # share of required columns found, 0..1


def find_header(sheet: RawSheet, columns: tuple[Column, ...], scan_rows: int = HEADER_SCAN_ROWS) -> HeaderMatch | None:
    """The row among the first `scan_rows` that names the most required columns; None if none names any."""
    required = [column for column in columns if column.required]
    best: HeaderMatch | None = None
    for index, row in enumerate(sheet.rows[:scan_rows]):
        keys = [header_key(cell) for cell in row.cells]
        positions = {}
        for column in columns:
            wanted = column.keys()
            position = next((i for i, key in enumerate(keys) if key and key in wanted), None)
            if position is not None:
                positions[column.name] = position
        found = [column.name for column in required if column.name in positions]
        if not found:
            continue
        score = len(found) / len(required) if required else 1.0
        missing = tuple(column.name for column in required if column.name not in positions)
        if best is None or score > best.score:
            best = HeaderMatch(index, row.number, positions, missing, score)
    return best
