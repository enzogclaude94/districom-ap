"""Operations record: ``<root>\\state\\operations.jsonl``, append-only, one JSON event per line.

Every operation that changes the installation (day close, update, rollback, manual maintenance) gets an id
and leaves ``started``, its ``step`` events and exactly one end: ``completed``, ``failed`` or ``reverted``.
An operation without an end is **incomplete**.

Write-ahead rule: each event is written and ``fsync``-ed before the action it describes is reported as done,
and an end is written only after the operation really ended. So a lost event can only make an operation look
*less* finished than it is, never more.

Reading is strict and never edits the file (the original lines stay as evidence). Each line is classified:

* **valid**: a JSON object with every required field, of the right type, consistent with the operation's
  history (``started`` once and first, nothing after the end, one end).
* **torn**: the last line of the file cut by a crash in the middle of a write. It is tolerated because of the
  write-ahead rule; the next append terminates it and writes a ``torn_line_sealed`` entry that names it, so it
  is still recognised as torn later and the next event is never glued to it.
* **invalid**: anything else (an unparsable line in the middle, a missing field, a wrong type, an unknown
  event or kind, an event of an operation that never started, an event after the end). It is a
  :class:`Problem`: the operation it names becomes **uncertain**, and a problem without a known operation
  makes the whole record uncertain. Uncertainty always blocks re-enabling writes (``districom_ops.safety``).
"""

import json
import os
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from districom_backup.repo import fsync_dir

from districom_ops.logs import REDACTOR


class Kind(StrEnum):
    DAY_CLOSE = "day_close"
    UPDATE = "update"
    ROLLBACK = "rollback"
    MANUAL = "manual"


class Event(StrEnum):
    STARTED = "started"
    STEP = "step"
    COMPLETED = "completed"
    FAILED = "failed"
    REVERTED = "reverted"


ENDS = {Event.COMPLETED, Event.FAILED, Event.REVERTED}
SEAL = "torn_line_sealed"


class OperationError(Exception):
    pass


@dataclass(frozen=True)
class Problem:
    """An invalid line. Messages name fields and lines, never values (they could hold anything)."""

    line: int
    code: str
    detail: str
    operation: str | None = None

    def __str__(self) -> str:
        where = f" (operation {self.operation})" if self.operation else ""
        return f"line {self.line}: {self.code}: {self.detail}{where}"


@dataclass
class Operation:
    id: str
    kind: Kind | None
    reason: str
    started_at: str | None
    events: list[dict[str, Any]] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)

    @property
    def end(self) -> Event | None:
        for event in self.events:
            if event["event"] in ENDS:
                return Event(event["event"])
        return None

    @property
    def uncertain(self) -> bool:
        """Its history has invalid entries: its real state cannot be reconstructed safely."""
        return bool(self.problems)

    @property
    def incomplete(self) -> bool:
        return self.end is None


@dataclass
class Record:
    operations: dict[str, Operation]
    torn_lines: list[int]
    problems: list[Problem] = field(default_factory=list)

    @property
    def certain(self) -> bool:
        """Every line is valid or a tolerated torn line."""
        return not self.problems

    def incomplete(self) -> list[Operation]:
        return [op for op in self.operations.values() if op.incomplete and not op.uncertain]

    def uncertain(self) -> list[Operation]:
        return [op for op in self.operations.values() if op.uncertain]


class _InvalidEntryError(Exception):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _text(entry: dict[str, Any], name: str) -> str:
    if name not in entry:
        raise _InvalidEntryError("missing_field", f"field '{name}' is missing")
    value = entry[name]
    if not isinstance(value, str) or not value.strip():
        raise _InvalidEntryError("wrong_type", f"field '{name}' must be a non-empty text")
    return value


def _check_entry(entry: dict[str, Any]) -> Event:
    """Structure of one event; raises :class:`_InvalidEntryError`. Consistency with its operation is checked apart."""
    try:
        datetime.fromisoformat(_text(entry, "at"))
    except ValueError:
        raise _InvalidEntryError("wrong_type", "field 'at' is not an ISO date and time") from None
    try:
        event = Event(_text(entry, "event"))
    except ValueError:
        raise _InvalidEntryError("unknown_event", "field 'event' is not a known event") from None
    if event is Event.STARTED:
        try:
            Kind(_text(entry, "kind"))
        except ValueError:
            raise _InvalidEntryError("unknown_kind", "field 'kind' is not a known kind") from None
        _text(entry, "reason")
    elif event is Event.STEP:
        _text(entry, "step")
    return event


class OperationsLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({k: REDACTOR.value(k, v) for k, v in entry.items()}, ensure_ascii=False) + "\n"
        new = not self.path.exists()
        prefix = "" if new else self._terminate_torn_tail()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(prefix + line)
            handle.flush()
            os.fsync(handle.fileno())
        if new:
            fsync_dir(self.path.parent)

    def _terminate_torn_tail(self) -> str:
        """If a crash left the last line without its newline, end it and, when it is not a whole JSON object,
        seal it, so the new event starts on its own line and the torn line stays recognisable."""
        data = self.path.read_bytes()
        if not data or data.endswith(b"\n"):
            return ""
        lines = data.split(b"\n")
        try:
            whole = isinstance(json.loads(lines[-1].decode("utf-8")), dict)
        except ValueError:
            whole = False
        if whole:
            return "\n"
        return "\n" + json.dumps({"at": _now(), "record": SEAL, "line": len(lines)}) + "\n"

    def start(self, kind: Kind, reason: str) -> str:
        if not reason.strip():
            raise OperationError("an operation needs a reason")
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        op_id = f"{kind.value}-{stamp}-{secrets.token_hex(3)}"
        self._append(
            {"at": _now(), "operation": op_id, "event": Event.STARTED.value, "kind": kind.value, "reason": reason}
        )
        return op_id

    def step(self, op_id: str, name: str, **detail: Any) -> None:
        self._require_open(op_id)
        self._append({"at": _now(), "operation": op_id, "event": Event.STEP.value, "step": name, **detail})

    def finish(self, op_id: str, end: Event, **detail: Any) -> None:
        if end not in ENDS:
            raise OperationError(f"{end} is not an end event")
        self._require_open(op_id)
        self._append({"at": _now(), "operation": op_id, "event": end.value, **detail})

    def _require_open(self, op_id: str) -> None:
        op = self.read().operations.get(op_id)
        if op is None or op.kind is None:
            raise OperationError(f"unknown operation {op_id}")
        if not op.incomplete:
            raise OperationError(f"operation {op_id} already ended ({op.end})")

    def read(self) -> Record:
        """Strict, read-only reconstruction. Raises ``OSError`` if the file exists but cannot be read."""
        operations: dict[str, Operation] = {}
        problems: list[Problem] = []
        if not self.path.exists():
            return Record(operations, [], problems)
        data = self.path.read_bytes()
        raw_lines = data.split(b"\n")
        unterminated = not data.endswith(b"\n")
        if not unterminated:
            raw_lines.pop()
        unparsable: list[int] = []
        sealed: set[int] = set()

        def problem(number: int, code: str, detail: str, op_id: str | None = None) -> None:
            item = Problem(number, code, detail, op_id)
            problems.append(item)
            if op_id is not None:
                operations.setdefault(op_id, Operation(op_id, None, "", None)).problems.append(item)

        for number, raw in enumerate(raw_lines, start=1):
            try:
                entry = json.loads(raw.decode("utf-8"))
            except ValueError:  # includes UnicodeDecodeError
                unparsable.append(number)
                continue
            if not isinstance(entry, dict):
                problem(number, "not_an_object", "the line is JSON but not an object")
                continue
            if "record" in entry:
                if entry.get("record") == SEAL and entry.get("line") == number - 1 and number - 1 in unparsable:
                    sealed.add(number - 1)
                else:
                    problem(number, "bad_seal", "record entry that does not seal the previous torn line")
                continue
            op_id = entry.get("operation")
            if not isinstance(op_id, str) or not op_id.strip():
                problem(number, "missing_field", "field 'operation' is missing or not a text")
                continue
            try:
                event = _check_entry(entry)
            except _InvalidEntryError as exc:
                problem(number, exc.code, exc.detail, op_id)
                continue
            op = operations.get(op_id)
            if event is Event.STARTED:
                if op is not None:
                    problem(number, "duplicate_start", "the operation already has events before this start", op_id)
                    continue
                operations[op_id] = Operation(op_id, Kind(entry["kind"]), entry["reason"], entry["at"], [entry])
                continue
            if op is None or op.kind is None:
                problem(number, "unknown_operation", "event of an operation that never started", op_id)
                continue
            if op.end is not None:
                problem(number, "event_after_end", f"event after the end ({op.end})", op_id)
                continue
            op.events.append(entry)

        torn: list[int] = []
        for number in unparsable:
            if number in sealed or (unterminated and number == len(raw_lines)):
                torn.append(number)
            else:
                problem(number, "unreadable_line", "the line is not valid JSON and is not a torn last line")
        problems.sort(key=lambda p: p.line)
        return Record(operations, torn, problems)
