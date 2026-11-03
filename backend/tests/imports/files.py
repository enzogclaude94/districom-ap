"""Synthetic files with the structure of the real exports, built in code.

`biff7_xls` writes what STORE SYS exports: an Excel 5/95 (BIFF7) workbook in an OLE2 compound file, its text in the
workbook's codepage (cp1252), its codes as numbers and its dates as date-formatted numbers. No library writes BIFF7
any more (xlwt writes BIFF8), so the few records xlrd needs are written here by hand.
"""

import io
import struct
import zipfile
from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

Value = str | int | float | Decimal | date | datetime | None

SECTOR = 512
ENDOFCHAIN = 0xFFFFFFFE
FREESECT = 0xFFFFFFFF
FATSECT = 0xFFFFFFFD
NOSTREAM = 0xFFFFFFFF
DATE_XF = 1  # XF 1 uses the built-in date format 14
DATETIME_XF = 2  # XF 2 uses the built-in format 22 (m/d/yy h:mm)


def _record(kind: int, data: bytes) -> bytes:
    return struct.pack("<HH", kind, len(data)) + data


def _serial(value: date | datetime) -> float:
    moment = value if isinstance(value, datetime) else datetime(value.year, value.month, value.day)
    return (moment - datetime(1899, 12, 30)).total_seconds() / 86400


def _biff7_stream(sheet_name: str, rows: Sequence[Sequence[Value]], codepage: int) -> bytes:
    encoding = f"cp{codepage}"
    bof_globals = _record(0x0809, struct.pack("<HHHH", 0x0500, 0x0005, 0x0DBB, 0x07CC))
    xf_default = struct.pack("<HHHBBII", 0, 0, 0x0001, 0x20, 0, 0, 0)
    xfs = b"".join(_record(0x00E0, struct.pack("<HHHBBII", 0, fmt, 0x0001, 0x20, 0, 0, 0)) for fmt in (14, 22))
    font = _record(0x0031, struct.pack("<HHHHHBBBB", 200, 0, 0x7FFF, 400, 0, 0, 0, 0, 0) + bytes([5]) + b"Arial")
    head = (
        bof_globals
        + _record(0x0042, struct.pack("<H", codepage))
        + _record(0x0022, struct.pack("<H", 0))
        + font * 5
        + _record(0x00E0, xf_default) * 16
    )
    # The XFs after the 16 style XFs: index 16 = default cell, 17 = date, 18 = datetime.
    head += _record(0x00E0, xf_default) + xfs
    name = sheet_name.encode(encoding)
    boundsheet_size = 4 + 7 + len(name)  # record header + position, visibility, type, name length + name
    sheet_offset = len(head) + boundsheet_size + 4  # + the globals' EOF
    globals_part = head + _record(0x0085, struct.pack("<IBB", sheet_offset, 0, 0) + bytes([len(name)]) + name)
    globals_part += _record(0x000A, b"")

    width = max((len(row) for row in rows), default=0)
    sheet = _record(0x0809, struct.pack("<HHHH", 0x0500, 0x0010, 0x0DBB, 0x07CC))
    sheet += _record(0x0200, struct.pack("<HHHHH", 0, len(rows), 0, width, 0))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if value is None:
                continue
            if isinstance(value, str):
                data = value.encode(encoding)
                sheet += _record(0x0204, struct.pack("<HHHH", r, c, 16, len(data)) + data)
            elif isinstance(value, datetime):
                sheet += _record(0x0203, struct.pack("<HHHd", r, c, 16 + DATETIME_XF, _serial(value)))
            elif isinstance(value, date):
                sheet += _record(0x0203, struct.pack("<HHHd", r, c, 16 + DATE_XF, _serial(value)))
            else:
                sheet += _record(0x0203, struct.pack("<HHHd", r, c, 16, float(value)))
    sheet += _record(0x000A, b"")
    return globals_part + sheet


