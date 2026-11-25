"""Reversible block of the application's writes (W2-C9-B): ``<root>\\state\\write-block.json`` and
``ALLOW_CONNECTIONS`` of the application database (``docs/WINDOWS_W2C_PLAN.md``, C9-B; Enzo 2026-11-11/16/17).

**Block** (:func:`block`, run by the operation that owns the open maintenance window, holding the operations lock):

1. Caddy to maintenance mode (C5). Until C5, a hook the journal records as ``not_applicable_yet``.
2. The writing tasks suspended (C6): checked before anything changes (no heavy backup task holds ``backup.lock``)
   and recorded.
3. ``ALTER DATABASE … WITH ALLOW_CONNECTIONS false``, by ``districom_ops`` from the ``postgres`` database. No new
   connection of any role gets in, superuser included.
4. Wait, at most ``wait_seconds``, for the transactions in progress to end. If the wait runs out nothing is forced:
   everything is reverted.
5. End the remaining client sessions of the database (``pg_terminate_backend``). ``districom_ops`` cannot end a
   superuser's session: if one is connected, step 6 fails and the block reverts.
6. Verify, in ``pg_database`` and ``pg_stat_activity``, that the flag is ``false`` and no client session is left.

**Journal.** Written with ``fsync`` and rename **before** each step: the owner operation, the step and what was
changed. The reversal undoes exactly what is recorded and is idempotent. States (the machine is closed: any
other transition is refused):

=================  ==========================================================  =================================
state              means                                                       leaves by
=================  ==========================================================  =================================
``none``           no block                                                    ``blocking``
``blocking``       block in progress (step *k*)                                ``blocked`` · ``reverting`` ·
                                                                               ``resuming`` (manual)
``blocked``        block verified; the owner operation goes on                 ``closed_complete`` ·
                                                                               ``reverting`` · ``resuming``
                                                                               (manual)
``closed_complete``  close finished: only a **candidate** for the automatic    ``resuming``
                   recovery, which by itself enables nothing
``reverting``      undoing what is recorded                                    ``none`` · ``resuming`` (manual)
``resuming``       re-enabling writes                                          ``none`` · back to the state it
                                                                               came from (failure)
=================  ==========================================================  =================================

Any state but ``none`` keeps the application down (``safety.start_blockers``: the supervisor records ``blocked``).

**Who may do what.** Only the live owner reverts (the journal keeps the token of its lock: after an interruption
no new process can pass for it). After an interruption only :func:`resume` resolves the state: manual (an
administrator, with the operation and a reason, audited) from any state, or automatic (the supervisor) only
from ``closed_complete`` with its evidence verified against the cluster. **Any doubt keeps writes blocked.**
A reversal that cannot be verified puts the flag back to ``false`` if it can and leaves the journal in
``reverting``: blocked, until a controlled ``resume`` (Enzo 2026-11-19).

**What is read and logged.** ``districom_ops`` is member of ``pg_read_all_stats`` only to see ``xact_start`` and
the backend type of the application's sessions in ``pg_stat_activity``; the block never reads ``query`` and logs
only counts, steps and technical reasons (Enzo 2026-11-19, decision 1).

**Evidence (Enzo 2026-11-20).** C9-B only verifies, technically, the evidence it is given (checkpoint,
timeline, cluster state, SHA-256 of the copies). C10/C11 must produce authentic evidence of the close, and C6/C7
must guarantee the origin, integrity and recoverability of the backups: a file hash is not a validated restore.

Until C10 and C11 no real close reaches ``closed_complete``: :func:`complete` exists for them and the tests use
fabricated journals and evidence only. No real day close is validated here.
"""

import contextlib
import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from districom_backup.repo import write_atomic

from districom_ops import database, tasklock
from districom_ops.install import Install
from districom_ops.maintenance import MaintenanceWindow, WindowState
from districom_ops.oplock import OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog
from districom_ops.safety import writes_resume_check

