"""W2-C6 acceptance: the backups of W2-A in operation, with the real services, the real Task Scheduler and
PostgreSQL 17, in order (each test needs the previous ones).

The job runs the W2-C2 setup tests first (runtimes, backup disk, database, services, migrations), as administrator
on a disposable runner, with fictitious secrets and data. ``repo2`` is a local folder (and, for C6-5, an address
that refuses connections): the real bucket stays PENDIENTE. Tasks are forced with ``schtasks /run``; their real
schedule over days stays PENDIENTE.
"""

import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from districom_backup.base_backup import list_backups

from backups_tests.conftest import metrics
from districom_ops import backup_tasks, database, dump, schedule, services, tasklock
from districom_ops.oplock import OperationLock
from services_tests.test_c2_services import CONFIG, CTX, ROOT, cli, superuser_sql, wait_until

OFFSITE = Path(os.environ.get("DISTRICOM_C6_OFFSITE", r"C:\c6-offsite"))
STATE = ROOT / "state"
TASKS_STATE = STATE / "tasks"
UNREACHABLE = ":sftp,host=127.0.0.1,port=9,user=nobody:repo2"
BULK_MB = int(os.environ.get("DISTRICOM_C6_BULK_MB", "900"))
CANCEL_WAIT = 90

pytestmark = pytest.mark.timeout(2400)


# --- helpers -----------------------------------------------------------------------------------------------------


def install() -> Any:
    return CTX.install()


def settings() -> backup_tasks.TaskSettings:
    return backup_tasks.TaskSettings.read(ROOT / "config" / "tasks.json")


def now() -> str:
    return datetime.now(UTC).isoformat()


def record(task: str) -> dict[str, Any]:
    return backup_tasks.read_record(settings(), task) or {}


def history(since: str, task: str | None = None) -> list[dict[str, Any]]:
    path = TASKS_STATE / "history.jsonl"
    found: list[dict[str, Any]] = []
    if not path.exists():
        return found
    for line in path.read_text("utf-8").splitlines():
        entry = json.loads(line)
        when = entry.get("finished_at") or entry.get("at") or ""
        if when >= since and (task is None or entry["task"] == task):
            found.append(entry)
    return found


def start(task: str) -> str:
    since = now()
    cli("tasks", "start", task)
    return since


