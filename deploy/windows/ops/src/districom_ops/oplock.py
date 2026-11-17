"""Exclusive lock between operations (W2-C9-A): ``<root>\\state\\operations.lock``.

Every operation that starts, stops, maintains, updates or (from C9-B) re-enables writes takes this lock, so two
incompatible operations never run at once (``docs/WINDOWS_W2C_PLAN.md``, C9-A, Enzo 2026-11-17).

Rules:

* The lock is the operating system's lock on the file (``msvcrt.locking`` on Windows, ``flock`` elsewhere, for
  the unit tests). It belongs to the open handle: a second handle is refused, even in the same process, and the
  operating system frees it when the process dies. It never expires.
* Whoever does not get it fails at once, or after a short bounded wait, naming the owner. Nobody waits forever.
* **No process waits for another process while it holds the lock.** That, and the bounded waits, rule out a
  circular wait (the start protocol in ``services.start_stack`` / ``services.supervise`` relies on it).
* Next to it, ``operations.lock.json`` says who holds it: operation, process id, a token and the time. It is
  only for messages: no decision is ever taken on it (a dead process may have left it behind).
* Releasing the lock, or a process dying, never changes the persistent state: the maintenance window, the
  operations record and (C9-B) the write block stay as they were. An interrupted operation stays incomplete.
"""

import json
import os
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import IO, Any

from districom_backup.repo import write_atomic

LOCK_FILE = "operations.lock"
HOLDER_FILE = "operations.lock.json"


class LockBusy(Exception):  # noqa: N818 - the name says what the caller meets
    """The lock is held by another operation. ``holder`` is what its owner recorded (for messages only)."""

    def __init__(self, operation: str, holder: dict[str, Any] | None) -> None:
        self.holder = holder
        who = describe(holder)
        super().__init__(f"{operation} refused: another operation holds the operations lock ({who})")


def describe(holder: dict[str, Any] | None) -> str:
    if not holder:
        return "owner not recorded"
    return f"{holder.get('operation')} by process {holder.get('pid')} since {holder.get('acquired_at')}"


def _try_lock(handle: IO[bytes]) -> bool:
    if sys.platform == "win32":
        import msvcrt

        try:
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _unlock(handle: IO[bytes]) -> None:
    if sys.platform == "win32":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class OperationLock:
    """``with OperationLock(state, "services_start").acquire(timeout=10): ...``"""

    def __init__(self, state_dir: Path, operation: str) -> None:
        self.path = state_dir / LOCK_FILE
        self.holder_path = state_dir / HOLDER_FILE
        self.operation = operation
        self.token = secrets.token_hex(8)
        self._handle: IO[bytes] | None = None

    @property
    def held(self) -> bool:
        return self._handle is not None

    def holder(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.holder_path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def acquire(self, timeout: float = 0, poll_seconds: float = 0.2) -> "OperationLock":
        """Take the lock, waiting at most ``timeout`` seconds. Raises :class:`LockBusy`."""
        if self._handle is not None:
            raise RuntimeError("this lock object already holds the lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + timeout
        while True:
            handle = open(self.path, "a+b")  # noqa: SIM115 - kept open while the lock is held
            if _try_lock(handle):
                self._handle = handle
                break
            handle.close()
            if time.monotonic() >= deadline:
                raise LockBusy(self.operation, self.holder())
            time.sleep(poll_seconds)
        record = {
            "operation": self.operation,
            "pid": os.getpid(),
            "token": self.token,
            "acquired_at": datetime.now(UTC).isoformat(),
        }
        write_atomic(self.holder_path, json.dumps(record).encode("utf-8"))
        return self

    def release(self) -> None:
        handle, self._handle = self._handle, None
        if handle is None:
            return
        try:
            holder = self.holder()
            if holder and holder.get("token") == self.token:
                self.holder_path.unlink(missing_ok=True)
        finally:
            try:
                _unlock(handle)
            finally:
                handle.close()

    def __enter__(self) -> "OperationLock":
        if self._handle is None:
            self.acquire()
        return self

    def __exit__(
        self, kind: type[BaseException] | None, value: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.release()
