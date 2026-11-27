"""W2-C10 acceptance: the verification of the WAL at shutdown with the real PostgreSQL 17.6 service, the real
repo1 on the virtual backup disk and an encrypted repo2 (a local folder: the real bucket stays PENDIENTE).

Runs after ``test_c6_backups.py`` in the job «Windows backups (W2-C6)» (D4), with the same installation, and
reports its criteria separately (``w2c10-results``). Every C10 run is inside a maintenance window and a C9-B
block of its own operation; afterwards PostgreSQL is started again, the block reverted and the window closed.
Windows is never shut down. Timelines (C10-6, C10-7) and the immediate shutdown use a disposable cluster with
its own repositories, under ``DISTRICOM_C10_LAB``. Faults are injected only at the hooks of ``wal_shutdown``.
"""

import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from districom_backup import pgtools
from districom_backup.base_backup import list_backups, take_base_backup
from districom_backup.offsite import REMOTE_NAME, CryptRemote
from districom_backup.repo import init_repo
from districom_backup.restore import RestoreStatus, archive_command, restore

from backups_tests.conftest import metrics
from backups_tests.test_c6_backups import app_sql, start, wait_result
from districom_ops import backup_tasks, database, services, tasklock, winservice, write_block
from districom_ops import wal_shutdown as ws
from districom_ops.maintenance import open_stopping_tasks
from districom_ops.oplock import OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog
from services_tests.test_c2_services import CTX, ROOT, cli, superuser_sql, wait_until

STATE = ROOT / "state"
LAB = Path(os.environ.get("DISTRICOM_C10_LAB", r"C:\c10-lab"))
LAB_KEY = "c10-lab-key-only-for-this-disposable-remote"

pytestmark = pytest.mark.timeout(1800)


# --- helpers -----------------------------------------------------------------------------------------------------


def install() -> Any:
    return CTX.install()


def settings() -> backup_tasks.TaskSettings:
    return backup_tasks.TaskSettings.read(ROOT / "config" / "tasks.json")


def remote() -> CryptRemote:
    s = settings()
    assert s.offsite_remote
    assert s.rclone
    return CryptRemote(s.offsite_remote, (ROOT / "config" / "offsite.secret").read_text("utf-8").strip(), str(s.rclone))


def summary(result: ws.Result) -> dict[str, Any]:
    after = result.checks.get("wal_after_cut", {})
    return {
        "verdict": result.verdict,
        "failed": sorted(n for n, c in result.checks.items() if not c.get("ok")),
        "records_after_cut": sorted({f"{r['rmgr']}/{r['type']}" for r in after.get("records", [])}),
        "problems": result.problems()[:6],
    }


def incompatible(result: ws.Result) -> set[tuple[str, str]]:
    seen = result.checks["wal_after_cut"].get("by_category", {}).get("incompatible", [])
    return {(r["rmgr"], r["type"]) for r in seen}


def heap_inserts(found: set[tuple[str, str]]) -> set[str]:
    return {kind for rmgr, kind in found if rmgr == "Heap" and kind.startswith("INSERT")}


def allows_connections() -> bool:
    name = install().database[2]
    return superuser_sql(f"SELECT datallowconn FROM pg_database WHERE datname = '{name}';") == "t"


def journal_state() -> str:
    return write_block.read_journal(STATE).state.value


def operation_steps(owner: str) -> list[str]:
    entries = [json.loads(line) for line in (STATE / "operations.jsonl").read_text("utf-8").splitlines() if line]
    return [e["step"] for e in entries if e.get("operation") == owner and e.get("event") == "step"]


@contextmanager
def blocked(reason: str, *, block: bool = True) -> Iterator[tuple[Any, OperationLock, str]]:
    """What the trial command does, with the run left to the test: the window and the block of a new operation;
    afterwards PostgreSQL started again, the block reverted and the window closed as reverted."""
    inst = install()
    window = write_block._window(inst)
    owner = open_stopping_tasks(window, Kind.MANUAL, reason, 120)
    lock = OperationLock(STATE, "c10_test").acquire(timeout=30)
    try:
        if block:
            write_block.block(inst, lock, owner, wait_seconds=30)
        yield inst, lock, owner
    finally:
        try:
            services.start_chain(inst, until=services.PG)
            if block:
                write_block.revert(inst, lock, owner)
        finally:
            window.close(owner, Event.REVERTED, lock=lock)
            lock.release()


