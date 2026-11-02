"""F1.1b: detection by signature and the CSV, XLSX and XLS readers, on synthetic files (docs/IMPORTS.md §4)."""

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from app.platform.imports.formats import UnsupportedFormatError, detect_format
from app.platform.imports.headers import Column, find_header, header_key
from app.platform.imports.readers import MAX_ROWS, read_workbook
from app.platform.imports.storage import FileTooLargeError, display_filename, store_upload, stored_path
from app.platform.imports.workbook import encode_cells
from tests.imports.files import biff7_xls, biff8_xls, compound_file, csv_bytes, xlsx, zip_with

ROWS = [
    ["codigo", "descripcion", "cantidad", "fecha"],
    [1001, "AÑEJO TINTO 750 ML", 6, date(2026, 10, 23)],
    [1002, "Caña Ñandú", Decimal("1.5"), datetime(2026, 10, 23, 8, 15)],
    [None, None, None, None],
    [1003, "", -2, date(2026, 10, 24)],
]


def write(tmp_path: Path, name: str, data: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(data)
    return path


def read(tmp_path: Path, name: str, data: bytes):  # type: ignore[no-untyped-def]
    path = write(tmp_path, name, data)
    detected = detect_format(path, name)
    return detected, read_workbook(path, detected.format)


# --- XLS ----------------------------------------------------------------------------------------------------------


def test_biff7_as_store_sys_exports_it_reads_codepage_text_numbers_and_dates(tmp_path: Path) -> None:
    detected, book = read(tmp_path, "ventas.xls", biff7_xls(ROWS, sheet_name="Ventas"))
    assert detected.format == "XLS"
    assert not detected.extension_mismatch
    assert book.metadata == {"biff_version": 70, "codepage": 1252, "encoding": "cp1252"}
    sheet = book.sheets[0]
    assert sheet.name == "Ventas"
    assert [r.number for r in sheet.rows] == [1, 2, 3, 4, 5]
    assert sheet.rows[1].cells == (Decimal(1001), "AÑEJO TINTO 750 ML", Decimal(6), date(2026, 10, 23))
    assert sheet.rows[2].cells == (Decimal(1002), "Caña Ñandú", Decimal("1.5"), datetime(2026, 10, 23, 8, 15))
    assert sheet.rows[3].cells == ()
    assert sheet.rows[3].is_blank()
    assert sheet.rows[4].cells == (Decimal(1003), None, Decimal(-2), date(2026, 10, 24))


def test_biff7_text_really_is_cp1252_on_disk() -> None:
    data = biff7_xls([["Ñ"]])
    assert "Ñ".encode("cp1252") in data
    assert "Ñ".encode() not in data


def test_biff8_reads_the_same_cells(tmp_path: Path) -> None:
    _, book = read(tmp_path, "ventas.xls", biff8_xls(ROWS))
    assert book.metadata["biff_version"] == 80
    sheet = book.sheets[0]
    assert sheet.rows[1].cells == (Decimal(1001), "AÑEJO TINTO 750 ML", Decimal(6), date(2026, 10, 23))
    assert sheet.rows[2].cells == (Decimal(1002), "Caña Ñandú", Decimal("1.5"), datetime(2026, 10, 23, 8, 15))


def test_damaged_xls_is_unreadable(tmp_path: Path) -> None:
    data = biff7_xls(ROWS)
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "ventas.xls", data[:600])
    assert error.value.reason == "unreadable_file"


def test_ole2_file_that_is_not_a_workbook_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "doc.xls", compound_file("WordDocument", b"x" * 5000))
    assert error.value.reason == "unreadable_file"


# --- XLSX ---------------------------------------------------------------------------------------------------------


def test_xlsx_reads_numbers_dates_and_text(tmp_path: Path) -> None:
    detected, book = read(tmp_path, "clientes.xlsx", xlsx(ROWS))
    assert detected.format == "XLSX"
    sheet = book.sheets[0]
    assert sheet.rows[1].cells == (Decimal(1001), "AÑEJO TINTO 750 ML", Decimal(6), date(2026, 10, 23))
    assert sheet.rows[2].cells == (Decimal(1002), "Caña Ñandú", Decimal("1.5"), datetime(2026, 10, 23, 8, 15))
    assert sheet.rows[3].cells == ()


