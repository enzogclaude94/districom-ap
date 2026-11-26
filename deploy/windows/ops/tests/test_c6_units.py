"""W2-C6 units that run anywhere: the AB coordination with real processes, the task runner and its records, the
retentions, the Task Scheduler definitions and the C3 grants of the tasks.

The real tasks (Task Scheduler, PostgreSQL 17, rclone) run on Windows in ``backups_tests/`` (job «Windows backups
(W2-C6)»).
"""

import json
import os
import subprocess
import sys
import textwrap
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest
from districom_backup.repo import init_repo

from districom_ops import backup_tasks, dump, maintenance, ntfs, schedule, services, tasklock, write_block
from districom_ops.backup_tasks import Context, TaskFailedError, TaskSettings
from districom_ops.maintenance import MaintenanceError, MaintenanceWindow
from districom_ops.oplock import OperationLock
from districom_ops.oplog import Kind, OperationsLog
from districom_ops.winservice import service_sid
from tests.test_services_units import make_install

SRC = Path(__file__).resolve().parents[1] / "src"
BACKUP_SRC = Path(__file__).resolve().parents[2] / "backup" / "src"


def make_settings(tmp_path: Path) -> TaskSettings:
    root = tmp_path / "DistriCom"
    (root / "state").mkdir(parents=True)
    (root / "config").mkdir()
    repo = init_repo(tmp_path / "disk" / "repo1")
    return TaskSettings(
        root=root,
        repo=repo,
        pg_bin=tmp_path / "pg" / "bin",
        pg_port=5432,
        app_database="districom",
        backup_role="districom_backup",
        python=Path(sys.executable),
        rclone=None,
        offsite_remote=None,
        keep_base=2,
        keep_dumps=2,
        restore_test_port=55432,
        maintenance_max_minutes=60,
    )


@pytest.fixture
def settings(tmp_path: Path) -> TaskSettings:
    return make_settings(tmp_path)


def window(settings: TaskSettings) -> MaintenanceWindow:
    return MaintenanceWindow(settings.state, OperationsLog(settings.state / "operations.jsonl"))


def heavy_task_process(state: Path, honour_cancel: bool) -> subprocess.Popen[str]:
    """A separate process that holds ``backup.lock`` like a heavy task; it stops when asked, or never."""
    code = textwrap.dedent(
        f"""
        import time
        from pathlib import Path
        from districom_ops import tasklock
        state = Path({str(state)!r})
        lock = tasklock.backup_lock(state)
        assert lock.try_acquire()
        print("held", flush=True)
        for _ in range(1200):
            if {honour_cancel!r} and tasklock.cancel_request(state) is not None:
                break
            time.sleep(0.1)
        lock.release()
        """
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(BACKUP_SRC)])}
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "held"
    return process


# --- AB: operations and heavy tasks -----------------------------------------------------------------------------


@pytest.mark.criterion("C6-AB")
def test_with_no_heavy_task_maintenance_opens_at_once_and_the_request_is_cleared(settings: TaskSettings) -> None:
    op = maintenance.open_stopping_tasks(window(settings), Kind.MANUAL, "prueba", wait_seconds=30)
    assert window(settings).status(60).window is not None
    assert window(settings).status(60).window.operation_id == op  # type: ignore[union-attr]
    assert tasklock.cancel_request(settings.state) is None


@pytest.mark.criterion("C6-AB")
def test_a_heavy_task_that_stops_is_waited_for_outside_the_lock_then_maintenance_opens(settings: TaskSettings) -> None:
    process = heavy_task_process(settings.state, honour_cancel=True)
    try:
        start = time.monotonic()
        op = maintenance.open_stopping_tasks(window(settings), Kind.DAY_CLOSE, "cierre", wait_seconds=30)
        assert time.monotonic() - start < 30
        assert process.wait(timeout=30) == 0
        assert window(settings).status(60).window.operation_id == op  # type: ignore[union-attr]
        assert tasklock.cancel_request(settings.state) is None
        assert not tasklock.heavy_task_running(settings.state)
    finally:
        process.kill()


