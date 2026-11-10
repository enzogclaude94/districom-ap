"""Integrity of the installed executables (W2-C3): runtimes, service wrappers and the release in use.

``record`` writes the SHA-256 of every file to ``config\\integrity.json`` (administrators only, C3). ``verify``
compares them again. The permissions are what stops a change; this is how a change made anyway (by an
administrator, by a tool running as one, or on disk) is seen. Paths are reported relative to the root, never
contents. Python bytecode caches and links are skipped: they are not installed files.
"""

import hashlib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

from districom_ops import ntfs
from districom_ops.install import Install

SKIPPED_DIRS = {"__pycache__"}
SKIPPED_SUFFIXES = {".pyc"}


@dataclass(frozen=True)
class Report:
    files: int
    changed: list[str]
    missing: list[str]
    added: list[str]

    @property
    def ok(self) -> bool:
        return not (self.changed or self.missing or self.added)


def trees(install: Install) -> list[Path]:
    found = [install.runtime, install.service_dir]
    if install.release:
        found.append(install.release_dir())
    return found


def _files(tree: Path) -> Iterator[Path]:
    stack = [tree]
    while stack:
        folder = stack.pop()
        try:
            entries = sorted(folder.iterdir())
        except (FileNotFoundError, NotADirectoryError):
            continue
        for entry in entries:
            if ntfs.is_link(entry):
                continue
            if entry.is_dir():
                if entry.name not in SKIPPED_DIRS:
                    stack.append(entry)
            elif entry.suffix not in SKIPPED_SUFFIXES:
                yield entry


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def measure(install: Install) -> dict[str, str]:
    result: dict[str, str] = {}
    for tree in trees(install):
        for path in _files(tree):
            result[str(path.relative_to(install.root))] = _digest(path)
    return result


def record(install: Install) -> bool:
    """Returns False when the record already matches (nothing written)."""
    content = json.dumps({"release": install.release, "files": measure(install)}, indent=0, sort_keys=True)
    target = install.integrity_record
    if target.exists() and target.read_text("utf-8") == content:
        return False
    target.write_text(content, encoding="utf-8")  # keeps the permissions of an existing file
    return True


def verify(install: Install) -> Report:
    if not install.integrity_record.exists():
        return Report(0, [], ["(no record: run services install)"], [])
    recorded: dict[str, str] = json.loads(install.integrity_record.read_text("utf-8"))["files"]
    current = measure(install)
    changed = sorted(p for p in recorded.keys() & current.keys() if recorded[p] != current[p])
    missing = sorted(recorded.keys() - current.keys())
    return Report(len(current), changed, missing, sorted(current.keys() - recorded.keys()))
