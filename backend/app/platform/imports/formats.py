"""File format by its real signature, never by its extension (ADR 014 §2.3, docs/IMPORTS.md §4).

- OLE2 (`D0 CF 11 E0 A1 B1 1A E1`) → XLS (BIFF2-8, read with xlrd).
- ZIP with `[Content_Types].xml` and a workbook part → XLSX; a macro-enabled workbook (`.xlsm`) is refused.
- Text that is not HTML or XML → CSV.
- Anything else, HTML or SpreadsheetML saved as `.xls` included, is unsupported.

The extension only filters what may be uploaded; a mismatch with the signature is recorded, and the signature wins.
"""

import zipfile
from dataclasses import dataclass
from pathlib import Path

OLE2_MAGIC = bytes.fromhex("d0cf11e0a1b11ae1")
ZIP_MAGIC = b"PK\x03\x04"
ALLOWED_EXTENSIONS = {".xls": "XLS", ".xlsx": "XLSX", ".csv": "CSV"}
HEAD_BYTES = 4096
MACRO_CONTENT_TYPE = "application/vnd.ms-excel.sheet.macroEnabled"
# Markup at the start of a text file: an HTML or SpreadsheetML "Excel" export, not a CSV.
MARKUP_PREFIXES = (b"<html", b"<!doctype", b"<?xml", b"<table", b"<workbook")


class UnsupportedFormatError(Exception):
    """The content is not a file the readers accept. `reason` is a stable code; the message is safe to show."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason
        self.message = message


@dataclass(frozen=True)
class DetectedFormat:
    format: str
    extension_format: str | None  # what the extension says, None for an extension that says nothing

    @property
    def extension_mismatch(self) -> bool:
        return self.extension_format is not None and self.extension_format != self.format


def extension_format(filename: str) -> str | None:
    return ALLOWED_EXTENSIONS.get(Path(filename).suffix.lower())


def detect_format(path: Path, filename: str) -> DetectedFormat:
    with path.open("rb") as handle:
        head = handle.read(HEAD_BYTES)
    if not head:
        raise UnsupportedFormatError("empty_file", "El archivo está vacío.")
    declared = extension_format(filename)
    if head.startswith(OLE2_MAGIC):
        return DetectedFormat("XLS", declared)
    if head.startswith(ZIP_MAGIC):
        _check_xlsx(path)
        return DetectedFormat("XLSX", declared)
    if b"\x00" in head:
        raise UnsupportedFormatError("unsupported_format", "El archivo no es una planilla XLS, XLSX ni un CSV.")
    start = head.removeprefix(b"\xef\xbb\xbf").lstrip().lower()
    if start.startswith(MARKUP_PREFIXES):
        raise UnsupportedFormatError(
            "unsupported_format",
            "El archivo es una página HTML o XML guardada como planilla; todavía no se puede importar.",
        )
    return DetectedFormat("CSV", declared)


def _check_xlsx(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = set(archive.namelist())
            if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
                raise UnsupportedFormatError("unsupported_format", "El archivo comprimido no es una planilla XLSX.")
            content_types = archive.read("[Content_Types].xml")
    except zipfile.BadZipFile as exc:
        raise UnsupportedFormatError("unreadable_file", "El archivo XLSX está dañado y no se puede leer.") from exc
    if MACRO_CONTENT_TYPE.encode() in content_types or "xl/vbaProject.bin" in names:
        raise UnsupportedFormatError("unsupported_format", "Las planillas con macros (.xlsm) no se aceptan.")
