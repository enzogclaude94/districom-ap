"""Coordination between the backup tasks (W2-C6) and the operations of C9-A: design AB (Enzo, 2026-11-25).

* ``state\\tasks\\backup.lock``: the operating system's lock (like ``operations.lock``) that a **heavy** task holds
  for its whole run. Two heavy tasks never overlap (C6-4), and whoever asks whether one is running asks the
  operating system, never a file a dead process may have left: a process that dies frees it.
* ``state\\tasks\\<task>.lock``: the same, per task, so a task never overlaps itself and an interrupted run is
  recognised (its record says ``running`` but nobody holds its lock).
* A task takes ``operations.lock`` **only** for its start check and its registration, then lets it go (A of AB).
  It holds ``backup.lock`` while it works (B of AB). The operations lock is never held during a backup.
* An operation that must not run with a heavy task (maintenance, day close, update) first calls :func:`quiesce`:
  under ``operations.lock`` it writes a **persistent** cancellation request, then waits **outside** the lock, for
  a bounded time, until the operating system confirms that ``backup.lock`` is free. With ``operations.lock``
  taken again it checks once more (:func:`require_no_heavy_task`) and only then goes on; if the confirmation
  does not come, it refuses with the reason (fail safe). The request stays until the operation has opened its
  maintenance window (from then on the window keeps the tasks from starting) or has given up.
* Nobody waits for another process while holding ``operations.lock`` (the C9-A rule): the wait of
  :func:`quiesce` happens with no lock held, and a task tries ``backup.lock`` without waiting.
"""

import json
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from districom_backup.repo import write_atomic

from districom_ops.oplock import OperationLock, _try_lock, _unlock

TASKS_DIR = "tasks"
BACKUP_LOCK = "backup.lock"
RUNNING = "running.json"
CANCEL_REQUEST = "cancel-request.json"
DEFAULT_CANCEL_WAIT_SECONDS = 120
POLL_SECONDS = 0.5


class TaskLockError(Exception):
    pass


def tasks_dir(state_dir: Path) -> Path:
    return state_dir / TASKS_DIR


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


class FileLock:
    """An operating system lock on one file, never waited for: :meth:`try_acquire` answers at once."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._handle: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def try_acquire(self) -> bool:
        if self._handle is not None:
            raise RuntimeError("this lock object already holds the lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.path, "a+b")  # noqa: SIM115 - kept open while the lock is held
        if _try_lock(handle):
            self._handle = handle
            return True
        handle.close()
        return False

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            _unlock(handle)
        finally:
            handle.close()

    def __enter__(self) -> "FileLock":
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.release()


def lock_is_free(path: Path) -> bool:
    """Ask the operating system whether anybody holds the lock on ``path`` (taking and freeing it at once)."""
    if not path.exists():
        return True
    probe = FileLock(path)
    if probe.try_acquire():
        probe.release()
        return True
    return False


def backup_lock(state_dir: Path) -> FileLock:
    return FileLock(tasks_dir(state_dir) / BACKUP_LOCK)


def task_lock(state_dir: Path, task: str) -> FileLock:
    return FileLock(tasks_dir(state_dir) / f"{task}.lock")


def heavy_task_running(state_dir: Path) -> bool:
    return not lock_is_free(tasks_dir(state_dir) / BACKUP_LOCK)


def running_task(state_dir: Path) -> dict[str, Any] | None:
    """What the running heavy task recorded about itself (for messages only; decisions use the lock)."""
    return _read_json(tasks_dir(state_dir) / RUNNING)


def describe_running(state_dir: Path) -> str:
    record = running_task(state_dir) or {}
    return f"{record.get('task', 'unknown task')} (run {record.get('run_id', '?')}, since {record.get('started_at')})"


def require_no_heavy_task(state_dir: Path, operation: str) -> None:
    """The check an operation makes with ``operations.lock`` held, right before it goes on (fail safe)."""
    if heavy_task_running(state_dir):
        raise TaskLockError(
            f"{operation} refused: a heavy backup task is still running ({describe_running(state_dir)}); "
            "it did not confirm that it stopped"
        )


# --- the persistent cancellation request -------------------------------------------------------------------------


@dataclass(frozen=True)
class CancelRequest:
    request_id: str
    operation: str
    reason: str
    requested_at: str


def cancel_request(state_dir: Path) -> CancelRequest | None:
    """The pending request, if any. A file that exists but cannot be read counts as a request (fail safe)."""
    path = tasks_dir(state_dir) / CANCEL_REQUEST
    if not path.exists():
        return None
    data = _read_json(path) or {}
    return CancelRequest(
        request_id=str(data.get("request_id", "unreadable")),
        operation=str(data.get("operation", "unknown")),
        reason=str(data.get("reason", "unreadable cancellation request")),
        requested_at=str(data.get("requested_at", "")),
    )


def _write_request(state_dir: Path, lock: OperationLock, operation: str, reason: str) -> CancelRequest:
    if not lock.held:
        raise TaskLockError("a cancellation request is written with the operations lock held")
    request = CancelRequest(secrets.token_hex(8), operation, reason, _now())
    tasks_dir(state_dir).mkdir(parents=True, exist_ok=True)
    write_atomic(tasks_dir(state_dir) / CANCEL_REQUEST, json.dumps(request.__dict__).encode("utf-8"))
    return request


def clear_request(state_dir: Path, lock: OperationLock, request_id: str | None = None) -> bool:
    """Remove the request (only ``request_id``'s when given), with the operations lock held."""
    if not lock.held:
        raise TaskLockError("a cancellation request is cleared with the operations lock held")
    current = cancel_request(state_dir)
    if current is None or (request_id is not None and current.request_id != request_id):
        return False
    (tasks_dir(state_dir) / CANCEL_REQUEST).unlink(missing_ok=True)
    return True


@dataclass(frozen=True)
class Quiesce:
    request_id: str
    was_running: bool
    stopped: bool
    waited_seconds: float
    task: str | None

    def describe(self) -> str:
        if not self.was_running:
            return "no heavy backup task was running"
        if self.stopped:
            return f"heavy backup task {self.task} stopped after {self.waited_seconds:.1f} s"
        return f"heavy backup task {self.task} did not stop within {self.waited_seconds:.0f} s"


def quiesce(state_dir: Path, operation: str, reason: str, wait_seconds: float, *, lock_seconds: float = 10) -> Quiesce:
    """Ask the heavy backup tasks to stop and wait, bounded and **without** any lock held, until the operating
    system says none is running. The request stays: the caller clears it (:func:`clear_request`) once it has
    opened its window or given up. ``stopped`` False means the caller must refuse (it never goes on blindly)."""
    with OperationLock(state_dir, f"{operation}_stop_backup_tasks").acquire(timeout=lock_seconds) as lock:
        request = _write_request(state_dir, lock, operation, reason)
        running = heavy_task_running(state_dir)
        task = (running_task(state_dir) or {}).get("task") if running else None
    start = time.monotonic()
    if not running:
        return Quiesce(request.request_id, False, True, 0.0, None)
    deadline = start + wait_seconds
    while heavy_task_running(state_dir):
        if time.monotonic() >= deadline:
            return Quiesce(request.request_id, True, False, time.monotonic() - start, task)
        time.sleep(POLL_SECONDS)
    return Quiesce(request.request_id, True, True, time.monotonic() - start, task)


def owns_request(state_dir: Path, request_id: str) -> bool:
    current = cancel_request(state_dir)
    return current is not None and current.request_id == request_id
