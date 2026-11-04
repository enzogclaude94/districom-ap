"""WAL gap detection that follows timelines.

Recovery from a base backup needs every segment from the backup's start segment onwards, along
the history of the timeline being recovered: on each parent timeline up to its switch point, then
on the child from the switch point on. A change of timeline is therefore not a gap, but a missing
``.history`` file is: without it recovery cannot follow the switch.

The check covers the segments between the backup start and the newest segment of the latest
timeline. Whether that newest segment is recent enough is a different question (archive lag),
answered by comparing with the server's current LSN.
"""

from dataclasses import dataclass, field
from pathlib import Path

from districom_backup.walnames import (
    DEFAULT_SEGMENT_SIZE,
    HistoryEntry,
    Segment,
    history_timeline,
    is_segment_name,
    parse_history,
    parse_segment,
    segment_name,
    segno_of_lsn,
)


@dataclass
class GapReport:
    start: str
    latest_timeline: int
    last_segment: str | None
    missing_segments: list[str] = field(default_factory=list)
    missing_history: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_segments or self.missing_history or self.problems)

    def summary(self) -> str:
        if self.ok:
            return f"OK: no WAL gaps from {self.start} to {self.last_segment} (timeline {self.latest_timeline})"
        parts = []
        if self.missing_segments:
            parts.append(f"missing segments: {', '.join(self.missing_segments)}")
        if self.missing_history:
            parts.append(f"missing history files: {', '.join(f'{t:08X}.history' for t in self.missing_history)}")
        parts.extend(self.problems)
        return "WAL GAP: " + "; ".join(parts)


def _timeline_path(latest: int, histories: dict[int, list[HistoryEntry]]) -> list[tuple[int, int | None, int | None]]:
    """``(timeline, begin_lsn, end_lsn)`` from the oldest ancestor to ``latest`` (``None`` = open)."""
    if latest == 1 or latest not in histories:
        return [(latest, None, None)]
    path: list[tuple[int, int | None, int | None]] = []
    begin: int | None = None
    for entry in histories[latest]:
        path.append((entry.timeline, begin, entry.switch_lsn))
        begin = entry.switch_lsn
    path.append((latest, begin, None))
    return path


def find_gaps(wal_dir: Path, start: Segment, segment_size: int = DEFAULT_SEGMENT_SIZE) -> GapReport:
    present: dict[int, set[int]] = {}
    histories: dict[int, list[HistoryEntry]] = {}
    for item in wal_dir.iterdir():
        name = item.name
        if is_segment_name(name):
            segment = parse_segment(name, segment_size)
            present.setdefault(segment.timeline, set()).add(segment.segno)
        elif (tli := history_timeline(name)) is not None:
            histories[tli] = parse_history(item.read_text(encoding="utf-8"))

    timelines = set(present) | set(histories) | {start.timeline}
    latest = max(timelines)
    report = GapReport(start=segment_name(start, segment_size), latest_timeline=latest, last_segment=None)

    report.missing_history = sorted(t for t in timelines if t > 1 and t not in histories)
    for tli, entries in histories.items():
        report.missing_history.extend(
            e.timeline for e in entries if e.timeline > 1 and e.timeline not in histories and e.timeline != tli
        )
    report.missing_history = sorted(set(report.missing_history))
    if latest in report.missing_history:
        report.problems.append(f"cannot follow timeline {latest} without its history file")
        return report

    path = _timeline_path(latest, histories)
    if start.timeline not in {tli for tli, _, _ in path}:
        report.problems.append(f"the backup's timeline {start.timeline} is not an ancestor of timeline {latest}")
        return report

    # Segment ranges of each timeline on the path, clipped to the backup start.
    ranges: list[tuple[int, int, int]] = []
    on_path = False
    for tli, begin_lsn, end_lsn in path:
        if tli == start.timeline:
            on_path = True
        if not on_path:
            continue
        first = start.segno if tli == start.timeline else segno_of_lsn(begin_lsn or 0, segment_size)
        first = max(first, start.segno)
        if end_lsn is None:
            newest = max(present.get(tli, set()), default=None)
            if newest is None:
                continue
            last = newest
        else:
            last = segno_of_lsn(end_lsn - 1, segment_size)
        if last >= first:
            ranges.append((tli, first, last))

    # The segment holding a switch point exists on both timelines: the child's copy starts with the
    # parent's WAL, and recovery tries the newest timeline first. Either copy satisfies it.
    needed: dict[int, list[int]] = {}
    for tli, first, last in ranges:
        for segno in range(first, last + 1):
            needed.setdefault(segno, []).append(tli)
    for segno in sorted(needed):
        candidates = needed[segno]
        if not any(segno in present.get(tli, set()) for tli in candidates):
            report.missing_segments.append(segment_name(Segment(max(candidates), segno), segment_size))
    if ranges:
        tli, _, last = ranges[-1]
        report.last_segment = segment_name(Segment(tli, last), segment_size)
    return report
