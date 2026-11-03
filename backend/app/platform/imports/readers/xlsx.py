"""XLSX with openpyxl in read-only mode with cached values (`data_only`): formulas are never evaluated and macros
never run (a macro-enabled workbook is refused at detection). openpyxl parses the XML with defusedxml when it is
installed, which it is (pyproject). Before opening, the archive is checked against a decompression bomb."""

import warnings
import zipfile
from datetime import datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any

import openpyxl

from app.platform.imports.formats import UnsupportedFormatError
from app.platform.imports.readers import check_size, moment, number, text_cell, trim_trailing
from app.platform.imports.workbook import Cell, RawRow, RawSheet, Workbook

MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200
# The ratio only matters for a large entry: a small repetitive part (styles, an empty sheet) compresses a lot
# legitimately.
RATIO_CHECK_FROM_BYTES = 10 * 1024 * 1024


def read_xlsx(path: Path) -> Workbook:
    _check_archive(path)
    # A file object, not the path: openpyxl refuses a path by its extension, and stored files have none (and the
    # signature, not the extension, decides the format).
    handle = path.open("rb")
    try:
        with warnings.catch_warnings():  # print areas, styles and similar remarks: they never change the values
            warnings.simplefilter("ignore")
            book = openpyxl.load_workbook(handle, read_only=True, data_only=True, keep_links=False)
    except Exception as exc:  # openpyxl raises KeyError, XML and zip errors for a damaged file
        handle.close()
        raise UnsupportedFormatError("unreadable_file", "El archivo XLSX está dañado y no se puede leer.") from exc
    try:
        sheets = tuple(_sheet(sheet) for sheet in book.worksheets)
    except UnsupportedFormatError:
        raise
    except Exception as exc:
        raise UnsupportedFormatError("unreadable_file", "El archivo XLSX está dañado y no se puede leer.") from exc
    finally:
        book.close()
        handle.close()
    return Workbook(format="XLSX", sheets=sheets)


def _check_archive(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except zipfile.BadZipFile as exc:
        raise UnsupportedFormatError("unreadable_file", "El archivo XLSX está dañado y no se puede leer.") from exc
    total = sum(entry.file_size for entry in entries)
    too_compressed = any(
        entry.file_size > RATIO_CHECK_FROM_BYTES
        and entry.file_size / max(entry.compress_size, 1) > MAX_COMPRESSION_RATIO
        for entry in entries
    )
    if total > MAX_UNCOMPRESSED_BYTES or too_compressed:
        raise UnsupportedFormatError("file_too_complex", "El archivo XLSX descomprimido es demasiado grande.")


def _sheet(worksheet: Any) -> RawSheet:  # a read-only worksheet; openpyxl types it as a union of sheet kinds
    rows = []
    for index, values in enumerate(worksheet.iter_rows(values_only=True), start=1):
        check_size(index, len(values))
        rows.append(RawRow(number=index, cells=trim_trailing([_cell(value) for value in values])))
    return RawSheet(name=worksheet.title, rows=tuple(rows))


def _cell(value: object) -> Cell:
    if value is None:
        return None
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int | float):
        return number(value)
    if isinstance(value, Decimal):
        return value
    if isinstance(value, datetime):
        return moment(value)
    if isinstance(value, time):
        return value.isoformat()
    if isinstance(value, str):
        return text_cell(value)
    return str(value)