def compound_file(stream_name: str, stream: bytes) -> bytes:
    """An OLE2 compound file (CFB v3, 512-byte sectors) with one stream, as Excel 5/95 writes a workbook."""
    stream = stream.ljust(max(4096, len(stream)), b"\x00")  # at least the mini-stream cutoff: a regular stream
    stream = stream.ljust(-(-len(stream) // SECTOR) * SECTOR, b"\x00")
    sectors = len(stream) // SECTOR
    if sectors + 2 > SECTOR // 4:
        raise ValueError("stream too large for a one-sector FAT")
    fat = [FATSECT, ENDOFCHAIN] + [3 + i if i < sectors - 1 else ENDOFCHAIN for i in range(sectors)]
    fat_sector = struct.pack(f"<{SECTOR // 4}I", *(fat + [FREESECT] * (SECTOR // 4 - len(fat))))

    def entry(name: str, kind: int, child: int, start: int, size: int) -> bytes:
        encoded = (name + "\x00").encode("utf-16-le") if name else b""
        return (
            encoded.ljust(64, b"\x00")
            + struct.pack("<HBB", len(encoded), kind, 1 if kind else 0)
            + struct.pack("<III", NOSTREAM, NOSTREAM, child)
            + b"\x00" * 16
            + struct.pack("<I", 0)
            + b"\x00" * 16
            + struct.pack("<IQ", start, size)
        )

    directory = (
        entry("Root Entry", 5, 1, ENDOFCHAIN, 0)
        + entry(stream_name, 2, NOSTREAM, 2, len(stream))
        + entry("", 0, NOSTREAM, 0, 0) * 2
    )
    header = (
        bytes.fromhex("d0cf11e0a1b11ae1")
        + b"\x00" * 16
        + struct.pack("<HHHHH", 0x003E, 0x0003, 0xFFFE, 9, 6)
        + b"\x00" * 6
        + struct.pack("<IIIIIIIII", 0, 1, 1, 0, 4096, ENDOFCHAIN, 0, ENDOFCHAIN, 0)
        + struct.pack("<109I", 0, *([FREESECT] * 108))
    )
    return header + fat_sector + directory + stream


def biff7_xls(rows: Sequence[Sequence[Value]], sheet_name: str = "Hoja1", codepage: int = 1252) -> bytes:
    return compound_file("Book", _biff7_stream(sheet_name, rows, codepage))


def biff8_xls(rows: Sequence[Sequence[Value]], sheet_name: str = "Hoja1") -> bytes:
    import xlwt  # type: ignore[import-untyped]

    book = xlwt.Workbook(encoding="utf-8")
    sheet = book.add_sheet(sheet_name)
    date_style = xlwt.easyxf(num_format_str="DD/MM/YYYY")
    datetime_style = xlwt.easyxf(num_format_str="DD/MM/YYYY HH:MM")
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            if value is None:
                continue
            if isinstance(value, datetime):
                sheet.write(r, c, value, datetime_style)
            elif isinstance(value, date):
                sheet.write(r, c, value, date_style)
            elif isinstance(value, Decimal):
                sheet.write(r, c, float(value))
            else:
                sheet.write(r, c, value)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def xlsx(rows: Sequence[Sequence[Value]], sheet_name: str = "Hoja1") -> bytes:
    import openpyxl

    book = openpyxl.Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = sheet_name
    for row in rows:
        sheet.append([float(v) if isinstance(v, Decimal) else v for v in row])
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()


def csv_bytes(rows: Sequence[Sequence[str]], separator: str = ";", encoding: str = "utf-8", bom: bool = False) -> bytes:
    lines = []
    for row in rows:
        cells = []
        for value in row:
            if separator in value or '"' in value or "\n" in value:
                value = '"' + value.replace('"', '""') + '"'
            cells.append(value)
        lines.append(separator.join(cells))
    data = ("\r\n".join(lines) + "\r\n").encode(encoding)
    return (b"\xef\xbb\xbf" + data) if bom else data


def zip_with(entries: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return out.getvalue()
