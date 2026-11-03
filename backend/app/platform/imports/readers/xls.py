"""XLS (BIFF2-8) with xlrd 2.x. STORE SYS exports BIFF7 (Excel 5/95), which openpyxl cannot read; the text of a
BIFF7 file is decoded with the workbook's own codepage (cp1252 in the samples), as xlrd does."""

import io
from pathlib import Path

import xlrd  # type: ignore[import-untyped]

from app.platform.imports.formats import UnsupportedFormatError
from app.platform.imports.readers import check_size, moment, number, text_cell, trim_trailing
from app.platform.imports.workbook import Cell, RawRow, RawSheet, Workbook


def read_xls(path: Path) -> Workbook:
    log = io.StringIO()  # xlrd writes its warnings to a file; never to stdout
    try:
        book = xlrd.open_workbook(
            file_contents=path.read_bytes(), formatting_info=False, ragged_rows=True, logfile=log, verbosity=0
        )
    except Exception as exc:  # xlrd raises several unrelated classes for a damaged file
        raise UnsupportedFormatError("unreadable_file", "El archivo XLS está dañado y no se puede leer.") from exc
    try:
        sheets = tuple(_sheet(book, book.sheet_by_index(index)) for index in range(book.nsheets))
    except UnsupportedFormatError:
        raise
    except Exception as exc:
        raise UnsupportedFormatError("unreadable_file", "El archivo XLS está dañado y no se puede leer.") from exc
    finally:
        book.release_resources()
    return Workbook(
        format="XLS",
        sheets=sheets,
        metadata={"biff_version": book.biff_version, "codepage": book.codepage or 0, "encoding": book.encoding or ""},
    )


def _sheet(book: xlrd.book.Book, sheet: xlrd.sheet.Sheet) -> RawSheet:
    check_size(sheet.nrows, sheet.ncols)
    rows = []
    for index in range(sheet.nrows):
        cells = [
            _cell(book, sheet.cell_type(index, col), sheet.cell_value(index, col))
            for col in range(sheet.row_len(index))
        ]
        rows.append(RawRow(number=index + 1, cells=trim_trailing(cells)))
    return RawSheet(name=sheet.name, rows=tuple(rows))


def _cell(book: xlrd.book.Book, kind: int, value: object) -> Cell:
    if kind == xlrd.XL_CELL_TEXT:
        return text_cell(str(value))
    if kind == xlrd.XL_CELL_NUMBER:
        return number(float(value))  # type: ignore[arg-type]
    if kind == xlrd.XL_CELL_DATE:
        try:
            return moment(xlrd.xldate.xldate_as_datetime(value, book.datemode))
        except (ValueError, OverflowError, xlrd.xldate.XLDateError):
            return number(float(value))  # type: ignore[arg-type]  # not a valid date: keep the serial number
    if kind == xlrd.XL_CELL_BOOLEAN:
        return "TRUE" if value else "FALSE"
    return None  # empty, blank or an error cell (#N/A, #DIV/0!)
