"""WAL file names, LSNs and timeline history files.

A segment name is ``TTTTTTTTXXXXXXXXYYYYYYYY``: timeline, then the segment number split in a
"log id" and the segment inside it. How many segments fit in a log id depends on the cluster's
``wal_segment_size``, so every conversion takes it explicitly.
"""

import re
from dataclasses import dataclass

DEFAULT_SEGMENT_SIZE = 16 * 1024 * 1024
_SEGMENT_RE = re.compile(r"^([0-9A-F]{8})([0-9A-F]{8})([0-9A-F]{8})$")
_PARTIAL_RE = re.compile(r"^[0-9A-F]{24}\.partial$")
_BACKUP_RE = re.compile(r"^([0-9A-F]{24})\.[0-9A-F]{8}\.backup$")
_HISTORY_RE = re.compile(r"^([0-9A-F]{8})\.history$")
_LSN_RE = re.compile(r"^([0-9A-Fa-f]{1,8})/([0-9A-Fa-f]{1,8})$")


@dataclass(frozen=True, order=True)
class Segment:
    """A WAL segment: its timeline and its absolute segment number."""

    timeline: int
    segno: int


@dataclass(frozen=True)
class HistoryEntry:
    """One line of a ``.history`` file: ``timeline`` was followed until ``switch_lsn``."""

    timeline: int
    switch_lsn: int


def segments_per_log_id(segment_size: int) -> int:
    if segment_size <= 0 or 0x100000000 % segment_size:
        raise ValueError(f"invalid wal_segment_size: {segment_size}")
    return 0x100000000 // segment_size


def is_segment_name(name: str) -> bool:
    return _SEGMENT_RE.match(name) is not None


def is_partial_name(name: str) -> bool:
    return _PARTIAL_RE.match(name) is not None


def is_history_name(name: str) -> bool:
    return _HISTORY_RE.match(name) is not None


def backup_file_segment(name: str) -> str | None:
    """Segment name a ``.backup`` history file belongs to, or ``None``."""
    match = _BACKUP_RE.match(name)
    return match.group(1) if match else None


def history_timeline(name: str) -> int | None:
    match = _HISTORY_RE.match(name)
    return int(match.group(1), 16) if match else None


def parse_segment(name: str, segment_size: int = DEFAULT_SEGMENT_SIZE) -> Segment:
    match = _SEGMENT_RE.match(name)
    if match is None:
        raise ValueError(f"not a WAL segment name: {name!r}")
    timeline, log_id, seg = (int(part, 16) for part in match.groups())
    per_log_id = segments_per_log_id(segment_size)
    if seg >= per_log_id:
        raise ValueError(f"segment {name!r} out of range for wal_segment_size {segment_size}")
    return Segment(timeline, log_id * per_log_id + seg)


def segment_name(segment: Segment, segment_size: int = DEFAULT_SEGMENT_SIZE) -> str:
    per_log_id = segments_per_log_id(segment_size)
    log_id, seg = divmod(segment.segno, per_log_id)
    return f"{segment.timeline:08X}{log_id:08X}{seg:08X}"


def parse_lsn(text: str) -> int:
    match = _LSN_RE.match(text.strip())
    if match is None:
        raise ValueError(f"not an LSN: {text!r}")
    return (int(match.group(1), 16) << 32) | int(match.group(2), 16)


def format_lsn(lsn: int) -> str:
    return f"{lsn >> 32:X}/{lsn & 0xFFFFFFFF:X}"


def segno_of_lsn(lsn: int, segment_size: int = DEFAULT_SEGMENT_SIZE) -> int:
    return lsn // segment_size


def parse_history(text: str) -> list[HistoryEntry]:
    """Parse a timeline history file: ``<parent tli> <switch LSN> <reason>`` per line, ``#`` comments."""
    entries: list[HistoryEntry] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = line.split()
        if len(fields) < 2:
            raise ValueError(f"malformed history line: {raw!r}")
        entries.append(HistoryEntry(int(fields[0]), parse_lsn(fields[1])))
    return entries
