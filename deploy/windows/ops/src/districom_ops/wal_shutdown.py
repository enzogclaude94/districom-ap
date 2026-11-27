"""Verification of the WAL at shutdown (W2-C10, ``docs/WINDOWS_W2C_PLAN.md`` §C10; decisions D1 to D4 of Enzo,
2026-11-27): after the recorded cut LSN nothing but the shutdown itself wrote WAL, the shutdown was clean, and the
WAL up to the cut is in ``repo1`` and in ``repo2``, verified by content.

C10 is a **verifier**. It runs inside the maintenance window of its own operation, with the C9-B write block of
that same operation in place (journal ``blocked``, same owner, same lock) and no heavy backup task (it holds
``backup.lock`` itself while it works). It does not shut Windows down, does not decide what happens after a FAIL
(C11 does) and does not prove that the copies restore (C7). It leaves PostgreSQL **stopped**.

Steps (:func:`verify`):

1. **Cut.** In one session of the superuser, on the ``postgres`` database: ``CHECKPOINT`` (so no timed checkpoint
   falls right after the cut), the restore point of the day and ``pg_switch_wal()``. Its result is the **cut LSN**:
   the end of the switch record, in the **cut segment**. No SQL runs after it: a new session would read the
   catalogs and could write WAL (hint bits) after the cut.
2. **Fixed set.** Before anything else, the exact set of files the cut needs, from the newest retained base backup
   of ``repo1`` to the cut segment, following the timeline history, plus every ``.history`` file of that history.
   The older segments, from the oldest retained base backup, are checked for continuity (presence and size).
3. **repo1.** Wait for the archiver to put the cut segment there (the file, not a query). Every file of the set must
   be there; its SHA-256 must be that of the server's file while the server still has it (``pg_wal``), and a
   segment must be whole. The archived cut segment must end with this run's restore point and switch.
4. **Clean shutdown** (fast mode: the Windows service, or ``pg_ctl -m fast``) and wait until it is stopped.
5. **The WAL after the cut**, every record from the first byte after the cut segment to the end of the WAL, read
   with ``pg_waldump`` from the server's files or, when the shutdown already archived and removed one, from its copy
   in ``repo1``. Only a closed list is allowed (:data:`ALLOWED`); anything else is a FAIL that names its resource
   manager, type, LSN and transaction (never data). The last record must be the shutdown checkpoint.
6. **pg_controldata**: «shut down», the latest checkpoint is that last record (its redo too) and the timeline is
   the one of the cut.
7. **repo2** (D1): only after repo1 verified (a copy that does not verify is never sent), a synchronous
   ``offsite push``; then every file of the fixed set is **downloaded** from the encrypted remote, decrypted by
   rclone and compared by SHA-256 with the reference (the server's file, else repo1's). The upload's success and the
   remote's metadata are not evidence. Unreachable or unverifiable: FAIL with its reason (D2).

Results are kept per check (``cut``, ``repo1``, ``shutdown``, ``wal_after_cut``, ``controldata``, ``repo2``) and
in ``state\\wal-shutdown\\<operation>-<time>\\``, readable by administrators only: ``result.json``, the dumps of
``pg_waldump`` and ``pg_controldata``, the WAL after the cut on a FAIL, and the files downloaded from repo2 that
the evidence lists (the cut segment and the history files) or that did not match. PASS only when every check
passes; then ``evidence`` is the evidence of the close that C9-B verifies (:func:`write_block.evidence_problems`).

**The list of allowed records** (D3; fixed on PostgreSQL 17.6, C10-8): ``XLOG/SWITCH`` (the shutdown itself
switches segment when archiving is on and WAL was written since the cut; also ``archive_timeout``),
``XLOG/CHECKPOINT_SHUTDOWN`` and ``Standby/RUNNING_XACTS`` (the background writer's snapshot of running
transactions). ``XLOG/FPI_FOR_HINT`` is a FAIL: with data checksums a mere read after the cut writes it. Widening
the list needs evidence and Enzo's approval, never a green CI.
"""

import contextlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from districom_backup.base_backup import BaseBackupInfo, list_backups
from districom_backup.offsite import REMOTE_NAME, CryptRemote, OffsiteError, push
from districom_backup.repo import RepoUnavailableError, sha256_file, wal_dir, write_atomic
from districom_backup.walnames import (
    HistoryEntry,
    Segment,
    format_lsn,
    is_history_name,
    parse_history,
    parse_lsn,
    parse_segment,
    segment_name,
    segno_of_lsn,
)

from districom_ops import tasklock
from districom_ops.install import Install
from districom_ops.logs import REDACTOR
from districom_ops.oplock import OperationLock

log = logging.getLogger("districom_ops")

