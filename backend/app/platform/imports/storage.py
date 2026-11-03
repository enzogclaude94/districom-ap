"""Original files under UPLOAD_DIR (ADR 014 §2.3, docs/IMPORTS.md §9).

A file is copied in chunks while its size is counted and its SHA-256 computed, written under a temporary name,
flushed to disk and renamed to its UUID: a crash never leaves a half-written file under a real name. It is then made
read-only. The app never modifies nor deletes an original; the name the user gave is kept only for display.
"""

import hashlib
import os
import re
import unicodedata
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

CHUNK_BYTES = 1024 * 1024
STORAGE_KEY = re.compile(r"^[0-9a-f]{32}$")
MAX_FILENAME = 255


class FileTooLargeError(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    storage_key: str
    size_bytes: int
    sha256: str


def store_upload(source: BinaryIO, upload_dir: Path, max_bytes: int) -> StoredFile:
    upload_dir.mkdir(parents=True, exist_ok=True)
    key = uuid.uuid4().hex
    partial = upload_dir / f".incoming-{key}"
    digest = hashlib.sha256()
    size = 0
    try:
        with partial.open("xb") as target:
            while chunk := source.read(CHUNK_BYTES):
                size += len(chunk)
                if size > max_bytes:
                    raise FileTooLargeError
                digest.update(chunk)
                target.write(chunk)
            target.flush()
            os.fsync(target.fileno())
        partial.chmod(0o440)
        partial.rename(upload_dir / key)
    except BaseException:
        partial.unlink(missing_ok=True)  # only the temporary copy: an original is never removed
        raise
    return StoredFile(storage_key=key, size_bytes=size, sha256=digest.hexdigest())


def stored_path(upload_dir: Path, storage_key: str) -> Path:
    if not STORAGE_KEY.fullmatch(storage_key):
        raise ValueError("Invalid storage key")
    return upload_dir / storage_key


def display_filename(filename: str | None) -> str:
    """The user's file name without any directory, control characters or excess length; only ever shown."""
    name = unicodedata.normalize("NFC", filename or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C").strip()
    if len(name) > MAX_FILENAME:
        stem, dot, suffix = name.rpartition(".")
        name = (
            (stem[: MAX_FILENAME - len(suffix) - 1] + dot + suffix)
            if dot and len(suffix) <= 10
            else name[:MAX_FILENAME]
        )
    return name or "archivo"
