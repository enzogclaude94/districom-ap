"""W2-C9-B on a real PostgreSQL: roles (H1), block, reversal, interruptions, resume and automatic recovery.

One disposable cluster for the module, made by the real ``database.init``. Every test starts and must end with
no write block: the journal ``none``, the flag ``true``, no window and no operation left open.
"""

import json
import time
from collections.abc import Iterator
from pathlib import Path
from threading import Thread
from typing import Any

import pytest

from db_tests.cluster import (
    APP_PASSWORD,
    APP_USER,
    DATABASE,
    OPS_PASSWORD,
    OPS_USER,
    Cluster,
    Session,
    child,
    fabricated_evidence,
    make_install,
    values,
    workdir,
)
from db_tests.conftest import metrics
from districom_ops import database, services, write_block
from districom_ops.database import QueryError
from districom_ops.maintenance import MaintenanceWindow
from districom_ops.oplock import LockBusy, OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog
from districom_ops.write_block import State, WriteBlockError

pytestmark = pytest.mark.timeout(600)


@pytest.fixture(scope="module")
def cluster(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Cluster]:
    c = Cluster(workdir(tmp_path_factory, "c9b"))
    metrics["PostgreSQL"] = f"PostgreSQL {c.version()}"
    metrics["database init con el modelo de C9-B (s)"] = c.init_seconds
    c.app("CREATE TABLE c9_probe (id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, who text NOT NULL);")
    yield c
    c.stop()


def state(cluster: Cluster) -> Path:
    return cluster.install.layout.state


def oplog(cluster: Cluster) -> OperationsLog:
    return OperationsLog(state(cluster) / "operations.jsonl")


def window(cluster: Cluster) -> MaintenanceWindow:
    return MaintenanceWindow(state(cluster), oplog(cluster))


def journal(cluster: Cluster) -> write_block.Journal:
    return write_block.read_journal(state(cluster))


@pytest.fixture(autouse=True)
def nothing_left(cluster: Cluster) -> Iterator[None]:
    yield
    problems = []
    if journal(cluster).state is not State.NONE:
        problems.append(f"journal {journal(cluster).state}")
    if not cluster.allows():
        problems.append("ALLOW_CONNECTIONS false")
    if window(cluster).path.exists():
        problems.append("window open")
    if oplog(cluster).read().incomplete():
        problems.append("operation left open")
    # Back to a clean state for the next test, whatever happened (a failed test must not cascade).
    cluster.superuser(f'ALTER DATABASE "{DATABASE}" WITH ALLOW_CONNECTIONS true;')
    for name in ("write-block.json", "maintenance.json", "operations.jsonl"):
        (state(cluster) / name).unlink(missing_ok=True)
    assert not problems, problems


def opened(cluster: Cluster, kind: Kind = Kind.DAY_CLOSE) -> tuple[OperationLock, str]:
    lock = OperationLock(state(cluster), kind.value).acquire()
    return lock, window(cluster).open(kind, "cierre de prueba", lock)


def lock_is_free(cluster: Cluster) -> bool:
    try:
        OperationLock(state(cluster), "probe").acquire(timeout=5).release()
    except LockBusy:
        return False
    return True


def manual_resume(cluster: Cluster, owner: str, install: Any = None) -> write_block.ResumeResult:
    with OperationLock(state(cluster), "resume").acquire() as lock:
        return write_block.resume(
            install or cluster.install, lock, "resolución manual", target=owner, account="S-1-5-test"
        )


def close_and_resume(cluster: Cluster, owner: str) -> None:
    """What an administrator does after an interruption: end the owner operation, then resume."""
    if window(cluster).path.exists():
        with OperationLock(state(cluster), "manual").acquire() as lock:
            window(cluster).close(owner, Event.REVERTED, lock)
    result = manual_resume(cluster, owner)
    assert result.resumed or journal(cluster).state is State.NONE
    assert cluster.allows()
    assert cluster.app("SELECT 1;") == ["1"]


def snapshot(cluster: Cluster) -> dict[str, Any]:
    """What the block may touch, as PostgreSQL and the files see it."""
    return {
        "database": cluster.superuser(
            "SELECT datallowconn, datdba::regrole, datacl::text FROM pg_database WHERE datname = 'districom';"
        ),
        "roles": cluster.superuser(
            "SELECT rolname, rolcanlogin, rolsuper, rolcreatedb, rolcreaterole FROM pg_roles "
            "WHERE rolname !~ '^pg_' ORDER BY 1;"
        ),
        "members": cluster.superuser(
            "SELECT r.rolname, m.rolname FROM pg_auth_members a JOIN pg_roles r ON r.oid = a.roleid "
            "JOIN pg_roles m ON m.oid = a.member ORDER BY 1, 2;"
        ),
        "hba": (cluster.install.pg_data / "pg_hba.conf").read_bytes(),
        "rows": cluster.app("SELECT count(*) FROM c9_probe;"),
    }


