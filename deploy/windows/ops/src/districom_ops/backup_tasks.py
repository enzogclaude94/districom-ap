"""The backup tasks of W2-C6 and their runner: ``districom-ops tasks run <task> --settings <config\\tasks.json>``.

The runner uses ``districom_backup`` (W2-A) without changing its logic: the base backup, the WAL continuity
check, the retention and the off-site copy are its functions or its command line. What is added here is the
operation around them: the start check, the coordination with C9 (design AB, ``districom_ops.tasklock``), the
cancellation, the time limits and a persistent record of every run for C8.

**Start (C6-7).** With ``operations.lock`` taken for a moment (never for the work), a task starts only when the
application could start too (``safety.start_blockers``: no maintenance window, no incomplete or uncertain
operation, a readable record, a C9-B journal in ``none``), when no operation asked the tasks to stop, and, for a
heavy task, when no other heavy task holds ``backup.lock``. Otherwise it does not start and records ``refused``
with the reason. Then it lets ``operations.lock`` go and holds ``backup.lock`` while it works.

**Run records** (``state\\tasks``): ``<task>.json`` is the state of the last run that started (``running``,
``succeeded``, ``failed``, ``cancelled``, ``timeout`` or ``interrupted``) or of the last refusal when no run of
that task is alive; ``history.jsonl`` keeps every outcome. An interrupted run (Windows stopped, the process was
killed) is recognised by its next run: the record says ``running`` and nobody holds the task's lock. The record
never claims that a copy is recoverable: **created** (it exists, with its hash or manifest) and **verified**
(``pg_verifybackup`` passed, or the dump was restored and compared) are separate, and neither is a recovery test
of the whole system (C7, C13).

**Cancellation.** A heavy task checks twice a second whether a cancellation request exists or its time limit
passed. Its long steps run as child processes, which it ends together with their own children; then it removes
what it was writing (always a temporary name: nothing is published before it is verified) and only then frees
``backup.lock``. A copy published earlier is never touched. Retention is the exception: it checks the request
before each removal, but a removal itself is not interrupted (it takes seconds); an interrupted removal is
finished by the next retention from its intent record, so a half-removed backup never looks like a valid one.
"""

import contextlib
import json
import logging
import os
import re
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from districom_backup.base_backup import list_backups
from districom_backup.repo import TMP_PREFIX, RepoUnavailableError, base_dir, require_repo, wal_dir, write_atomic
from districom_backup.retention import apply_retention
from districom_backup.wal_gaps import find_gaps

from districom_ops import tasklock
from districom_ops.logs import REDACTOR
from districom_ops.maintenance import MaintenanceWindow
from districom_ops.oplock import LockBusy, OperationLock
from districom_ops.oplog import OperationsLog
from districom_ops.safety import start_blockers
from districom_ops.schedule import TASKS

log = logging.getLogger("districom_ops")
HISTORY = "history.jsonl"
RETENTION_INTENT = "retention-intent.json"
OFFSITE_STATE = "offsite-copy.json"  # never "<task>.json": that is the record of the task "offsite"
WORK = "work"
LOCK_SECONDS = 10


class TaskSettingsError(Exception):
    pass


