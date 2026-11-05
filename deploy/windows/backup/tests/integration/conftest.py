import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

from districom_backup.pgtools import PG_BIN_ENV
from tests.integration.support import Primary, make_primary, unique_dir


@pytest.fixture(scope="session", autouse=True)
def _require_pg_bin() -> None:
    # Missing binaries are an error, never a skip: a PoC that silently skips proves nothing.
    if not os.environ.get(PG_BIN_ENV):
        pytest.fail(f"{PG_BIN_ENV} must point to the PostgreSQL 17 bin directory")


@pytest.fixture(scope="session")
def work_root() -> Path:
    """Base folder for clusters and repositories.

    Not pytest's ``tmp_path``: pytest creates it with mode 0o700, which on Windows becomes an ACL
    for the current user only. initdb and pg_ctl run PostgreSQL with a restricted token (without
    the Administrators group), which then cannot even see that folder. A plain folder inherits the
    normal ACL of the temporary directory, as a real installation folder would.
    """
    root = Path(os.environ.get("DISTRICOM_POC_WORKDIR", Path(tempfile.gettempdir()) / "districom-w2a"))
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    return root


@pytest.fixture
def workdir(work_root: Path, request: pytest.FixtureRequest) -> Iterator[Path]:
    path = unique_dir(work_root, request.node.name)
    yield path
    shutil.rmtree(path, ignore_errors=True)  # clusters and copies add up to gigabytes


@pytest.fixture
def primary(workdir: Path) -> Iterator[Primary]:
    with make_primary(workdir) as instance:
        yield instance