DIAGNOSTICS = "wal-shutdown"
TASK = "wal_verify_shutdown"  # how a refused backup task names C10 while it holds backup.lock
ALLOWED = frozenset({("XLOG", "SWITCH"), ("XLOG", "CHECKPOINT_SHUTDOWN"), ("Standby", "RUNNING_XACTS")})
# Between this run's restore point and its switch (before the cut): only the switch and the background writer's
# snapshot may stand there.
ALLOWED_IN_TAIL = frozenset({("XLOG", "SWITCH"), ("Standby", "RUNNING_XACTS")})
SHUTDOWN_CHECKPOINT = ("XLOG", "CHECKPOINT_SHUTDOWN")
ARCHIVE_WAIT_SECONDS = 300
STOP_SECONDS = 300
MAX_SEGMENTS_AFTER_CUT = 64
DOWNLOAD_BATCH = 32
MAX_PROBLEMS = 40  # per check; the dumps in the diagnostics folder have everything
OWNER = re.compile(r"^[A-Za-z0-9_.:-]{1,48}$")
_RECORD = re.compile(
    r"^rmgr: (?P<rmgr>\S+)\s+len \(rec/tot\):\s*\d+/\s*\d+, tx:\s*(?P<tx>\d+), "
    r"lsn: (?P<lsn>[0-9A-F]+/[0-9A-F]+), prev (?P<prev>[0-9A-F]+/[0-9A-F]+), desc: (?P<desc>.*)$"
)
# Where PostgreSQL itself stops reading: an invalid record (or none valid at all after the start).
_END = re.compile(r"error in WAL record at [0-9A-F]+/[0-9A-F]+|could not find a valid record after")
_MISSING = re.compile(r'could not find file "([0-9A-F]{24})"')
_CHECKPOINT = re.compile(r"redo ([0-9A-F]+/[0-9A-F]+); tli (\d+);")
_TOOL_TIMEOUT = 600


class WalShutdownError(Exception):
    """C10 did not start (a precondition) or could not go on; the message names the reason, never a value."""


# --- pg_waldump --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Record:
    lsn: int
    prev: int
    rmgr: str
    kind: str
    tx: int
    desc: str  # kept only in the diagnostic dump (administrators), never logged nor in result.json

    @property
    def key(self) -> tuple[str, str]:
        return self.rmgr, self.kind

    def name(self) -> str:
        return f"{self.rmgr}/{self.kind} at {format_lsn(self.lsn)} (tx {self.tx})"

    def to_json(self) -> dict[str, Any]:
        return {"lsn": format_lsn(self.lsn), "rmgr": self.rmgr, "type": self.kind, "tx": self.tx}


def parse_records(text: str) -> list[Record]:
    records = []
    for line in text.splitlines():
        match = _RECORD.match(line.strip())
        if match is None:
            continue
        desc = match.group("desc").strip()
        kind = desc.split()[0] if desc else ""
        records.append(Record(parse_lsn(match.group("lsn")), parse_lsn(match.group("prev")), match.group("rmgr"),
                              kind, int(match.group("tx")), desc))  # fmt: skip
    return records


@dataclass(frozen=True)
class Dump:
    """One run of ``pg_waldump``: its records and how it ended. ``end`` is ``complete`` (the end bound was reached),
    ``end_of_wal`` (an invalid record: where PostgreSQL itself stops), ``missing`` (it needs ``missing``) or
    ``error``."""

    records: list[Record]
    end: str
    missing: str | None
    text: str


def classify_end(returncode: int, stderr: str) -> tuple[str, str | None]:
    if returncode == 0:
        return "complete", None
    missing = _MISSING.search(stderr)
    if missing:
        return "missing", missing.group(1)
    if _END.search(stderr):
        return "end_of_wal", None
    return "error", None


def checkpoint_fields(record: Record) -> tuple[int, int] | None:
    """``(redo, timeline)`` of a checkpoint record, from its description."""
    match = _CHECKPOINT.search(record.desc)
    return (parse_lsn(match.group(1)), int(match.group(2))) if match else None


def judge_after_cut(records: list[Record], timeline: int) -> tuple[list[str], dict[str, Any]]:
    """The closed list (D3) and the shape of a clean shutdown. Returns the problems and what was seen, by category:
    ``shutdown`` (the checkpoint and the switch right before it), ``control`` (allowed, not from the shutdown) and
    ``incompatible`` (everything else)."""
    problems: list[str] = []
    seen: dict[str, list[dict[str, Any]]] = {"shutdown": [], "control": [], "incompatible": []}
    if not records:
        return ["no WAL record after the cut: the shutdown checkpoint is missing"], seen
    for index, record in enumerate(records):
        following = records[index + 1] if index + 1 < len(records) else None
        if record.key not in ALLOWED:
            seen["incompatible"].append(record.to_json())
            problems.append(f"record not allowed after the cut: {record.name()}")
        elif record.key == SHUTDOWN_CHECKPOINT or (
            record.key == ("XLOG", "SWITCH") and following is not None and following.key == SHUTDOWN_CHECKPOINT
        ):
            seen["shutdown"].append(record.to_json())
        else:
            seen["control"].append(record.to_json())
    checkpoints = [r for r in records if r.key == SHUTDOWN_CHECKPOINT]
    last = records[-1]
    if last.key != SHUTDOWN_CHECKPOINT:
        problems.append(f"the last record is not the shutdown checkpoint but {last.name()}")
    if len(checkpoints) > 1:
        problems.append(f"{len(checkpoints)} shutdown checkpoints after the cut")
    if last.key == SHUTDOWN_CHECKPOINT:
        fields = checkpoint_fields(last)
        if fields is None:
            problems.append("the shutdown checkpoint's description could not be read")
        else:
            redo, tli = fields
            if redo != last.lsn:
                problems.append("the shutdown checkpoint's redo is not the checkpoint itself")
            if tli != timeline:
                problems.append(f"the shutdown checkpoint is on timeline {tli}, not {timeline}")
    return problems, seen