@dataclass(frozen=True)
class TaskSettings:
    """``config\\tasks.json``, written by ``tasks register`` from ``districom.env``. No secret is in it: the tasks
    read their passwords from their own secret files, which C3 lets only them (and administrators) read."""

    root: Path
    repo: Path
    pg_bin: Path
    pg_port: int
    app_database: str
    backup_role: str
    python: Path
    rclone: Path | None
    offsite_remote: str | None
    keep_base: int
    keep_dumps: int
    restore_test_port: int
    maintenance_max_minutes: int

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def tasks(self) -> Path:
        return tasklock.tasks_dir(self.state)

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def backup_secret(self) -> Path:
        return self.config / "backup-db.secret"

    @property
    def dump_secret(self) -> Path:
        return self.config / "dump.secret"

    @property
    def offsite_secret(self) -> Path:
        return self.config / "offsite.secret"

    def log_dir(self, task: str) -> Path:
        return self.root / "logs" / "tasks" / task

    def to_json(self) -> str:
        data = {k: (str(v) if isinstance(v, Path) else v) for k, v in self.__dict__.items()}
        return json.dumps(data, indent=1, sort_keys=True)

    @classmethod
    def read(cls, path: Path) -> "TaskSettings":
        try:
            data = json.loads(path.read_text("utf-8"))
            return cls(
                root=Path(data["root"]),
                repo=Path(data["repo"]),
                pg_bin=Path(data["pg_bin"]),
                pg_port=int(data["pg_port"]),
                app_database=str(data["app_database"]),
                backup_role=str(data["backup_role"]),
                python=Path(data["python"]),
                rclone=Path(data["rclone"]) if data.get("rclone") else None,
                offsite_remote=data.get("offsite_remote") or None,
                keep_base=int(data["keep_base"]),
                keep_dumps=int(data["keep_dumps"]),
                restore_test_port=int(data["restore_test_port"]),
                maintenance_max_minutes=int(data["maintenance_max_minutes"]),
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise TaskSettingsError(f"task settings unreadable: {type(exc).__name__}") from exc


def read_secret(path: Path) -> str:
    try:
        value = path.read_text("utf-8").strip()
    except OSError as exc:
        raise TaskFailedError(f"cannot read {path.name}") from exc
    if len(value) < 16:
        raise TaskFailedError(f"{path.name} is too short")
    REDACTOR.register(value)
    return value


# --- outcomes ------------------------------------------------------------------------------------------------------


class TaskFailedError(Exception):
    """The work failed; the message names the step, never a value."""


class TaskStoppedError(Exception):
    """A cancellation request or the time limit stopped the work."""

    def __init__(self, status: str, reason: str) -> None:
        super().__init__(reason)
        self.status = status  # cancelled or timeout
        self.reason = reason


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _account() -> dict[str, str]:
    """Who the task runs as (C6-2, C6-3): the user SID (``LocalService``) and the task SID ``S-1-5-87-…`` that the
    Task Scheduler adds to its token. SIDs only: no name, no other personal data."""
    if sys.platform != "win32":
        return {"account": f"uid:{os.getuid()}", "task_sid": ""}
    try:
        result = subprocess.run(
            ["whoami.exe", "/user", "/groups", "/fo", "csv", "/nh"],  # noqa: S607 - a Windows system tool
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return {"account": "unknown", "task_sid": ""}
    found = re.findall(r"S-1-\d+(?:-\d+)+", result.stdout)
    task = next((sid for sid in found if sid.startswith("S-1-5-87-")), "")
    return {"account": found[0] if found else "unknown", "task_sid": task}


def record_path(settings: TaskSettings, task: str) -> Path:
    return settings.tasks / f"{task}.json"


def read_record(settings: TaskSettings, task: str) -> dict[str, Any] | None:
    try:
        data = json.loads(record_path(settings, task).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _append_history(settings: TaskSettings, entry: dict[str, Any]) -> None:
    settings.tasks.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(entry, sort_keys=True) + "\n").encode("utf-8")
    with (settings.tasks / HISTORY).open("ab") as handle:
        handle.write(line)
        handle.flush()
        os.fsync(handle.fileno())


def _write_record(settings: TaskSettings, task: str, entry: dict[str, Any], *, current: bool = True) -> None:
    """``current`` False: only the history (a refusal while another run of the same task is alive)."""
    if current:
        settings.tasks.mkdir(parents=True, exist_ok=True)
        write_atomic(record_path(settings, task), json.dumps(entry, sort_keys=True, indent=1).encode("utf-8"))
    if entry.get("status") != "running":
        _append_history(settings, entry)


# --- child processes that can be stopped --------------------------------------------------------------------------


def _kill_tree(process: "subprocess.Popen[bytes]") -> None:
    """End a child and every process it started (pg_basebackup, rclone…), then reap it."""
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(  # noqa: S603
            ["taskkill.exe", "/T", "/F", "/PID", str(process.pid)],  # noqa: S607
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    else:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=60)


@dataclass
class Context:
    settings: TaskSettings
    task: str
    run_id: str
    deadline: float
    work: Path
    detail: dict[str, Any] = field(default_factory=dict)

    def stop_reason(self) -> TaskStoppedError | None:
        request = tasklock.cancel_request(self.settings.state)
        if request is not None:
            return TaskStoppedError("cancelled", f"cancellation requested by {request.operation}: {request.reason}")
        if time.monotonic() >= self.deadline:
            limit = TASKS[self.task].time_limit_minutes
            return TaskStoppedError("timeout", f"time limit of {limit} min reached")
        return None

    def check(self) -> None:
        stop = self.stop_reason()
        if stop is not None:
            raise stop

    def run(
        self, args: list[str], *, env: dict[str, str] | None = None, step: str, poll: float = 0.5, tell: bool = False
    ) -> str:
        """Run one long step as a child process; stop it at once on a request or at the time limit. Its output
        goes to a file in the work folder (never to a pipe) and is only read here; it is not logged. ``tell``: a
        tool whose messages carry no data of the database (initdb, pg_ctl, createdb, rclone, offsite-push) gets
        its last line, redacted, in the failure reason; pg_dump, pg_restore and psql never do."""
        self.check()
        self.work.mkdir(parents=True, exist_ok=True)
        out = self.work / f"{step}.out"
        flags: dict[str, Any] = {}
        if sys.platform == "win32":
            flags["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            flags["start_new_session"] = True
        with out.open("wb") as handle:
            process = subprocess.Popen(  # noqa: S603
                args,
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                env={**os.environ, **(env or {}), "PGCLIENTENCODING": "UTF8", "LC_MESSAGES": "C"},
                **flags,
            )
            try:
                while process.poll() is None:
                    stop = self.stop_reason()
                    if stop is not None:
                        _kill_tree(process)
                        raise stop
                    time.sleep(poll)
            except BaseException:
                _kill_tree(process)
                raise
        text = out.read_text("utf-8", errors="replace")
        if process.returncode != 0:
            raise TaskFailedError(f"{step} exited {process.returncode}" + (_last_line(text) if tell else ""))
        return text


def _last_line(text: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return f": {REDACTOR.text(lines[-1])[:300]}" if lines else ""


# --- the runner ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Outcome:
    task: str
    status: str
    reason: str | None
    detail: dict[str, Any]


def _window(settings: TaskSettings) -> MaintenanceWindow:
    return MaintenanceWindow(settings.state, OperationsLog(settings.state / "operations.jsonl"))


def _start_refusal(settings: TaskSettings, task: str, heavy_lock: tasklock.FileLock | None) -> str | None:
    """With ``operations.lock`` held: why the task must not start (None: it may)."""
    reasons = list(start_blockers(_window(settings), settings.maintenance_max_minutes))
    request = tasklock.cancel_request(settings.state)
    if request is not None:
        reasons.append(f"operation {request.operation} asked the backup tasks to stop ({request.reason})")
    if reasons:
        return "; ".join(reasons)
    if heavy_lock is not None and not heavy_lock.try_acquire():
        return f"another heavy backup task is running: {tasklock.describe_running(settings.state)}"
    return None


def clean_work(settings: TaskSettings) -> list[str]:
    """Remove what an earlier heavy run was writing: its work folder and the temporary base backups (``.tmp-``),
    never a published copy and never anything in ``wal`` (PostgreSQL's archiver writes there at any time). Only
    called with ``backup.lock`` held, so no other heavy task is writing."""
    removed = []
    work = settings.tasks / WORK
    if work.exists():
        for item in work.iterdir():
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
            removed.append(f"work/{item.name}")
    with contextlib.suppress(RepoUnavailableError, OSError):
        for item in base_dir(settings.repo).glob(f"{TMP_PREFIX}*"):
            shutil.rmtree(item, ignore_errors=True) if item.is_dir() else item.unlink(missing_ok=True)
            removed.append(f"base/{item.name}")
    dumps = settings.repo / "dumps"
    if dumps.is_dir():
        for item in dumps.glob(f"{TMP_PREFIX}*"):
            item.unlink(missing_ok=True)
            removed.append(f"dumps/{item.name}")
    return removed


def run_task(settings: TaskSettings, task: str, work: Callable[[Context], None] | None = None) -> Outcome:
    """Run ``task`` once (from the Task Scheduler or by hand). Never raises for a refusal or a failure: both are
    recorded and returned. ``work`` replaces the task's work (tests)."""
    spec = TASKS[task]
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + secrets.token_hex(3)
    base: dict[str, Any] = {"task": task, "run_id": run_id, "heavy": spec.heavy, **_account(), "pid": os.getpid()}
    own = tasklock.task_lock(settings.state, task)
    if not own.try_acquire():
        same = "the same task is already running"
        _write_record(settings, task, {**base, "status": "refused", "at": _now(), "reason": same}, current=False)
        return Outcome(task, "refused", same, {})
    heavy = tasklock.backup_lock(settings.state) if spec.heavy else None
    try:
        previous = read_record(settings, task)
        if previous and previous.get("status") == "running":
            # Its lock was free: that run is not alive (Windows stopped, or its process was killed).
            interrupted = {**previous, "status": "interrupted", "finished_at": _now(),
                           "reason": f"found by run {run_id}: the run ended without recording its result"}  # fmt: skip
            _write_record(settings, task, interrupted)
        try:
            with OperationLock(settings.state, f"backup_task_{task}").acquire(timeout=LOCK_SECONDS):
                refusal = _start_refusal(settings, task, heavy)
                if refusal is None:
                    if heavy is not None:
                        write_atomic(settings.tasks / tasklock.RUNNING,
                                     json.dumps({**base, "started_at": _now()}).encode("utf-8"))  # fmt: skip
                    _write_record(settings, task, {**base, "status": "running", "started_at": _now()})
        except LockBusy as exc:
            refusal = str(exc)
        if refusal is not None:
            entry = {**base, "status": "refused", "at": _now(), "reason": refusal}
            _write_record(settings, task, entry)
            log.warning("backup_task_refused", extra={"task": task, "run": run_id, "reason": refusal})
            return Outcome(task, "refused", refusal, {})
        # operations.lock is free again: the work holds only backup.lock (heavy) and the task's own lock.
        return _work(settings, spec.name, base, run_id, work, heavy is not None)
    finally:
        if heavy is not None and heavy.held:
            running = tasklock.running_task(settings.state) or {}
            if running.get("run_id") == run_id:
                (settings.tasks / tasklock.RUNNING).unlink(missing_ok=True)
            heavy.release()
        own.release()


def _work(
    settings: TaskSettings,
    task: str,
    base: dict[str, Any],
    run_id: str,
    work: Callable[[Context], None] | None,
    heavy: bool,
) -> Outcome:
    started = _now()
    limit = TASKS[task].time_limit_minutes * 60
    ctx = Context(settings, task, run_id, time.monotonic() + limit, settings.tasks / WORK / run_id)
    status, reason = "succeeded", None
    log.info("backup_task_started", extra={"task": task, "run": run_id})
    try:
        if heavy:
            cleaned = clean_work(settings)
            if cleaned:
                ctx.detail["cleaned_from_earlier_runs"] = cleaned[:20]
        (work or WORK_FUNCTIONS[task])(ctx)
    except TaskStoppedError as exc:
        status, reason = exc.status, exc.reason
    except TaskFailedError as exc:
        status, reason = "failed", str(exc)
    except RepoUnavailableError:
        status, reason = "failed", "backup repository not available (backup disk absent or not prepared)"
    except Exception as exc:
        status, reason = "failed", f"{type(exc).__name__}"
    finally:
        if heavy and status != "succeeded":
            clean_work(settings)  # before backup.lock is freed: whoever sees it free sees no half-written copy
        elif ctx.work.exists():
            shutil.rmtree(ctx.work, ignore_errors=True)
    entry = {**base, "status": status, "reason": reason, "started_at": started, "finished_at": _now(),
             "detail": ctx.detail}  # fmt: skip
    _write_record(settings, task, entry)
    (log.info if status == "succeeded" else log.error)(
        "backup_task_finished", extra={"task": task, "run": run_id, "status": status, "reason": reason}
    )
    return Outcome(task, status, reason, ctx.detail)


# --- the work of each task ----------------------------------------------------------------------------------------


def _pg_env(settings: TaskSettings) -> dict[str, str]:
    return {
        "PGPASSWORD": read_secret(settings.backup_secret),
        "DISTRICOM_PG_BIN": str(settings.pg_bin),
        "PGCONNECT_TIMEOUT": "10",
        "PGAPPNAME": "districom_backup_task",
    }


def work_base_backup(ctx: Context) -> None:
    s = ctx.settings
    require_repo(s.repo)
    output = ctx.run(
        [str(s.python), "-m", "districom_backup", "base-backup", "--repo", str(s.repo), "--port", str(s.pg_port),
         "--user", s.backup_role],
        env=_pg_env(s),
        step="base_backup",
    )  # fmt: skip
    info = json.loads(output.strip().splitlines()[-1])
    ctx.detail.update(
        label=info["label"],
        start_wal=info["start_wal"],
        timeline=info["timeline"],
        created=True,
        verified="pg_verifybackup",  # before publishing; not a restore (C7)
    )


def work_wal_gaps(ctx: Context) -> None:
    s = ctx.settings
    backups = list_backups(s.repo)
    if not backups:
        raise TaskFailedError("no base backup in repo1 yet: WAL continuity cannot be checked")
    oldest = backups[0]
    report = find_gaps(wal_dir(s.repo), oldest.start_segment(), oldest.wal_segment_size)
    ctx.detail.update(from_backup=oldest.label, ok=report.ok, summary=report.summary()[:500])
    if not report.ok:
        raise TaskFailedError("WAL gaps found from the oldest base backup")


def _newest_wal(repo: Path) -> str | None:
    names = sorted(p.name for p in wal_dir(repo).iterdir() if len(p.name) == 24 and not p.name.startswith("."))
    return names[-1] if names else None


def work_offsite(ctx: Context) -> None:
    """``offsite-push`` of W2-A as a child process. Success records what reached repo2; a failure records the lag:
    what repo2 has (from the last success) and what is waiting. The database is never stopped (C6-5)."""
    s = ctx.settings
    require_repo(s.repo)
    state_path = s.tasks / OFFSITE_STATE
    previous: dict[str, Any] = {}
    with contextlib.suppress(OSError, ValueError):
        previous = json.loads(state_path.read_text("utf-8"))
    backups = list_backups(s.repo)
    waiting = {"newest_wal": _newest_wal(s.repo), "newest_base": backups[-1].label if backups else None}
    try:
        if not s.offsite_remote or s.rclone is None:
            raise TaskFailedError("off-site copy not configured (OFFSITE_REMOTE and rclone)")
        output = ctx.run(
            [str(s.python), "-m", "districom_backup", "offsite-push", "--remote", s.offsite_remote, "--rclone",
             str(s.rclone), "--repo", str(s.repo)],
            env={"DISTRICOM_OFFSITE_PASSWORD": read_secret(s.offsite_secret)},
            step="offsite_push",
            tell=True,
        )  # fmt: skip
    except (TaskFailedError, TaskStoppedError):
        last = previous.get("last_success_at")
        lag = None
        if last:
            lag = int((datetime.now(UTC) - datetime.fromisoformat(last)).total_seconds() // 60)
        ctx.detail.update(
            lag_minutes=lag,
            in_repo2=previous.get("pushed"),
            waiting=waiting,
            last_success_at=last,
        )
        write_atomic(state_path, json.dumps({**previous, "last_failure_at": _now(), "waiting": waiting}).encode())
        raise
    pushed = {**waiting, "manifest": output.strip().splitlines()[-1] if output.strip() else None}
    write_atomic(state_path, json.dumps({"last_success_at": _now(), "pushed": pushed}).encode("utf-8"))
    ctx.detail.update(pushed=pushed, copied=True, verified=False)  # a pull verifies it (C7)


def work_retention(ctx: Context) -> None:
    """Finish an interrupted removal first, then W2-A's retention, then the dumps'. The newest ``keep`` base
    backups (and every WAL they need) and the newest dumps, always including the newest verified one, stay."""
    from districom_ops import dump

    s = ctx.settings
    require_repo(s.repo)
    intent_path = s.tasks / RETENTION_INTENT
    finished = _finish_intent(s, intent_path)
    if finished:
        ctx.detail["finished_from_interrupted_run"] = finished
    ctx.check()
    backups = list_backups(s.repo)
    doomed = [b.label for b in backups[: max(len(backups) - s.keep_base, 0)]]
    write_atomic(intent_path, json.dumps({"labels": doomed, "at": _now()}).encode("utf-8"))
    report = apply_retention(s.repo, s.keep_base, bin_dir=s.pg_bin)
    intent_path.unlink(missing_ok=True)
    ctx.detail.update(kept=report.kept, removed_backups=report.removed_backups, removed_wal=len(report.removed_wal))
    removed_dumps = dump.apply_dump_retention(s.repo, s.keep_dumps, ctx.check)
    ctx.detail["removed_dumps"] = removed_dumps


def _finish_intent(settings: TaskSettings, intent_path: Path) -> list[str]:
    """Metadata first, then the data: a backup whose removal was interrupted is never listed as valid again."""
    try:
        labels = json.loads(intent_path.read_text("utf-8")).get("labels", [])
    except (OSError, ValueError):
        return []
    done = []
    for label in labels:
        if not isinstance(label, str) or "/" in label or "\\" in label or label.startswith("."):
            continue
        (base_dir(settings.repo) / f"{label}.json").unlink(missing_ok=True)
        shutil.rmtree(base_dir(settings.repo) / label, ignore_errors=True)
        done.append(label)
    intent_path.unlink(missing_ok=True)
    return done


def work_dump(ctx: Context) -> None:
    from districom_ops import dump

    dump.dump_and_verify(ctx)


WORK_FUNCTIONS: dict[str, Callable[[Context], None]] = {
    "base_backup": work_base_backup,
    "dump": work_dump,
    "offsite": work_offsite,
    "retention": work_retention,
    "wal_gaps": work_wal_gaps,
}


# --- status for operators and C8 ----------------------------------------------------------------------------------


def status(settings: TaskSettings) -> dict[str, Any]:
    request = tasklock.cancel_request(settings.state)
    report: dict[str, Any] = {
        "heavy_task_running": tasklock.heavy_task_running(settings.state),
        "running": tasklock.running_task(settings.state) if tasklock.heavy_task_running(settings.state) else None,
        "cancel_request": request.__dict__ if request else None,
        "tasks": {},
    }
    for name in TASKS:
        record = read_record(settings, name) or {}
        alive = not tasklock.lock_is_free(settings.tasks / f"{name}.lock")
        state = record.get("status")
        if state == "running" and not alive:
            state = "interrupted (not recorded yet)"
        report["tasks"][name] = {k: record.get(k) for k in ("status", "reason", "started_at", "finished_at", "at")}
        report["tasks"][name]["status"] = state
    return report