def wait_result(task: str, since: str, seconds: float = 900) -> dict[str, Any]:
    """The first outcome of ``task`` recorded after ``since`` (a run that ended, or a refusal)."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        done = history(since, task)
        if done:
            return done[0]
        time.sleep(1)
    pytest.fail(f"{task} recorded no outcome in {seconds} s: {record(task)}")


def wait_running(task: str, seconds: float = 120) -> dict[str, Any]:
    def running() -> bool:
        current = tasklock.running_task(STATE)
        return bool(current and current.get("task") == task and tasklock.heavy_task_running(STATE))

    wait_until(running, seconds, f"{task} did not start")
    return tasklock.running_task(STATE) or {}


def app_sql(sql: str) -> str:
    inst = install()
    _, _, name, port = inst.database
    result = subprocess.run(
        [str(inst.pg_bin("psql")), "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p",
         str(port), "-U", "postgres", "-d", name, "-f", "-"],
        input=sql,
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": database.superuser_password(inst)},
        timeout=1800,
        check=True,
    )  # fmt: skip
    return result.stdout.strip()


def verify_backup(label: str) -> bool:
    inst = install()
    result = subprocess.run(
        [sys.executable, "-m", "districom_backup", "verify", "--repo", str(inst.repo1), label],
        capture_output=True,
        text=True,
        env={**os.environ, "DISTRICOM_PG_BIN": str(inst.pg_bin("pg_ctl").parent)},
        timeout=1800,
        check=False,
    )
    return result.returncode == 0


def labels() -> list[str]:
    return [b.label for b in list_backups(install().repo1)]


def temporaries() -> list[str]:
    repo = install().repo1
    found = [p.name for p in (repo / "base").glob(".tmp-*")]
    found += [p.name for p in (repo / "dumps").glob(".tmp-*")] if (repo / "dumps").exists() else []
    work = TASKS_STATE / "work"
    found += [p.name for p in work.iterdir()] if work.exists() else []
    return found


def set_env(key: str, value: str) -> None:
    lines = [line for line in CONFIG.read_text("utf-8").splitlines() if not line.startswith(f"{key}=")]
    CONFIG.write_text("\n".join([*lines, f"{key}={value}"]) + "\n", encoding="utf-8")


def schtasks(*args: str) -> str:
    result = subprocess.run(["schtasks.exe", *args], capture_output=True, text=True, timeout=120, check=False)  # noqa: S607
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def bulk(on: bool) -> None:
    """Fictitious data (dropped afterwards), so a base backup lasts long enough to be interrupted."""
    if on:
        app_sql(
            "CREATE TABLE IF NOT EXISTS c6_bulk AS SELECT g, repeat(md5(g::text), 30) AS filler "
            f"FROM generate_series(1, {BULK_MB * 1000}) g;"
        )
    else:
        app_sql("DROP TABLE IF EXISTS c6_bulk;")


# --- installation ---------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C6-2")
def test_tasks_install_is_idempotent_and_each_task_has_its_own_account_and_schedule() -> None:
    config = ROOT / "config"
    keys = {"dump.secret": secrets.token_urlsafe(36), "offsite.secret": secrets.token_urlsafe(36)}
    for name, value in keys.items():
        (config / name).write_text(value, encoding="utf-8")
    CTX.secrets += list(keys.values())
    (OFFSITE / "repo2").mkdir(parents=True, exist_ok=True)
    set_env("OFFSITE_REMOTE", str(OFFSITE / "repo2"))
    set_env("BACKUP_CANCEL_WAIT_SECONDS", str(CANCEL_WAIT))
    set_env("BACKUP_KEEP_BASE", "3")

    first = json.loads(cli("tasks", "install").stdout.strip().splitlines()[-1])["changes"]
    assert any("repo1 created" in c for c in first), first
    assert any(database.ARCHIVE_CONF in c for c in first), first
    assert sum(1 for c in first if c.endswith(": created")) == len(schedule.TASKS), first
    second = json.loads(cli("tasks", "install").stdout.strip().splitlines()[-1])["changes"]
    assert second == [], second

    # The off-site folder stands for the bucket: only the off-site task may write it (a bucket has its own access).
    subprocess.run(
        ["icacls.exe", str(OFFSITE), "/grant", f"*{schedule.TASKS['offsite'].sid}:(OI)(CI)M", "/T", "/Q"],  # noqa: S607
        check=True,
        capture_output=True,
    )
    for spec in schedule.TASKS.values():
        xml = schtasks("/query", "/tn", spec.task_name, "/xml")
        assert "<UserId>S-1-5-19</UserId>" in xml, spec.task_name  # LocalService: no password exists
        assert "<ProcessTokenSidType>Unrestricted</ProcessTokenSidType>" in xml
        assert f"T{spec.start}:00</StartBoundary>" in xml
        shown = schtasks("/showsid", "/tn", spec.task_name)
        sid = re.search(r"S-1-5-87(?:-\d+)+", shown)
        assert sid is not None, shown
        assert sid.group(0) == spec.sid, (spec.task_name, sid.group(0), spec.sid)
    metrics["tareas registradas"] = ", ".join(f"{s.task_name} {s.start}" for s in schedule.TASKS.values())


@pytest.mark.criterion("C6-1")
def test_postgresql_archives_every_segment_into_repo1() -> None:
    cli("services", "start")
    assert superuser_sql("SHOW archive_mode") == "on"
    repo_wal = install().repo1 / "wal"
    superuser_sql("CREATE TABLE IF NOT EXISTS c6_archive (n int)")
    names = []
    for n in range(3):
        superuser_sql(f"INSERT INTO c6_archive VALUES ({n})")
        name = superuser_sql("SELECT pg_walfile_name(pg_current_wal_lsn())")
        superuser_sql("SELECT pg_switch_wal()")
        wait_until(lambda name=name: (repo_wal / name).is_file(), 120, f"{name} did not reach repo1")
        names.append(name)
    stats = superuser_sql("SELECT failed_count || '|' || coalesce(last_archived_wal, '') FROM pg_stat_archiver")
    failed, last = stats.split("|")
    assert failed == "0", stats
    assert last >= names[-1]
    metrics["segmentos archivados en repo1 (C6-1)"] = ", ".join(names)


# --- forced runs -------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C6-3")
@pytest.mark.criterion("C6-6")
def test_forced_runs_succeed_with_their_account_and_record_their_state() -> None:
    marker = f"C6-MARKER-{secrets.token_hex(8)}"
    app_sql(f"CREATE TABLE IF NOT EXISTS c6_marker (note text); INSERT INTO c6_marker VALUES ('{marker}');")
    timings = {}
    for task in ("base_backup", "dump", "offsite", "retention"):
        since = start(task)
        outcome = wait_result(task, since)
        assert outcome["status"] == "succeeded", outcome
        assert outcome["account"] == schedule.LOCAL_SERVICE, outcome  # never an administrator nor SYSTEM
        assert outcome["task_sid"] == schedule.TASKS[task].sid, outcome  # its own task SID
        timings[task] = round(
            (datetime.fromisoformat(outcome["finished_at"]) - datetime.fromisoformat(outcome["started_at"]))
            .total_seconds(), 1)  # fmt: skip
    superuser_sql("SELECT pg_switch_wal()")
    time.sleep(5)
    since = start("wal_gaps")
    gaps = wait_result("wal_gaps", since)
    assert gaps["status"] == "succeeded", gaps
    metrics["duración de las corridas forzadas (s)"] = timings

    base = history("", "base_backup")[-1]["detail"]
    assert base["verified"] == "pg_verifybackup"
    assert verify_backup(base["label"])
    pushed = history("", "offsite")[-1]["detail"]
    assert pushed["copied"] is True
    assert pushed["verified"] is False  # a pull verifies it (C7)
    assert any((OFFSITE / "repo2").rglob("*")), "nothing reached repo2"

    # C6-6: encrypted with the dump key, restored by the task into a disposable instance.
    detail = history("", "dump")[-1]["detail"]
    assert detail["verified"] == "restored into a disposable instance", detail
    assert detail["restore_test"]["tables"] == detail["restore_test"]["listed_tables"] > 0
    inst = install()
    encrypted = inst.repo1 / "dumps" / f"{detail['label']}{dump.SUFFIX}"
    head = encrypted.read_bytes()[:8]
    assert head == b"RCLONE\x00\x00", head  # rclone crypt header, never the PGDMP of a plain dump
    rclone = inst.artifact("rclone").path(inst.runtime)
    work = OFFSITE / "check"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir()
    key = (ROOT / "config" / "dump.secret").read_text("utf-8").strip()
    wrong = subprocess.run(
        [str(rclone), "copyto", f"DISTRICOMOFFSITE:{detail['label']}.dump", str(work / "wrong.dump")],
        env=dump.crypt_env(inst.repo1 / "dumps", secrets.token_urlsafe(36), rclone),
        capture_output=True,
        text=True,
        check=False,
    )
    assert wrong.returncode != 0 or not (work / "wrong.dump").exists(), "another key opened the dump"
    subprocess.run(
        [str(rclone), "copyto", f"DISTRICOMOFFSITE:{detail['label']}.dump", str(work / "plain.dump")],
        env=dump.crypt_env(inst.repo1 / "dumps", key, rclone),
        capture_output=True,
        check=True,
    )
    assert (work / "plain.dump").read_bytes()[:5] == b"PGDMP"
    data = subprocess.run(
        [str(inst.pg_bin("pg_restore")), "--data-only", "-t", "c6_marker", "-f", "-", str(work / "plain.dump")],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert marker in data
    metrics["dump verificado (C6-6)"] = detail["restore_test"]


# --- no overlap, coordination ----------------------------------------------------------------------------------


@pytest.mark.criterion("C6-4")
@pytest.mark.criterion("C6-AB")
def test_heavy_tasks_never_overlap_and_maintenance_stops_one_safely() -> None:
    bulk(True)
    before = labels()
    assert before
    since = start("base_backup")
    wait_running("base_backup")
    wait_until(lambda: any(p.name.startswith(".tmp-") for p in (install().repo1 / "base").iterdir()), 120,
               "the base backup wrote nothing yet")  # fmt: skip
    # C6-4: another heavy task is refused while the base backup runs; the light check runs beside it.
    for other in ("dump", "retention"):
        refused = wait_result(other, start(other), 120)
        assert refused["status"] == "refused", refused
        assert "another heavy backup task is running" in refused["reason"]
    light = wait_result("wal_gaps", start("wal_gaps"), 300)
    assert light["status"] in ("succeeded", "failed"), light  # it ran (a WAL gap would be a failure, not overlap)

    # AB: maintenance asks the running task to stop, waits outside the lock and opens once it is confirmed.
    opened_at = time.monotonic()
    op = cli("maintenance", "open", "--kind", "manual", "--reason", "prueba C6").stdout.strip().splitlines()[-1]
    waited = round(time.monotonic() - opened_at, 1)
    outcome = wait_result("base_backup", since, 60)
    assert outcome["status"] == "cancelled", outcome
    assert "maintenance_manual" in outcome["reason"]
    assert waited < CANCEL_WAIT + 30
    assert not tasklock.heavy_task_running(STATE)
    assert tasklock.cancel_request(STATE) is None
    assert temporaries() == []
    assert labels() == before
    assert all(verify_backup(label) for label in before)
    metrics["espera de maintenance open hasta la confirmación (s)"] = waited

    # C6-7: with the window open no task starts, and the refusal is recorded.
    refused = wait_result("base_backup", start("base_backup"), 120)
    assert refused["status"] == "refused"
    assert "maintenance" in refused["reason"]
    cli("maintenance", "close", "--operation", op)


@pytest.mark.criterion("C6-7")
def test_a_task_does_not_start_with_a_busy_lock_an_incomplete_operation_or_a_write_block_journal() -> None:
    with OperationLock(STATE, "update").acquire():
        refused = wait_result("dump", start("dump"), 120)
    assert refused["status"] == "refused"
    assert "update by process" in refused["reason"]

    journal = STATE / "write-block.json"
    assert not journal.exists()
    journal.write_text(json.dumps({"state": "blocked", "owner": "c6-test", "token": "t", "step": 6}), "utf-8")
    try:
        refused = wait_result("dump", start("dump"), 120)
    finally:
        journal.unlink()
    assert refused["status"] == "refused"
    assert "write block" in refused["reason"].lower() or "journal" in refused["reason"].lower()

    op = cli("maintenance", "open", "--kind", "update", "--reason", "prueba C6-7").stdout.strip().splitlines()[-1]
    try:
        refused = wait_result("offsite", start("offsite"), 120)
    finally:
        cli("maintenance", "close", "--operation", op, "--reverted")
    assert refused["status"] == "refused"
    metrics["motivos de rechazo (C6-7)"] = "bloqueo ocupado, diario de C9-B, ventana de mantenimiento"


@pytest.mark.criterion("C6-I")
def test_a_killed_task_is_recognised_as_interrupted_and_leaves_no_half_copy() -> None:
    before = labels()
    since = start("base_backup")
    running = wait_running("base_backup")
    wait_until(lambda: any(p.name.startswith(".tmp-") for p in (install().repo1 / "base").iterdir()), 120,
               "the base backup wrote nothing yet")  # fmt: skip
    subprocess.run(["taskkill.exe", "/T", "/F", "/PID", str(running["pid"])], capture_output=True, check=False)  # noqa: S607
    wait_until(lambda: not tasklock.heavy_task_running(STATE), 60, "the killed task still holds backup.lock")
    assert record("base_backup")["status"] == "running"  # nothing could record the end
    bulk(False)
    again = wait_result("base_backup", start("base_backup"))
    assert again["status"] == "succeeded", again
    interrupted = [h for h in history(since, "base_backup") if h["status"] == "interrupted"]
    assert interrupted, history(since, "base_backup")
    assert interrupted[0]["run_id"] == running["run_id"]
    assert temporaries() == []
    assert set(before) <= set(labels())
    assert all(verify_backup(label) for label in labels())


@pytest.mark.criterion("C6-AB")
def test_services_stop_asks_a_running_task_to_stop_and_stops_in_any_case() -> None:
    bulk(True)
    before = labels()
    since = start("base_backup")
    wait_running("base_backup")
    started = time.monotonic()
    cli("services", "stop")
    metrics["services stop con una tarea pesada en curso (s)"] = round(time.monotonic() - started, 1)
    outcome = wait_result("base_backup", since, 120)
    assert outcome["status"] in ("cancelled", "failed"), outcome  # asked to stop, or PostgreSQL went away first
    assert not tasklock.heavy_task_running(STATE)
    assert tasklock.cancel_request(STATE) is None
    assert temporaries() == []
    assert labels() == before
    cli("services", "start")
    bulk(False)


@pytest.mark.criterion("C6-5")
def test_without_the_remote_the_database_keeps_going_and_the_lag_is_recorded() -> None:
    good = wait_result("offsite", start("offsite"))
    assert good["status"] == "succeeded", good
    set_env("OFFSITE_REMOTE", UNREACHABLE)
    cli("tasks", "install")
    try:
        since = start("offsite")
        superuser_sql("INSERT INTO c6_archive VALUES (100)")  # the database writes while the copy fails
        name = superuser_sql("SELECT pg_walfile_name(pg_current_wal_lsn())")
        superuser_sql("SELECT pg_switch_wal()")
        failed = wait_result("offsite", since)
        wait_until(lambda: (install().repo1 / "wal" / name).is_file(), 120, "archiving stopped without the remote")
    finally:
        set_env("OFFSITE_REMOTE", str(OFFSITE / "repo2"))
        cli("tasks", "install")
    assert failed["status"] == "failed", failed
    lag = failed["detail"]
    assert lag["last_success_at"] == json.loads((TASKS_STATE / "offsite.json").read_text())["last_success_at"]
    assert lag["lag_minutes"] is not None
    assert lag["waiting"]["newest_wal"]
    assert lag["in_repo2"]["newest_wal"]
    assert services.ready(install(), services.CADDY), "the application stopped while the copy failed"
    metrics["atraso registrado sin remoto (C6-5)"] = {k: lag[k] for k in ("lag_minutes", "waiting", "in_repo2")}
    recovered = wait_result("offsite", start("offsite"))
    assert recovered["status"] == "succeeded", recovered
