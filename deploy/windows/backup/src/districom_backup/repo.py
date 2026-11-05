"""Layout of a backup repository and durable file writes.

```
<repo>/
  .districom-backup-repo     marker: the repository is prepared and mounted
  wal/                       archived WAL segments, .history and .backup files
  base/<label>/              verified base backups (plain format, with backup_manifest)
  base/<label>.json          metadata of each base backup
  manifests/                 content manifests written before each off-site copy
```

A missing marker means the backup disk is not there: every command refuses to write, so
PostgreSQL keeps the WAL and retries instead of archiving into an empty folder on the system disk.
"""

import hashlib
import os
import shutil
import sys
import time
from collections.abc import Callable
from pathlib import Path

MARKER = ".districom-backup-repo"
WAL_DIR = "wal"
BASE_DIR = "base"
MANIFEST_DIR = "manifests"
TMP_PREFIX = ".tmp-"
_CHUNK = 1024 * 1024


class RepoUnavailableError(RuntimeError):
    """The repository marker is missing: the backup disk is absent or not prepared."""


def init_repo(repo: Path) -> Path:
    """Prepare an empty repository (the equivalent of ``backup-disk-init``)."""
    repo.mkdir(parents=True, exist_ok=True)
    for sub in (WAL_DIR, BASE_DIR, MANIFEST_DIR):
        (repo / sub).mkdir(exist_ok=True)
    marker = repo / MARKER
    if not marker.exists():
        write_atomic(marker, b"districom backup repository\n")
    return repo


def require_repo(repo: Path) -> Path:
    if not (repo / MARKER).is_file():
        raise RepoUnavailableError(f"backup repository not available (missing {MARKER}): {repo}")
    return repo


def wal_dir(repo: Path) -> Path:
    return require_repo(repo) / WAL_DIR


def base_dir(repo: Path) -> Path:
    return require_repo(repo) / BASE_DIR


def manifest_dir(repo: Path) -> Path:
    return require_repo(repo) / MANIFEST_DIR


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def same_content(a: Path, b: Path) -> bool:
    if a.stat().st_size != b.stat().st_size:
        return False
    with a.open("rb") as fa, b.open("rb") as fb:
        while True:
            ca, cb = fa.read(_CHUNK), fb.read(_CHUNK)
            if ca != cb:
                return False
            if not ca:
                return True


def fsync_dir(directory: Path) -> None:
    """Persist a rename. Windows has no directory handles to fsync; NTFS journals the rename."""
    if sys.platform == "win32":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def replace_with_retry(
    src: Path,
    dst: Path,
    *,
    attempts: int = 20,
    delay: float = 0.25,
    replace: Callable[[Path, Path], None] | None = None,
) -> None:
    """Rename, retrying while another process holds a file open (antivirus, indexer, backup agent).

    On Windows a rename fails with a sharing violation (``PermissionError``) while any process has
    the file open without ``FILE_SHARE_DELETE``. The error is raised after the last attempt.
    """
    do_replace = replace or os.replace  # looked up per call, so a test can simulate a locked file
    for attempt in range(attempts):
        try:
            do_replace(src, dst)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)


def copy_durable(src: Path, dst: Path, *, replace: Callable[[Path, Path], None] | None = None) -> None:
    """Copy ``src`` to ``dst`` through a temporary name: ``dst`` is either absent or complete."""
    tmp = dst.with_name(f"{TMP_PREFIX}{dst.name}.{os.getpid()}")
    try:
        with src.open("rb") as fin, tmp.open("xb") as fout:
            shutil.copyfileobj(fin, fout, _CHUNK)
            fout.flush()
            os.fsync(fout.fileno())
        replace_with_retry(tmp, dst, replace=replace)
        fsync_dir(dst.parent)
    finally:
        tmp.unlink(missing_ok=True)


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{TMP_PREFIX}{path.name}.{os.getpid()}")
    try:
        with tmp.open("xb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        replace_with_retry(tmp, path)
        fsync_dir(path.parent)
    finally:
        tmp.unlink(missing_ok=True)