def judge_tail(tail: list[Record], before: list[Record], restore_point: str) -> list[str]:
    """Before the cut, from the end of this run's restore point to the cut: only the switch (and the background
    writer's snapshot), ending with the switch; and the record before them is this run's restore point."""
    problems = []
    if not tail or tail[-1].key != ("XLOG", "SWITCH"):
        problems.append("the archived cut segment does not end with this run's switch")
    problems += [f"record between the restore point and the switch: {r.name()}" for r in tail
                 if r.key not in ALLOWED_IN_TAIL]  # fmt: skip
    named = before[0].desc.split(maxsplit=1)[1:] if len(before) == 1 else []
    if len(before) != 1 or before[0].key != ("XLOG", "RESTORE_POINT") or named != [restore_point]:
        problems.append("the restore point of this run is not right before the switch")
    return problems


def judge_controldata(fields: dict[str, str], checkpoint: Record | None, timeline: int) -> list[str]:
    if not fields:
        return ["pg_controldata could not be read"]
    problems = []
    if fields.get("Database cluster state") != "shut down":
        problems.append(f"the cluster state is {fields.get('Database cluster state')!r}, not 'shut down'")
    location = fields.get("Latest checkpoint location")
    if checkpoint is None or location != format_lsn(checkpoint.lsn):
        problems.append("the latest checkpoint is not the last record after the cut")
    if location is not None and fields.get("Latest checkpoint's REDO location") != location:
        problems.append("the latest checkpoint's redo is not the checkpoint itself")
    if fields.get("Latest checkpoint's TimeLineID") != str(timeline):
        problems.append(f"the latest checkpoint is not on timeline {timeline}")
    return problems


# --- the fixed set of WAL the cut needs ----------------------------------------------------------------------------


@dataclass
class WalSet:
    """Fixed before the shutdown: ``needed`` (content-verified in both repositories), ``histories`` (also content)
    and ``older`` (from the oldest retained base backup: continuity, presence and size)."""

    base: str
    needed: list[str]
    histories: list[str]
    older: list[str]
    oldest_base: str

    @property
    def content(self) -> list[str]:
        return [*self.histories, *self.needed]


def timeline_path(timeline: int, history: list[HistoryEntry]) -> list[tuple[int, int | None, int | None]]:
    """``(timeline, begin LSN, end LSN)`` from the oldest ancestor to ``timeline`` (None: open)."""
    path: list[tuple[int, int | None, int | None]] = []
    begin: int | None = None
    for entry in history:
        path.append((entry.timeline, begin, entry.switch_lsn))
        begin = entry.switch_lsn
    path.append((timeline, begin, None))
    return path


def segments_between(
    start: Segment, cut: Segment, path: list[tuple[int, int | None, int | None]], segment_size: int
) -> tuple[list[str], list[str]]:
    """The segment names from ``start`` to ``cut``, each on the newest timeline of ``path`` that holds it (what
    recovery reads: the segment of a switch point exists on both timelines and the child's copy is the one).
    Returns the names and the problems."""
    timelines = [tli for tli, _, _ in path]
    if start.timeline not in timelines:
        return [], [f"the base backup's timeline {start.timeline} is not in the history of timeline {cut.timeline}"]
    ranges = []
    for tli, begin, end in path[timelines.index(start.timeline) :]:
        first = segno_of_lsn(begin, segment_size) if begin is not None else 0
        last = segno_of_lsn(end - 1, segment_size) if end is not None else cut.segno
        ranges.append((tli, first, last))
    names, problems = [], []
    for segno in range(start.segno, cut.segno + 1):
        covering = [tli for tli, first, last in ranges if first <= segno <= last]
        if not covering:
            problems.append(f"no timeline of the history holds segment number {segno}")
            continue
        names.append(segment_name(Segment(max(covering), segno), segment_size))
    return names, problems


def fix_set(
    backups: list[BaseBackupInfo], cut: Segment, history: list[HistoryEntry], segment_size: int
) -> tuple[WalSet | None, list[str]]:
    """The set is fixed from what repo1 says about its base backups and from the server's history of the cut's
    timeline: never from which WAL files happen to be present."""
    path = timeline_path(cut.timeline, history)
    on_path = {tli for tli, _, _ in path}
    usable = [b for b in backups if b.timeline in on_path and b.start_segment().segno <= cut.segno]
    if not usable:
        return None, ["repo1 has no base backup from which the cut can be recovered"]
    newest, oldest = usable[-1], usable[0]
    needed, problems = segments_between(newest.start_segment(), cut, path, segment_size)
    older: list[str] = []
    if newest.start_segment().segno > oldest.start_segment().segno:
        before_newest = Segment(cut.timeline, newest.start_segment().segno - 1)
        older, more = segments_between(oldest.start_segment(), before_newest, path, segment_size)
        problems += more
    histories = [f"{tli:08X}.history" for tli, _, _ in path if tli > 1]
    return WalSet(newest.label, needed, histories, older, oldest.label), problems


# --- what C10 works on ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Target:
    """The cluster and the repositories. ``stop`` shuts the server down cleanly and returns once it is stopped;
    ``remote`` is None when repo2 is not configured (``remote_problem`` says why)."""

    pg_bin: Path
    data: Path
    port: int
    repo1: Path
    diagnostics: Path
    stop: Callable[[], None]
    remote: CryptRemote | None = None
    remote_problem: str | None = None
    password: str | None = None
    superuser: str = "postgres"

    def tool(self, name: str) -> str:
        return str(self.pg_bin / (name + (".exe" if sys.platform == "win32" else "")))


