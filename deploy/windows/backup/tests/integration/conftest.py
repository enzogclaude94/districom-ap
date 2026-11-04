import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from districom_backup.pgtools import PG_BIN_ENV
from tests.integration.support import Primary, make_primary


@pytest.fixture(scope="session", autouse=True)
def _require_pg_bin() -> None:
    # Missing binaries are an error, never a skip: a PoC that silently skips proves nothing.
    if not os.environ.get(PG_BIN_ENV):
        pytest.fail(f"{PG_BIN_ENV} must point to the PostgreSQL 17 bin directory")


@pytest.fixture
def primary(tmp_path: Path) -> Iterator[Primary]:
    with make_primary(tmp_path) as instance:
        yield instance
