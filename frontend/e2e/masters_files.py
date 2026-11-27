"""E2E only: synthetic STORE SYS masters for the Maestros flows (F1.7), same columns as the real exports and invented
values. Each run brings its own customer, zone, seller and article, so its problems are new: a zone no rule
classifies, a seller code nobody linked and an article without category.
Usage: `uv run --directory ../backend python ../frontend/e2e/masters_files.py SEED DIR` prints `CODE ZONE SELLER`.
"""

import sys
from pathlib import Path

from openpyxl import Workbook

seed, out = int(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
tag = seed % 1_000_000
customer, article = 800_000 + tag % 100_000, 60_000_000 + tag
zone, seller = f"ZONA E2E {tag}", f"VENDEDOR E2E {tag}"


def save(name: str, sheet: str, header: list[str], rows: list[list[object]]) -> None:
    book = Workbook()
    ws = book.active
    assert ws is not None
    ws.title = sheet
    ws.append(header)
    for row in rows:
        ws.append(row)
    book.save(out / name)


save(
    "clientes.xlsx",
    "clientes",
    ["codigo", "fantasia", "razons", "direccion", "telefono", "cuit", "zona", "vend"],
    [[customer, f"ALMACEN MAESTROS {tag}", None, "CALLE FALSA 123", "", "  -        -", zone, seller]],
)
save("articulos.xlsx", "lista de articulos", ["codart", "desart", "precio"], [[article, f"ARTICULO E2E {tag}", 1234.5]])
print(f"{article} {zone} {seller}")
