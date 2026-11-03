from collections.abc import Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.platform.imports.registry import registry
from tests.imports.adapter import CREATE_TABLE, OTHER_TYPE_KEY, TYPE_KEY, ItemsAdapter, OtherAdapter, specs


@dataclass
class SyntheticTypes:
    items: ItemsAdapter
    other: OtherAdapter


@pytest.fixture
def synthetic_types(db: Session) -> Iterator[SyntheticTypes]:
    """The synthetic import types registered under Comercial, and their target table, for one test."""
    with db.get_bind().begin() as conn:  # type: ignore[union-attr]
        conn.execute(text(CREATE_TABLE))
    items, other = ItemsAdapter(), OtherAdapter()
    for spec in specs(items, other):
        registry.register("commercial", spec)
    try:
        yield SyntheticTypes(items, other)
    finally:
        if items.knobs.release is not None:
            items.knobs.release.set()
        registry.unregister(TYPE_KEY)
        registry.unregister(OTHER_TYPE_KEY)
        db.rollback()
        with db.get_bind().begin() as conn:  # type: ignore[union-attr]
            conn.execute(text("DROP TABLE IF EXISTS test_import_items"))
