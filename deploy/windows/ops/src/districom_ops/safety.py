"""Joint, conservative check that a later stage (C9) must pass before re-enabling writes.

C1 does not unblock anything: it only answers whether it would be safe. The answer is yes only when **all** of
these hold, and any doubt is a no:

* the maintenance window is CLOSED (not open, overdue or unreadable);
* the operations record can be read and every line is valid or a tolerated torn line;
* no operation is incomplete (started without an end) or uncertain (invalid entries in its history).

A missing ``maintenance.json`` alone is not evidence: the window is removed *before* its operation's end is
written, so a crash in between leaves no window and an operation without end, and this check says no.
"""

from dataclasses import dataclass
from datetime import datetime

from districom_ops.maintenance import MaintenanceWindow, WindowState


@dataclass(frozen=True)
class ResumeCheck:
    allowed: bool
    reasons: tuple[str, ...]

    def describe(self) -> str:
        if self.allowed:
            return "writes may resume: window closed, operations record consistent, no operation pending"
        return "FAIL writes stay blocked: " + "; ".join(self.reasons)


def writes_resume_check(window: MaintenanceWindow, max_minutes: int, now: datetime | None = None) -> ResumeCheck:
    """Read only. C9 must call this, and only this, before re-enabling the application's writes."""
    reasons: list[str] = []
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
    for op in record.incomplete():
        reasons.append(f"operation {op.id} ({op.kind}) has no end")
    if status.window is not None:
        owner = record.operations.get(status.window.operation_id)
        if owner is None:
            reasons.append(f"window owner {status.window.operation_id} is not in the operations record")
        elif owner.end is not None:
            reasons.append(f"window owner {owner.id} already ended ({owner.end}) but the window is still there")
    return ResumeCheck(not reasons, tuple(reasons))
