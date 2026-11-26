"""Maintenance window: ``<root>\\state\\maintenance.json``.

The window says that the application is down **on purpose** and which operation owns it (day close, update,
rollback or manual). While it is open the monitor (C8) reports maintenance instead of alerting for the app.

Invariants (Enzo, 2026-11-06, W2-C revision 1):

* A window has exactly one owner operation, recorded in the operations log. Opening a second window while one
  is open is refused; only its owner closes it.
* **Expiry never closes the window and never re-enables writes.** After ``MAINTENANCE_MAX_MINUTES`` the status
  becomes OVERDUE, which is an alert (FAIL for the monitor). The window, and with it the write block of C9,
  stays as it is until the owner operation completes or is reverted explicitly.
* A window file that cannot be read is treated as an open window with an unknown owner (fail-safe), never as
  "no window".
* A closed (absent) window is necessary but **not sufficient** to re-enable writes: that decision belongs to
  ``districom_ops.safety.writes_resume_check``, which also requires a consistent operations record without
  incomplete or uncertain operations.
* Opening and closing happen under the exclusive operations lock (C9-A, ``districom_ops.oplock``): the check
  that no window is open and the writing of the new one can no longer interleave with another opening. The
  window file is also created exclusively, so even without the lock a second window can never replace the first.
* **Backup tasks (W2-C6, design AB):** a window is never opened while a heavy backup task holds ``backup.lock``:
  :meth:`MaintenanceWindow.open` checks it with the operations lock held and refuses (fail safe). To get there, an
  operation first asks the tasks to stop and waits outside the lock (:func:`open_stopping_tasks`, which
  ``maintenance open`` uses; day close and update will use it too). Closing a window also clears a cancellation
  request left by an operation that ended before clearing its own.
"""

import json
import os
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from districom_backup.repo import fsync_dir

from districom_ops import tasklock
from districom_ops.oplock import OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog

LOCK_SECONDS = 10


class WindowState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    OVERDUE = "OVERDUE"
    UNREADABLE = "UNREADABLE"


class MaintenanceError(Exception):
    pass


@dataclass(frozen=True)
class Window:
    operation_id: str
    kind: Kind
    reason: str
    opened_at: datetime


@dataclass(frozen=True)
class WindowStatus:
    state: WindowState
    window: Window | None
    minutes_open: int | None

    @property
    def alert(self) -> bool:
        """For the monitor: an overdue or unreadable window is a failure that someone must look at."""
        return self.state in {WindowState.OVERDUE, WindowState.UNREADABLE}

    def describe(self) -> str:
        if self.state is WindowState.CLOSED:
            return "no maintenance window"
        if self.state is WindowState.UNREADABLE or self.window is None:
            return "FAIL maintenance window file unreadable: owner unknown, writes stay blocked"
        w = self.window
        text = f"maintenance by {w.kind.value} {w.operation_id} for {self.minutes_open} min ({w.reason})"
        if self.state is WindowState.OVERDUE:
            return f"FAIL {text}: overdue, still open; writes stay blocked until the operation ends"
        return f"WARN {text}"