def run(reason: str, hooks: ws.Hooks | None = None) -> ws.Result:
    with blocked(reason) as (inst, lock, owner):
        return ws.run(inst, lock, owner, hooks)


def back_to_normal() -> None:
    assert journal_state() == "none"
    assert not (STATE / "maintenance.json").exists()
    assert database.ready(install())
    assert allows_connections()
    assert not tasklock.heavy_task_running(STATE)


@pytest.fixture(scope="module", autouse=True)
def prepared() -> Iterator[None]:
    """A fresh base backup (as every day), so the fixed set is the WAL since it, and a table of the superuser in
    ``postgres`` for the faults (the application database refuses every connection while blocked)."""
    if not database.ready(install()):
        cli("services", "start")
    outcome = wait_result("base_backup", start("base_backup"))
    assert outcome["status"] == "succeeded", outcome
    superuser_sql("CREATE TABLE IF NOT EXISTS c10_late (n int)")
    # The C6 tests created and dropped large tables: vacuum now, so autovacuum has less pending that could write
    # WAL between a cut and its shutdown (that is a FAIL, rightly: C10-8 isolates itself from it).
    superuser_sql("VACUUM (ANALYZE)")
    app_sql("VACUUM (ANALYZE)")
    # C10-9's table, after the VACUUM (a manual VACUUM ignores autovacuum_enabled and sets the hint bits): written
    # now and never read until its test, so its hint bits are still unset then.
    superuser_sql("DROP TABLE IF EXISTS c10_fresh")
    superuser_sql("CREATE TABLE c10_fresh (n int, filler text) WITH (autovacuum_enabled = false)")
    superuser_sql("INSERT INTO c10_fresh SELECT g, repeat('x', 100) FROM generate_series(1, 20000) g")
    yield
    if not database.ready(install()):
        cli("services", "start", check=False)


# --- C10-1, C10-8: a clean shutdown ------------------------------------------------------------------------------


@pytest.mark.criterion("C10-1")
def test_the_trial_command_passes_with_a_clean_shutdown_and_both_repositories_verified_by_content() -> None:
    output = cli("wal", "verify-shutdown", "--reason", "prueba C10-1").stdout.strip().splitlines()[-1]
    answer = json.loads(output)
    assert answer["verdict"] == "PASS", answer
    result = json.loads((Path(answer["diagnostics"]) / "result.json").read_text("utf-8"))
    assert result["evidence_complete"] is True
    checks = result["checks"]
    assert all(checks[n]["ok"] for n in ("cut", "repo1", "shutdown", "wal_after_cut", "controldata", "repo2"))
    assert checks["repo1"]["set"] >= 1, checks["repo1"]
    assert checks["repo1"]["compared_with_server"] >= 1, checks["repo1"]
    assert checks["repo2"]["pushed"] is True
    assert checks["repo2"]["downloaded_and_compared"] == checks["repo2"]["set"] == checks["repo1"]["set"]
    assert {(r["rmgr"], r["type"]) for r in checks["wal_after_cut"]["records"]} <= ws.ALLOWED
    assert checks["controldata"]["state"] == "shut down"
    assert write_block.evidence_problems(result["evidence"]) == []
    back_to_normal()
    metrics["C10-1 prueba completa (comando)"] = {
        "corte": result["cut"]["cut_lsn"], "segmento": result["cut"]["cut_segment"],
        "conjunto fijo": result["cut"]["fixed_set"], "registros después del corte": checks["wal_after_cut"]["records"],
        "repo1": {k: checks["repo1"][k] for k in ("set", "compared_with_server", "older_continuity")},
        "repo2": {k: checks["repo2"][k] for k in ("pushed", "downloaded_and_compared", "continuity_checked")},
    }  # fmt: skip


@pytest.mark.criterion("C10-1")
def test_the_evidence_of_a_pass_is_the_one_c9b_verifies_against_the_stopped_cluster() -> None:
    with blocked("prueba C10-1 evidencia") as (inst, lock, owner):
        result = ws.run(inst, lock, owner)
        assert result.passed, summary(result)
        assert not database.ready(inst), "C10 leaves PostgreSQL stopped"
        assert write_block.verify_evidence(inst, result.evidence) == []
        repos = {c["repo"] for c in result.evidence["copies"]} if result.evidence else set()
        assert repos == {"repo1", "repo2"}
        assert {"wal_shutdown_started", "wal_shutdown_finished"} <= set(operation_steps(owner))
    back_to_normal()
    metrics["C10-1 evidencia verificada por C9-B"] = {k: v for k, v in (result.evidence or {}).items() if k != "copies"}