# --- C9-H1 -----------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-H1")
def test_the_application_role_cannot_lift_the_block_nor_reach_another_database(cluster: Cluster) -> None:
    with pytest.raises(QueryError) as caught:
        cluster.app(f'ALTER DATABASE "{DATABASE}" WITH ALLOW_CONNECTIONS true;')
    assert caught.value.code == "sql"  # must be owner
    for other in ("postgres", "template1"):
        with pytest.raises(QueryError) as caught:
            cluster.app("SELECT 1;", dbname=other)
        assert caught.value.code == "rejected", other
    with pytest.raises(QueryError):
        cluster.app(f'SET ROLE "{OPS_USER}";')
    assert cluster.superuser(f"SELECT pg_has_role('{APP_USER}', '{OPS_USER}', 'MEMBER');") == ["f"]
    assert cluster.superuser(f"SELECT count(*) FROM pg_auth_members WHERE member = '{APP_USER}'::regrole;") == ["0"], (
        "the application role is member of a role"
    )
    assert cluster.superuser(f"SELECT rolsuper FROM pg_roles WHERE rolname = '{OPS_USER}';") == ["f"]
    assert cluster.superuser(f"SELECT datdba::regrole::text FROM pg_database WHERE datname = '{DATABASE}';") == [
        OPS_USER
    ]
    # districom_ops, not a superuser, does change the flag (from postgres) and back.
    cluster.ops(f'ALTER DATABASE "{DATABASE}" WITH ALLOW_CONNECTIONS false;')
    assert not cluster.allows()
    with pytest.raises(QueryError) as caught:
        cluster.app("SELECT 1;")
    assert caught.value.code == "not_accepting"
    cluster.ops(f'ALTER DATABASE "{DATABASE}" WITH ALLOW_CONNECTIONS true;')
    assert cluster.allows()
    # Nothing global was revoked: PUBLIC keeps CONNECT on postgres and template1 (pg_hba stops the application).
    assert cluster.superuser("SELECT has_database_privilege('public', 'postgres', 'CONNECT');") == ["t"]


@pytest.mark.criterion("C9-H1")
def test_the_model_is_only_what_init_creates(cluster: Cluster) -> None:
    hba = (cluster.install.pg_data / "pg_hba.conf").read_text("utf-8")
    assert f"host    all    {APP_USER}    all    reject" in hba
    assert cluster.superuser(
        f"SELECT string_agg(r.rolname, ',' ORDER BY r.rolname) FROM pg_auth_members a JOIN pg_roles r "
        f"ON r.oid = a.roleid WHERE a.member = '{OPS_USER}'::regrole;"
    ) == ["pg_read_all_stats,pg_signal_backend"]
    assert cluster.superuser("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public';",
                             dbname=DATABASE) == [APP_USER]  # fmt: skip


# --- C9-1, C9-5, C9-3 ------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-1")
@pytest.mark.criterion("C9-5")
@pytest.mark.criterion("C9-3")
def test_every_role_is_cut_and_kept_out_and_the_reversal_puts_everything_back(cluster: Cluster) -> None:
    cluster.superuser(
        "CREATE ROLE c9_public LOGIN PASSWORD 'Fict1cia-pub-1'; CREATE ROLE c9_tester LOGIN PASSWORD 'Fict1cia-tst-1';"
    )
    cluster.superuser(f'GRANT CONNECT ON DATABASE "{DATABASE}" TO c9_tester;')
    cluster.superuser(f'GRANT CONNECT ON DATABASE "{DATABASE}" TO PUBLIC;')  # c9_public: only through PUBLIC
    cluster.app("GRANT INSERT ON c9_probe TO PUBLIC;")
    sessions: list[Session] = []
    try:
        roles = [(APP_USER, APP_PASSWORD), ("c9_public", "Fict1cia-pub-1"), ("c9_tester", "Fict1cia-tst-1")]
        for role, password in roles:
            session = Session(cluster, role, password)
            assert session.run(f"INSERT INTO c9_probe (who) VALUES ('{role}');"), session.stderr()
            sessions.append(session)
        before = snapshot(cluster)
        lock, owner = opened(cluster)
        started = time.monotonic()
        write_block.block(cluster.install, lock, owner, wait_seconds=10)
        metrics["bloqueo con tres sesiones abiertas (s)"] = round(time.monotonic() - started, 1)
        assert journal(cluster).state is State.BLOCKED
        for session in sessions:
            assert not session.run("INSERT INTO c9_probe (who) VALUES ('después');"), "a session wrote after the block"
        for role, password in [*roles, ("postgres", database.superuser_password(cluster.install))]:
            with pytest.raises(QueryError) as caught:
                database.query(cluster.install, "SELECT 1;", role=role, password=password, dbname=DATABASE)
            assert caught.value.code == "not_accepting", role
        assert not cluster.allows()
        assert cluster.client_sessions() == 0  # C9-5
        write_block.revert(cluster.install, lock, owner)
        window(cluster).close(owner, Event.REVERTED, lock)
        lock.release()
        after = snapshot(cluster)
        assert {k: v for k, v in after.items() if k != "rows"} == {k: v for k, v in before.items() if k != "rows"}
        assert after["rows"] == before["rows"], "rows written after the block"
        assert journal(cluster).state is State.NONE
        steps = [e.get("step") for e in oplog(cluster).read().operations[owner].events]
        assert "writes_blocked" in steps
        assert "write_block_reverted" in steps
    finally:
        for session in sessions:
            session.close()
        cluster.app("REVOKE INSERT ON c9_probe FROM PUBLIC;")
        cluster.superuser(f'REVOKE CONNECT ON DATABASE "{DATABASE}" FROM PUBLIC, c9_tester;')
        cluster.superuser("DROP ROLE c9_public; DROP ROLE c9_tester;")


