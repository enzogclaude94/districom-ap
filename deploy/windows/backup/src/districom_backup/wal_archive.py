"""``archive_command`` and ``restore_command``.

PostgreSQL calls the archive command once per finished WAL file and keeps the file in ``pg_wal``
until the command exits 0. So the command must exit 0 only when the file is durably in the
repository, and must never overwrite a different file with the same name: that would mean two
servers (or two timelines) writing into one repository, and silently replacing history.
"""

from enum import StrEnum
from pathlib import Path

from districom_backup.repo import copy_durable, same_content, wal_dir


class ArchiveConflictError(RuntimeError):
    """The repository already holds a different file with this name."""


class ArchiveResult(StrEnum):
    ARCHIVED = "archived"
    ALREADY_ARCHIVED = "already-archived"


def archive_wal(source: Path, repo: Path, name: str) -> ArchiveResult:
    """Copy one WAL file (``%p``) into the repository as ``name`` (``%f``)."""
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid WAL file name: {name!r}")
    destination = wal_dir(repo) / name
    if destination.exists():
        if same_content(source, destination):
            return ArchiveResult.ALREADY_ARCHIVED
        raise ArchiveConflictError(f"{name} already archived with different content; refusing to overwrite")
    copy_durable(source, destination)
    return ArchiveResult.ARCHIVED


def restore_wal(repo: Path, name: str, target: Path) -> bool:
    """Copy ``name`` from the repository to ``target`` (``%p``). ``False`` if the repository lacks it.

    PostgreSQL asks for files that may not exist (the next timeline's history, the segment after
    the last one): a missing file is a normal answer, not an error.
    """
    if "/" in name or "\\" in name or name.startswith("."):
        raise ValueError(f"invalid WAL file name: {name!r}")
    source = wal_dir(repo) / name
    if not source.is_file():
        return False
    copy_durable(source, target)
    return True
