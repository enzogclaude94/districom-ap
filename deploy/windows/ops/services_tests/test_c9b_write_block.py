"""W2-C9-B on the disposable Windows runner: the write block against the real services and the real cluster that
``database init`` made (PostgreSQL 17, the C9-B roles), and the automatic recovery by the real ``DistriComStack``.

Runs after ``test_c9a_coordination.py`` in the job «Windows services (W2-C2)». The evidence of the close is
fabricated (C10 and C11 make the real one), true to the stopped cluster: no real day close is validated here.
"""

import hashlib
import json
import time
from collections.abc import Iterator
from typing import Any

import pytest

from districom_ops import database, services, winservice, write_block
from districom_ops.maintenance import MaintenanceWindow
from districom_ops.oplock import OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog
from districom_ops.winservice import State
from districom_ops.write_block import State as Block
from services_tests.conftest import metrics
from services_tests.test_c2_services import CTX, DISKS, RELEASE, ROOT, attach, cli, disk_root, superuser_sql, wait_until

STATE = ROOT / "state"

pytestmark = pytest.mark.timeout(1500)


@pytest.fixture(scope="module", autouse=True)
def running() -> Iterator[None]:
    if not disk_root().exists():  # test_c9a_coordination leaves backup1 attached
        attach(DISKS / "backup1.vhdx")
    cli("services", "install", "--release", RELEASE)
    cli("services", "start")
    yield
    cli("services", "stop", check=False)


def name() -> str:
    return CTX.install().database[2]


def allows() -> bool:
    return superuser_sql(f"SELECT datallowconn FROM pg_database WHERE datname = '{name()}';") == "t"


def app_sessions() -> int:
    return int(superuser_sql(
        f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{name()}' AND backend_type = 'client backend';"
    ))  # fmt: skip


def journal() -> write_block.Journal:
    return write_block.read_journal(STATE)


def window() -> MaintenanceWindow:
    return MaintenanceWindow(STATE, OperationsLog(STATE / "operations.jsonl"))


def record() -> dict[str, Any]:
    return services.supervisor_state(CTX.install()) or {}