@dataclass
class Hooks:
    """Points where the tests inject a fault (C10-2 to C10-9). Production passes none."""

    after_cut: Callable[[], object] | None = None
    after_archive: Callable[[], object] | None = None
    after_push: Callable[[], object] | None = None
    stop: Callable[[], object] | None = None


@dataclass
class Result:
    operation: str
    started_at: str
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    cut: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] | None = None
    diagnostics: str = ""
    finished_at: str | None = None

    def check(self, name: str) -> dict[str, Any]:
        return self.checks.setdefault(name, {"ok": False, "problems": []})

    def fail(self, name: str, problem: str) -> None:
        entry = self.check(name)
        entry["ok"] = False
        entry["problems"].append(problem)

    @property
    def passed(self) -> bool:
        names = ("cut", "repo1", "shutdown", "wal_after_cut", "controldata", "repo2")
        return all(self.checks.get(n, {}).get("ok") is True for n in names)

    @property
    def verdict(self) -> str:
        return "PASS" if self.passed else "FAIL"

    def problems(self) -> list[str]:
        return [f"{name}: {p}" for name, check in self.checks.items() for p in check.get("problems", [])]

    def to_json(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "verdict": self.verdict,
            "evidence_complete": self.passed,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "cut": self.cut,
            "checks": self.checks,
            "evidence": self.evidence,
            "diagnostics": self.diagnostics,
            "allowed_after_cut": sorted("/".join(k) for k in ALLOWED),
        }


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _env(target: Target) -> dict[str, str]:
    env = {**os.environ, "PGCLIENTENCODING": "UTF8", "PGCONNECT_TIMEOUT": "10", "LC_MESSAGES": "C", "LANG": "C",
           "LC_ALL": "C", "PGAPPNAME": "districom_wal_shutdown"}  # fmt: skip
    if target.password:
        env["PGPASSWORD"] = target.password
    return env


