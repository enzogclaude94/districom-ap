"""Synthetic AXUM «Compró / No compró» reports with the structure of the real one (docs/IMPORTS.md §3.6,
inputs.md): XLSX, one sheet `Data`, the header in row 1 with an empty first column, a `Vendedor: NN` row opening
each salesperson's group and a blank row between groups, every cell as text. No value comes from a real file."""

import io
from collections.abc import Sequence
from datetime import date

from openpyxl import Workbook

HEADER = [
    "",
    "Cliente Id",
    "Nombre",
    "Direccion",
    "Compro?",
    "Fecha y Hora Subida",
    "Hora Ini.",
    "Hora Fin.",
    "Minutos",
]
NO_PURCHASE = "commercial.axum.no_purchase"


def visit(
    code: int | str,
    name: str,
    result: str = "TIENE STOCK",
    *,
    day: date = date(2026, 10, 20),
    uploaded: str = "12:30:00",
    start: str = "09:00:00",
    end: str = "09:02:30",
    minutes: str = "2:30",
    address: str = "CALLE SINTETICA 100",
) -> list[str]:
    return ["", str(code), name, address, result, f"{day:%d/%m/%Y} {uploaded}", start, end, minutes]


def axum_file(groups: Sequence[tuple[str, Sequence[Sequence[str]]]], header: Sequence[str] = HEADER) -> bytes:
    """`groups`: (salesperson code, rows) in order, written as AXUM writes them."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "Data"
    sheet.append(list(header))
    for index, (salesperson, rows) in enumerate(groups):
        if index:
            sheet.append([None] * len(header))
        sheet.append([f"Vendedor: {salesperson}"])
        for row in rows:
            sheet.append(list(row))
    out = io.BytesIO()
    book.save(out)
    return out.getvalue()