SENTINEL = "c9-texto-que-no-debe-registrarse-7f3a"


def logged_text(cluster: Cluster, caplog: pytest.LogCaptureFixture) -> str:
    """Everything the block left in writing: the log records (with their fields) and the operations record."""
    records = [json.dumps({k: str(v) for k, v in vars(r).items()}) for r in caplog.records]
    return "\n".join(records) + (state(cluster) / "operations.jsonl").read_text("utf-8")


@pytest.mark.criterion("C9-1")
@pytest.mark.criterion("C9-D3")
def test_a_superuser_session_makes_the_block_revert_never_forced(
    cluster: Cluster, caplog: pytest.LogCaptureFixture
) -> None:
    password = database.superuser_password(cluster.install)
    session = Session(cluster, "postgres", password)
    try:
        assert session.run(f"SELECT '{SENTINEL}';")
        before = snapshot(cluster)
        lock, owner = opened(cluster)
        with pytest.raises(WriteBlockError, match="superuser"):
            write_block.block(cluster.install, lock, owner, wait_seconds=5)
        # Reverted and verified on PostgreSQL itself: the flag, the roles, the members, pg_hba, the rows.
        assert journal(cluster).state is State.NONE
        assert cluster.allows()
        assert snapshot(cluster) == before
        assert cluster.app("SELECT 1;") == ["1"]
        assert session.run("SELECT 1;"), "the superuser session was ended"
        steps = oplog(cluster).read().operations[owner].events
        failed = [e for e in steps if e.get("step") == "write_block_failed"]
        assert failed
        assert "superuser" in failed[0]["reason"]
        assert any(e.get("step") == "write_block_reverted" for e in steps)
        text = logged_text(cluster, caplog)
        assert "write_block_failed" in text
        for secret in (SENTINEL, password, APP_PASSWORD, OPS_PASSWORD, "SELECT", "ALTER DATABASE"):
            assert secret not in text, f"the record or the log holds {secret[:8]}…"
        window(cluster).close(owner, Event.REVERTED, lock)
        lock.release()
    finally:
        session.close()