@contextmanager
def autovacuum_off() -> Iterator[None]:
    """``autovacuum = off`` (``postgresql.auto.conf``: no WAL) with no worker left, and back to the default after
    the shutdown and restart of the test."""
    superuser_sql("ALTER SYSTEM SET autovacuum = off")
    superuser_sql("SELECT pg_reload_conf()")
    try:
        wait_until(lambda: superuser_sql("SHOW autovacuum") == "off", 30, "autovacuum is still on")
        workers = "SELECT count(*) FROM pg_stat_activity WHERE backend_type = 'autovacuum worker'"
        wait_until(lambda: superuser_sql(workers) == "0", 300, "an autovacuum worker is still running")
        yield
    finally:
        if not database.ready(install()):
            cli("services", "start", check=False)
        superuser_sql("ALTER SYSTEM RESET autovacuum")
        superuser_sql("SELECT pg_reload_conf()")
        wait_until(lambda: superuser_sql("SHOW autovacuum") == "on", 30, "autovacuum is still off")


@pytest.mark.criterion("C10-8")
def test_the_allowed_list_is_exactly_what_postgresql_17_6_writes_after_the_cut_and_at_shutdown() -> None:
    assert superuser_sql("SHOW server_version").startswith("17.6"), superuser_sql("SHOW server_version")
    # 25 s after the cut: the background writer logs its snapshot (every 15 s after WAL activity), and then the
    # shutdown switches segment (archiving on, WAL since the cut) before its checkpoint.
    # Autovacuum is off for this test only: in the first CI run of C10 a worker vacuumed between the cut and the
    # shutdown (Heap2/PRUNE_VACUUM_*, Btree/VACUUM, XLOG/FPI_FOR_HINT...) and C10 failed, as it must (D3). That is
    # a finding for C7, not a reason to widen the list; here the test needs the baseline of PostgreSQL itself.
    with autovacuum_off():
        result = run("prueba C10-8", ws.Hooks(after_cut=lambda: time.sleep(25)))
    assert result.passed, summary(result)
    seen = {(r["rmgr"], r["type"]) for r in result.checks["wal_after_cut"]["records"]}
    assert seen == ws.ALLOWED, seen
    categories = result.checks["wal_after_cut"]["by_category"]
    assert [r["type"] for r in categories["shutdown"]][-2:] == ["SWITCH", "CHECKPOINT_SHUTDOWN"]
    assert [r["type"] for r in categories["control"]] == ["RUNNING_XACTS"] * len(categories["control"])
    back_to_normal()
    metrics["C10-8 registros observados en PostgreSQL 17.6"] = {k: [f"{r['rmgr']}/{r['type']}" for r in v]
                                                               for k, v in categories.items()}  # fmt: skip


# --- C10-2, C10-3, C10-9: WAL after the cut -----------------------------------------------------------------------


@pytest.mark.criterion("C10-2")
def test_a_superuser_write_after_the_cut_fails_and_is_named() -> None:
    result = run("prueba C10-2", ws.Hooks(after_cut=lambda: superuser_sql("INSERT INTO c10_late VALUES (2)")))
    assert result.verdict == "FAIL"
    # The first insert into an empty page is INSERT+INIT in PostgreSQL 17.
    assert heap_inserts(incompatible(result)), summary(result)
    assert ("Transaction", "COMMIT") in incompatible(result)
    assert any("Heap/INSERT" in p for p in result.checks["wal_after_cut"]["problems"])
    assert result.evidence is None
    assert (Path(result.diagnostics) / "wal-after-cut").is_dir(), "the WAL after the cut is kept to diagnose"
    back_to_normal()
    metrics["C10-2 escritura después del corte"] = summary(result)


