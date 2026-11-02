"""CSV with the standard library (docs/IMPORTS.md §4). Every cell is text: the adapter of each report parses
numbers and dates by column, since `1.234,56` and `1234.56` can both appear.

- Encoding: UTF-8 (with or without BOM), otherwise CP1252; anything else is refused, never guessed.
- Separator: `;`, `,` or tab, detected on the first lines.
- The row number is the file line where the record starts (a quoted value may span lines).
"""

import csv
import io
from pathlib import Path

from app.platform.imports.formats import UnsupportedFormatError
from app.platform.imports.readers import MAX_COLUMNS, check_size, text_cell, trim_trailing
from app.platform.imports.workbook import Cell, RawRow, RawSheet, Workbook

SEPARATORS = (";", ",", "\t")
SNIFF_BYTES = 64 * 1024


def read_csv(path: Path) -> Workbook:
    content, encoding = _decode(path.read_bytes())
    separator = _separator(content)
    reader = csv.reader(io.StringIO(content, newline=""), delimiter=separator, strict=True)
    rows: list[RawRow] = []
    start = 1
    try:
        for values in reader:
            check_size(len(rows) + 1, min(len(values), MAX_COLUMNS + 1))
            cells: list[Cell] = [text_cell(value) for value in values]
            rows.append(RawRow(number=start, cells=trim_trailing(cells)))
            start = reader.line_num + 1
    except csv.Error as exc:
        raise UnsupportedFormatError("unreadable_file", "El CSV tiene comillas sin cerrar o está dañado.") from exc
    return Workbook(
        format="CSV",
        sheets=(RawSheet(name="", rows=tuple(rows)),),
        metadata={"encoding": encoding, "separator": "tab" if separator == "\t" else separator},
    )


def _decode(data: bytes) -> tuple[str, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data[3:].decode("utf-8"), "utf-8-sig"
        except UnicodeDecodeError as exc:
            raise UnsupportedFormatError("unknown_encoding", "El CSV no está en UTF-8 ni en Windows-1252.") from exc
    for encoding in ("utf-8", "cp1252"):
        try:
            return data.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    raise UnsupportedFormatError("unknown_encoding", "El CSV no está en UTF-8 ni en Windows-1252.")


def _separator(content: str) -> str:
    sample = content[:SNIFF_BYTES]
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(SEPARATORS)).delimiter
    except csv.Error:
        first = next((line for line in sample.splitlines() if line.strip()), "")
        counts = {separator: first.count(separator) for separator in SEPARATORS}
        best = max(counts, key=lambda separator: counts[separator])
        return best if counts[best] else ";"
