"""Joint, conservative check that a later stage (C9) must pass before re-enabling writes.

C1 does not unblock anything: it only answers whether it would be safe. The answer is yes only when **all** of
these hold, and any doubt is a no:

* the maintenance window is CLOSED (not open, overdue or unreadable);
* the operations record can be read and every line is valid or a tolerated torn line;
* no operation is incomplete (started without an end) or uncertain (invalid entries in its history).

A missing ``maintenance.json`` alone is not evidence: the window is removed *before* its operation's end is
written, so a crash in between leaves no window and an operation without end, and this check says no.

**The one exception (C9-B, Enzo 2026-11-16):** a ``resume`` is itself an operation of the record, incomplete while
it runs. With a :class:`~districom_ops.oplock.LockContext` (only the lock module makes one, from the lock this
process holds), the check skips exactly that operation, and only if it exists, is active, is of kind ``resume``
and is the one the lock is held for. Nothing else changes: any other incomplete or uncertain operation, an
invalid line, an open or overdue window still say no. Without a context the check is as strict as ever.
"""

from dataclasses import dataclass
from datetime import datetime

from districom_ops.maintenance import MaintenanceWindow, WindowState
from districom_ops.oplock import LockContext, LockContextError
from districom_ops.oplog import Kind


@dataclass(frozen=True)
class ResumeCheck:
    allowed: bool
    reasons: tuple[str, ...]

    def describe(self) -> str:
        if self.allowed:
            return "writes may resume: window closed, operations record consistent, no operation pending"
        return "FAIL writes stay blocked: " + "; ".join(self.reasons)


def writes_resume_check(
    window: MaintenanceWindow, max_minutes: int, now: datetime | None = None, context: LockContext | None = None
) -> ResumeCheck:
    """Read only. C9 must call this, and only this, before re-enabling the application's writes."""
    reasons: list[str] = []
    own: str | None = None
    if context is not None:
        if not isinstance(context, LockContext):
            return ResumeCheck(False, ("recovery context refused: not a lock context",))
        try:
            context.verify()
        except LockContextError as exc:
            return ResumeCheck(False, (f"recovery context refused: {exc}",))
        own = context.operation_id
    status = window.status(max_minutes, now)
    if status.state is not WindowState.CLOSED:
        reasons.append(status.describe())
    try:
        record = window.oplog.read()
    except OSError as exc:
        reasons.append(f"operations record unreadable ({type(exc).__name__})")
        return ResumeCheck(False, tuple(reasons))
    if not record.certain:
        lines = ", ".join(str(p.line) for p in record.problems)
        reasons.append(f"operations record has {len(record.problems)} invalid line(s): {lines}")
    for op in record.uncertain():
        reasons.append(f"operation {op.id} cannot be reconstructed safely")
    if own is not None:
        mine = record.operations.get(own)
        if mine is None:
            reasons.append(f"recovery context refused: operation {own} is not in the operations record")
        elif mine.kind is not Kind.RESUME:
            reasons.append(f"recovery context refused: operation {own} is not a resume")
        elif not mine.incomplete:
            reasons.append(f"recovery context refused: operation {own} already ended ({mine.end})")
    for op in record.incomplete():
        if own is not None and op.id == own and op.kind is Kind.RESUME:
            continue  # the resume that holds the lock: the one exception
        reasons.append(f"operation {op.id} ({op.kind}) has no end")
    if status.window is not None:
        owner = record.operations.get(status.window.operation_id)
        if owner is None:
            reasons.append(f"window owner {status.window.operation_id} is not in the operations record")
        elif owner.end is not None:
            reasons.append(f"window owner {owner.id} already ended ({owner.end}) but the window is still there")
    return ResumeCheck(not reasons, tuple(reasons))


def start_blockers(
    window: MaintenanceWindow, max_minutes: int, now: datetime | None = None, candidate_ok: bool = False
) -> tuple[str, ...]:
    """Why the application must not start now (C9-A): the reasons writes could not resume, plus (C9-B) a write
    block journal in any state but ``none``, ``closed_complete`` included, or one that cannot be read.

    ``services start``, ``services start --direct`` and the supervisor use this, so the application never comes
    up with a maintenance window open, overdue or unreadable, an unreadable or invalid operations record, an
    operation incomplete or uncertain, or a write block not lifted. An empty tuple means the start may go on."""
    from districom_ops import write_block  # write_block uses this module

    check = writes_resume_check(window, max_minutes, now)
    reasons = list(check.reasons)
    journal_reason = write_block.journal_blocker(window.path.parent, candidate_ok)
    if journal_reason:
        reasons.append(journal_reason)
    return tuple(reasons)