@pytest.mark.criterion("C9-D3")
@pytest.mark.parametrize("failure", ["set_true", "verify"])
def test_a_reversal_that_cannot_be_verified_stays_blocked_until_a_controlled_resume(
    cluster: Cluster, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    """A superuser session makes the block fail; then the reversal itself fails: ALTER … true refused
    (``set_true``) or the check after it unreadable (``verify``, the flag already true)."""
    session = Session(cluster, "postgres", database.superuser_password(cluster.install))
    real_set = write_block.Database.set_allow_connections
    real_allows = write_block.Database.allows_connections
    calls = {"after_true": 0}

    def set_allow(self: write_block.Database, allowed: bool) -> None:
        if allowed and failure == "set_true":
            raise WriteBlockError("operations connection failed: injected")
        real_set(self, allowed)
        if allowed:
            calls["after_true"] = 1

    def allows(self: write_block.Database) -> bool:
        if failure == "verify" and calls["after_true"] == 1:
            calls["after_true"] = 2
            raise WriteBlockError("pg_database could not be read: injected")
        return real_allows(self)

    try:
        lock, owner = opened(cluster)
        monkeypatch.setattr(write_block.Database, "set_allow_connections", set_allow)
        monkeypatch.setattr(write_block.Database, "allows_connections", allows)
        with pytest.raises(WriteBlockError, match="could not be verified"):
            write_block.block(cluster.install, lock, owner, wait_seconds=5)
        monkeypatch.undo()
        assert journal(cluster).state is State.REVERTING  # blocked: only a resume leaves it
        assert not cluster.allows(), "an unverified reversal left connections enabled"
        with pytest.raises(QueryError) as caught:
            cluster.app("SELECT 1;")
        assert caught.value.code == "not_accepting"
        assert write_block.journal_blocker(state(cluster)) is not None
        with pytest.raises(WriteBlockError, match="only resume"):
            write_block.revert(cluster.install, lock, owner)  # not even the live owner retries it
        assert not cluster.allows()
        events = oplog(cluster).read().operations[owner].events
        record = [e for e in events if e.get("step") == "write_block_revert_failed"]
        assert record
        assert record[0]["datallowconn"] == "false"
        assert any(r.getMessage() == "write_block_revert_failed" and r.levelname == "CRITICAL" for r in caplog.records)
        window(cluster).close(owner, Event.REVERTED, lock)
        lock.release()
        session.close()
        close_and_resume(cluster, owner)  # the controlled recovery
        assert journal(cluster).state is State.NONE
    finally:
        session.close()


# --- C9-D1: pg_read_all_stats only for districom_ops -------------------------------------------------------------


@pytest.mark.criterion("C9-D1")
def test_only_the_operations_role_sees_the_transactions_of_other_roles(cluster: Cluster) -> None:
    cluster.superuser("CREATE ROLE c9_nostats LOGIN PASSWORD 'Fict1cia-nst-4Kp8Wq2Ze';")
    app = Session(cluster, APP_USER, APP_PASSWORD)
    superuser = Session(cluster, "postgres", database.superuser_password(cluster.install))
    try:
        assert app.run(f"BEGIN; INSERT INTO c9_probe (who) VALUES ('{SENTINEL}');")
        assert superuser.run("BEGIN; SELECT 1;")
        in_xact = (f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{DATABASE}' "
                   f"AND usename = '{APP_USER}' AND xact_start IS NOT NULL;")  # fmt: skip
        # Positive: districom_ops, through pg_read_all_stats, sees the application's open transaction.
        assert cluster.ops(in_xact) == ["1"]
        assert write_block.Database(cluster.install).in_transaction() == 2
        # Negative: the same query by a role without the membership sees nothing on this PostgreSQL version.
        no_stats = database.query(cluster.install, in_xact, role="c9_nostats", password="Fict1cia-nst-4Kp8Wq2Ze",
                                  dbname="postgres")  # fmt: skip
        assert no_stats == ["0"], "without pg_read_all_stats another role's transaction is visible"
        # The application role is not a member and does not see the superuser's session.
        assert cluster.superuser(f"SELECT pg_has_role('{APP_USER}', 'pg_read_all_stats', 'MEMBER');") == ["f"]
        assert cluster.superuser(f"SELECT pg_has_role('{OPS_USER}', 'pg_read_all_stats', 'MEMBER');") == ["t"]
        hidden = cluster.app("SELECT count(*) FROM pg_stat_activity a JOIN pg_roles r ON r.oid = a.usesysid "
                             "WHERE r.rolsuper AND a.xact_start IS NOT NULL;")  # fmt: skip
        assert hidden == ["0"]
        with pytest.raises(QueryError):
            cluster.app(f'GRANT pg_read_all_stats TO "{APP_USER}";')
        with pytest.raises(QueryError):
            cluster.app(f'GRANT "{OPS_USER}" TO "{APP_USER}";')
        assert cluster.superuser(f"SELECT pg_has_role('{APP_USER}', 'pg_read_all_stats', 'MEMBER');") == ["f"]
    finally:
        app.close()
        superuser.close()
        cluster.superuser("DROP ROLE c9_nostats;")
        cluster.app("DELETE FROM c9_probe;")


@pytest.mark.criterion("C9-D1")
def test_the_block_never_records_the_text_of_the_sessions(cluster: Cluster, caplog: pytest.LogCaptureFixture) -> None:
    session = Session(cluster, APP_USER, APP_PASSWORD)
    try:
        assert session.run(f"SELECT '{SENTINEL}';")  # the session's last query holds the text
        lock, owner = opened(cluster)
        caplog.set_level("DEBUG", logger="districom_ops")
        write_block.block(cluster.install, lock, owner, wait_seconds=10)
        write_block.revert(cluster.install, lock, owner)
        window(cluster).close(owner, Event.REVERTED, lock)
        lock.release()
        text = logged_text(cluster, caplog)
        assert "writes_blocked" in text
        for secret in (SENTINEL, APP_PASSWORD, OPS_PASSWORD, "pg_stat_activity", "SELECT"):
            assert secret not in text, secret[:8]
    finally:
        session.close()


# --- C9-D2: the public schema belongs to the application ----------------------------------------------------------


@pytest.mark.criterion("C9-D2")
def test_the_application_owns_public_and_what_it_creates_but_not_the_database(cluster: Cluster) -> None:
    assert cluster.superuser(f"SELECT datdba::regrole::text FROM pg_database WHERE datname = '{DATABASE}';") == [
        OPS_USER
    ]
    assert cluster.superuser("SELECT nspowner::regrole::text FROM pg_namespace WHERE nspname = 'public';",
                             dbname=DATABASE) == [APP_USER]  # fmt: skip
    # Positive: it creates, changes and drops in public and in a schema of its own, and owns them.
    cluster.app("CREATE SCHEMA c9_own; CREATE TABLE c9_own.t (id int); CREATE TABLE public.c9_t (id int);"
                "ALTER TABLE public.c9_t ADD COLUMN v text; INSERT INTO c9_own.t VALUES (1);")  # fmt: skip
    owners = cluster.superuser("SELECT DISTINCT relowner::regrole::text FROM pg_class "
                               "WHERE relname IN ('t', 'c9_t');", dbname=DATABASE)  # fmt: skip
    assert owners == [APP_USER]
    cluster.app("DROP SCHEMA c9_own CASCADE; DROP TABLE public.c9_t;")
    # Negative: the database itself stays districom_ops'.
    for sql in (f'ALTER DATABASE "{DATABASE}" OWNER TO "{APP_USER}";',
                f'ALTER DATABASE "{DATABASE}" WITH ALLOW_CONNECTIONS true;',
                f'ALTER DATABASE "{DATABASE}" WITH ALLOW_CONNECTIONS false;',
                f'SET ROLE "{OPS_USER}";',
                f'SET SESSION AUTHORIZATION "{OPS_USER}";'):  # fmt: skip
        with pytest.raises(QueryError):
            cluster.app(sql)
    assert cluster.allows()


# --- C9-2 ------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-2")
def test_a_long_transaction_ends_well_before_the_cut(cluster: Cluster) -> None:
    session = Session(cluster, APP_USER, APP_PASSWORD)
    try:
        assert session.run("BEGIN; INSERT INTO c9_probe (who) VALUES ('transacción larga');")
        lock, owner = opened(cluster)
        errors: list[BaseException] = []

        def blocking() -> None:
            try:
                write_block.block(cluster.install, lock, owner, wait_seconds=60)
            except BaseException as exc:
                errors.append(exc)

        thread = Thread(target=blocking)
        thread.start()
        deadline = time.monotonic() + 30
        while journal(cluster).step < 4 and time.monotonic() < deadline:
            time.sleep(0.1)
        assert journal(cluster).step == 4, "the block did not reach the wait"
        time.sleep(1)
        assert journal(cluster).step == 4, "the block went on while a transaction was open"
        assert session.run("COMMIT;"), "the transaction was cut"
        thread.join(60)
        assert not errors, errors
        assert journal(cluster).state is State.BLOCKED
        write_block.revert(cluster.install, lock, owner)
        window(cluster).close(owner, Event.COMPLETED, lock)
        lock.release()
        assert cluster.app("SELECT count(*) FROM c9_probe WHERE who = 'transacción larga';") == ["1"]
    finally:
        session.close()
        cluster.app("DELETE FROM c9_probe;")


@pytest.mark.criterion("C9-2")
def test_a_transaction_that_outlasts_the_wait_reverts_the_block_without_cutting_it(cluster: Cluster) -> None:
    session = Session(cluster, APP_USER, APP_PASSWORD)
    try:
        assert session.run("BEGIN; INSERT INTO c9_probe (who) VALUES ('no se corta');")
        lock, owner = opened(cluster)
        with pytest.raises(WriteBlockError, match="still in progress"):
            write_block.block(cluster.install, lock, owner, wait_seconds=2)
        assert journal(cluster).state is State.NONE
        assert cluster.allows()
        assert session.run("COMMIT;"), "the transaction was cut"
        assert cluster.app("SELECT count(*) FROM c9_probe WHERE who = 'no se corta';") == ["1"]
        window(cluster).close(owner, Event.REVERTED, lock)
        lock.release()
    finally:
        session.close()
        cluster.app("DELETE FROM c9_probe;")


# --- C9-4: killed at each step and in each state -----------------------------------------------------------------

BLOCK = """
lock = OperationLock(state, "day_close").acquire()
owner = window.open(Kind.DAY_CLOSE, "cierre interrumpido", lock)
print(owner, flush=True)
write_block.block(install, lock, owner, wait_seconds=10)
"""
KILLS = [
    *((f"save:blocking:{k}", State.BLOCKING, k, k <= 3) for k in range(1, 7)),
    ("save:blocked:6", State.BLOCKED, 6, False),
]


@pytest.mark.criterion("C9-4")
@pytest.mark.parametrize(("kill", "expected", "step", "allowed"), KILLS, ids=[k[0] for k in KILLS])
def test_a_block_killed_at_each_step(cluster: Cluster, kill: str, expected: State, step: int, allowed: bool) -> None:
    result = child(cluster, BLOCK, kill_at=kill)
    assert result.returncode == 9, result.stderr
    owner = result.stdout.split()[0]
    assert lock_is_free(cluster)
    found = journal(cluster)
    assert (found.state, found.step, found.owner) == (expected, step, owner)
    assert cluster.allows() is allowed, "the flag is not what the journal records"
    assert any("write block" in r for r in services.blockers(cluster.install)), "the start is not blocked"
    intruder = OperationLock(state(cluster), "intruder")  # nobody else can pass for the owner
    with intruder.acquire() as lock, pytest.raises(WriteBlockError, match="live owner"):
        write_block.revert(cluster.install, lock, owner)
    close_and_resume(cluster, owner)


EVIDENCE_FORM = {
    "cut_lsn": "0/1000000",
    "checkpoint_lsn": "0/1000000",
    "timeline": 1,
    "copies": [
        {"repo": "repo1", "path": "x", "sha256": "0" * 64},
        {"repo": "repo2", "path": "y", "sha256": "0" * 64},
    ],
}
STATE_KILLS = {
    "reverting": BLOCK + "write_block.revert(install, lock, owner)\n",
    "closed_complete": BLOCK + f"write_block.complete(install, lock, owner, {EVIDENCE_FORM!r})\n",
}


@pytest.mark.criterion("C9-4")
@pytest.mark.parametrize("name", list(STATE_KILLS))
def test_a_process_killed_in_the_other_states(cluster: Cluster, name: str) -> None:
    result = child(cluster, STATE_KILLS[name], kill_at=f"save:{name}:6")
    assert result.returncode == 9, result.stderr
    owner = result.stdout.split()[0]
    assert lock_is_free(cluster)
    assert journal(cluster).state is State(name)
    assert not cluster.allows()
    assert any("write block" in r for r in services.blockers(cluster.install))
    close_and_resume(cluster, owner)


# --- C9-6 ------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-6")
def test_the_flag_survives_a_restart_of_postgresql(cluster: Cluster) -> None:
    lock, owner = opened(cluster)
    write_block.block(cluster.install, lock, owner, wait_seconds=10)
    lock.release()
    cluster.stop()
    cluster.start()
    assert not cluster.allows(), "a restart lifted the block"
    assert journal(cluster).state is State.BLOCKED
    close_and_resume(cluster, owner)  # through postgres, with districom_ops


# --- C9-7 ------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-7")
def test_a_broken_operations_connection_changes_nothing(cluster: Cluster, caplog: pytest.LogCaptureFixture) -> None:
    broken = make_install(values(cluster.root, cluster.port, ops_password="Fict1cia-mal4-Qx9Zr2Lw"))
    lock, owner = opened(cluster)
    with pytest.raises(WriteBlockError, match="auth"):
        write_block.block(broken, lock, owner)
    assert journal(cluster).state is State.NONE
    assert cluster.allows()
    write_block.block(cluster.install, lock, owner, wait_seconds=10)
    window(cluster).close(owner, Event.REVERTED, lock)
    lock.release()
    cluster.superuser("CREATE ROLE c9_noperm LOGIN PASSWORD 'Fict1cia-nop-8Tq2Vm5Rk';")
    try:
        for install in (broken, make_install(values(cluster.root, cluster.port, ops_user="c9_noperm",
                                                    ops_password="Fict1cia-nop-8Tq2Vm5Rk"))):  # fmt: skip
            caplog.clear()
            with pytest.raises(WriteBlockError):
                manual_resume(cluster, owner, install)
            assert not cluster.allows(), "writes were re-enabled with a broken operations connection"
            assert journal(cluster).state is State.BLOCKED
            assert any(r.getMessage() == "resume_failed" and r.levelname == "ERROR" for r in caplog.records)
            assert oplog(cluster).read().incomplete() == [], "the failed resume did not end"
    finally:
        cluster.superuser("DROP ROLE c9_noperm;")
    close_and_resume(cluster, owner)


# --- C9-8 ------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-8")
def test_an_expired_window_or_a_finished_wait_never_enables_writes(cluster: Cluster) -> None:
    lock, owner = opened(cluster)
    write_block.block(cluster.install, lock, owner, wait_seconds=10)
    lock.release()
    path = window(cluster).path  # the window opened two hours ago: overdue (the limit is 60 minutes)
    data = json.loads(path.read_text("utf-8"))
    path.write_text(json.dumps({**data, "opened_at": "2000-01-01T00:00:00+00:00"}), "utf-8")
    assert any("overdue" in r.lower() for r in services.blockers(cluster.install))
    with OperationLock(state(cluster), "resume").acquire() as resume_lock:
        with pytest.raises(WriteBlockError, match=r"(?i)overdue"):
            write_block.resume(cluster.install, resume_lock, "con la ventana vencida", target=owner)
        with pytest.raises(WriteBlockError, match="closed_complete"):
            write_block.resume(cluster.install, resume_lock, "automática", automatic=True)
    assert not cluster.allows()
    assert journal(cluster).state is State.BLOCKED
    close_and_resume(cluster, owner)


# --- C9-9 ------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-9")
def test_repeating_the_reversal_or_the_resume_changes_nothing(cluster: Cluster) -> None:
    lock, owner = opened(cluster)
    write_block.block(cluster.install, lock, owner, wait_seconds=10)
    write_block.revert(cluster.install, lock, owner)
    before = (state(cluster) / "write-block.json").read_bytes()
    write_block.revert(cluster.install, lock, owner)  # nothing to undo: ends at once
    assert (state(cluster) / "write-block.json").read_bytes() == before
    write_block.block(cluster.install, lock, owner, wait_seconds=10)
    window(cluster).close(owner, Event.REVERTED, lock)
    lock.release()
    assert manual_resume(cluster, owner).resumed
    again = manual_resume(cluster, owner)
    assert not again.resumed, "a second resume did something"
    assert cluster.allows()


# --- C9-X7: a resume killed at each step --------------------------------------------------------------------------

RESUME = """
lock = OperationLock(state, "resume").acquire()
write_block.resume(install, lock, "resume interrumpido", target={owner!r}, account="S-1-5-test")
"""
RESUME_KILLS = [
    ("step:resume_started", True),
    ("step:journal_found", True),
    ("step:resume_check_passed", True),
    ("save:resuming:6", True),
    ("allow:true", True),
    ("step:connections_allowed", True),
    ("step:connections_verified", True),
    ("save:none:6", False),
]


@pytest.mark.criterion("C9-X7")
@pytest.mark.criterion("C9-9")
@pytest.mark.parametrize(("kill", "blocked_after"), RESUME_KILLS, ids=[k[0] for k in RESUME_KILLS])
def test_a_resume_killed_at_each_step_is_reconciled(cluster: Cluster, kill: str, blocked_after: bool) -> None:
    lock, owner = opened(cluster)
    write_block.block(cluster.install, lock, owner, wait_seconds=10)
    window(cluster).close(owner, Event.REVERTED, lock)
    lock.release()
    result = child(cluster, RESUME.format(owner=owner), kill_at=kill)
    assert result.returncode == 9, result.stderr
    assert lock_is_free(cluster)
    killed = next(op for op in oplog(cluster).read().incomplete() if op.kind is Kind.RESUME)
    if blocked_after:
        assert journal(cluster).state is not State.NONE
        assert any("write block" in r or "has no end" in r for r in services.blockers(cluster.install))
    final = manual_resume(cluster, owner)
    record = oplog(cluster).read().operations[killed.id]
    assert record.end is Event.FAILED, "the interrupted resume was not closed"
    ending = record.events[-1]
    assert ending["reconciled_by"] == final.operation
    if kill in ("allow:true", "step:connections_allowed", "step:connections_verified"):
        assert ending["datallowconn"] is True  # found true: it was put back to false before going on
        history = [h["state"] for h in journal(cluster).history]
        assert history[-3:] == ["blocked", "resuming", "none"], history
    assert journal(cluster).state is State.NONE
    assert cluster.allows()


# --- C9-R: automatic recovery ---------------------------------------------------------------------------------------


def closed_complete(cluster: Cluster, evidence_dir: Path, tamper: dict[str, Any] | None = None) -> str:
    """A fabricated closed_complete journal, as C11 would leave it: block, owner ended, PostgreSQL stopped."""
    lock, owner = opened(cluster)
    write_block.block(cluster.install, lock, owner, wait_seconds=10)
    cluster.stop()
    evidence = {**fabricated_evidence(cluster, evidence_dir), **(tamper or {})}
    written = write_block.read_journal(state(cluster))
    write_block._save(state(cluster), written, State.CLOSED_COMPLETE, evidence=evidence)
    window(cluster).close(owner, Event.COMPLETED, lock)
    lock.release()
    return owner


def automatic(cluster: Cluster, install: Any = None) -> write_block.ResumeResult:
    with OperationLock(state(cluster), "supervisor_start").acquire() as lock:
        return write_block.resume(install or cluster.install, lock, "recuperación automática", automatic=True,
                                  account="DistriComStack", start_postgresql=cluster.start)  # fmt: skip


@pytest.mark.criterion("C9-R")
def test_the_automatic_recovery_from_a_verified_close(cluster: Cluster, tmp_path: Path) -> None:
    closed_complete(cluster, tmp_path)
    result = automatic(cluster)
    assert result.resumed
    assert journal(cluster).state is State.NONE
    assert cluster.allows()
    steps = [e.get("step") for e in oplog(cluster).read().operations[result.operation].events]
    assert steps[1:5] == ["resume_started", "journal_found", "evidence_verified", "resume_check_passed"]


def _copy_gone(evidence: dict[str, Any]) -> dict[str, Any]:
    return {"copies": [{**evidence["copies"][0], "path": "no-existe"}, evidence["copies"][1]]}


REFUSALS: dict[str, Any] = {
    "checkpoint": lambda e: {"checkpoint_lsn": "0/1", "cut_lsn": "0/1"},
    "timeline": lambda e: {"timeline": e["timeline"] + 1},
    "hash": lambda e: {"copies": [{**e["copies"][0], "sha256": "f" * 64}, e["copies"][1]]},
    "copy_missing": _copy_gone,
    "one_repo": lambda e: {"copies": e["copies"][:1]},
    "absent": None,
}


@pytest.mark.criterion("C9-R")
@pytest.mark.parametrize("case", list(REFUSALS))
def test_an_invalid_evidence_keeps_writes_blocked(cluster: Cluster, tmp_path: Path, case: str) -> None:
    owner = closed_complete(cluster, tmp_path)
    found = journal(cluster)
    assert found.evidence is not None
    tamper = REFUSALS[case]
    evidence = None if tamper is None else {**found.evidence, **tamper(found.evidence)}
    (state(cluster) / "write-block.json").write_text(json.dumps({**found.to_json(), "evidence": evidence}), "utf-8")
    with pytest.raises(WriteBlockError, match="evidence"):
        automatic(cluster)
    assert journal(cluster).state is State.CLOSED_COMPLETE
    cluster.start()
    assert not cluster.allows()
    close_and_resume(cluster, owner)  # an administrator decides


@pytest.mark.criterion("C9-R")
@pytest.mark.parametrize("case", ["running", "blocked_journal", "incomplete", "window", "ops_broken", "probe"])
def test_any_other_combination_keeps_writes_blocked(cluster: Cluster, tmp_path: Path, case: str) -> None:
    owner = closed_complete(cluster, tmp_path)
    install: Any = cluster.install
    extra = None
    if case == "running":
        cluster.start()  # the shutdown can no longer be verified
    elif case == "blocked_journal":
        found = journal(cluster)
        (state(cluster) / "write-block.json").write_text(json.dumps({**found.to_json(), "state": "blocked"}), "utf-8")
    elif case == "incomplete":
        extra = oplog(cluster).start(Kind.UPDATE, "actualización cortada")
    elif case == "window":
        with OperationLock(state(cluster), "manual").acquire() as lock:
            extra = window(cluster).open(Kind.MANUAL, "ventana abierta", lock)
    elif case == "ops_broken":
        install = make_install(values(cluster.root, cluster.port, ops_password="Fict1cia-mal4-Qx9Zr2Lw"))
    elif case == "probe":
        install = make_install(values(cluster.root, cluster.port, app_password="Fict1cia-mal4-App7Kp3W"))
    with pytest.raises(WriteBlockError):
        automatic(cluster, install)
    if case not in ("running",):
        assert journal(cluster).state in (State.CLOSED_COMPLETE, State.BLOCKED)
    if not database.ready(cluster.install):
        cluster.start()
    assert not cluster.allows(), f"{case}: writes enabled"
    if case == "probe":  # the flag had been set to true and was put back to false before failing
        history = [h["state"] for h in journal(cluster).history]
        assert history[-2:] == ["resuming", "closed_complete"], history
    if extra is not None:
        with OperationLock(state(cluster), "manual").acquire() as lock:
            if window(cluster).path.exists():
                window(cluster).close(extra, Event.REVERTED, lock)
            else:
                oplog(cluster).finish(extra, Event.FAILED)
    close_and_resume(cluster, owner)