@pytest.mark.criterion("C10-3")
def test_a_transaction_opened_before_the_cut_and_committed_after_it_fails() -> None:
    inst = install()
    late = subprocess.Popen(
        [str(inst.pg_bin("psql")), "-X", "-q", "-h", "127.0.0.1", "-p", str(inst.database[3]), "-U", "postgres",
         "-d", "postgres"],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True,
        env={**os.environ, "PGPASSWORD": database.superuser_password(inst), "PGAPPNAME": "c10_late"},
    )  # fmt: skip
    assert late.stdin is not None
    try:
        late.stdin.write("BEGIN;\nINSERT INTO c10_late VALUES (3);\n")
        late.stdin.flush()
        xid_sql = (
            "SELECT backend_xid FROM pg_stat_activity WHERE application_name = 'c10_late' AND backend_xid IS NOT NULL"
        )
        wait_until(lambda: superuser_sql(xid_sql) != "", 60, "the late transaction did not start")
        xid = int(superuser_sql(xid_sql))

        def commit() -> None:
            assert late.stdin is not None
            late.stdin.write("COMMIT;\n")
            late.stdin.close()
            late.wait(timeout=60)

        result = run("prueba C10-3", ws.Hooks(after_cut=commit))
    finally:
        if late.poll() is None:
            late.kill()
    assert result.verdict == "FAIL"
    found = incompatible(result)
    assert ("Transaction", "COMMIT") in found, summary(result)
    assert not heap_inserts(found), "the insert was before the cut"
    commits = [r for r in result.checks["wal_after_cut"]["by_category"]["incompatible"] if r["type"] == "COMMIT"]
    assert commits[0]["tx"] == xid
    assert superuser_sql("SELECT count(*) FROM c10_late WHERE n = 3") == "1"
    back_to_normal()
    metrics["C10-3 COMMIT tardío"] = {**summary(result), "xid": xid}


@pytest.mark.criterion("C10-9")
def test_a_read_after_the_cut_writes_fpi_for_hint_and_fails() -> None:
    result = run("prueba C10-9", ws.Hooks(after_cut=lambda: superuser_sql("SELECT count(*) FROM c10_fresh")))
    assert result.verdict == "FAIL", summary(result)
    found = incompatible(result)
    assert ("XLOG", "FPI_FOR_HINT") in found, summary(result)  # a read: hint bits, with data checksums
    assert not heap_inserts(found), "a read changed no data"
    assert ("Transaction", "COMMIT") not in found, "a read changed no data"
    back_to_normal()
    metrics["C10-9 lectura después del corte"] = summary(result)


# --- C10-4: not a clean shutdown -------------------------------------------------------------------------------


def kill_postgresql() -> None:
    pid = int((install().pg_data / "postmaster.pid").read_text("utf-8").splitlines()[0])
    subprocess.run(["taskkill.exe", "/T", "/F", "/PID", str(pid)], capture_output=True, check=False)  # noqa: S607
    wait_until(lambda: not database.ready(install()), 60, "PostgreSQL still answers after being killed")
    # The service notices that its server died and stops: only then can it be started again.
    winservice.wait_for(database.PG_SERVICE, {winservice.State.STOPPED}, 120)


@pytest.mark.criterion("C10-4")
def test_a_killed_server_fails() -> None:
    result = run("prueba C10-4 proceso cortado", ws.Hooks(stop=kill_postgresql))
    assert result.verdict == "FAIL"
    assert not result.checks["controldata"]["ok"], summary(result)
    assert result.checks["controldata"]["state"] != "shut down"
    assert not result.checks["wal_after_cut"]["ok"]
    back_to_normal()  # PostgreSQL recovered from the crash when it was started again
    metrics["C10-4 proceso cortado"] = summary(result)


# --- C10-5, C10-6: the repositories ----------------------------------------------------------------------------


def first_needed() -> str:
    """The first segment of the next fixed set: the start of the newest base backup."""
    return list_backups(install().repo1)[-1].start_wal


@pytest.mark.criterion("C10-5")
def test_a_truncated_cut_segment_in_repo1_fails_and_is_not_sent_to_repo2() -> None:
    kept: dict[str, bytes] = {}

    def truncate() -> None:
        wal = install().repo1 / "wal"
        newest = max(p for p in wal.iterdir() if len(p.name) == 24)
        kept[newest.name] = newest.read_bytes()
        newest.write_bytes(kept[newest.name][:4096])

    try:
        result = run("prueba C10-5 repo1 truncado", ws.Hooks(after_archive=truncate))
    finally:
        for name, data in kept.items():
            (install().repo1 / "wal" / name).write_bytes(data)
    (name,) = kept
    assert name == result.cut["cut_segment"]
    problems = result.checks["repo1"]["problems"]
    assert f"{name} truncated in repo1 (4096 bytes)" in problems, problems
    assert f"{name} in repo1 differs from the server's" in problems
    assert result.checks["repo2"]["pushed"] is False  # a copy that does not verify is never sent
    assert any("never sent" in p for p in result.checks["repo2"]["problems"])
    back_to_normal()
    metrics["C10-5 repo1 truncado"] = summary(result)


