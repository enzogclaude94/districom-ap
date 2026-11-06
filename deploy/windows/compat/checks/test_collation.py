"""B-4: the order of Spanish names in the listings the backend sorts.

The backend sorts with plain ``ORDER BY column``, so the order comes from the database collation. On Windows the
database is created with ICU ``es-AR`` (docs/WINDOWS_DEPLOYMENT.md §2). These checks prove:

1. the database really uses ICU ``es-AR``;
2. no column the backend sorts by overrides the collation (so ICU ``es-AR`` is what applies);
3. a real listing of the backend returns the names in Spanish order;
4. how that order differs from an English collation, the closest to the Linux image (glibc ``en_US.utf8``).
"""

from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.parties.models import Employee
from app.platform.parties.service import list_employees

MARK = "W2B"  # a word at the end that keeps these rows apart from any seeded employee

# Spanish alphabetical order: accents do not change the letter, ñ is its own letter after n, case does not matter.
SPANISH_ORDER = [
    "abeja",
    "Álamo",
    "árbol",
    "Cecilia",
    "Chávez",
    "Díaz",
    "nube",
    "Núñez",
    "ñandú",
    "Ñoño",
    "oso",
    "Penas",
    "Peña",
    "Pérez",
    "zorro",
]

# Every column the backend uses in ORDER BY for names (grep "order_by(...name" in backend/app, 2026-11-06).
SORTED_COLUMNS = [
    ("public", "employees", "full_name"),
    ("public", "third_parties", "name_key"),
    ("public", "customers", "display_name_key"),
    ("settlements", "expense_categories", "name"),
    ("commercial", "zones", "name_key"),
    ("commercial", "document_types", "source_name_key"),
]


@pytest.mark.criterion("B-4")
def test_database_uses_icu_es_ar(db: Session) -> None:
    row = db.execute(
        text(
            "SELECT datlocprovider::text, coalesce(to_jsonb(d) ->> 'datlocale', to_jsonb(d) ->> 'daticulocale'),"
            " pg_encoding_to_char(encoding) FROM pg_database d WHERE datname = current_database()"
        )
    ).one()
    assert tuple(row) == ("i", "es-AR", "UTF8")


@pytest.mark.criterion("B-4")
@pytest.mark.parametrize(("schema", "table", "column"), SORTED_COLUMNS)
def test_sorted_columns_use_the_database_collation(db: Session, schema: str, table: str, column: str) -> None:
    row = db.execute(
        text(
            "SELECT collation_name FROM information_schema.columns"
            " WHERE table_schema = :schema AND table_name = :table AND column_name = :column"
        ),
        {"schema": schema, "table": table, "column": column},
    ).one_or_none()
    assert row is not None, f"{schema}.{table}.{column} does not exist: update SORTED_COLUMNS"
    assert row[0] is None, f"{schema}.{table}.{column} overrides the collation with {row[0]}"


@pytest.mark.criterion("B-4")
def test_employee_listing_is_in_spanish_order(db: Session, metrics: dict[str, Any]) -> None:
    # Inserted in reverse so the order cannot come from insertion order or ids.
    for name in reversed(SPANISH_ORDER):
        db.add(Employee(full_name=f"{name} {MARK}"))
    db.flush()

    rows, total = list_employees(db, search=MARK, limit=100)
    listed = [employee.full_name.removesuffix(f" {MARK}") for employee in rows]
    assert total == len(SPANISH_ORDER)

    english = list(
        db.scalars(
            text(
                "SELECT regexp_replace(full_name, ' W2B$', '') FROM employees"
                ' WHERE full_name LIKE :pattern ORDER BY full_name COLLATE "en-US-x-icu", id'
            ),
            {"pattern": f"% {MARK}"},
        )
    )
    metrics["B-4"] = {
        "listado del backend (es-AR)": listed,
        "mismo dato con collation en-US (referencia, cercano a glibc en_US.utf8 de la imagen Linux)": english,
        "difieren": listed != english,
    }
    assert listed == SPANISH_ORDER
