"""Coordination between the backup tasks (W2-C6) and the operations of C9-A: design AB (Enzo, 2026-11-25).

* ``state\\tasks\\backup.lock``: the operating system's lock (like ``operations.lock``) that a **heavy** task holds
  for its whole run. Two heavy tasks never overlap (C6-4), and whoever asks whether one is running asks the
  operating system, never a file a dead process may have left: a process that dies frees it.
* ``state\\tasks\\<task>.lock``: the same, per task, so a task never overlaps itself and an interrupted run is
  recognised (its record says ``running`` but nobody holds its lock).
* A task takes ``operations.lock`` **only** for its start check and its registration, then lets it go (A of AB).
  It holds ``backup.lock`` while it works (B of AB). The operations lock is never held during a backup.
* An operation that must not run with a heavy task (maintenance, day close, update) first calls :func:`quiesce`: under
  ``operations.lock`` it writes a **persistent** cancellation request of its own (owned: see below), then waits
  **outside** the lock, for a bounded time, until the operating system confirms that ``backup.lock`` is free. With
  ``operations.lock`` taken again it checks once more (:func:`require_no_heavy_task`) and only then goes on; if the
  confirmation does not come, it refuses with the reason (fail safe). The request stands until the operation that
  wrote it has opened its maintenance window (from then on the window keeps the tasks from starting) or has given up;
  only that operation can clear it, and a requester that dies leaves an orphan that is reconciled.
* Nobody waits for another process while holding ``operations.lock`` (the C9-A rule): the wait of
  :func:`quiesce` happens with no lock held, and a task tries ``backup.lock`` without waiting.
"""

import contextlib
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
REQUESTS_DIR = "cancel-requests"
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


# --- the persistent cancellation requests, each owned by its requester -----------------------------------------
#
# Every request is its own pair of files in ``state\\tasks\\cancel-requests\\``: ``<id>.owner``, an operating system
# lock that the requesting process holds for as long as the request stands, and ``<id>.json``, what it asks. The
# lock is taken **before** the description is written, and both are written with ``operations.lock`` held, so:
#
# * two requests never replace each other (one file each): every obligation to stop stays until its own owner
#   clears it, and a task sees all of them;
# * only the owner can clear its request (:meth:`OwnedRequest.clear`); nobody else has a call that removes a live
#   request, so no operation can clear another's by accident;
# * a requester that dies frees its lock: its request becomes an **orphan**, which the operating system, not a
#   file, says. Orphans are removed (:func:`reconcile`) with ``operations.lock`` held, at the start of every task,
#   when a maintenance window closes and by ``tasks clear-cancel``; until then they stop nobody waiting for
#   nothing, but they are reported.
# * A request is **valid** while its owner holds the lock. A request file that cannot be read, a folder that cannot
#   be listed or an owner lock that cannot be probed counts as valid (fail safe): tasks do not start.


@dataclass(frozen=True)
class CancelRequest:
    request_id: str
    operation: str
    reason: str
    requested_at: str
    live: bool = True


def requests_dir(state_dir: Path) -> Path:
    return tasks_dir(state_dir) / REQUESTS_DIR


def _owner_path(state_dir: Path, request_id: str) -> Path:
    return requests_dir(state_dir) / f"{request_id}.owner"


def _owner_alive(path: Path) -> bool:
    try:
        return not lock_is_free(path) if path.exists() else False
    except OSError:
        return True  # cannot tell: fail safe


def cancel_requests(state_dir: Path) -> list[CancelRequest]:
    """Every request, valid (``live``) or orphan, oldest first."""
    folder = requests_dir(state_dir)
    try:
        names = sorted(p.name for p in folder.iterdir()) if folder.exists() else []
    except OSError:
        return [CancelRequest("unreadable", "unknown", "the cancellation requests cannot be listed", "", True)]
    found = []
    for name in names:
        if name.startswith(".") or not name.endswith(".json"):
            continue
        request_id = name[: -len(".json")]
        data = _read_json(folder / name) or {}
        found.append(
            CancelRequest(
                request_id=request_id,
                operation=str(data.get("operation", "unknown")),
                reason=str(data.get("reason", "unreadable cancellation request")),
                requested_at=str(data.get("requested_at", "")),
                live=_owner_alive(_owner_path(state_dir, request_id)),
            )
        )
    return sorted(found, key=lambda r: (r.requested_at, r.request_id))


def cancel_request(state_dir: Path) -> CancelRequest | None:
    """The oldest **valid** request, if any: what stops a running task and keeps new ones from starting."""
    live = [r for r in cancel_requests(state_dir) if r.live]
    return live[0] if live else None


