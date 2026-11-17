"""E2E only: a synthetic AXUM «Compró / No compró» report (same structure as the real export, invented values) for
the Mapeos flow. Its salesperson code and its day depend on the seed, so each run brings a new pending code over a
day nobody loaded. The customers are the codes of `store_sys_files.py` with the same seed.
Usage: `uv run --directory ../backend python ../frontend/e2e/axum_files.py SEED DIR`.
"""

import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, os.getcwd())  # the backend directory: the tests' own AXUM writer

from tests.modules.commercial.axum import axum_file, visit  # noqa: E402

seed, out = int(sys.argv[1]), Path(sys.argv[2])
out.mkdir(parents=True, exist_ok=True)
first_code = 900_000 + (seed % 9_000) * 10
salesperson = f"E{seed % 1_000_000:06d}"
day = date(2023, 1, 1) + timedelta(days=seed % 3_000)
(out / "compro_no_compro.xlsx").write_bytes(
    axum_file(
        [
            (
                salesperson,
                [
                    visit(first_code, f"ALMACEN E2E {first_code}", "CERRADO", day=day),
                    visit(first_code + 1, f"ALMACEN E2E {first_code + 1}", "TIENE STOCK", day=day),
                    visit(first_code + 2, f"ALMACEN E2E {first_code + 2}", "Si", day=day),
                ],
            )
        ]
    )
)
print(salesperson)