def _sql(target: Target, sql: str) -> list[str]:
    """One superuser session on ``postgres``; the statements go by stdin and the output is never logged."""
    try:
        result = subprocess.run(  # noqa: S603
            [target.tool("psql"), "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1", "-p",
             str(target.port), "-U", target.superuser, "-d", "postgres", "-f", "-"],
            input=sql.encode("utf-8"), capture_output=True, env=_env(target), timeout=_TOOL_TIMEOUT, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise WalShutdownError(f"the superuser session could not run ({type(exc).__name__})") from None
    if result.returncode != 0:
        raise WalShutdownError(f"the superuser session failed ({result.returncode})")
    return [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]


def _waldump(target: Target, directory: Path, timeline: int, start: int, end: int | None = None) -> Dump:
    args = [target.tool("pg_waldump"), "-p", str(directory), "-t", str(timeline), "-s", format_lsn(start)]
    if end is not None:
        args += ["-e", format_lsn(end)]
    try:
        result = subprocess.run(args, capture_output=True, env=_env(target), timeout=_TOOL_TIMEOUT, check=False)  # noqa: S603
    except (OSError, subprocess.TimeoutExpired) as exc:
        return Dump([], "error", None, f"pg_waldump could not run ({type(exc).__name__})")
    out = result.stdout.decode("utf-8", "replace")
    err = result.stderr.decode("utf-8", "replace")
    end_kind, missing = classify_end(result.returncode, err)
    return Dump(parse_records(out), end_kind, missing, out + err)


def controldata(target: Target) -> tuple[dict[str, str], str]:
    try:
        result = subprocess.run(  # noqa: S603
            [target.tool("pg_controldata"), "-D", str(target.data)], capture_output=True, env=_env(target),
            timeout=60, check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return {}, ""
    text = result.stdout.decode("utf-8", "replace")
    if result.returncode != 0:
        return {}, text
    fields = {}
    for line in text.splitlines():
        name, sep, value = line.partition(":")
        if sep:
            fields[name.strip()] = value.strip()
    return fields, text


def _sha(path: Path) -> str | None:
    try:
        return sha256_file(path)
    except OSError:
        return None


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(path, text.encode("utf-8"))


# --- the steps -----------------------------------------------------------------------------------------------------


@dataclass
class Cut:
    restore_point: str
    restore_point_lsn: int
    cut_lsn: int
    segment: Segment
    segment_size: int

    @property
    def timeline(self) -> int:
        return self.segment.timeline

    @property
    def segment_name(self) -> str:
        return segment_name(self.segment, self.segment_size)

    def to_json(self) -> dict[str, Any]:
        return {"restore_point": self.restore_point, "restore_point_lsn": format_lsn(self.restore_point_lsn),
                "cut_lsn": format_lsn(self.cut_lsn), "cut_segment": self.segment_name, "timeline": self.timeline,
                "segment_size": self.segment_size}  # fmt: skip


def take_cut(target: Target, owner: str) -> Cut:
    if not OWNER.match(owner):
        raise WalShutdownError("the operation id is not a plain identifier")
    name = f"districom-c10-{datetime.now(UTC):%Y%m%d}-{owner}"
    # The name is a checked identifier (OWNER) and a fixed prefix: nothing else reaches the SQL text.
    sql = (
        "SELECT setting FROM pg_settings WHERE name = 'wal_segment_size';\n"  # noqa: S608
        "SELECT pg_is_in_recovery();\n"
        "SELECT pg_walfile_name(pg_current_wal_lsn());\n"
        "CHECKPOINT;\n"
        f"SELECT pg_create_restore_point('{name}');\n"
        "SELECT pg_switch_wal();\n"
    )
    rows = _sql(target, sql)
    if len(rows) != 5 or rows[1] != "f" or not rows[0].isdigit():
        raise WalShutdownError("the cluster did not answer the cut as a primary")
    size = int(rows[0])
    timeline = parse_segment(rows[2], size).timeline
    point, cut = parse_lsn(rows[3]), parse_lsn(rows[4])
    if not point < cut:
        raise WalShutdownError("the switch did not follow the restore point (no WAL was switched)")
    # The end of the switch record lies inside the segment it closed (``pg_switch_wal``); never on its boundary.
    return Cut(name, point, cut, Segment(timeline, segno_of_lsn(cut - 1, size)), size)


def _local_history(target: Target, timeline: int) -> list[HistoryEntry]:
    if timeline == 1:
        return []
    path = target.data / "pg_wal" / f"{timeline:08X}.history"
    try:
        return parse_history(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise WalShutdownError(f"the server's history of timeline {timeline} cannot be read") from exc


def _wait_archived(target: Target, name: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    path = target.repo1 / "wal" / name
    while not path.is_file():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.5)
    return True


def check_repo1(target: Target, cut: Cut, walset: WalSet, local: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """Content of the fixed set and continuity of the older segments. Returns the problems and repo1's hashes."""
    problems, hashes = [], {}
    folder = wal_dir(target.repo1)
    for name in walset.content:
        path = folder / name
        digest = _sha(path) if path.is_file() else None
        if digest is None:
            problems.append(f"{name} missing from repo1")
            continue
        hashes[name] = digest
        if not is_history_name(name) and path.stat().st_size != cut.segment_size:
            problems.append(f"{name} truncated in repo1 ({path.stat().st_size} bytes)")
        if name in local and local[name] != digest:
            problems.append(f"{name} in repo1 differs from the server's")
    for name in walset.older:
        path = folder / name
        if not path.is_file():
            problems.append(f"{name} missing from repo1 (gap since the oldest base backup)")
        elif path.stat().st_size != cut.segment_size:
            problems.append(f"{name} truncated in repo1 ({path.stat().st_size} bytes)")
    return problems, hashes


def check_tail(target: Target, cut: Cut) -> tuple[list[str], str]:
    """The archived cut segment ends with this run's restore point and switch (read from repo1)."""
    folder = target.repo1 / "wal"
    tail = _waldump(target, folder, cut.timeline, cut.restore_point_lsn, cut.cut_lsn)
    if tail.end != "complete" or not tail.records:
        return ["the archived cut segment could not be read up to the cut"], tail.text
    before = _waldump(target, folder, cut.timeline, tail.records[0].prev, cut.restore_point_lsn)
    if before.end != "complete":
        return ["the restore point could not be read in the archived cut segment"], tail.text + before.text
    return judge_tail(tail.records, before.records, cut.restore_point), before.text + tail.text


def read_after_cut(target: Target, cut: Cut, staging: Path) -> tuple[Dump, list[str]]:
    """Every record after the cut segment, staging the segments one by one from the server's ``pg_wal`` or, when the
    shutdown already archived and removed one, from repo1. A segment in both must be the same."""
    staging.mkdir(parents=True, exist_ok=True)
    problems: list[str] = []
    start = (cut.segment.segno + 1) * cut.segment_size
    segno = cut.segment.segno + 1
    for _ in range(MAX_SEGMENTS_AFTER_CUT):
        name = segment_name(Segment(cut.timeline, segno), cut.segment_size)
        local, archived = target.data / "pg_wal" / name, target.repo1 / "wal" / name
        if local.is_file() and archived.is_file() and _sha(local) != _sha(archived):
            problems.append(f"{name} after the cut differs between the server and repo1")
        source = local if local.is_file() else archived
        if not source.is_file():
            return Dump([], "missing", name, ""), [*problems, f"{name} (WAL after the cut) is neither on the server "
                                                               "nor in repo1"]  # fmt: skip
        shutil.copyfile(source, staging / name)
        dump = _waldump(target, staging, cut.timeline, start)
        if dump.end != "missing":
            if dump.end == "error":
                problems.append("pg_waldump could not read the WAL after the cut")
            return dump, problems
        segno += 1
    return dump, [*problems, f"more than {MAX_SEGMENTS_AFTER_CUT} segments after the cut"]


def _download(remote: CryptRemote, names: list[str], folder: Path) -> str | None:
    """Download ``wal/<name>`` for every name into ``folder`` (rclone decrypts). Returns an error, or None."""
    folder.mkdir(parents=True, exist_ok=True)
    listing = folder.parent / "repo2-files.txt"
    listing.write_text("\n".join(names) + "\n", encoding="utf-8")
    try:
        result = remote.run("copy", "--files-from-raw", str(listing), "--no-traverse", f"{REMOTE_NAME}:wal",
                            str(folder))  # fmt: skip
    finally:
        listing.unlink(missing_ok=True)
    if result.returncode != 0:
        return REDACTOR.text(result.stderr.strip().splitlines()[-1] if result.stderr.strip() else "rclone failed")
    return None


def _remote_sizes(remote: CryptRemote) -> dict[str, int] | None:
    result = remote.run("lsjson", "--files-only", f"{REMOTE_NAME}:wal")
    if result.returncode != 0:
        return None
    try:
        return {item["Name"]: int(item["Size"]) for item in json.loads(result.stdout)}
    except (ValueError, KeyError, TypeError):
        return None


def check_repo2(
    target: Target, cut: Cut, walset: WalSet, reference: dict[str, str], keep: set[str]
) -> tuple[list[str], dict[str, Any], list[dict[str, str]]]:
    """Download every file of the fixed set and compare it with the reference. Returns the problems, a summary and
    the copies kept for the evidence."""
    remote = target.remote
    assert remote is not None
    problems: list[str] = []
    folder = target.diagnostics / "repo2"
    copies, compared, errors = [], 0, []
    # In batches, each one compared and removed before the next: the day's WAL never sits whole on the disk twice.
    for index in range(0, len(walset.content), DOWNLOAD_BATCH):
        batch = walset.content[index : index + DOWNLOAD_BATCH]
        error = _download(remote, batch, folder)
        if error and error not in errors:
            errors.append(error)
        for name in batch:
            path = folder / name
            digest = _sha(path) if path.is_file() else None
            if digest is None:
                problems.append(f"{name} missing from repo2")
                continue
            compared += 1
            if digest != reference.get(name):
                problems.append(f"{name} in repo2 differs from the reference")
                continue  # kept for the diagnosis
            if name in keep:
                copies.append({"repo": "repo2", "path": str(path), "sha256": digest})
            else:
                path.unlink(missing_ok=True)
    problems[:0] = [f"the download from repo2 failed: {e}" for e in errors]
    sizes = _remote_sizes(remote) if walset.older else {}
    if sizes is None:
        problems.append("repo2's WAL listing could not be read (continuity)")
    else:
        for name in walset.older:
            if name not in sizes:
                problems.append(f"{name} missing from repo2 (gap since the oldest base backup)")
            elif sizes[name] != cut.segment_size:
                problems.append(f"{name} truncated in repo2")
    summary = {"downloaded_and_compared": compared, "set": len(walset.content), "continuity_checked": len(walset.older)}
    return problems, summary, copies


def verify(target: Target, owner: str, hooks: Hooks | None = None) -> Result:
    """Run the steps on ``target``; never raises for a FAIL (the result says it). Leaves PostgreSQL stopped (or as
    the failing step left it)."""
    hooks = hooks or Hooks()
    result = Result(owner, _now(), diagnostics=str(target.diagnostics))
    target.diagnostics.mkdir(parents=True, exist_ok=True)
    try:
        _verify(target, owner, hooks, result)
    except WalShutdownError as exc:
        result.fail("run", str(exc))
    except (OSError, RepoUnavailableError) as exc:
        result.fail("run", f"{type(exc).__name__} while verifying")
    result.finished_at = _now()
    for name in ("cut", "repo1", "shutdown", "wal_after_cut", "controldata", "repo2"):
        entry = result.checks.setdefault(name, {"ok": False, "problems": ["not reached"]})
        if not entry["problems"] and entry.get("ok") is not True:
            entry["problems"].append("not reached")
    _write(target.diagnostics / "result.json", json.dumps(result.to_json(), indent=1, ensure_ascii=False))
    return result


def _ok(result: Result, name: str, problems: list[str], **detail: Any) -> None:
    entry = result.check(name)
    entry.update(detail)
    every = [*entry["problems"], *problems]
    entry["ok"] = not every
    entry["problems"] = every[:MAX_PROBLEMS] + ([f"... and {len(every) - MAX_PROBLEMS} more"]
                                                if len(every) > MAX_PROBLEMS else [])  # fmt: skip


def _verify(target: Target, owner: str, hooks: Hooks, result: Result) -> None:
    diag = target.diagnostics
    cut = take_cut(target, owner)
    result.cut = cut.to_json()
    log.info("wal_shutdown_cut", extra={"operation": owner, **{k: v for k, v in result.cut.items()
                                                               if k != "restore_point"}})  # fmt: skip
    if hooks.after_cut:
        hooks.after_cut()

    # 2. The fixed set, before the server can change anything more.
    history = _local_history(target, cut.timeline)
    try:
        backups = list_backups(target.repo1)
    except RepoUnavailableError:
        backups = []
        result.fail("repo1", "repo1 is not available (backup disk absent or not prepared)")
    walset, set_problems = fix_set(backups, cut.segment, history, cut.segment_size)
    if walset is not None:
        result.cut["fixed_set"] = {"from_base": walset.base, "segments": len(walset.needed),
                                   "first": walset.needed[0] if walset.needed else None,
                                   "histories": walset.histories, "older_segments": len(walset.older),
                                   "oldest_base": walset.oldest_base}  # fmt: skip
        _write(diag / "fixed-set.json", json.dumps({"needed": walset.needed, "histories": walset.histories,
                                                    "older": walset.older}, indent=1))  # fmt: skip

    # 3. repo1: the archiver's copy of the cut segment, then the set against the server's files.
    archived = _wait_archived(target, cut.segment_name, ARCHIVE_WAIT_SECONDS)
    if hooks.after_archive:
        hooks.after_archive()
    local: dict[str, str] = {}
    for name in walset.content if walset else []:
        digest = _sha(target.data / "pg_wal" / name)
        if digest is not None:
            local[name] = digest
    repo1_problems = list(set_problems)
    repo1_hashes: dict[str, str] = {}
    if not archived:
        repo1_problems.append(f"{cut.segment_name} (the cut segment) did not reach repo1 in {ARCHIVE_WAIT_SECONDS} s")
    if walset is not None and backups:
        more, repo1_hashes = check_repo1(target, cut, walset, local)
        repo1_problems += more
    elif walset is None and not set_problems:
        repo1_problems.append("the fixed set could not be computed")
    tail_problems, tail_text = check_tail(target, cut) if archived else (["not read: the cut segment is not in "
                                                                          "repo1"], "")  # fmt: skip
    _write(diag / "waldump-cut-tail.txt", tail_text)
    _ok(result, "cut", tail_problems)
    _ok(result, "repo1", repo1_problems, set=len(walset.content) if walset else 0,
        compared_with_server=len(local), older_continuity=len(walset.older) if walset else 0)  # fmt: skip

    # 4. Clean shutdown.
    try:
        (hooks.stop or target.stop)()
        _ok(result, "shutdown", [], mode="fast" if hooks.stop is None else "test hook")
    except Exception as exc:  # the stop failed: what the WAL and the control file say is still read
        _ok(result, "shutdown", [f"the clean shutdown failed ({type(exc).__name__}: {REDACTOR.text(str(exc))[:200]})"])

    # 5. The WAL after the cut.
    staging = diag / "wal-after-cut"
    dump, read_problems = read_after_cut(target, cut, staging)
    _write(diag / "waldump-after-cut.txt", dump.text)
    judged, seen = judge_after_cut(dump.records, cut.timeline)
    last = dump.records[-1] if dump.records else None
    _ok(result, "wal_after_cut", [*read_problems, *judged], records=[r.to_json() for r in dump.records][:200],
        by_category=seen, read_until=dump.end)  # fmt: skip

    # 6. pg_controldata.
    fields, text = controldata(target)
    _write(diag / "controldata.txt", text)
    checkpoint = last if last is not None and last.key == SHUTDOWN_CHECKPOINT else None
    _ok(result, "controldata", judge_controldata(fields, checkpoint, cut.timeline),
        state=fields.get("Database cluster state"), checkpoint=fields.get("Latest checkpoint location"),
        timeline=fields.get("Latest checkpoint's TimeLineID"))  # fmt: skip

    # 7. repo2: pushed only after repo1 verified, then downloaded and compared by content.
    reference = {**repo1_hashes, **local}
    keep = {cut.segment_name, *(walset.histories if walset else [])}
    if target.remote is None:
        _ok(result, "repo2", [target.remote_problem or "repo2 is not configured"])
    elif walset is None:
        _ok(result, "repo2", ["not checked: the fixed set could not be computed"])
    else:
        pushed, push_problems = False, []
        if result.checks["repo1"]["ok"]:
            try:
                push(target.repo1, target.remote)
                pushed = True
            except (OffsiteError, OSError, subprocess.SubprocessError) as exc:
                push_problems.append(f"the push to repo2 failed: {REDACTOR.text(str(exc)).splitlines()[-1][:300]}")
        else:
            push_problems.append("not pushed: repo1 did not verify (a copy that does not verify is never sent)")
        if hooks.after_push:
            hooks.after_push()
        problems, summary, copies2 = check_repo2(target, cut, walset, reference, keep)
        _ok(result, "repo2", [*push_problems, *problems], pushed=pushed, **summary)
        if result.passed:
            copies1 = [{"repo": "repo1", "path": str(target.repo1 / "wal" / n), "sha256": repo1_hashes[n]}
                       for n in sorted(keep)]  # fmt: skip
            assert last is not None  # a PASS has the shutdown checkpoint as its last record
            result.evidence = {"cut_lsn": format_lsn(cut.cut_lsn), "checkpoint_lsn": format_lsn(last.lsn),
                               "timeline": cut.timeline, "restore_point": cut.restore_point,
                               "copies": copies1 + copies2}  # fmt: skip
    if result.passed:
        shutil.rmtree(staging, ignore_errors=True)  # the WAL after the cut is only kept to diagnose a FAIL


# --- in an installation: preconditions, the target and the trial command ------------------------------------------


def preconditions(install: Install, lock: OperationLock, owner: str) -> tasklock.FileLock:
    """C10-10. With ``lock`` (``operations.lock``) held: the open window of ``owner`` and its active operation, the
    C9-B block of ``owner`` with this lock, the database closed to connections, no valid cancellation request of
    another operation and no heavy backup task. Returns ``backup.lock``, held: no heavy task starts until it is
    released (:func:`run` does it)."""
    from districom_ops import write_block
    from districom_ops.maintenance import WindowState

    if not lock.held:
        raise WalShutdownError("C10 needs the operations lock held")
    state = install.layout.state
    status = write_block._window(install).status(install.ops.maintenance_max_minutes)
    if status.state is not WindowState.OPEN or status.window is None or status.window.operation_id != owner:
        raise WalShutdownError("C10 needs the open (not overdue) maintenance window of its own operation")
    op = write_block._record(install).read().operations.get(owner)
    if op is None or not op.incomplete or op.uncertain:
        raise WalShutdownError(f"operation {owner} is not active in the operations record")
    journal = write_block.read_journal(state)
    if journal.state is not write_block.State.BLOCKED or journal.owner != owner or journal.owner_lock != lock.token:
        raise WalShutdownError("C10 needs the verified write block of its own operation, held by this lock")
    try:
        if write_block.Database(install).allows_connections():
            raise WalShutdownError("the application database still accepts connections")
    except write_block.WriteBlockError as exc:
        raise WalShutdownError(f"the write block cannot be confirmed in PostgreSQL: {exc}") from exc
    for orphan in tasklock.reconcile(state, lock):
        log.warning("cancel_request_orphan_reconciled", extra={"request": orphan.request_id,
                                                                "operation": orphan.operation})  # fmt: skip
    live = [r for r in tasklock.cancel_requests(state) if r.live]
    if live:
        raise WalShutdownError(f"operation {live[0].operation} asked the backup tasks to stop: not starting")
    heavy = tasklock.backup_lock(state)
    if not heavy.try_acquire():
        raise WalShutdownError(f"a heavy backup task is running ({tasklock.describe_running(state)})")
    return heavy


def _restrict(folder: Path) -> None:
    """Administrators and SYSTEM only (the policy of C3 for ``state\\wal-shutdown``): it holds WAL."""
    if sys.platform != "win32":
        folder.chmod(0o700)
        return
    from districom_ops import ntfs, permissions

    ntfs.write(folder, True, permissions.Rule(folder, protected=True).aces(True))


def target_for(install: Install, diagnostics: Path) -> Target:
    from districom_ops import backup_tasks, database, winservice
    from districom_ops.winservice import State

    try:
        settings = backup_tasks.TaskSettings.read(install.tasks_settings)
    except backup_tasks.TaskSettingsError as exc:
        raise WalShutdownError("the backup tasks are not installed (config\\tasks.json): run tasks install") from exc
    remote, remote_problem = None, None
    if not settings.offsite_remote or settings.rclone is None:
        remote_problem = "repo2 is not configured (OFFSITE_REMOTE and rclone)"
    else:
        try:
            password = backup_tasks.read_secret(settings.offsite_secret)
            remote = CryptRemote(settings.offsite_remote, password, str(settings.rclone))
        except backup_tasks.TaskFailedError as exc:
            remote_problem = f"repo2's key cannot be read: {exc}"

    def stop() -> None:
        winservice.stop(database.PG_SERVICE)
        winservice.wait_for(database.PG_SERVICE, {State.STOPPED}, STOP_SECONDS)

    return Target(
        pg_bin=install.pg_bin("pg_ctl").parent,
        data=install.pg_data,
        port=install.database[3],
        repo1=settings.repo,
        diagnostics=diagnostics,
        stop=stop,
        remote=remote,
        remote_problem=remote_problem,
        password=database.superuser_password(install),
    )


def run(install: Install, lock: OperationLock, owner: str, hooks: Hooks | None = None) -> Result:
    """C10 in an installation, by the owner of the window and of the block. Raises :class:`WalShutdownError` when it
    does not start (nothing changed); otherwise returns the result, PASS or FAIL, with PostgreSQL stopped."""
    from districom_ops import write_block

    state = install.layout.state
    heavy = preconditions(install, lock, owner)
    oplog = write_block._record(install)
    try:
        running = {"task": TASK, "run_id": owner, "started_at": _now(), "pid": os.getpid()}
        write_atomic(tasklock.tasks_dir(state) / tasklock.RUNNING, json.dumps(running).encode("utf-8"))
        folder = state / DIAGNOSTICS
        folder.mkdir(parents=True, exist_ok=True)
        _restrict(folder)
        diagnostics = folder / f"{owner}-{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
        target = target_for(install, diagnostics)
        oplog.step(owner, "wal_shutdown_started")
        log.info("wal_shutdown_started", extra={"operation": owner})
        result = verify(target, owner, hooks)
        failed = [name for name, check in result.checks.items() if not check.get("ok")]
        oplog.step(owner, "wal_shutdown_finished", verdict=result.verdict, failed=",".join(failed))
        (log.info if result.passed else log.error)(
            "wal_shutdown_finished", extra={"operation": owner, "verdict": result.verdict, "failed": failed,
                                            "problems": [p[:200] for p in result.problems()][:20]})  # fmt: skip
        return result
    finally:
        with contextlib.suppress(OSError):
            running_now = tasklock.running_task(state) or {}
            if running_now.get("task") == TASK and running_now.get("run_id") == owner:
                (tasklock.tasks_dir(state) / tasklock.RUNNING).unlink(missing_ok=True)
        heavy.release()


def trial(install: Install, reason: str, cancel_wait_seconds: float, hooks: Hooks | None = None) -> Result:
    """``districom-ops wal verify-shutdown``: C10 inside a window and a block of its own operation, then everything
    back as it was (PostgreSQL started again, the block reverted, the window closed as reverted). It never shuts
    Windows down and never repairs a FAIL: the diagnostics stay. If PostgreSQL does not start again, the block and
    the window stay (the safe side) until an administrator resolves them (``resume``)."""
    from districom_ops import services, write_block
    from districom_ops.maintenance import open_stopping_tasks
    from districom_ops.oplog import Event, Kind

    window = write_block._window(install)
    owner = open_stopping_tasks(window, Kind.MANUAL, reason, cancel_wait_seconds)
    lock = services.operations_lock(install, "wal_verify_shutdown").acquire(timeout=services.LOCK_SECONDS)
    try:
        try:
            write_block.block(install, lock, owner)
        except write_block.WriteBlockError:
            window.close(owner, Event.REVERTED, lock=lock)
            raise
        try:
            return run(install, lock, owner, hooks)
        finally:
            services.start_chain(install, until=services.PG)
            write_block.revert(install, lock, owner)
            window.close(owner, Event.REVERTED, lock=lock)
    finally:
        lock.release()