def test_xlsx_formulas_are_not_evaluated(tmp_path: Path) -> None:
    _, book = read(tmp_path, "f.xlsx", xlsx([["a", "b"], [1, "=1+1"]]))
    assert book.sheets[0].rows[1].cells == (Decimal(1),)  # no cached value, nothing computed: the cell is empty


def test_macro_enabled_workbook_is_refused(tmp_path: Path) -> None:
    macro = b"application/vnd.ms-excel.sheet.macroEnabled.main+xml"
    data = zip_with(
        {
            "[Content_Types].xml": b'<Types><Override ContentType="' + macro + b'"/></Types>',
            "xl/workbook.xml": b"<workbook/>",
            "xl/vbaProject.bin": b"\x00",
        }
    )
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "m.xlsx", data)
    assert error.value.reason == "unsupported_format"


def test_zip_that_is_not_a_workbook_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "datos.xlsx", zip_with({"datos.txt": b"hola"}))
    assert error.value.reason == "unsupported_format"


def test_damaged_xlsx_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "c.xlsx", xlsx(ROWS)[:300])
    assert error.value.reason == "unreadable_file"


def test_xlsx_decompression_bomb_is_refused(tmp_path: Path) -> None:
    data = zip_with(
        {
            "[Content_Types].xml": b"<Types/>",
            "xl/workbook.xml": b"<workbook/>",
            "xl/worksheets/sheet1.xml": b"0" * (12 * 1024 * 1024),
        }
    )
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "bomb.xlsx", data)
    assert error.value.reason == "file_too_complex"


# --- CSV ----------------------------------------------------------------------------------------------------------

CSV_ROWS = [
    ["codigo", "descripcion", "cantidad"],
    ["1001", "Añejo; 750", "1.234,50"],
    ["1002", 'Dice "hola"\nchau', "2"],
]


@pytest.mark.parametrize(
    ("separator", "encoding", "bom", "expected_encoding"),
    [
        (";", "utf-8", False, "utf-8"),
        (",", "utf-8", True, "utf-8-sig"),
        ("\t", "cp1252", False, "cp1252"),
    ],
)
def test_csv_encodings_and_separators(
    tmp_path: Path, separator: str, encoding: str, bom: bool, expected_encoding: str
) -> None:
    detected, book = read(tmp_path, "nc.csv", csv_bytes(CSV_ROWS, separator, encoding, bom))
    assert detected.format == "CSV"
    assert book.metadata["encoding"] == expected_encoding
    assert book.metadata["separator"] == ("tab" if separator == "\t" else separator)
    rows = book.sheets[0].rows
    assert rows[1].cells == ("1001", "Añejo; 750", "1.234,50")  # CSV cells stay text: the adapter parses numbers
    assert rows[2].cells == ("1002", 'Dice "hola"\nchau', "2")
    assert [r.number for r in rows] == [1, 2, 3]


def test_csv_row_numbers_follow_file_lines_across_quoted_newlines(tmp_path: Path) -> None:
    _, book = read(tmp_path, "x.csv", csv_bytes([["a", "b"], ["1", "dos\nlíneas"], ["2", "x"]]))
    assert [r.number for r in book.sheets[0].rows] == [1, 2, 4]


def test_csv_in_an_unknown_encoding_is_refused(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "x.csv", b"codigo;nombre\r\n1;\x81\x8d\r\n")
    assert error.value.reason == "unknown_encoding"


def test_csv_with_an_unclosed_quote_is_unreadable(tmp_path: Path) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "x.csv", b'a;b\r\n1;"abierta\r\n2;3\r\n')
    assert error.value.reason == "unreadable_file"


# --- signature, not extension -------------------------------------------------------------------------------------


def test_stored_xlsx_without_extension_is_read(tmp_path: Path) -> None:
    path = write(tmp_path, "0" * 32, xlsx(ROWS))
    book = read_workbook(path, detect_format(path, "clientes.xlsx").format)
    assert book.sheets[0].rows[1].cells[0] == Decimal(1001)


def test_signature_wins_over_a_misleading_extension(tmp_path: Path) -> None:
    detected, book = read(tmp_path, "ventas.csv", biff7_xls(ROWS))
    assert detected.format == "XLS"
    assert detected.extension_mismatch
    assert book.sheets[0].rows[1].cells[1] == "AÑEJO TINTO 750 ML"

    detected, _ = read(tmp_path, "nc.xls", csv_bytes(CSV_ROWS))
    assert (detected.format, detected.extension_mismatch) == ("CSV", True)

    detected, _ = read(tmp_path, "clientes.xls", xlsx(ROWS))
    assert (detected.format, detected.extension_mismatch) == ("XLSX", True)