class OwnedRequest:
    """A request written by this process. It stands while this object holds its lock; only it can clear it."""

    def __init__(self, state_dir: Path, request: CancelRequest, owner: FileLock) -> None:
        self.state_dir = state_dir
        self.request = request
        self._owner = owner

    @property
    def request_id(self) -> str:
        return self.request.request_id

    @property
    def standing(self) -> bool:
        return self._owner.held

    def clear(self, lock: OperationLock) -> bool:
        """Remove this request (and only this one), with the operations lock held."""
        if not lock.held:
            raise TaskLockError("a cancellation request is cleared with the operations lock held")
        if not self._owner.held:
            return False
        # A reader may hold the description open for a moment (Windows refuses the removal then): with the owner
        # lock released it is an orphan anyway, which stops nobody and the next reconciliation removes.
        with contextlib.suppress(OSError):
            (requests_dir(self.state_dir) / f"{self.request_id}.json").unlink(missing_ok=True)
        self._owner.release()
        with contextlib.suppress(OSError):
            _owner_path(self.state_dir, self.request_id).unlink(missing_ok=True)
        return True

    def abandon(self) -> None:
        """Let the request go without the operations lock (an error on the way): it becomes an orphan, which
        :func:`reconcile` removes. Never leaves a valid request without an owner that waits."""
        self._owner.release()


def _write_request(state_dir: Path, lock: OperationLock, operation: str, reason: str) -> OwnedRequest:
    if not lock.held:
        raise TaskLockError("a cancellation request is written with the operations lock held")
    folder = requests_dir(state_dir)
    folder.mkdir(parents=True, exist_ok=True)
    request = CancelRequest(secrets.token_hex(8), operation, reason, _now())
    owner = FileLock(_owner_path(state_dir, request.request_id))
    if not owner.try_acquire():  # a new random id: nobody can hold it
        raise TaskLockError("the owner lock of a new cancellation request is taken")
    try:
        data = {k: v for k, v in request.__dict__.items() if k != "live"}
        write_atomic(folder / f"{request.request_id}.json", json.dumps(data).encode("utf-8"))
    except BaseException:
        owner.release()
        raise
    return OwnedRequest(state_dir, request, owner)


def reconcile(state_dir: Path, lock: OperationLock) -> list[CancelRequest]:
    """Remove the orphan requests (their owner died), with the operations lock held; returns them. A valid
    request is never touched."""
    if not lock.held:
        raise TaskLockError("cancellation requests are reconciled with the operations lock held")
    removed = []
    for request in cancel_requests(state_dir):
        if request.live or request.request_id == "unreadable":
            continue
        try:
            (requests_dir(state_dir) / f"{request.request_id}.json").unlink(missing_ok=True)
        except OSError:
            continue  # held open by a reader for a moment: the next reconciliation removes it
        with contextlib.suppress(OSError):
            _owner_path(state_dir, request.request_id).unlink(missing_ok=True)
        removed.append(request)
    folder = requests_dir(state_dir)
    with contextlib.suppress(OSError):
        for owner in folder.glob("*.owner"):  # an owner file whose description was never written or was removed
            if not (folder / f"{owner.stem}.json").exists() and not _owner_alive(owner):
                owner.unlink(missing_ok=True)
    return removed


@dataclass(frozen=True)
class Quiesce:
    request: OwnedRequest
    was_running: bool
    stopped: bool
    waited_seconds: float
    task: str | None

    @property
    def request_id(self) -> str:
        return self.request.request_id

    def describe(self) -> str:
        if not self.was_running:
            return "no heavy backup task was running"
        if self.stopped:
            return f"heavy backup task {self.task} stopped after {self.waited_seconds:.1f} s"
        return f"heavy backup task {self.task} did not stop within {self.waited_seconds:.0f} s"


def quiesce(state_dir: Path, operation: str, reason: str, wait_seconds: float, *, lock_seconds: float = 10) -> Quiesce:
    """Ask the heavy backup tasks to stop and wait, bounded and **without** any lock held, until the operating
    system says none is running. The request stands until the caller clears it (``result.request.clear(lock)``)
    once it has opened its window or given up; if the caller dies, it becomes an orphan. ``stopped`` False means
    the caller must refuse (it never goes on blindly)."""
    with OperationLock(state_dir, f"{operation}_stop_backup_tasks").acquire(timeout=lock_seconds) as lock:
        request = _write_request(state_dir, lock, operation, reason)
        running = heavy_task_running(state_dir)
        task = (running_task(state_dir) or {}).get("task") if running else None
    try:
        start = time.monotonic()
        if not running:
            return Quiesce(request, False, True, 0.0, None)
        deadline = start + wait_seconds
        while heavy_task_running(state_dir):
            if time.monotonic() >= deadline:
                return Quiesce(request, True, False, time.monotonic() - start, task)
            time.sleep(POLL_SECONDS)
        return Quiesce(request, True, True, time.monotonic() - start, task)
    except BaseException:
        request.abandon()
        raise


def owns_request(state_dir: Path, request_id: str) -> bool:
    return any(r.request_id == request_id and r.live for r in cancel_requests(state_dir))