@pytest.mark.criterion("C6-AB")
def test_while_waiting_the_operations_lock_is_free_and_the_request_persists(settings: TaskSettings) -> None:
    """The wait happens without operations.lock (nobody waits holding it) and the request is on disk meanwhile:
    a task that tries to start in between is refused."""
    process = heavy_task_process(settings.state, honour_cancel=False)
    seen: dict[str, Any] = {}

    def during() -> None:
        time.sleep(1)
        with OperationLock(settings.state, "probe").acquire(timeout=1):
            seen["lock_free"] = True
        seen["request"] = tasklock.cancel_request(settings.state)
        seen["refusal"] = backup_tasks.run_task(settings, "dump", work=lambda ctx: None)

    thread = threading.Thread(target=during)
    try:
        thread.start()
        with pytest.raises(MaintenanceError, match="did not stop within"):
            maintenance.open_stopping_tasks(window(settings), Kind.UPDATE, "actualización", wait_seconds=3)
        thread.join()
    finally:
        process.kill()
        process.wait()
    assert seen["lock_free"] is True
    assert seen["request"] is not None
    assert seen["request"].operation == "maintenance_update"
    assert seen["refusal"].status == "refused"


@pytest.mark.criterion("C6-AB")
def test_without_the_confirmation_nothing_is_opened_and_the_request_is_cleared(settings: TaskSettings) -> None:
    process = heavy_task_process(settings.state, honour_cancel=False)
    try:
        with pytest.raises(MaintenanceError, match=r"did not stop within 2 s"):
            maintenance.open_stopping_tasks(window(settings), Kind.MANUAL, "prueba", wait_seconds=2)
        assert window(settings).status(60).window is None
        assert tasklock.cancel_request(settings.state) is None
        assert OperationsLog(settings.state / "operations.jsonl").read().operations == {}
        assert not (settings.state / "maintenance.json").exists()
    finally:
        process.kill()
        process.wait()


@pytest.mark.criterion("C6-AB")
def test_opening_a_window_by_any_path_is_refused_while_a_heavy_task_runs(settings: TaskSettings) -> None:
    """The check under the lock does not depend on the caller having waited (fail safe)."""
    process = heavy_task_process(settings.state, honour_cancel=False)
    try:
        with pytest.raises(MaintenanceError, match="heavy backup task is still running"):
            window(settings).open(Kind.MANUAL, "prueba")
        with OperationLock(settings.state, "day_close").acquire() as lock, pytest.raises(MaintenanceError):
            window(settings).open(Kind.DAY_CLOSE, "cierre", lock=lock)
    finally:
        process.kill()
        process.wait()
    assert window(settings).status(60).window is None


@pytest.mark.criterion("C6-AB")
def test_a_dead_heavy_task_frees_backup_lock(settings: TaskSettings) -> None:
    process = heavy_task_process(settings.state, honour_cancel=False)
    assert tasklock.heavy_task_running(settings.state)
    process.kill()
    process.wait()
    assert not tasklock.heavy_task_running(settings.state)


