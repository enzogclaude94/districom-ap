"""Verified base backups with ``pg_basebackup`` and ``pg_verifybackup``.

Plain format with a SHA-256 manifest and the WAL needed for consistency streamed into the backup
(``-X stream``): in PostgreSQL 17 ``pg_verifybackup`` only verifies the plain format, and a backup
that carries its own WAL is restorable even if the archive is damaged later. The backup is written
under a temporary name and only renamed into the repository once verified.
"""

import json
import re
import shutil
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from districom_backup import pgtools
from districom_backup.repo import TMP_PREFIX, base_dir, write_atomic
from districom_backup.walnames import Segment, parse_segment

_START_RE = re.compile(r"^START WAL LOCATION: (\S+) \(file ([0-9A-F]{24})\)$", re.MULTILINE)
_TIMELINE_RE = re.compile(r"^START TIMELINE: (\d+)$", re.MULTILINE)
_SEGSIZE_RE = re.compile(r"^Bytes per WAL segment:\s+(\d+)$", re.MULTILINE)


class BackupVerificationError(RuntimeError):
    """``pg_verifybackup`` rejected the backup."""


@dataclass(frozen=True)
class BaseBackupInfo:
    label: str
    start_lsn: str
    start_wal: str
    timeline: int
    wal_segment_size: int
    finished_at: str

    def path(self, repo: Path) -> Path:
        return base_dir(repo) / self.label

    def start_segment(self) -> Segment:
        return parse_segment(self.start_wal, self.wal_segment_size)


def new_label() -> str:
    return datetime.now(UTC).strftime("base-%Y%m%dT%H%M%S%fZ")


def _verify_or_raise(path: Path, bin_dir: Path | None) -> None:
    result = subprocess.run(
        [pgtools.tool("pg_verifybackup", bin_dir), str(path)], capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise BackupVerificationError(result.stderr.strip() or result.stdout.strip())


def is_valid_backup(path: Path, *, bin_dir: Path | None = None) -> tuple[bool, str]:
    """Run ``pg_verifybackup``; return whether it passed and its message."""
    try:
        _verify_or_raise(path, bin_dir)
    except BackupVerificationError as error:
        return False, str(error)
    return True, "backup successfully verified"


def take_base_backup(
    repo: Path,
    *,
    port: int,
    user: str = "postgres",
    host: str = "127.0.0.1",
    label: str | None = None,
    bin_dir: Path | None = None,
) -> BaseBackupInfo:
    label = label or new_label()
    target = base_dir(repo) / label
    tmp = base_dir(repo) / f"{TMP_PREFIX}{label}"
    if target.exists() or tmp.exists():
        raise FileExistsError(f"base backup {label} already exists")
    try:
        pgtools.run(
            [
                pgtools.tool("pg_basebackup", bin_dir),
                "-h",
                host,
                "-p",
                str(port),
                "-U",
                user,
                "-D",
                str(tmp),
                "-F",
                "p",
                "-X",
                "stream",
                "-c",
                "fast",
                "--manifest-checksums=SHA256",
                "-l",
                label,
                "--no-password",
            ],
            timeout=6 * 3600,
        )
        backup_label = (tmp / "backup_label").read_text(encoding="utf-8")
        start = _START_RE.search(backup_label)
        timeline = _TIMELINE_RE.search(backup_label)
        if start is None or timeline is None:
            raise BackupVerificationError("backup_label without START WAL LOCATION or START TIMELINE")
        control = pgtools.run([pgtools.tool("pg_controldata", bin_dir), str(tmp)])
        segsize = _SEGSIZE_RE.search(control)
        if segsize is None:
            raise BackupVerificationError("pg_controldata did not report the WAL segment size")
        _verify_or_raise(tmp, bin_dir)
        info = BaseBackupInfo(
            label=label,
            start_lsn=start.group(1),
            start_wal=start.group(2),
            timeline=int(timeline.group(1)),
            wal_segment_size=int(segsize.group(1)),
            finished_at=datetime.now(UTC).isoformat(),
        )
        tmp.rename(target)
        write_atomic(base_dir(repo) / f"{label}.json", json.dumps(asdict(info), indent=2).encode("utf-8"))
        return info
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)


def read_info(repo: Path, label: str) -> BaseBackupInfo:
    data = json.loads((base_dir(repo) / f"{label}.json").read_text(encoding="utf-8"))
    return BaseBackupInfo(**data)


def list_backups(repo: Path) -> list[BaseBackupInfo]:
    """Base backups with metadata and data, oldest first (labels sort by time)."""
    found: list[BaseBackupInfo] = []
    for meta in sorted(base_dir(repo).glob("*.json")):
        if meta.name.startswith(TMP_PREFIX) or not (meta.parent / meta.stem).is_dir():
            continue
        found.append(read_info(repo, meta.stem))
    return found