@pytest.mark.criterion("C10-5")
def test_a_missing_segment_in_repo1_fails_and_is_named() -> None:
    name = first_needed()
    path = install().repo1 / "wal" / name
    moved = LAB / "moved" / name
    moved.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(path, moved)
    try:
        result = run("prueba C10-5 repo1 faltante")
    finally:
        shutil.move(moved, path)
    assert f"{name} missing from repo1" in result.checks["repo1"]["problems"], summary(result)
    assert result.verdict == "FAIL"
    back_to_normal()
    metrics["C10-5 repo1 faltante"] = summary(result)


@pytest.mark.criterion("C10-5")
def test_a_different_or_missing_segment_in_repo2_fails_with_repo1_still_verified() -> None:
    crypt = remote()
    altered = LAB / "altered.bin"
    altered.parent.mkdir(parents=True, exist_ok=True)
    altered.write_bytes(b"\x00" * (16 * 1024 * 1024))
    names: dict[str, str] = {}

    def remember_cut() -> None:  # right after the archiver copied it, the cut segment is repo1's newest
        names["different"] = max(p.name for p in (install().repo1 / "wal").iterdir() if len(p.name) == 24)

    def overwrite_cut() -> None:
        assert crypt.run("copyto", str(altered), f"{REMOTE_NAME}:wal/{names['different']}").returncode == 0

    def delete_first() -> None:
        names["missing"] = first_needed()
        assert crypt.run("deletefile", f"{REMOTE_NAME}:wal/{names['missing']}").returncode == 0

    def put_back(key: str) -> None:
        name = names.get(key)
        if name:
            source = install().repo1 / "wal" / name
            assert crypt.run("copyto", str(source), f"{REMOTE_NAME}:wal/{name}").returncode == 0

    try:
        different = run("prueba C10-5 repo2 distinto", ws.Hooks(after_archive=remember_cut, after_push=overwrite_cut))
    finally:
        put_back("different")
    try:
        missing = run("prueba C10-5 repo2 faltante", ws.Hooks(after_push=delete_first))
    finally:
        put_back("missing")
    assert different.checks["repo1"]["ok"], "the results of each repository apart"
    assert missing.checks["repo1"]["ok"]
    assert names["different"] == different.cut["cut_segment"]
    assert f"{names['different']} in repo2 differs from the reference" in different.checks["repo2"]["problems"]
    assert (Path(different.diagnostics) / "repo2" / names["different"]).is_file(), "the mismatch is kept"
    assert f"{names['missing']} missing from repo2" in missing.checks["repo2"]["problems"], summary(missing)
    assert different.verdict == missing.verdict == "FAIL"
    back_to_normal()
    metrics["C10-5 repo2 distinto y faltante"] = {"distinto": summary(different), "faltante": summary(missing)}


@pytest.mark.criterion("C10-5")
def test_an_unreachable_repo2_fails_with_its_reason_and_c10_decides_nothing_more() -> None:
    config = ROOT / "config" / "tasks.json"
    original = config.read_text("utf-8")
    data = json.loads(original)
    data["offsite_remote"] = ":sftp,host=127.0.0.1,port=9,user=nobody:repo2"
    config.write_text(json.dumps(data), "utf-8")
    try:
        result = run("prueba C10-5 sin repo2")
    finally:
        config.write_text(original, "utf-8")
    assert result.checks["repo1"]["ok"], summary(result)
    assert not result.checks["repo2"]["ok"]
    assert any("push to repo2 failed" in p for p in result.checks["repo2"]["problems"])
    assert result.verdict == "FAIL"
    assert result.evidence is None  # D2: incomplete evidence, no degraded mode
    back_to_normal()
    metrics["C10-5 repo2 inalcanzable (D2)"] = summary(result)


