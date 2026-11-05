"""archive_command and restore_command without PostgreSQL (A-2, A-3, A-4, A-5)."""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from districom_backup.repo import MARKER, RepoUnavailableError, init_repo
from districom_backup.wal_archive import ArchiveConflictError, ArchiveResult, archive_wal, restore_wal

NAME = "000000010000000000000005"


def _segment(tmp_path: Path, content: bytes = b"\x01" * 65536) -> Path:
    path = tmp_path / "pg_wal" / NAME
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(content)
    return path


def _leftovers(repo: Path) -> list[str]:
    return [p.name for p in (repo / "wal").iterdir() if p.name != NAME]


@pytest.mark.criterion("A-2")
def test_archiving_twice_is_idempotent(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    source = _segment(tmp_path)
    assert archive_wal(source, repo, NAME) is ArchiveResult.ARCHIVED
    stat = (repo / "wal" / NAME).stat()
    assert archive_wal(source, repo, NAME) is ArchiveResult.ALREADY_ARCHIVED
    assert (repo / "wal" / NAME).stat().st_mtime_ns == stat.st_mtime_ns
    assert (repo / "wal" / NAME).read_bytes() == source.read_bytes()
    assert _leftovers(repo) == []


@pytest.mark.criterion("A-3")
def test_different_content_is_refused_and_not_overwritten(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    archive_wal(_segment(tmp_path, b"\x01" * 65536), repo, NAME)
    other = _segment(tmp_path, b"\x01" * 65535 + b"\x02")
    with pytest.raises(ArchiveConflictError):
        archive_wal(other, repo, NAME)
    assert (repo / "wal" / NAME).read_bytes() == b"\x01" * 65536
    assert _leftovers(repo) == []


@pytest.mark.criterion("A-3")
@pytest.mark.criterion("A-2")
def test_command_line_exit_codes(tmp_path: Path) -> None:
    """What PostgreSQL sees: 0 archived or already there, non-zero on conflict (it keeps the WAL)."""
    repo = init_repo(tmp_path / "repo")
    source = _segment(tmp_path)
    cmd = [sys.executable, "-m", "districom_backup", "archive-wal", "--repo", str(repo), str(source), NAME]
    assert subprocess.run(cmd, check=False, capture_output=True).returncode == 0
    assert subprocess.run(cmd, check=False, capture_output=True).returncode == 0
    source.write_bytes(b"\x09" * 65536)
    conflict = subprocess.run(cmd, check=False, capture_output=True, text=True)
    assert conflict.returncode == 2
    assert "different content" in conflict.stderr


@pytest.mark.criterion("A-4")
def test_rename_blocked_by_another_process_is_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulates the Windows sharing violation an antivirus causes while it scans the new file."""
    repo = init_repo(tmp_path / "repo")
    source = _segment(tmp_path)
    real_replace = os.replace
    calls = {"n": 0}

    def locked_twice(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        calls["n"] += 1
        if calls["n"] <= 2:
            raise PermissionError(13, "The process cannot access the file because it is being used by another process")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", locked_twice)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    assert archive_wal(source, repo, NAME) is ArchiveResult.ARCHIVED
    assert calls["n"] == 3
    assert (repo / "wal" / NAME).read_bytes() == source.read_bytes()
    assert _leftovers(repo) == []


@pytest.mark.criterion("A-4")
def test_rename_blocked_for_good_fails_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """If the file stays locked, archiving fails (PostgreSQL retries later) and leaves nothing behind."""
    repo = init_repo(tmp_path / "repo")
    source = _segment(tmp_path)

    def always_locked(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        raise PermissionError(13, "locked")

    monkeypatch.setattr(os, "replace", always_locked)
    monkeypatch.setattr(time, "sleep", lambda _s: None)
    with pytest.raises(PermissionError):
        archive_wal(source, repo, NAME)
    assert not (repo / "wal" / NAME).exists()
    assert _leftovers(repo) == []


@pytest.mark.criterion("A-4")
def test_existing_segment_held_open_by_another_process(tmp_path: Path) -> None:
    """A real second process keeps the archived file open (as a scanner would) while PostgreSQL retries."""
    repo = init_repo(tmp_path / "repo")
    source = _segment(tmp_path)
    archive_wal(source, repo, NAME)
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys,time; f=open(sys.argv[1],'rb'); print('open', flush=True); time.sleep(30)",
            str(repo / "wal" / NAME),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == "open"
        assert archive_wal(source, repo, NAME) is ArchiveResult.ALREADY_ARCHIVED
        other = tmp_path / "other"
        other.write_bytes(b"\x07" * 65536)
        with pytest.raises(ArchiveConflictError):
            archive_wal(other, repo, NAME)
        assert (repo / "wal" / NAME).read_bytes() == source.read_bytes()
    finally:
        holder.kill()
        holder.wait()


@pytest.mark.criterion("A-5")
def test_missing_repository_marker_refuses_to_archive(tmp_path: Path) -> None:
    """Without the prepared backup disk nothing is written: no WAL lands on the system disk by mistake."""
    repo = init_repo(tmp_path / "repo")
    (repo / MARKER).unlink()
    with pytest.raises(RepoUnavailableError):
        archive_wal(_segment(tmp_path), repo, NAME)
    assert list((repo / "wal").iterdir()) == []
    absent = tmp_path / "not-mounted"
    with pytest.raises(RepoUnavailableError):
        archive_wal(_segment(tmp_path), absent, NAME)
    assert not absent.exists()


def test_restore_command(tmp_path: Path) -> None:
    repo = init_repo(tmp_path / "repo")
    source = _segment(tmp_path)
    archive_wal(source, repo, NAME)
    target = tmp_path / "data" / "pg_wal" / "RECOVERYXLOG"
    target.parent.mkdir(parents=True)
    assert restore_wal(repo, NAME, target)
    assert target.read_bytes() == source.read_bytes()
    assert not restore_wal(repo, "00000002.history", tmp_path / "data" / "pg_wal" / "RECOVERYHISTORY")


@pytest.mark.parametrize("name", ["../x", "a/b", "a\\b", ".hidden"])
def test_names_cannot_escape_the_repository(tmp_path: Path, name: str) -> None:
    repo = init_repo(tmp_path / "repo")
    with pytest.raises(ValueError, match="invalid WAL file name"):
        archive_wal(_segment(tmp_path), repo, name)
