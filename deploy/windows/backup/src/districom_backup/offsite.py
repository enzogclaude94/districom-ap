"""Encrypted off-site copy of a repository with ``rclone crypt``.

``push`` first writes a content manifest (every file and its SHA-256) into the repository, then
copies the repository with ``rclone copy --immutable``: never ``sync``, so a deletion or an
overwrite in the local repository never propagates, and a changed file is an error.

``pull`` copies everything back into an empty directory and accepts the result only if the newest
manifest decrypts and every file it lists is present with the same hash. A wrong key, a truncated
copy or a tampered file is a clear error, never a silently partial repository.

The crypt remote is defined only through environment variables of the rclone process, so the key
is never written to an rclone config file.

What this module does **not** prove by itself: that the remote is outside the premises, that its
objects are locked (immutability) or how long it keeps them. Those depend on the real bucket.
"""

import json
import os
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from districom_backup.repo import (
    MANIFEST_DIR,
    MARKER,
    TMP_PREFIX,
    manifest_dir,
    require_repo,
    sha256_file,
    write_atomic,
)

REMOTE_NAME = "DISTRICOMOFFSITE"


class OffsiteError(RuntimeError):
    """The off-site copy or its verification failed."""


@dataclass(frozen=True)
class CryptRemote:
    """A crypt remote over ``base`` (a local folder in tests, a bucket path such as ``r2:bucket/repo2`` later)."""

    base: str
    password: str
    rclone: str = "rclone"

    def _env(self) -> dict[str, str]:
        obscured = subprocess.run(
            [self.rclone, "obscure", "-"], input=self.password, capture_output=True, text=True, check=True
        ).stdout.strip()
        prefix = f"RCLONE_CONFIG_{REMOTE_NAME}_"
        return {
            **os.environ,
            f"{prefix}TYPE": "crypt",
            f"{prefix}REMOTE": self.base,
            f"{prefix}PASSWORD": obscured,
            f"{prefix}FILENAME_ENCRYPTION": "standard",
            f"{prefix}DIRECTORY_NAME_ENCRYPTION": "true",
        }

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [self.rclone, *args], capture_output=True, text=True, env=self._env(), check=False, timeout=6 * 3600
        )


def _repo_files(repo: Path) -> list[Path]:
    files = []
    for path in sorted(repo.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(repo)
        if relative.parts[0] == MANIFEST_DIR or any(part.startswith(TMP_PREFIX) for part in relative.parts):
            continue
        files.append(path)
    return files


def write_manifest(repo: Path) -> Path:
    require_repo(repo)
    entries = {path.relative_to(repo).as_posix(): sha256_file(path) for path in _repo_files(repo)}
    # Empty directories matter: a base backup needs pg_notify, pg_replslot, pg_tblspc... to start.
    directories = sorted(
        p.relative_to(repo).as_posix()
        for p in repo.rglob("*")
        if p.is_dir() and not any(part.startswith(TMP_PREFIX) for part in p.relative_to(repo).parts)
    )
    name = datetime.now(UTC).strftime("manifest-%Y%m%dT%H%M%S%fZ.json")
    target = manifest_dir(repo) / name
    document = {"files": entries, "directories": directories}
    write_atomic(target, json.dumps(document, indent=1, sort_keys=True).encode("utf-8"))
    return target


def push(repo: Path, remote: CryptRemote) -> Path:
    manifest = write_manifest(repo)
    result = remote.run(
        "copy",
        "--immutable",
        "--create-empty-src-dirs",
        "--exclude",
        f"{TMP_PREFIX}*",
        "--exclude",
        f"**/{TMP_PREFIX}*/**",
        str(repo),
        f"{REMOTE_NAME}:",
    )
    if result.returncode != 0:
        raise OffsiteError(f"rclone copy failed ({result.returncode}): {result.stderr.strip()[-2000:]}")
    return manifest


def pull(remote: CryptRemote, destination: Path) -> Path:
    """Copy the remote into an empty ``destination`` and verify it against the newest manifest."""
    if destination.exists() and any(destination.iterdir()):
        raise OffsiteError(f"destination is not empty: {destination}")
    destination.mkdir(parents=True, exist_ok=True)
    result = remote.run("copy", "--create-empty-src-dirs", f"{REMOTE_NAME}:", str(destination))
    manifests = (
        sorted((destination / MANIFEST_DIR).glob("manifest-*.json")) if (destination / MANIFEST_DIR).is_dir() else []
    )
    if not manifests:
        raise OffsiteError(
            "no manifest could be read from the remote: wrong key, empty remote or failed copy"
            + (f" (rclone exited {result.returncode})" if result.returncode else "")
        )
    document = json.loads(manifests[-1].read_text(encoding="utf-8"))
    entries: dict[str, str] = document["files"]
    problems = []
    for relative in document.get("directories", []):
        (destination / relative).mkdir(parents=True, exist_ok=True)  # empty directories carry no data
    for relative, digest in entries.items():
        path = destination / relative
        if not path.is_file():
            problems.append(f"missing {relative}")
        elif sha256_file(path) != digest:
            problems.append(f"hash mismatch {relative}")
    if MARKER not in entries:
        problems.append(f"manifest does not list {MARKER}")
    if problems:
        raise OffsiteError(f"off-site copy failed verification: {'; '.join(problems[:20])}")
    return manifests[-1]
