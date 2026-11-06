"""Reading the typed cells of a STORE SYS export (docs/IMPORTS.md §4): codes arrive as integral numbers, dates as
`date` (or `datetime` with time), text as `str`. A cell of an unexpected type is treated as invalid, never guessed."""

import hashlib
import json
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.platform.imports.headers import HeaderMatch
from app.platform.imports.registry import RowMessage
from app.platform.imports.workbook import Cell, RawRow

CellGetter = Callable[[str], Cell]


def getter(header: HeaderMatch, row: RawRow) -> CellGetter:
    def cell(name: str) -> Cell:
        position = header.positions.get(name)
        return row.cells[position] if position is not None and position < len(row.cells) else None

    return cell


def text(cell: Cell) -> str | None:
    """Trimmed text with inner spaces collapsed; an empty cell is None. A number is written as it reads."""
    if cell is None:
        return None
    if isinstance(cell, Decimal):
        return str(int(cell)) if cell == cell.to_integral_value() else str(cell)
    if isinstance(cell, datetime | date):
        return cell.isoformat()
    return " ".join(cell.split()) or None


class InvalidCellError(ValueError):
    pass


def code(cell: Cell) -> str | None:
    """A STORE SYS code: an integral number (`1001`, never `1001.0`) or digits typed as text. None when empty."""
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None
    if isinstance(cell, Decimal):
        if cell != cell.to_integral_value() or cell < 0:
            raise InvalidCellError("code")
        return str(int(cell))
    if isinstance(cell, str) and cell.strip().isdigit():
        return str(int(cell.strip()))
    raise InvalidCellError("code")


def number(cell: Cell) -> Decimal | None:
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None
    if isinstance(cell, Decimal):
        return cell
    raise InvalidCellError("number")


def day(cell: Cell) -> date | None:
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None
    if isinstance(cell, datetime):
        return cell.date()
    if isinstance(cell, date):
        return cell
    raise InvalidCellError("date")


def moment(cell: Cell) -> datetime | None:
    if cell is None or (isinstance(cell, str) and not cell.strip()):
        return None
    if isinstance(cell, datetime):
        return cell
    if isinstance(cell, date):  # a date-time at midnight reaches the adapter as a date
        return datetime(cell.year, cell.month, cell.day)
    raise InvalidCellError("datetime")


def content_hash(values: dict[str, Any]) -> str:
    """The hex sha256 of a record, to tell an identical re-import from a changed one."""
    return hashlib.sha256(json.dumps(values, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def error(code: str, text: str, column: str | None = None) -> RowMessage:
    return RowMessage(code, "error", text, column)


def warning(code: str, text: str, column: str | None = None) -> RowMessage:
    return RowMessage(code, "warning", text, column)


def info(code: str, text: str, column: str | None = None) -> RowMessage:
    return RowMessage(code, "info", text, column)


def status_of(messages: list[RowMessage], *, pending: bool = False) -> str:
    """rejected with any error; else pending_resolution when something waits for a mapping; else warning or valid."""
    if any(m.severity == "error" for m in messages):
        return "rejected"
    if pending:
        return "pending_resolution"
    if any(m.severity == "warning" for m in messages):
        return "warning"
    return "valid"