@pytest.mark.criterion("C10-6")
def test_a_gap_since_the_oldest_base_backup_fails_and_is_named() -> None:
    backups = list_backups(install().repo1)
    assert len({b.start_wal for b in backups}) >= 2, [b.label for b in backups]
    name = backups[0].start_wal
    path = install().repo1 / "wal" / name
    moved = LAB / "moved" / name
    moved.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(path, moved)
    try:
        result = run("prueba C10-6 hueco")
    finally:
        shutil.move(moved, path)
    expected = f"{name} missing from repo1 (gap since the oldest base backup)"
    assert expected in result.checks["repo1"]["problems"], summary(result)
    back_to_normal()
    metrics["C10-6 hueco en repo1"] = summary(result)


# --- C10-6, C10-7, C10-4: a disposable cluster with two timelines -------------------------------------------------


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class Lab:
    def __init__(self, root: Path) -> None:
        self.root = root
        # initdb and pg_ctl, run by an administrator, drop to a restricted token (Administrators deny-only); the
        # installed runtime is readable only by its services (C3), so they could not load their DLLs from it. The
        # lab uses its own copy of the same PostgreSQL 17.6.
        home = install().artifact("postgresql").home(install().runtime)
        pg = LAB / "pg"
        if not pg.is_dir():
            shutil.copytree(home, pg)
        self.bin = pg / install().pg_bin("pg_ctl").parent.relative_to(home)
        self.repo = init_repo(root / "repo1")
        (root / "remote").mkdir()
        rclone = settings().rclone
        assert rclone is not None
        self.remote = CryptRemote(str(root / "remote"), LAB_KEY, str(rclone))
        self.instance = pgtools.Instance(root / "primary", free_port(), self.bin)

    def sql(self, text: str) -> str:
        psql = str(self.bin / "psql.exe")
        port = str(self.instance.port)
        return pgtools.run([psql, "-X", "-A", "-t", "-q", "-h", "127.0.0.1", "-p", port, "-U", "postgres", "-d",
                            "postgres", "-c", text]).strip()  # fmt: skip

    def target(self, name: str) -> ws.Target:
        return ws.Target(pg_bin=self.bin, data=self.instance.data_dir, port=self.instance.port, repo1=self.repo,
                         diagnostics=self.root / "diag" / name, stop=lambda: self.instance.stop("fast"),
                         remote=self.remote)  # fmt: skip


@pytest.fixture(scope="module")
def lab() -> Iterator[Lab]:
    """Timeline 1 with a base backup, a restore point and WAL lost after it; the restore to that point promoted
    as the new primary (timeline 2, ``00000002.history``), archiving into the same repository; a base backup on
    timeline 2. Fictitious data only."""
    shutil.rmtree(LAB / "timelines", ignore_errors=True)
    lab = Lab(LAB / "timelines")
    primary = lab.instance
    primary.initdb(extra=["--data-checksums"])
    primary.append_conf({"wal_level": "'replica'", "archive_mode": "'on'",
                         "archive_command": pgtools.conf_quote(archive_command(lab.repo))})  # fmt: skip
    primary.start()
    lab.sql("CREATE TABLE t (n int, note text)")
    lab.sql("INSERT INTO t SELECT g, md5(g::text) FROM generate_series(1, 20000) g")
    first = take_base_backup(lab.repo, port=primary.port, bin_dir=lab.bin)
    lab.sql("INSERT INTO t SELECT g, 'tl1' FROM generate_series(1, 5000) g")
    lab.sql("SELECT pg_create_restore_point('c10-lab')")
    lab.sql("INSERT INTO t SELECT g, 'lost' FROM generate_series(1, 5000) g")
    switched = lab.sql("SELECT pg_walfile_name(pg_switch_wal())")
    wait_until(lambda: (lab.repo / "wal" / switched).is_file(), 120, f"{switched} was not archived")
    primary.stop("fast")
    outcome = restore(lab.repo, first, lab.root / "timeline2", port=free_port(), target_name="c10-lab",
                      archive_into=lab.repo, bin_dir=lab.bin)  # fmt: skip
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail + "\n" + "\n".join(outcome.log_tail)
    lab.instance = outcome.instance
    lab.sql("INSERT INTO t SELECT g, 'tl2' FROM generate_series(1, 3000) g")
    lab.sql("SELECT pg_switch_wal()")
    wait_until(lambda: (lab.repo / "wal" / "00000002.history").is_file(), 60, "the history was not archived")
    take_base_backup(lab.repo, port=lab.instance.port, bin_dir=lab.bin)
    lab.sql("INSERT INTO t SELECT g, 'tl2b' FROM generate_series(1, 3000) g")
    yield lab
    if lab.instance.is_running():
        lab.instance.stop("immediate")