class MaintenanceWindow:
    def __init__(self, state_dir: Path, oplog: OperationsLog) -> None:
        self.path = state_dir / "maintenance.json"
        self.oplog = oplog

    def _read(self) -> Window | None:
        if not self.path.exists():
            return None
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return Window(
            operation_id=str(data["operation_id"]),
            kind=Kind(data["kind"]),
            reason=str(data["reason"]),
            opened_at=datetime.fromisoformat(data["opened_at"]),
        )

    def status(self, max_minutes: int, now: datetime | None = None) -> WindowStatus:
        """Read only: the status never changes the window, whatever the time."""
        try:
            window = self._read()
        except (ValueError, KeyError, TypeError, OSError):
            return WindowStatus(WindowState.UNREADABLE, None, None)
        if window is None:
            return WindowStatus(WindowState.CLOSED, None, None)
        elapsed = int(((now or datetime.now(UTC)) - window.opened_at).total_seconds() // 60)
        state = WindowState.OVERDUE if elapsed >= max_minutes else WindowState.OPEN
        return WindowStatus(state, window, max(elapsed, 0))

    @contextmanager
    def _locked(self, lock: OperationLock | None, operation: str) -> Iterator[OperationLock]:
        """Under ``lock`` (an operation that already holds it) or under a lock taken for this call only."""
        if lock is not None:
            if not lock.held or lock.path.parent != self.path.parent:
                raise MaintenanceError(f"{operation} needs the operations lock of this installation")
            yield lock
            return
        with OperationLock(self.path.parent, operation).acquire(timeout=LOCK_SECONDS) as taken:
            yield taken

    def _create(self, data: dict[str, str]) -> None:
        """Exclusive creation: never replaces a window, even if the lock were bypassed."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0))
        except FileExistsError as exc:
            raise MaintenanceError("a maintenance window appeared while opening this one") from exc
        with os.fdopen(fd, "wb") as handle:
            handle.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        fsync_dir(self.path.parent)

    def open(self, kind: Kind, reason: str, lock: OperationLock | None = None) -> str:
        """Open a window owned by a new operation; returns the operation id.

        A crash in the middle of the creation leaves a partial file, which reads as UNREADABLE: an open window
        with an unknown owner (fail-safe), never as no window."""
        if kind is Kind.RESUME:
            raise MaintenanceError("a resume never opens a maintenance window")
        with self._locked(lock, "maintenance_open"):
            if self.path.exists():
                current = self.status(max_minutes=10**9)
                owner = current.window.operation_id if current.window else "unknown (unreadable file)"
                raise MaintenanceError(f"a maintenance window is already open by {owner}")
            try:
                tasklock.require_no_heavy_task(self.path.parent, "maintenance open")
            except tasklock.TaskLockError as exc:
                raise MaintenanceError(str(exc)) from exc
            op_id = self.oplog.start(kind, reason)
            now = datetime.now(UTC).isoformat()
            self._create({"operation_id": op_id, "kind": kind.value, "reason": reason, "opened_at": now})
            self.oplog.step(op_id, "maintenance_window_opened")
            return op_id

    def close(
        self, operation_id: str, end: Event = Event.COMPLETED, lock: OperationLock | None = None, **detail: str
    ) -> None:
        """Close the window of ``operation_id`` and end that operation. Any other id is refused."""
        with self._locked(lock, "maintenance_close") as held:
            self._close(operation_id, end, **detail)
            # Only orphans (their requester died): a valid request belongs to an operation still running.
            tasklock.reconcile(self.path.parent, held)

    def _close(self, operation_id: str, end: Event, **detail: str) -> None:
        try:
            window = self._read()
        except (ValueError, KeyError, TypeError) as exc:
            raise MaintenanceError("the maintenance window file is unreadable; fix it by hand after checking") from exc
        if window is None:
            raise MaintenanceError("no maintenance window is open")
        if window.operation_id != operation_id:
            raise MaintenanceError(f"the window belongs to {window.operation_id}, not to {operation_id}")
        self.oplog.step(operation_id, "maintenance_window_closed")
        self.path.unlink()
        fsync_dir(self.path.parent)
        self.oplog.finish(operation_id, end, **detail)


def open_stopping_tasks(window: MaintenanceWindow, kind: Kind, reason: str, wait_seconds: float) -> str:
    """Open a window after the heavy backup tasks stopped (W2-C6, design AB).

    1. :func:`tasklock.quiesce`: a persistent cancellation request, written under the operations lock, then a
       bounded wait **without** the lock until the operating system confirms that ``backup.lock`` is free;
    2. with the operations lock taken again, :meth:`MaintenanceWindow.open` checks once more and opens;
    3. its own request is cleared (under the same lock; nobody else's): from now on the window keeps the tasks
       from starting.

    If the task does not confirm that it stopped, nothing is opened and the request is cleared: the refusal names
    the task and the time waited."""
    state = window.path.parent
    stopped = tasklock.quiesce(state, f"maintenance_{kind.value}", reason, wait_seconds, lock_seconds=LOCK_SECONDS)
    with OperationLock(state, "maintenance_open").acquire(timeout=LOCK_SECONDS) as lock:
        try:
            if not stopped.stopped:
                raise MaintenanceError(f"maintenance open refused: {stopped.describe()}")
            return window.open(kind, reason, lock=lock)
        finally:
            stopped.request.clear(lock)  # its own request only
