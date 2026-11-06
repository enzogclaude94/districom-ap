"""Operations record: ``<root>\\state\\operations.jsonl``, append-only, one JSON event per line.

Every operation that changes the installation (day close, update, rollback, manual maintenance) gets an id
and leaves ``started``, its ``step`` events and exactly one end: ``completed``, ``failed`` or ``reverted``.
An operation without an end is **incomplete**: later stages (C9, C11, C12) refuse to re-enable writes while
one exists.

Each event is written and ``fsync``-ed before the action it describes is reported as done. A crash in the
middle of a write can leave a torn last line: it is reported and ignored, never repaired by rewriting the
file, so history is never lost or edited.
"""

import json
import os
import secrets
from collections.abc import Iterator
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


class OperationError(Exception):
    pass


@dataclass
class Operation:
    id: str
    kind: Kind
    reason: str
    started_at: str
    events: list[dict[str, Any]] = field(default_factory=list)

    @property
    def end(self) -> Event | None:
        for event in reversed(self.events):
            if event["event"] in ENDS:
                return Event(event["event"])
        return None

    @property
    def incomplete(self) -> bool:
        return self.end is None


@dataclass
class Record:
    operations: dict[str, Operation]
    torn_lines: list[int]

    def incomplete(self) -> list[Operation]:
        return [op for op in self.operations.values() if op.incomplete]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class OperationsLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps({k: REDACTOR.value(k, v) for k, v in entry.items()}, ensure_ascii=False) + "\n"
        new = not self.path.exists()
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        if new:
            fsync_dir(self.path.parent)

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
        if op is None:
            raise OperationError(f"unknown operation {op_id}")
        if not op.incomplete:
            raise OperationError(f"operation {op_id} already ended ({op.end})")

    def _lines(self) -> Iterator[tuple[int, str]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8", errors="replace") as handle:
            yield from enumerate(handle, start=1)

    def read(self) -> Record:
        operations: dict[str, Operation] = {}
        torn: list[int] = []
        for number, line in self._lines():
            try:
                entry = json.loads(line)
                op_id = entry["operation"]
                event = Event(entry["event"])
            except (ValueError, KeyError, TypeError):
                torn.append(number)
                continue
            if event is Event.STARTED:
                operations[op_id] = Operation(op_id, Kind(entry["kind"]), entry.get("reason", ""), entry["at"])
            if op_id in operations:
                operations[op_id].events.append(entry)
        return Record(operations, torn)