def evidence() -> dict[str, Any]:
    """Fabricated evidence of the close, true to the stopped cluster; the copies are files on the backup disk."""
    fields = write_block._controldata(CTX.install())
    assert fields.get("Database cluster state") == "shut down", fields
    folder = disk_root() / "c9b-evidence"
    folder.mkdir(exist_ok=True)
    copies = []
    for repo in ("repo1", "repo2"):
        path = folder / f"{repo}-segment"
        path.write_bytes(f"{repo} fabricated WAL segment".encode())
        copies.append({"repo": repo, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    checkpoint = fields["Latest checkpoint location"]
    return {"cut_lsn": checkpoint, "checkpoint_lsn": checkpoint,
            "timeline": int(fields["Latest checkpoint's TimeLineID"]), "copies": copies}  # fmt: skip


def close_the_day(tamper: dict[str, Any] | None = None) -> str:
    """What C11 will do, with fabricated evidence: block, stop everything cleanly, mark the close complete."""
    install = CTX.install()
    lock = OperationLock(STATE, "day_close").acquire(timeout=10)
    try:
        owner = window().open(Kind.DAY_CLOSE, "cierre de prueba", lock)
        write_block.block(install, lock, owner, wait_seconds=30)
        services.stop_all(install)  # the stack stops the chain; PostgreSQL shuts down cleanly
        write_block.complete(install, lock, owner, {**evidence(), **(tamper or {})})
        window().close(owner, Event.COMPLETED, lock)
    finally:
        lock.release()
    return owner


@pytest.mark.criterion("C9-H1")
@pytest.mark.criterion("C9-M")
def test_the_installed_cluster_has_the_c9b_model_and_the_migrations_ran_on_it() -> None:
    install = CTX.install()
    user, password, db, _ = install.database
    ops_user = install.ops_database[0]
    assert superuser_sql(f"SELECT datdba::regrole::text FROM pg_database WHERE datname = '{db}';") == ops_user
    assert superuser_sql(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{ops_user}';") == "f"
    assert superuser_sql(f"SELECT pg_has_role('{user}', '{ops_user}', 'MEMBER');") == "f"
    with pytest.raises(database.QueryError) as caught:
        database.query(install, f'ALTER DATABASE "{db}" WITH ALLOW_CONNECTIONS true;', role=user, password=password,
                       dbname=db)  # fmt: skip
    assert caught.value.code == "sql"
    for other in ("postgres", "template1"):
        with pytest.raises(database.QueryError) as caught:
            database.query(install, "SELECT 1;", role=user, password=password, dbname=other)
        assert caught.value.code == "rejected"
    # C2-1 ran `alembic upgrade head` as the application role on this cluster: it owns what it created.
    owners = database.query(
        install, "SELECT DISTINCT relowner::regrole::text FROM pg_class c "
        "JOIN pg_namespace n ON n.oid = c.relnamespace "
        "WHERE n.nspname IN ('public', 'settlements', 'commercial') AND c.relkind IN ('r', 'S', 'v', 'i');",
        role="postgres", password=database.superuser_password(install), dbname=db,
    )  # fmt: skip
    assert owners == [user], owners


@pytest.mark.criterion("C9-1")
@pytest.mark.criterion("C9-5")
@pytest.mark.criterion("C9-6")
def test_the_block_cuts_the_running_backend_survives_a_restart_and_reverts() -> None:
    install = CTX.install()
    assert record()["phase"] == "running"
    assert app_sessions() > 0, "the backend holds pooled sessions"
    lock = OperationLock(STATE, "day_close").acquire(timeout=10)
    try:
        owner = window().open(Kind.DAY_CLOSE, "cierre de prueba", lock)
        started = time.monotonic()
        write_block.block(install, lock, owner, wait_seconds=30)
        metrics["bloqueo de escrituras con el backend en marcha (s)"] = round(time.monotonic() - started, 1)
        assert not allows()
        assert app_sessions() == 0
        assert services.https_get(install, "/api/v1/health/ready")[0] != 200, "the backend still reaches the database"
        winservice.stop(services.PG)  # C9-6: a restart of PostgreSQL does not lift the flag
        winservice.wait_for(services.PG, {State.STOPPED}, 120)
        time.sleep(10)
        assert winservice.status(services.PG).state is State.STOPPED, "the supervisor restarted it during a block"
        winservice.start(services.PG)
        wait_until(lambda: database.ready(install), 120, "PostgreSQL did not come back")
        assert not allows(), "a restart lifted the block"
        write_block.revert(install, lock, owner)
        window().close(owner, Event.REVERTED, lock)
    finally:
        lock.release()
    assert allows()
    assert journal().state is Block.NONE
    wait_until(lambda: services.https_get(install, "/api/v1/health/ready")[0] == 200, 120, "the app did not reconnect")


@pytest.mark.criterion("C9-R")
def test_the_stack_recovers_writes_after_a_verified_close() -> None:
    close_the_day()
    assert journal().state is Block.CLOSED_COMPLETE
    started = time.monotonic()
    cli("services", "start")  # the supervisor, as the stack's account: evidence, PostgreSQL, resume, the rest
    metrics["recuperación automática y arranque completo (s)"] = round(time.monotonic() - started, 1)
    assert record()["phase"] == "running"
    assert journal().state is Block.NONE
    assert allows()
    assert services.https_get(CTX.install(), "/api/v1/health/ready")[0] == 200
    resumes = [op for op in OperationsLog(STATE / "operations.jsonl").read().operations.values()
               if op.kind is Kind.RESUME]  # fmt: skip
    assert resumes[-1].end is Event.COMPLETED
    assert resumes[-1].events[1]["account"] == "DistriComStack"


@pytest.mark.criterion("C9-R")
@pytest.mark.criterion("C9-8")
def test_a_forged_close_keeps_the_application_blocked_until_an_administrator_resumes() -> None:
    owner = close_the_day(tamper={"timeline": 7})
    refused = cli("services", "start", check=False)
    assert refused.returncode == 2, refused.stderr
    final = record()
    assert final["phase"] == "blocked"
    assert "automatic recovery refused" in final["reason"]
    assert winservice.status(services.PG).state is State.RUNNING
    for service in (services.BACKEND, services.FRONTEND, services.CADDY):
        assert winservice.status(service).state is State.STOPPED, service
    assert not allows()
    assert journal().state is Block.CLOSED_COMPLETE
    cli("services", "stop")  # C9-8: a restart of the stack changes nothing
    assert cli("services", "start", check=False).returncode == 2
    assert not allows()
    result = cli("resume", "--operation", owner, "--reason", "evidencia revisada por el administrador")
    assert json.loads(result.stdout.strip().splitlines()[-1])["resumed"] is True
    assert allows()
    assert journal().state is Block.NONE
    cli("services", "stop")
    cli("services", "start")
    assert record()["phase"] == "running"