JOURNAL_FILE = "write-block.json"
WAIT_SECONDS = 60  # transactions in progress get this long to end; then the block reverts (never forced)
VERIFY_SECONDS = 15  # the ended sessions get this long to disappear from pg_stat_activity
TERMINATE_MS = 5000
NOT_YET = "not_applicable_yet"
TASKS_STOPPED = "no_heavy_backup_task_running"
LSN = re.compile(r"^[0-9A-F]{1,8}/[0-9A-F]{1,8}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
log = logging.getLogger("districom_ops")


class State(StrEnum):
    NONE = "none"
    BLOCKING = "blocking"
    BLOCKED = "blocked"
    CLOSED_COMPLETE = "closed_complete"
    REVERTING = "reverting"
    RESUMING = "resuming"


MANUAL_RESUMABLE = {State.BLOCKING, State.BLOCKED, State.CLOSED_COMPLETE, State.REVERTING}
TRANSITIONS: dict[State, set[State]] = {
    State.NONE: {State.BLOCKING},
    State.BLOCKING: {State.BLOCKED, State.REVERTING, State.RESUMING},
    State.BLOCKED: {State.CLOSED_COMPLETE, State.REVERTING, State.RESUMING},
    State.CLOSED_COMPLETE: {State.RESUMING},
    State.REVERTING: {State.NONE, State.RESUMING},
    State.RESUMING: {State.NONE, *MANUAL_RESUMABLE},
}


class WriteBlockError(Exception):
    """The block, its reversal or a resume did not happen. Writes stay as safe as they were (blocked, if they were)."""


class ResumeUnrecordedError(WriteBlockError):
    """Writes were resumed but neither the end of the operation nor the way back could be recorded: the operation
    is left incomplete on purpose (the application stays down) for a new ``resume`` to reconcile."""


class JournalError(WriteBlockError):
    """The journal cannot be read or a transition is not in the state machine: always treated as blocked."""


@dataclass(frozen=True)
class Journal:
    state: State
    owner: str | None = None
    owner_lock: str | None = None
    step: int = 0
    changed: dict[str, Any] = field(default_factory=dict)
    before: State | None = None  # while resuming: the state it came from
    resume: str | None = None  # while resuming: the resume operation
    evidence: dict[str, Any] | None = None
    history: tuple[dict[str, Any], ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "owner": self.owner,
            "owner_lock": self.owner_lock,
            "step": self.step,
            "changed": self.changed,
            "before": self.before.value if self.before else None,
            "resume": self.resume,
            "evidence": self.evidence,
            "history": list(self.history),
        }


def _path(state_dir: Path) -> Path:
    return state_dir / JOURNAL_FILE


def read_journal(state_dir: Path) -> Journal:
    """Strict: a missing file is ``none``; anything unreadable or malformed raises :class:`JournalError`."""
    path = _path(state_dir)
    if not path.exists():
        return Journal(State.NONE)
    try:
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
        state = State(data["state"])
        before = State(data["before"]) if data.get("before") else None
        step = data.get("step", 0)
        changed = data.get("changed") or {}
        history = data.get("history") or []
        evidence = data.get("evidence")
        if not isinstance(step, int) or not isinstance(changed, dict) or not isinstance(history, list):
            raise ValueError("wrong type")
        if evidence is not None and not isinstance(evidence, dict):
            raise ValueError("wrong type")
        for name in ("owner", "owner_lock", "resume"):
            if data.get(name) is not None and not isinstance(data.get(name), str):
                raise ValueError("wrong type")
        if state is not State.NONE and not data.get("owner"):
            raise ValueError("a block without owner")
        if state is State.RESUMING and (before is None or not data.get("resume")):
            raise ValueError("resuming without its origin")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise JournalError(f"write block journal unreadable ({type(exc).__name__})") from None
    return Journal(
        state, data.get("owner"), data.get("owner_lock"), step, changed, before, data.get("resume"), evidence,
        tuple(history),
    )  # fmt: skip


def journal_blocker(state_dir: Path, candidate_ok: bool = False) -> str | None:
    """The reason the journal keeps the application down, or None. ``candidate_ok``: a ``closed_complete``
    journal is not a reason (``services start`` lets the supervisor try the automatic recovery)."""
    try:
        journal = read_journal(state_dir)
    except JournalError as exc:
        return str(exc)
    if journal.state is State.NONE or (candidate_ok and journal.state is State.CLOSED_COMPLETE):
        return None
    return f"write block {journal.state.value} (operation {journal.owner}, step {journal.step})"


def _save(state_dir: Path, journal: Journal, state: State, **changes: Any) -> Journal:
    if state is not journal.state and state not in TRANSITIONS[journal.state]:
        raise JournalError(f"transition {journal.state.value} -> {state.value} is not in the state machine")
    now = datetime.now(UTC).isoformat(timespec="milliseconds")
    entry = {"at": now, "state": state.value, "step": changes.get("step", journal.step)}
    new = replace(journal, state=state, history=(*journal.history, entry), **changes)
    write_atomic(_path(state_dir), json.dumps(new.to_json(), ensure_ascii=False).encode("utf-8"))
    log.info("write_block_journal", extra={"state": state.value, "step": new.step, "operation": new.owner})
    return new


# --- the database, through districom_ops ---------------------------------------------------------------------


class Database:
    """The operations role (``OPS_DATABASE_URL``) on the ``postgres`` database; the application role only for the
    probe after a resume."""

    def __init__(self, install: Install) -> None:
        self.install = install
        self.user, self.app_password, self.name, _ = install.database
        self._ops_user, self._password, self._ops_db = install.ops_database
        if not database.IDENTIFIER.match(self.name):
            raise WriteBlockError("the database name is not a plain identifier")
        if not (self._ops_user and self._password):
            raise WriteBlockError("OPS_DATABASE_URL has no role or password")

    def _ops(self, sql: str) -> list[str]:
        try:
            return database.query(self.install, sql, role=self._ops_user, password=self._password, dbname=self._ops_db)
        except database.QueryError as exc:
            raise WriteBlockError(f"operations connection failed: {exc}") from exc

    def allows_connections(self) -> bool:
        rows = self._ops(f"SELECT datallowconn FROM pg_database WHERE datname = '{self.name}';")  # noqa: S608
        if rows not in (["t"], ["f"]):
            raise WriteBlockError("the application database is not in pg_database")
        return rows == ["t"]

    def set_allow_connections(self, allowed: bool) -> None:
        self._ops(f'ALTER DATABASE "{self.name}" WITH ALLOW_CONNECTIONS {"true" if allowed else "false"};')

    def _count(self, where: str) -> int:
        # The name is a checked identifier (__init__); ``where`` is one of two fixed texts.
        sql = f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{self.name}' AND backend_type = 'client backend'"  # noqa: S608
        rows = self._ops(sql + where + ";")
        if len(rows) != 1 or not rows[0].isdigit():
            raise WriteBlockError("pg_stat_activity could not be read")
        return int(rows[0])

    def sessions(self) -> int:
        return self._count("")

    def in_transaction(self) -> int:
        return self._count(" AND xact_start IS NOT NULL")

    def terminate(self) -> None:
        """Ends every client session of the database except superusers' (it cannot): step 6 then fails."""
        sql = (
            f"SELECT count(pg_terminate_backend(a.pid, {TERMINATE_MS})) FROM pg_stat_activity a "  # noqa: S608
            "JOIN pg_roles r ON r.oid = a.usesysid WHERE a.backend_type = 'client backend' AND NOT r.rolsuper "
        )
        self._ops(sql + f"AND a.datname = '{self.name}';")  # the name is a checked identifier (__init__)

    def probe_application(self) -> None:
        try:
            rows = database.query(
                self.install, "SELECT 1;", role=self.user, password=self.app_password, dbname=self.name
            )
        except database.QueryError as exc:
            raise WriteBlockError(f"the application's test connection failed: {exc.code}") from exc
        if rows != ["1"]:
            raise WriteBlockError("the application's test connection gave an unexpected answer")


def _require_lock(lock: OperationLock) -> None:
    if not lock.held:
        raise WriteBlockError("the write block needs the operations lock held")


def _record(install: Install) -> OperationsLog:
    return OperationsLog(install.layout.state / "operations.jsonl")


def _window(install: Install) -> MaintenanceWindow:
    return MaintenanceWindow(install.layout.state, _record(install))


# --- block, revert, complete ---------------------------------------------------------------------------------


def block(install: Install, lock: OperationLock, owner: str, *, wait_seconds: float = WAIT_SECONDS) -> None:
    """Block the application's writes for ``owner``, the operation of the open maintenance window."""
    _require_lock(lock)
    state = install.layout.state
    status = _window(install).status(install.ops.maintenance_max_minutes)
    if status.state is not WindowState.OPEN or status.window is None or status.window.operation_id != owner:
        raise WriteBlockError("blocking writes needs the open maintenance window of its operation")
    op = _record(install).read().operations.get(owner)
    if op is None or not op.incomplete or op.uncertain:
        raise WriteBlockError(f"operation {owner} is not active in the operations record")
    journal = read_journal(state)
    if journal.state is not State.NONE:
        raise WriteBlockError(f"a write block is already {journal.state.value} (operation {journal.owner})")
    db = Database(install)
    if not db.allows_connections():
        raise WriteBlockError("connections to the database are already disabled outside the journal; not touching it")
    # Step 2 (C6, design AB) is decided here, before the journal changes: the window that owns this block was opened
    # only once no heavy backup task held backup.lock, and no task starts while it is open. A task still running
    # refuses the block, with nothing changed.
    if tasklock.heavy_task_running(state):
        raise WriteBlockError(f"a heavy backup task is running ({tasklock.describe_running(state)}); not blocking")

    oplog = _record(install)
    journal = Journal(State.NONE, owner, lock.token)
    journal = _save(state, journal, State.BLOCKING, step=1, changed={"caddy": NOT_YET})
    oplog.step(owner, "write_block", block_step=1, caddy=NOT_YET)  # C5: Caddy to maintenance mode
    journal = _save(state, journal, State.BLOCKING, step=2, changed={**journal.changed, "tasks": TASKS_STOPPED})
    oplog.step(owner, "write_block", block_step=2, tasks=TASKS_STOPPED)
    journal = _save(state, journal, State.BLOCKING, step=3,
                    changed={**journal.changed, "allow_connections_was": True})  # fmt: skip
    try:
        db.set_allow_connections(False)
        oplog.step(owner, "write_block", block_step=3)
        journal = _save(state, journal, State.BLOCKING, step=4)
        deadline = time.monotonic() + wait_seconds
        while db.in_transaction() > 0:
            if time.monotonic() >= deadline:
                raise WriteBlockError(f"transactions still in progress after {wait_seconds:g} s; not forcing them")
            time.sleep(0.2)
        oplog.step(owner, "write_block", block_step=4)
        journal = _save(state, journal, State.BLOCKING, step=5)
        db.terminate()
        oplog.step(owner, "write_block", block_step=5)
        journal = _save(state, journal, State.BLOCKING, step=6)
        deadline = time.monotonic() + VERIFY_SECONDS
        while (left := db.sessions()) > 0 and time.monotonic() < deadline:
            time.sleep(0.2)
        if db.allows_connections():
            raise WriteBlockError("ALLOW_CONNECTIONS is still true")
        if left:
            raise WriteBlockError(f"{left} session(s) still connected (a superuser's cannot be ended)")
    except WriteBlockError as exc:
        log.error("write_block_failed", extra={"operation": owner, "step": journal.step, "reason": str(exc)})
        oplog.step(owner, "write_block_failed", block_step=journal.step, reason=str(exc))
        revert(install, lock, owner)
        raise
    _save(state, journal, State.BLOCKED, step=6)
    oplog.step(owner, "writes_blocked")
    log.info("writes_blocked", extra={"operation": owner})


def revert(install: Install, lock: OperationLock, owner: str) -> None:
    """Undo exactly what the journal records. Only the live owner (its lock); idempotent (``none`` = done)."""
    _require_lock(lock)
    state = install.layout.state
    journal = read_journal(state)
    if journal.state is State.NONE:
        return
    if journal.state not in (State.BLOCKING, State.BLOCKED, State.REVERTING):
        raise WriteBlockError(f"a write block in {journal.state.value} is not reverted; only resume leaves it")
    if journal.owner != owner or journal.owner_lock != lock.token:
        raise WriteBlockError("only the live owner reverts a write block; after an interruption use resume")
    if journal.changed.get("revert_failed"):
        raise WriteBlockError("a reversal that could not be verified is not retried; only resume leaves it")
    journal = _save(state, journal, State.REVERTING)
    db = Database(install)
    if journal.changed.get("allow_connections_was") is True:
        try:
            if not db.allows_connections():
                db.set_allow_connections(True)
            if not db.allows_connections():
                raise WriteBlockError("ALLOW_CONNECTIONS did not go back to true")
        except WriteBlockError as exc:
            _revert_failed(install, db, journal, exc)
    # C6: nothing to resume, the tasks start again on their own once the journal is none and the window closed.
    # C5: Caddy back to normal mode (a hook; nothing was changed yet).
    _save(state, journal, State.NONE)
    _record(install).step(owner, "write_block_reverted")
    log.warning("write_block_reverted", extra={"operation": owner})


def _revert_failed(install: Install, db: Database, journal: Journal, exc: WriteBlockError) -> None:
    """A reversal that cannot be verified (Enzo 2026-11-19, decision 3): back to the safe side. The flag goes to
    ``false`` again if it can, the journal stays in ``reverting`` (the application stays down, the supervisor
    records ``blocked``) and only a controlled ``resume`` leaves it. The reason is technical: no SQL, no values."""
    owner = journal.owner or ""
    with contextlib.suppress(WriteBlockError):
        db.set_allow_connections(False)
    _save(install.layout.state, journal, State.REVERTING, changed={**journal.changed, "revert_failed": True})
    try:
        flag = "false" if not db.allows_connections() else "true"
    except WriteBlockError:
        flag = "unknown"
    log.critical("write_block_revert_failed", extra={"operation": owner, "reason": str(exc), "datallowconn": flag})
    _record(install).step(owner, "write_block_revert_failed", reason=str(exc), datallowconn=flag)
    raise WriteBlockError(f"the reversal could not be verified; writes stay blocked until a resume: {exc}") from exc


def complete(install: Install, lock: OperationLock, owner: str, evidence: dict[str, Any]) -> None:
    """``blocked`` → ``closed_complete`` with the evidence of the safe close. **Only C11 calls this** (with the
    guarantees of C10); until then only tests, with fabricated evidence. The evidence is checked when it is used."""
    _require_lock(lock)
    journal = read_journal(install.layout.state)
    if journal.state is not State.BLOCKED or journal.owner != owner or journal.owner_lock != lock.token:
        raise WriteBlockError("only the live owner of a verified block marks the close complete")
    problems = evidence_problems(evidence)
    if problems:
        raise WriteBlockError("evidence of the close refused: " + "; ".join(problems))
    _save(install.layout.state, journal, State.CLOSED_COMPLETE, evidence=evidence)
    _record(install).step(owner, "write_block_closed_complete")


# --- evidence of the safe close (C10, C11) -------------------------------------------------------------------


def _lsn(text: str) -> int:
    high, low = text.split("/")
    return (int(high, 16) << 32) + int(low, 16)


def evidence_problems(evidence: object) -> list[str]:
    """Its form. Its content is compared with the cluster by :func:`verify_evidence`."""
    if not isinstance(evidence, dict):
        return ["evidence missing"]
    problems = []
    for name in ("cut_lsn", "checkpoint_lsn"):
        if not isinstance(evidence.get(name), str) or not LSN.match(evidence[name]):
            problems.append(f"{name} is not an LSN")
    timeline = evidence.get("timeline")
    if not isinstance(timeline, int) or isinstance(timeline, bool) or timeline < 1:
        problems.append("timeline is not a positive whole number")
    if not problems and _lsn(evidence["cut_lsn"]) > _lsn(evidence["checkpoint_lsn"]):
        problems.append("the cut LSN is after the shutdown checkpoint")
    copies = evidence.get("copies")
    if not isinstance(copies, list) or not copies:
        return [*problems, "no copies listed"]
    repos = set()
    for copy in copies:
        if not (isinstance(copy, dict) and isinstance(copy.get("path"), str) and isinstance(copy.get("sha256"), str)):
            problems.append("a copy without path or SHA-256")
            continue
        if not SHA256.match(copy["sha256"]):
            problems.append("a copy with a malformed SHA-256")
        repos.add(copy.get("repo"))
    if not {"repo1", "repo2"} <= repos:
        problems.append("the copies must be in repo1 and repo2")
    return problems


def _controldata(install: Install) -> dict[str, str]:
    env = {**os.environ, "LC_MESSAGES": "C", "LANG": "C", "LC_ALL": "C"}
    try:
        result = subprocess.run(  # noqa: S603
            [str(install.pg_bin("pg_controldata")), "-D", str(install.pg_data)],
            capture_output=True, env=env, timeout=60, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return {}
    if result.returncode != 0:
        return {}
    fields = {}
    for line in result.stdout.decode("utf-8", "replace").splitlines():
        name, sep, value = line.partition(":")
        if sep:
            fields[name.strip()] = value.strip()
    return fields


def verify_evidence(install: Install, evidence: object) -> list[str]:
    """The evidence against reality, with PostgreSQL **stopped**: ``pg_controldata`` must show a clean shutdown at
    the recorded checkpoint and timeline, and every listed copy must exist with its SHA-256."""
    problems = evidence_problems(evidence)
    if problems or not isinstance(evidence, dict):
        return problems
    if database.ready(install):
        return ["PostgreSQL is running: its clean shutdown cannot be verified"]
    fields = _controldata(install)
    if not fields:
        return ["pg_controldata could not be read"]
    if fields.get("Database cluster state") != "shut down":
        problems.append("the cluster was not shut down cleanly")
    if fields.get("Latest checkpoint location") != evidence["checkpoint_lsn"]:
        problems.append("the shutdown checkpoint is not the recorded one")
    if fields.get("Latest checkpoint's TimeLineID") != str(evidence["timeline"]):
        problems.append("the timeline is not the recorded one")
    for copy in evidence["copies"]:
        path = Path(copy["path"])
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            problems.append(f"copy in {copy.get('repo')} missing")
            continue
        if digest != copy["sha256"]:
            problems.append(f"copy in {copy.get('repo')} does not match its SHA-256")
    return problems


# --- resume --------------------------------------------------------------------------------------------------


def _reconcile(install: Install, db: Database, own: str) -> None:
    """End each earlier ``resume`` left incomplete, after putting the flag back to ``false`` if it had stayed
    ``true`` without the journal finishing, and record what was found."""
    oplog = _record(install)
    state = install.layout.state
    for op in oplog.read().incomplete():
        if op.kind is not Kind.RESUME or op.id == own:
            continue
        journal = read_journal(state)
        allowed = db.allows_connections()
        found = {"journal": journal.state.value, "datallowconn": allowed}
        if journal.state is State.RESUMING and journal.resume == op.id:
            if allowed:
                db.set_allow_connections(False)
                if db.allows_connections():
                    raise WriteBlockError("ALLOW_CONNECTIONS could not be put back to false")
            assert journal.before is not None
            _save(state, journal, journal.before, before=None, resume=None)
        oplog.finish(op.id, Event.FAILED, reconciled_by=own, **found)
        log.warning("resume_reconciled", extra={"operation": op.id, **found})


def _undo_resume(install: Install, db: Database, resuming: Journal, exc: Exception) -> None:
    """A failure after ``resuming`` was written (Enzo 2026-11-20): back to blocked, as far as it can go, then
    raise. In order: the journal goes back to ``resuming`` if the commit (``none``) was already written, the flag
    to ``false``, and the journal to the state it came from. Whatever step fails leaves a state a new ``resume``
    reconciles (``resuming`` with this operation, which stays incomplete or ends FAILED): never ``none`` with the
    flag ``false``, never a COMPLETED operation. The original error is kept as the cause."""
    state_dir = install.layout.state
    undone: list[str] = []
    try:
        current = read_journal(state_dir)
    except JournalError:
        current = None
    try:
        if current is None or current.state is not State.RESUMING:
            write_atomic(_path(state_dir), json.dumps(resuming.to_json(), ensure_ascii=False).encode("utf-8"))
            undone.append("journal_back_to_resuming")
    except OSError as write_error:
        log.critical("resume_undo_failed", extra={"operation": resuming.resume, "reason": type(write_error).__name__})
        raise ResumeUnrecordedError(
            f"writes were resumed but the operation could not be recorded ({type(exc).__name__}); the journal could "
            "not be put back either: the operation stays incomplete and the application stays down until a resume"
        ) from exc
    with contextlib.suppress(WriteBlockError):
        db.set_allow_connections(False)
    try:
        flag_false = not db.allows_connections()
    except WriteBlockError:
        flag_false = False
    if flag_false:
        undone.append("allow_connections_false")
        assert resuming.before is not None
        try:
            _save(state_dir, resuming, resuming.before, before=None, resume=None)
            undone.append(f"journal_back_to_{resuming.before.value}")
        except (OSError, WriteBlockError):
            pass  # still ``resuming`` with the flag false: the next resume reconciles it
    log.critical("resume_undone", extra={"operation": resuming.resume, "reason": type(exc).__name__,
                                         "undone": undone})  # fmt: skip
    detail = str(exc) if isinstance(exc, WriteBlockError) else type(exc).__name__
    if not flag_false:
        raise WriteBlockError(f"ALLOW_CONNECTIONS could not be put back to false after: {detail}") from exc
    raise WriteBlockError(f"resume failed and was undone ({', '.join(undone)}): {detail}") from exc


def secure_interrupted_resume(install: Install) -> str | None:
    """For the supervisor, with its lock, once PostgreSQL runs and the application stays down: a journal left in
    ``resuming`` may have left ``ALLOW_CONNECTIONS true``. Put it back to ``false`` (only ever towards blocked)
    and change nothing else: the journal and the operations record stay for a controlled ``resume`` to
    reconcile. Returns what was done, or None when the journal is not ``resuming``. Never raises."""
    try:
        journal = read_journal(install.layout.state)
    except JournalError:
        return None
    if journal.state is not State.RESUMING:
        return None
    try:
        db = Database(install)
        if db.allows_connections():
            db.set_allow_connections(False)
            if db.allows_connections():
                raise WriteBlockError("ALLOW_CONNECTIONS is still true")
            outcome = "allow_connections_put_back_to_false"
        else:
            outcome = "allow_connections_already_false"
    except WriteBlockError as exc:
        outcome = f"allow_connections_unknown: {exc}"
    log.critical("interrupted_resume_found", extra={"operation": journal.resume or "", "outcome": outcome})
    return outcome


@dataclass(frozen=True)
class ResumeResult:
    operation: str
    resumed: bool  # False: there was nothing to resume
    detail: str


def resume(
    install: Install,
    lock: OperationLock,
    reason: str,
    *,
    target: str | None = None,
    automatic: bool = False,
    account: str = "",
    start_postgresql: Callable[[], None] | None = None,
) -> ResumeResult:
    """Re-enable writes, with the lock held from the check to the end. Manual: an administrator names the
    operation of the block (``target``); any state but ``resuming`` alone. Automatic (the supervisor): only from
    ``closed_complete``, with the evidence verified while PostgreSQL is still stopped, then ``start_postgresql``.
    Raises :class:`WriteBlockError` and leaves writes blocked on any doubt."""
    _require_lock(lock)
    state = install.layout.state
    oplog = _record(install)
    op = oplog.start(Kind.RESUME, reason)
    oplog.step(op, "resume_started", automatic=automatic, account=account or "unknown")
    context = lock.context(op)
    try:
        journal = read_journal(state)
        oplog.step(op, "journal_found", journal=journal.state.value, block_owner=journal.owner or "")
        if automatic:
            if journal.state is not State.CLOSED_COMPLETE:
                raise WriteBlockError(f"automatic recovery only from closed_complete, not {journal.state.value}")
            problems = verify_evidence(install, journal.evidence)
            if problems:
                raise WriteBlockError("evidence of the close: " + "; ".join(problems))
            oplog.step(op, "evidence_verified")
            if start_postgresql is not None:
                start_postgresql()
        elif target is not None and journal.owner is not None and target != journal.owner:
            raise WriteBlockError(f"the write block belongs to {journal.owner}, not to {target}")
        db = Database(install)
        db.allows_connections()  # the operations connection works before anything is decided
        _reconcile(install, db, op)
        journal = read_journal(state)
        if journal.state is State.NONE:
            if not db.allows_connections():
                raise WriteBlockError("connections are disabled but the journal records no block: not touching them")
            oplog.finish(op, Event.COMPLETED, result="nothing_blocked")
            return ResumeResult(op, False, "no write block")
        if journal.state is State.RESUMING:
            raise WriteBlockError("the journal is still resuming after its reconciliation")
        if not automatic and target is None:
            raise WriteBlockError("a manual resume names the operation of the block (--operation)")
        check = writes_resume_check(_window(install), install.ops.maintenance_max_minutes, context=context)
        if not check.allowed:
            raise WriteBlockError("; ".join(check.reasons))
        oplog.step(op, "resume_check_passed")
        before = journal.state
        resuming = _save(state, journal, State.RESUMING, before=before, resume=op)
        # From here to the end of the operation every failure, of PostgreSQL or of the persistence (journal,
        # operations record, disk), goes through _undo_resume: never a completed operation before its journal is
        # ``none``, never an error hidden, always something a new resume can reconcile.
        try:
            db.set_allow_connections(True)
            if not db.allows_connections():
                raise WriteBlockError("ALLOW_CONNECTIONS did not become true")
            oplog.step(op, "connections_allowed")
            db.probe_application()
            db.sessions()  # pg_stat_activity answers
            oplog.step(op, "connections_verified")
            # C5: Caddy back to normal mode (hook; nothing to do yet).
            _save(state, resuming, State.NONE, before=None, resume=None, evidence=None)  # the commit point
            oplog.finish(op, Event.COMPLETED, result="writes_resumed")
        except Exception as exc:  # any failure here is undone and raised, never swallowed
            _undo_resume(install, db, resuming, exc)
        log.info("writes_resumed", extra={"operation": op, "automatic": automatic})
        return ResumeResult(op, True, "writes resumed")
    except WriteBlockError as exc:
        log.error("resume_failed", extra={"operation": op, "reason": str(exc)})
        if not isinstance(exc, ResumeUnrecordedError):  # never ended FAILED: writes were resumed
            oplog.finish(op, Event.FAILED, reason=str(exc))
        raise