@pytest.mark.parametrize(
    "data",
    [
        b"<html><body><table><tr><td>1</td></tr></table></body></html>",
        b"\xef\xbb\xbf  <?xml version='1.0'?><Workbook xmlns='urn:schemas-microsoft-com:office:spreadsheet'/>",
        b"<!DOCTYPE html><html></html>",
    ],
)
def test_html_or_spreadsheetml_saved_as_xls_is_refused(tmp_path: Path, data: bytes) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "ventas.xls", data)
    assert error.value.reason == "unsupported_format"


@pytest.mark.parametrize(
    ("data", "reason"),
    [
        (b"", "empty_file"),
        (b"\x89PNG\r\n\x1a\n\x00\x00", "unsupported_format"),
        (b"%PDF-1.4\x00", "unsupported_format"),
    ],
)
def test_binary_or_empty_files_are_refused(tmp_path: Path, data: bytes, reason: str) -> None:
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "ventas.xls", data)
    assert error.value.reason == reason


def test_row_ceiling(tmp_path: Path) -> None:
    data = ("a\r\n" * (MAX_ROWS + 1)).encode()
    with pytest.raises(UnsupportedFormatError) as error:
        read(tmp_path, "big.csv", data)
    assert error.value.reason == "too_many_rows"


# --- headers, staging encoding, storage ---------------------------------------------------------------------------


def test_header_keys_ignore_case_accents_and_punctuation() -> None:
    assert header_key("Razón  Social") == header_key("razon_social") == "razon social"
    assert header_key(None) == ""


def test_find_header_below_title_rows() -> None:
    from app.platform.imports.workbook import RawRow, RawSheet

    sheet = RawSheet(
        "S",
        (
            RawRow(1, ("DISTRIBUIDORA DEMO",)),
            RawRow(2, ("Ventas del 23/10/2026",)),
            RawRow(3, ("Código", "Descripción", "Cantidad")),
        ),
    )
    columns = (Column("code", ("codigo",)), Column("name", ("descripcion",)), Column("qty", ("cantidad",)))
    match = find_header(sheet, columns)
    assert match is not None
    assert (match.row_number, match.positions, match.missing, match.score) == (
        3,
        {"code": 0, "name": 1, "qty": 2},
        (),
        1.0,
    )
    partial = find_header(sheet, (*columns, Column("price", ("precio",))))
    assert partial is not None
    assert partial.missing == ("price",)
    assert partial.score == 0.75


def test_cells_keep_their_type_in_staging() -> None:
    cells = ("x", Decimal("1.50"), date(2026, 10, 23), datetime(2026, 10, 23, 8, 15), None)
    assert encode_cells(cells) == ["x", {"n": "1.50"}, {"d": "2026-10-23"}, {"dt": "2026-10-23T08:15:00"}, None]


def test_store_upload_keeps_the_bytes_read_only_with_a_uuid_name(tmp_path: Path) -> None:
    import hashlib
    import io

    data = biff7_xls(ROWS)
    stored = store_upload(io.BytesIO(data), tmp_path, max_bytes=len(data))
    path = stored_path(tmp_path, stored.storage_key)
    assert path.read_bytes() == data
    assert stored.sha256 == hashlib.sha256(data).hexdigest()
    assert stored.size_bytes == len(data)
    assert path.stat().st_mode & 0o222 == 0
    assert [p.name for p in tmp_path.iterdir()] == [stored.storage_key]


def test_store_upload_over_the_limit_leaves_nothing(tmp_path: Path) -> None:
    import io

    with pytest.raises(FileTooLargeError):
        store_upload(io.BytesIO(b"x" * 11), tmp_path, max_bytes=10)
    assert list(tmp_path.iterdir()) == []


def test_storage_keys_cannot_escape_the_directory(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Invalid storage key"):
        stored_path(tmp_path, "../etc/passwd")


@pytest.mark.parametrize(
    ("name", "shown"),
    [
        ("C:\\Users\\ventas\\ventas.xls", "ventas.xls"),
        ("../../etc/passwd.csv", "passwd.csv"),
        ("clientes\x00\n.xls", "clientes.xls"),
        ("", "archivo"),
    ],
)
def test_display_filename(name: str, shown: str) -> None:
    assert display_filename(name) == shown