@pytest.mark.criterion("C6-AB")
def test_services_stop_asks_the_tasks_to_stop_then_stops_anyway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C9-A kept: stopping is always allowed. The task is asked first, with the bounded wait outside the lock."""
    install = make_install(tmp_path / "DistriCom")
    install.layout.state.mkdir(parents=True)
    process = heavy_task_process(install.layout.state, honour_cancel=False)
    stopped: list[bool] = []
    monkeypatch.setattr(services, "stop_all", lambda inst: stopped.append(True))
    try:
        services.stop_services(install, cancel_wait_seconds=1)
    finally:
        process.kill()
        process.wait()
    assert stopped == [True]
    assert tasklock.cancel_request(install.layout.state) is None


@pytest.mark.criterion("C6-AB")
def test_the_write_block_refuses_with_a_heavy_task_running_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = make_install(tmp_path / "DistriCom")
    state = install.layout.state
    state.mkdir(parents=True)
    monkeypatch.setattr(write_block.Database, "allows_connections", lambda self: True)
    calls: list[bool] = []
    monkeypatch.setattr(write_block.Database, "set_allow_connections", lambda self, v: calls.append(v))
    with OperationLock(state, "day_close").acquire() as lock:
        win = MaintenanceWindow(state, OperationsLog(state / "operations.jsonl"))
        op = win.open(Kind.DAY_CLOSE, "cierre", lock=lock)
        process = heavy_task_process(state, honour_cancel=False)  # cannot happen in operation; checked anyway
        try:
            with pytest.raises(write_block.WriteBlockError, match="heavy backup task is running"):
                write_block.block(install, lock, op)
        finally:
            process.kill()
            process.wait()
    assert calls == []
    assert write_block.read_journal(state).state is write_block.State.NONE


# --- the runner ---------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C6-R")
def test_a_task_holds_operations_lock_only_to_start_and_records_its_run(settings: TaskSettings) -> None:
    seen: dict[str, Any] = {}

    def work(ctx: Context) -> None:
        with OperationLock(settings.state, "probe").acquire():  # free while the task works
            seen["operations_lock_free"] = True
        seen["backup_lock_held"] = tasklock.heavy_task_running(settings.state)
        seen["running"] = tasklock.running_task(settings.state)
        ctx.detail["created"] = True

    outcome = backup_tasks.run_task(settings, "base_backup", work=work)
    assert outcome.status == "succeeded"
    assert seen["operations_lock_free"]
    assert seen["backup_lock_held"]
    assert seen["running"]["task"] == "base_backup"
    record = backup_tasks.read_record(settings, "base_backup")
    assert record is not None
    assert record["status"] == "succeeded"
    assert record["detail"] == {"created": True}
    assert not tasklock.heavy_task_running(settings.state)
    assert not (settings.tasks / tasklock.RUNNING).exists()
    history = [json.loads(line) for line in (settings.tasks / "history.jsonl").read_text().splitlines()]
    assert [h["status"] for h in history] == ["succeeded"]


@pytest.mark.criterion("C6-R")
@pytest.mark.parametrize("case", ["window", "journal", "unreadable_journal", "lock", "request", "heavy", "incomplete"])
def test_a_task_does_not_start_with_an_incompatible_state_and_records_why(settings: TaskSettings, case: str) -> None:
    state = settings.state
    holder = None
    other = None
    if case == "window":
        window(settings).open(Kind.MANUAL, "prueba")
    elif case == "journal":
        (state / "write-block.json").write_text(json.dumps({"state": "blocked", "owner": "x", "token": "t"}))
    elif case == "unreadable_journal":
        (state / "write-block.json").write_text("{")
    elif case == "lock":
        holder = OperationLock(state, "update").acquire()
    elif case == "request":
        with OperationLock(state, "x").acquire() as lock:
            standing = tasklock._write_request(state, lock, "maintenance_day_close", "cierre")  # owner alive
        assert standing.standing
    elif case == "heavy":
        other = tasklock.backup_lock(state)
        assert other.try_acquire()
    elif case == "incomplete":
        OperationsLog(state / "operations.jsonl").start(Kind.UPDATE, "actualización a medias")
    ran: list[bool] = []
    try:
        outcome = backup_tasks.run_task(settings, "dump", work=lambda ctx: ran.append(True))
    finally:
        if holder:
            holder.release()
        if other:
            other.release()
    assert outcome.status == "refused"
    assert outcome.reason
    assert ran == []
    record = backup_tasks.read_record(settings, "dump")
    assert record is not None
    assert record["status"] == "refused"
    assert record["reason"] == outcome.reason
    if case == "lock":
        assert "update by process" in outcome.reason


@pytest.mark.criterion("C6-R")
def test_the_light_check_runs_beside_a_heavy_task_but_not_with_an_open_window(settings: TaskSettings) -> None:
    other = tasklock.backup_lock(settings.state)
    assert other.try_acquire()
    try:
        assert backup_tasks.run_task(settings, "wal_gaps", work=lambda ctx: None).status == "succeeded"
        assert backup_tasks.run_task(settings, "retention", work=lambda ctx: None).status == "refused"
    finally:
        other.release()
    window(settings).open(Kind.MANUAL, "prueba")
    assert backup_tasks.run_task(settings, "wal_gaps", work=lambda ctx: None).status == "refused"


def _sleeper(seconds: float) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


@pytest.mark.criterion("C6-R")
def test_a_cancellation_request_stops_the_child_cleans_the_temporary_and_keeps_published_copies(
    settings: TaskSettings,
) -> None:
    published = settings.repo / "base" / "base-20260101T000000000000Z"
    published.mkdir()
    (published / "backup_manifest").write_text("valid")

    def work(ctx: Context) -> None:
        (settings.repo / "base" / ".tmp-base-new").mkdir()
        ctx.run(_sleeper(60), step="slow")

    owned: list[tasklock.OwnedRequest] = []

    def request() -> None:
        time.sleep(1.5)
        with OperationLock(settings.state, "maintenance").acquire() as lock:
            owned.append(tasklock._write_request(settings.state, lock, "maintenance_day_close", "cierre"))

    thread = threading.Thread(target=request)
    thread.start()
    start = time.monotonic()
    outcome = backup_tasks.run_task(settings, "base_backup", work=work)
    thread.join()
    assert outcome.status == "cancelled"
    assert "maintenance_day_close" in (outcome.reason or "")
    assert time.monotonic() - start < 20
    assert not (settings.repo / "base" / ".tmp-base-new").exists()
    assert (published / "backup_manifest").read_text() == "valid"
    assert not tasklock.heavy_task_running(settings.state)


@pytest.mark.criterion("C6-R")
def test_the_time_limit_stops_a_task_and_records_timeout(settings: TaskSettings) -> None:
    def work(ctx: Context) -> None:
        ctx.deadline = time.monotonic() + 1
        ctx.run(_sleeper(60), step="slow")

    outcome = backup_tasks.run_task(settings, "dump", work=work)
    assert outcome.status == "timeout"
    assert backup_tasks.read_record(settings, "dump")["status"] == "timeout"  # type: ignore[index]


@pytest.mark.criterion("C6-R")
def test_a_failed_child_is_a_failed_run_named_by_its_step(settings: TaskSettings) -> None:
    def work(ctx: Context) -> None:
        ctx.run([sys.executable, "-c", "raise SystemExit(3)"], step="pg_dump")

    outcome = backup_tasks.run_task(settings, "dump", work=work)
    assert outcome.status == "failed"
    assert outcome.reason == "pg_dump exited 3"  # the output of pg_dump may carry data: never in the reason


@pytest.mark.criterion("C6-R")
def test_a_failed_tool_without_data_tells_its_last_line_redacted(settings: TaskSettings) -> None:
    from districom_ops.logs import REDACTOR

    secret = "s3cr3t-" + "x" * 24
    REDACTOR.register(secret)

    def work(ctx: Context) -> None:
        code = f"print('first'); print('initdb: error: cannot use {secret} here'); raise SystemExit(1)"
        ctx.run([sys.executable, "-c", code], step="initdb", tell=True)

    outcome = backup_tasks.run_task(settings, "dump", work=work)
    assert outcome.status == "failed"
    assert outcome.reason is not None
    assert outcome.reason.startswith("initdb exited 1: initdb: error: cannot use ")
    assert secret not in outcome.reason


@pytest.mark.criterion("C6-R")
def test_no_task_record_overwrites_the_off_site_state(settings: TaskSettings) -> None:
    """The record of the task ``offsite`` once was ``offsite.json``, the same file as the copy state (CI, W2-C6)."""
    shared = {backup_tasks.OFFSITE_STATE, backup_tasks.RETENTION_INTENT}
    records = {backup_tasks.record_path(settings, task).name for task in schedule.TASKS}
    assert not shared & records


@pytest.mark.criterion("C6-R")
def test_an_interrupted_run_is_recognised_and_its_temporaries_removed(settings: TaskSettings) -> None:
    settings.tasks.mkdir(parents=True)
    record = {"task": "base_backup", "run_id": "old", "status": "running", "started_at": "2026-11-25T15:00:00+00:00"}
    backup_tasks.record_path(settings, "base_backup").write_text(json.dumps(record))
    (settings.repo / "base" / ".tmp-base-old").mkdir()
    (settings.tasks / "work" / "old").mkdir(parents=True)
    outcome = backup_tasks.run_task(settings, "base_backup", work=lambda ctx: None)
    assert outcome.status == "succeeded"
    history = [json.loads(line) for line in (settings.tasks / "history.jsonl").read_text().splitlines()]
    assert [h["status"] for h in history] == ["interrupted", "succeeded"]
    assert history[0]["run_id"] == "old"
    assert not (settings.repo / "base" / ".tmp-base-old").exists()
    assert not (settings.tasks / "work" / "old").exists()


@pytest.mark.criterion("C6-R")
def test_a_second_run_of_the_same_task_is_refused_without_overwriting_the_live_record(settings: TaskSettings) -> None:
    result: dict[str, Any] = {}

    def work(ctx: Context) -> None:
        result["second"] = backup_tasks.run_task(settings, "wal_gaps", work=lambda c: None)
        result["record"] = backup_tasks.read_record(settings, "wal_gaps")

    backup_tasks.run_task(settings, "wal_gaps", work=work)
    assert result["second"].status == "refused"
    assert result["record"]["status"] == "running"


@pytest.mark.criterion("C6-R")
def test_retention_finishes_an_interrupted_removal_metadata_first(settings: TaskSettings) -> None:
    base = settings.repo / "base"
    (base / "base-old").mkdir()
    (base / "base-old" / "half").write_text("x")  # a removal cut in the middle: data partly gone, metadata there
    (base / "base-old.json").write_text("{}")
    settings.tasks.mkdir(parents=True)
    intent = settings.tasks / backup_tasks.RETENTION_INTENT
    intent.write_text(json.dumps({"labels": ["base-old", "../evil"]}))
    done = backup_tasks._finish_intent(settings, intent)
    assert done == ["base-old"]
    assert not (base / "base-old.json").exists()
    assert not (base / "base-old").exists()
    assert not intent.exists()


@pytest.mark.criterion("C6-R")
def test_dump_retention_keeps_the_newest_and_the_newest_verified(settings: TaskSettings) -> None:
    folder = settings.repo / "dumps"
    folder.mkdir()
    for day, verified in (("01", True), ("02", False), ("03", False), ("04", False)):
        label = f"dump-202611{day}T130000Z"
        (folder / f"{label}{dump.SUFFIX}").write_bytes(b"enc")
        (folder / f"{label}.json").write_text(json.dumps({"label": label, "verified": verified}))
    (folder / f"dump-20261105T130000Z{dump.SUFFIX}").write_bytes(b"orphan without metadata")
    removed = dump.apply_dump_retention(settings.repo, 2, lambda: None)
    left = sorted(d["label"] for d in dump.list_dumps(settings.repo))
    assert left == ["dump-20261101T130000Z", "dump-20261103T130000Z", "dump-20261104T130000Z"]
    assert "dump-20261102T130000Z" in removed
    assert not (folder / f"dump-20261105T130000Z{dump.SUFFIX}").exists()


@pytest.mark.criterion("C6-R")
def test_a_missing_or_weak_secret_fails_the_task_without_showing_it(settings: TaskSettings) -> None:
    settings.backup_secret.write_text("short")
    with pytest.raises(TaskFailedError, match="too short") as info:
        backup_tasks.read_secret(settings.backup_secret)
    assert "short" not in str(info.value).replace("too short", "")
    settings.backup_secret.unlink()
    with pytest.raises(TaskFailedError, match=r"cannot read backup-db\.secret"):
        backup_tasks.read_secret(settings.backup_secret)


@pytest.mark.criterion("C6-R")
def test_status_reports_tasks_requests_and_unrecorded_interruptions(settings: TaskSettings) -> None:
    settings.tasks.mkdir(parents=True)
    backup_tasks.record_path(settings, "dump").write_text(json.dumps({"status": "running"}))
    report = backup_tasks.status(settings)
    assert report["tasks"]["dump"]["status"] == "interrupted (not recorded yet)"
    assert report["heavy_task_running"] is False
    assert report["cancel_request"] is None


@pytest.mark.criterion("C6-R")
def test_a_task_refused_by_a_busy_operations_lock_names_the_owner(settings: TaskSettings) -> None:
    with OperationLock(settings.state, "services_start").acquire():
        outcome = backup_tasks.run_task(settings, "wal_gaps", work=lambda ctx: None)
    assert outcome.status == "refused"
    assert "services_start by process" in (outcome.reason or "")


# --- Task Scheduler definitions, accounts and permissions --------------------------------------------------------


@pytest.mark.criterion("C6-U")
def test_task_definitions_run_as_their_own_task_sid_without_password_and_with_limits() -> None:
    ns = {"t": schedule.SCHEMA}
    for spec in schedule.TASKS.values():
        text = schedule.render(spec, Path(r"C:\DistriCom\py.exe"), Path(r"C:\DistriCom\config\tasks.json"), Path("C:/"))
        tree = ET.fromstring(text.split("\n", 1)[1])  # noqa: S314 - our own rendering, not untrusted input
        principal = tree.find("t:Principals/t:Principal", ns)
        assert principal is not None
        assert principal.findtext("t:UserId", namespaces=ns) == schedule.LOCAL_SERVICE
        assert principal.findtext("t:ProcessTokenSidType", namespaces=ns) == "Unrestricted"
        assert principal.findtext("t:RunLevel", namespaces=ns) == "LeastPrivilege"
        # schtasks refuses LogonType ServiceAccount, "out of range" (CI, W2-C6): the UserId alone says it
        assert principal.find("t:LogonType", ns) is None
        assert tree.findtext("t:Settings/t:MultipleInstancesPolicy", namespaces=ns) == "IgnoreNew"
        assert tree.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=ns) == f"PT{spec.time_limit_minutes + 10}M"
        arguments = tree.findtext("t:Actions/t:Exec/t:Arguments", namespaces=ns) or ""
        assert f"tasks run {spec.name} --settings" in arguments
        assert "password" not in text.lower()
        assert spec.sid.startswith("S-1-5-87-")
    base = schedule.TASKS["base_backup"]
    # inside the window from 15:00 to 17:00, never run late out of it
    assert base.start == "15:00"
    assert base.time_limit_minutes == 120
    assert not base.run_late
    assert set(schedule.HEAVY) == {"base_backup", "dump", "offsite", "retention"}


@pytest.mark.criterion("C6-U")
def test_registration_is_idempotent(tmp_path: Path) -> None:
    calls: list[list[str]] = []
    registered: set[str] = set()

    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "/create":
            registered.add(args[2])
        code = 0 if args[0] != "/query" or args[2] in registered else 1
        return subprocess.CompletedProcess(args, code, "", "")

    python, settings = Path(r"C:\py.exe"), Path(r"C:\tasks.json")
    first = schedule.register(tmp_path, python, settings, tmp_path, run=run)
    assert len(first) == len(schedule.TASKS)
    assert all(c.endswith("created") for c in first)
    calls.clear()
    assert schedule.register(tmp_path, python, settings, tmp_path, run=run) == []
    assert all(c[0] == "/query" for c in calls)
    changed = schedule.register(tmp_path, Path(r"C:\other.exe"), settings, tmp_path, run=run)
    assert all(c.endswith("updated") for c in changed)


@pytest.mark.criterion("C6-U")
def test_the_task_accounts_get_only_what_they_need(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom")
    without = services.permission_policy(install)
    install.tasks_settings.parent.mkdir(parents=True)
    install.tasks_settings.write_text("{}")
    policy = services.permission_policy(install)
    sids = {n: s.sid for n, s in schedule.TASKS.items()}
    pg = service_sid(services.PG)

    def grants(path: Path) -> dict[str, int]:
        rule = policy.rule_for(path)
        return dict(rule.grants) if rule else {}

    config = install.root / "config"
    assert set(grants(config / "dump.secret")) == {sids["dump"]}
    assert set(grants(config / "offsite.secret")) == {sids["offsite"]}
    assert set(grants(install.backup_secret)) == {sids["base_backup"], sids["dump"]}
    assert set(grants(install.tasks_settings)) == set(sids.values())
    assert all(m == ntfs.READ for m in grants(config / "dump.secret").values())
    assert grants(config / "districom.env") == {service_sid(services.STACK): ntfs.READ}  # no task reads it
    assert sids["wal_gaps"] not in grants(config / "dump.secret")
    state = grants(install.root / "state")
    assert all(state[s] == ntfs.MODIFY for s in sids.values())
    paths = [r.path for r in policy.rules]
    assert len(paths) == len(set(paths)), "one rule per path"
    assert {r.path for r in without.rules} <= set(paths)
    assert not any(sid.startswith("S-1-5-87-") for r in without.rules for sid, _ in r.grants)
    release_windows = grants(install.release_dir() / "windows")
    assert pg in release_windows
    assert all(s in release_windows for s in sids.values())
    assert permissions_have_no_broad_principal(policy)


def permissions_have_no_broad_principal(policy: Any) -> bool:
    from districom_ops.permissions import BROAD

    return not any(sid in BROAD for r in policy.rules for sid, _ in r.grants)


@pytest.mark.criterion("C6-U")
def test_the_archive_settings_use_the_w2a_commands_and_the_release_python(tmp_path: Path) -> None:
    from districom_ops import backups

    install = make_install(tmp_path / "DistriCom")
    text = backups.archive_settings(install)
    assert "archive_mode = on" in text
    assert "archive_timeout = 300" in text
    assert "-m districom_backup archive-wal --repo" in text
    assert "%p %f" in text
    assert "-m districom_backup restore-wal --repo" in text
    assert "%f %p" in text
    assert install.ops_python.as_posix() in text
    assert install.repo1.as_posix().split(":")[-1] in text


@pytest.mark.criterion("C6-U")
def test_the_backup_role_is_neither_superuser_nor_owner(tmp_path: Path) -> None:
    from districom_ops import database

    install = make_install(tmp_path / "DistriCom")
    sql = database.init_sql(install, "backup-password-of-the-test")
    line = next(x for x in sql.splitlines() if x.startswith(f'CREATE ROLE "{database.BACKUP_ROLE}"'))
    assert "LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE" in line
    assert f'GRANT pg_read_all_data TO "{database.BACKUP_ROLE}";' in sql
    assert f'OWNER "{database.BACKUP_ROLE}"' not in sql
    assert "include_if_exists = 'districom-archive.conf'" in database.settings(install)


@pytest.mark.criterion("C6-U")
def test_the_tasks_configuration_is_validated_without_showing_values() -> None:
    from districom_ops.config import ConfigError, load_tasks_config

    good = load_tasks_config({"OFFSITE_REMOTE": "r2:bucket/repo2", "BACKUP_KEEP_BASE": "5"}, 5432)
    assert good.offsite_remote == "r2:bucket/repo2"
    assert good.keep_base == 5
    assert good.keep_dumps == 14
    with pytest.raises(ConfigError) as info:
        load_tasks_config({"OFFSITE_REMOTE": "x y'", "BACKUP_KEEP_BASE": "0", "RESTORE_TEST_PORT": "5432"}, 5432)
    text = str(info.value)
    assert "OFFSITE_REMOTE" in text
    assert "BACKUP_KEEP_BASE" in text
    assert "RESTORE_TEST_PORT" in text
    assert "x y'" not in text
