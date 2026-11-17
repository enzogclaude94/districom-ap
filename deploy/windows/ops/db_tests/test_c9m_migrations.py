"""C9-M: the migrations and the backend against the C9-B model, with their contract unchanged.

The migrations run as the application role of ``DATABASE_URL``, as always, on a database it no longer owns
(``districom_ops`` does). They must still go up from empty, down to base and up again, the models must match the
migrations, and the application must own everything it created. ``DISTRICOM_C9M_SUITE=1`` also runs the whole
backend suite against this cluster (the CI job does; it takes several minutes).
"""

import os
import subprocess
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from db_tests.cluster import APP_PASSWORD, APP_USER, DATABASE, OPS_DIR, Cluster
from db_tests.conftest import metrics

BACKEND = Path(os.environ.get("DISTRICOM_BACKEND_DIR", OPS_DIR.parents[2] / "backend"))
PYTHON = BACKEND / ".venv" / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
SECRET_KEY = "ci-only-secret-key-not-used-anywhere-else-0123456789"

pytestmark = pytest.mark.timeout(3600)


@pytest.fixture(scope="module")
def cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Cluster]:
    if not PYTHON.exists():
        pytest.fail(f"the backend environment is missing ({PYTHON}): run uv sync in backend")
    c = Cluster(tmp_path_factory.mktemp("c9m"))
    metrics["PostgreSQL"] = f"PostgreSQL {c.version()}"
    yield c
    c.stop()


def url(cluster: Cluster) -> str:
    return f"postgresql+psycopg://{APP_USER}:{APP_PASSWORD}@127.0.0.1:{cluster.port}/{DATABASE}"


def backend(cluster: Cluster, *args: str, timeout: float = 600) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "DATABASE_URL": url(cluster), "TEST_DATABASE_URL": url(cluster), "SECRET_KEY": SECRET_KEY}
    return subprocess.run(
        [str(PYTHON), *args], cwd=BACKEND, env=env, capture_output=True, text=True, timeout=timeout, check=False,
        encoding="utf-8", errors="replace",
    )  # fmt: skip


def owned_by_others(cluster: Cluster) -> list[str]:
    """Schemas, relations, functions and types outside the system that the application role does not own
    (objects that belong to an extension are the extension's: left out)."""
    not_extension = "NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = {oid} AND d.deptype = 'e')"
    system = "('pg_catalog', 'information_schema', 'pg_toast')"
    outside = f"n.nspname NOT IN {system} AND n.nspname NOT LIKE 'pg_%'"
    app = f"'{APP_USER}'::regrole"
    queries = {
        "schema": f"SELECT nspname FROM pg_namespace n WHERE {outside} AND nspowner <> {app} "
                  f"AND {not_extension.format(oid='n.oid')}",
        "relation": "SELECT n.nspname || '.' || c.relname FROM pg_class c JOIN pg_namespace n "
                    f"ON n.oid = c.relnamespace WHERE {outside} AND c.relowner <> {app} "
                    f"AND {not_extension.format(oid='c.oid')}",
        "function": "SELECT p.proname FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace "
                    f"WHERE {outside} AND p.proowner <> {app} AND {not_extension.format(oid='p.oid')}",
        "type": "SELECT t.typname FROM pg_type t JOIN pg_namespace n ON n.oid = t.typnamespace "
                f"WHERE {outside} AND t.typowner <> {app} AND {not_extension.format(oid='t.oid')} "
                "AND t.typelem = 0 AND t.typrelid = 0",
    }  # fmt: skip
    found = []
    for kind, sql in queries.items():
        found += [f"{kind} {row}" for row in cluster.superuser(sql + ";", dbname=DATABASE)]
    return found


@pytest.mark.criterion("C9-M")
def test_migrations_up_down_and_up_again_as_the_application_role(cluster: Cluster) -> None:
    for step in (("upgrade", "head"), ("downgrade", "base"), ("upgrade", "head"), ("check",)):
        result = backend(cluster, "-m", "alembic", *step)
        assert result.returncode == 0, f"alembic {' '.join(step)}:\n{result.stdout[-3000:]}\n{result.stderr[-3000:]}"
    assert owned_by_others(cluster) == [], "objects the application created belong to another role"
    assert cluster.app("SELECT count(*) > 0 FROM settlements.accounts;") == ["t"]  # it uses what it created
    assert cluster.superuser(f"SELECT datdba::regrole::text FROM pg_database WHERE datname = '{DATABASE}';") == [
        "districom_ops"
    ]
    extensions = cluster.superuser("SELECT extname || ':' || extowner::regrole::text FROM pg_extension "
                                   "WHERE extname <> 'plpgsql' ORDER BY 1;", dbname=DATABASE)  # fmt: skip
    assert extensions == [f"btree_gist:{APP_USER}", f"citext:{APP_USER}"], extensions


@pytest.mark.criterion("C9-M-SUITE")
@pytest.mark.skipif(os.environ.get("DISTRICOM_C9M_SUITE") != "1", reason="set DISTRICOM_C9M_SUITE=1 (CI)")
def test_the_backend_suite_passes_against_the_model(cluster: Cluster) -> None:
    result = backend(cluster, "-m", "pytest", "-q", "-p", "no:cacheprovider", timeout=3500)
    tail = result.stdout.strip().splitlines()[-1:] or [""]
    metrics["suite del backend contra el modelo de C9-B"] = tail[0]
    assert result.returncode == 0, result.stdout[-6000:]