@pytest.mark.criterion("C10-7")
def test_a_legitimate_timeline_change_gives_no_false_fail(lab: Lab) -> None:
    result = ws.verify(lab.target("c10-7"), "manual-c10-7")
    assert result.passed, summary(result)
    assert result.cut["timeline"] == 2
    fixed = result.cut["fixed_set"]
    assert fixed["histories"] == ["00000002.history"]
    assert fixed["older_segments"] >= 1  # from the base backup of timeline 1, across the switch
    metrics["C10-7 cambio de timeline legítimo"] = {**summary(result), "conjunto fijo": fixed}


@pytest.mark.criterion("C10-6")
def test_a_missing_history_file_fails_in_repo1_and_in_repo2(lab: Lab) -> None:
    lab.instance.start()
    history = lab.repo / "wal" / "00000002.history"
    kept = history.read_bytes()
    history.unlink()
    try:
        in_repo1 = ws.verify(lab.target("c10-6-repo1"), "manual-c10-6a")
    finally:
        history.write_bytes(kept)
    assert "00000002.history missing from repo1" in in_repo1.checks["repo1"]["problems"], summary(in_repo1)
    lab.instance.start()

    def delete_history() -> None:
        assert lab.remote.run("deletefile", f"{REMOTE_NAME}:wal/00000002.history").returncode == 0

    in_repo2 = ws.verify(lab.target("c10-6-repo2"), "manual-c10-6b", ws.Hooks(after_push=delete_history))
    assert in_repo2.checks["repo1"]["ok"]
    assert "00000002.history missing from repo2" in in_repo2.checks["repo2"]["problems"], summary(in_repo2)
    metrics["C10-6 .history faltante"] = {"repo1": summary(in_repo1), "repo2": summary(in_repo2)}


@pytest.mark.criterion("C10-4")
def test_an_immediate_shutdown_fails(lab: Lab) -> None:
    lab.instance.start()
    result = ws.verify(lab.target("c10-4"), "manual-c10-4", ws.Hooks(stop=lambda: lab.instance.stop("immediate")))
    assert result.verdict == "FAIL"
    assert result.checks["controldata"]["state"] == "in production", summary(result)
    assert not result.checks["wal_after_cut"]["ok"]
    metrics["C10-4 apagado inmediato"] = summary(result)


# --- C10-10: preconditions ---------------------------------------------------------------------------------------


@pytest.mark.criterion("C10-10")
def test_c10_does_not_start_without_its_block_with_a_live_request_or_with_a_heavy_task() -> None:
    refused = []
    with blocked("prueba C10-10 sin bloqueo", block=False) as (inst, lock, owner):
        with pytest.raises(ws.WalShutdownError, match="write block of its own operation") as caught:
            ws.run(inst, lock, owner)
        refused.append(str(caught.value))
    with blocked("prueba C10-10 pedido y tarea") as (inst, lock, owner):
        request = tasklock._write_request(STATE, lock, "c10_other_operation", "prueba C10-10")
        try:
            with pytest.raises(ws.WalShutdownError, match="c10_other_operation asked") as caught:
                ws.run(inst, lock, owner)
            refused.append(str(caught.value))
        finally:
            request.clear(lock)
        task = tasklock.backup_lock(STATE)
        assert task.try_acquire()
        try:
            with pytest.raises(ws.WalShutdownError, match="heavy backup task is running") as caught:
                ws.run(inst, lock, owner)
            refused.append(str(caught.value))
        finally:
            task.release()
        assert database.ready(inst), "a refused C10 changed nothing: PostgreSQL still runs"
        assert not any(owner in p.name for p in (STATE / ws.DIAGNOSTICS).glob("*")), "a refused C10 wrote results"
        record = OperationsLog(STATE / "operations.jsonl").read().operations[owner]
        assert record.incomplete  # still the window's operation; C10 recorded no start
    back_to_normal()
    metrics["C10-10 rechazos"] = refused
