"""Retention that never removes what a retained base backup needs.

Keeps the newest ``keep`` base backups. Everything a kept backup needs for recovery stays:
its data, every WAL segment from its start segment on (on any timeline) and every ``.history``
file, which are tiny and needed to follow timeline switches. Removed: older base backups, WAL
segments that precede the oldest kept backup's start segment (``pg_archivecleanup``, which
compares segment numbers and ignores the timeline) and their ``.backup`` history files.
"""

import shutil
from dataclasses import dataclass, field
from pathlib import Path

from districom_backup import pgtools
from districom_backup.base_backup import BaseBackupInfo, list_backups
from districom_backup.repo import base_dir, wal_dir
from districom_backup.walnames import backup_file_segment, parse_segment


@dataclass
class RetentionReport:
    kept: list[str] = field(default_factory=list)
    removed_backups: list[str] = field(default_factory=list)
    removed_wal: list[str] = field(default_factory=list)
    oldest_needed_wal: str | None = None


def apply_retention(repo: Path, keep: int, *, bin_dir: Path | None = None) -> RetentionReport:
    if keep < 1:
        raise ValueError("keep at least one base backup")
    backups: list[BaseBackupInfo] = list_backups(repo)
    report = RetentionReport()
    if not backups:
        return report
    kept, removed = backups[-keep:], backups[:-keep]
    report.kept = [b.label for b in kept]
    oldest = kept[0]
    report.oldest_needed_wal = oldest.start_wal
    oldest_segno = oldest.start_segment().segno

    for backup in removed:
        shutil.rmtree(base_dir(repo) / backup.label)
        (base_dir(repo) / f"{backup.label}.json").unlink()
        report.removed_backups.append(backup.label)

    directory = wal_dir(repo)
    before = {p.name for p in directory.iterdir()}
    pgtools.run([pgtools.tool("pg_archivecleanup", bin_dir), str(directory), oldest.start_wal])
    for item in directory.iterdir():
        segment = backup_file_segment(item.name)
        if segment is not None and parse_segment(segment, oldest.wal_segment_size).segno < oldest_segno:
            item.unlink()
    after = {p.name for p in directory.iterdir()}
    report.removed_wal = sorted(before - after)
    if any(name.endswith(".history") for name in report.removed_wal):
        raise RuntimeError("retention removed a timeline history file")
    return report
