"""E2E only: synthetic STORE SYS exports (same columns as the real ones, invented values) for the Imports flow.

Written as XLSX, which the importers read like the original BIFF files; the real exports are never in the
repository. Each run passes its own seed, so its customer codes and sales days do not collide with an earlier run
on the same database. Usage: `uv run --directory ../backend python ../frontend/e2e/store_sys_files.py SEED DIR`.
"""

import sys
from datetime import date, timedelta
from pathlib import Path

from openpyxl import Workbook

seed, out = int(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
first_code = 900_000 + (seed % 9_000) * 10
codes = [first_code + n for n in range(3)]
# Three consecutive past days, a different week for every seed.
start = date(2024, 1, 1) + timedelta(days=(seed % 5_000) * 3)
days = [start + timedelta(days=n) for n in range(3)]


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
    [[c, f"ALMACEN E2E {c}", f"RAZON E2E {c}", "CALLE FALSA 123", "", "  -        -", "RUTA 01 - LUVI", "VENDEDOR E2E"]
     for c in codes],
)

SALES = ["codigo", "descripcion", "cantidad", "rubro", "cod_cliente", "cliente", "vendedor", "zona", "fecha",
         "lista_de_precios"]


def sale(product: int, customer: int, day: date, quantity: int) -> list[object]:
    return [product, f"ARTICULO E2E {product}", quantity, "GASEOSAS", customer, f"ALMACEN E2E {customer}",
            "VENDEDOR E2E", "RUTA 01 - LUVI", day, "LISTA 1"]


# The full export: three days, every customer, one article that is not in the master.
save("ventas_completo.xlsx", "ventas", SALES,
     [sale(77_000 + n, c, d, 1 + n) for d in days for n, c in enumerate(codes)] + [sale(99_999_999, codes[0], days[0], 2)])
# A filtered export of the second day, with other quantities: it cannot prove what it replaces.
save("ventas_filtrado.xlsx", "ventas", SALES, [sale(77_000, codes[0], days[1], 5), sale(77_001, codes[1], days[1], 6)])
(out / "no_es_un_reporte.xlsx").write_bytes(b"esto no es una planilla")
print(f"{codes[0]} {days[0].isoformat()} {days[-1].isoformat()}")
