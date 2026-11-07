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
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from districom_backup.repo import fsync_dir, write_atomic

from districom_ops.oplog import Event, Kind, OperationsLog


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

    def open(self, kind: Kind, reason: str) -> str:
        """Open a window owned by a new operation; returns the operation id."""
        if self.path.exists():
            current = self.status(max_minutes=10**9)
            owner = current.window.operation_id if current.window else "unknown (unreadable file)"
            raise MaintenanceError(f"a maintenance window is already open by {owner}")
        op_id = self.oplog.start(kind, reason)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        data = {"operation_id": op_id, "kind": kind.value, "reason": reason, "opened_at": datetime.now(UTC).isoformat()}
        write_atomic(self.path, json.dumps(data, ensure_ascii=False).encode("utf-8"))
        self.oplog.step(op_id, "maintenance_window_opened")
        return op_id

    def close(self, operation_id: str, end: Event = Event.COMPLETED, **detail: str) -> None:
        """Close the window of ``operation_id`` and end that operation. Any other id is refused."""
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
