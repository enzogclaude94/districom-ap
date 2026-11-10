"""Backup disk identity (``.districom-backup-disk`` marker), checked before PostgreSQL may start.

A name, a drive letter or a marker file alone is not proof of identity: a marker can be copied to another disk
and another disk can get the same letter. The check therefore compares three things, and any doubt is a refusal:

1. the marker exists at the configured root, is readable and carries the configured ``BACKUP_DISK_ID`` (a random
   id written once by ``prepare``);
2. the **volume** that holds the root is the one recorded in the marker when it was prepared (on Windows the
   volume GUID ``\\\\?\\Volume{…}\\`` from the mount manager). A marker copied to another disk, or another disk
   given the same letter, fails here;
3. that volume is neither the system volume nor the volume of the PostgreSQL data, so backups can never land on
   the disk they protect.

What this does not prove is the physical serial number of the drive (a sector-by-sector clone keeps the volume
GUID). Reading it needs administrator rights, so it is recorded for the laboratory checks, not enforced at start.
"""

import json
import os
import re
import sys
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from districom_backup.repo import write_atomic

MARKER = ".districom-backup-disk"
DISK_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")


class BackupDiskError(Exception):
    pass


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _GetVolumePathNameW = _kernel32.GetVolumePathNameW
    _GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _GetVolumePathNameW.restype = wintypes.BOOL
    _GetVolumeNameForVolumeMountPointW = _kernel32.GetVolumeNameForVolumeMountPointW
    _GetVolumeNameForVolumeMountPointW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    _GetVolumeNameForVolumeMountPointW.restype = wintypes.BOOL

    def volume_id(path: Path) -> str:
        """The volume GUID path of the volume that holds ``path``. Raises OSError if it cannot be read."""
        mount = ctypes.create_unicode_buffer(1024)
        if not _GetVolumePathNameW(str(path), mount, len(mount)):
            raise ctypes.WinError(ctypes.get_last_error())
        guid = ctypes.create_unicode_buffer(1024)
        if not _GetVolumeNameForVolumeMountPointW(mount.value, guid, len(guid)):
            raise ctypes.WinError(ctypes.get_last_error())
        return guid.value.lower()

    def system_root() -> Path:
        return Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\")

else:

    def volume_id(path: Path) -> str:
        """Outside Windows (unit tests only): the device number stands for the volume."""
        return f"dev:{os.stat(path).st_dev}"

    def system_root() -> Path:
        return Path("/")


@dataclass(frozen=True)
class DiskCheck:
    ok: bool
    code: str
    detail: str

    def describe(self) -> str:
        return "backup disk ok" if self.ok else f"FAIL backup disk {self.code}: {self.detail}"


def prepare(root: Path, data_dir: Path) -> str:
    """Mark an empty, separate volume as the backup disk once; returns the id to set as ``BACKUP_DISK_ID``."""
    marker = root / MARKER
    if marker.exists():
        raise BackupDiskError("the disk already has a marker: it was prepared before, keep its id")
    if not root.is_dir():
        raise BackupDiskError("the backup disk root does not exist (is the disk connected?)")
    volume = volume_id(root)
    if volume == volume_id(system_root()):
        raise BackupDiskError("the backup disk root is on the system volume")
    if data_dir.exists() and volume == volume_id(data_dir):
        raise BackupDiskError("the backup disk root is on the same volume as the PostgreSQL data")
    disk_id = str(uuid.uuid4())
    data = {"disk_id": disk_id, "volume": volume, "prepared_at": datetime.now(UTC).isoformat(timespec="seconds")}
    write_atomic(marker, json.dumps(data).encode("utf-8"))
    return disk_id


def verify(root: Path, expected_id: str, data_dir: Path) -> DiskCheck:
    """Read only. Every failure says why, without values other than the volume being compared."""
    marker = root / MARKER
    try:
        if not marker.is_file():
            return DiskCheck(False, "missing", "no marker at the configured root (disk absent or not prepared)")
        data = json.loads(marker.read_text("utf-8"))
        volume = volume_id(root)
    except (OSError, ValueError):
        return DiskCheck(False, "unreadable", "the marker or its volume cannot be read")
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("disk_id"), str)
        or not isinstance(data.get("volume"), str)
    ):
        return DiskCheck(False, "unreadable", "the marker has no disk id or volume")
    if data["disk_id"] != expected_id:
        return DiskCheck(False, "identity_mismatch", "the marker belongs to another disk (BACKUP_DISK_ID differs)")
    if data["volume"] != volume:
        return DiskCheck(
            False, "volume_mismatch", "the marker was prepared on another volume (copied marker or another disk)"
        )
    try:
        if volume == volume_id(system_root()):
            return DiskCheck(False, "system_volume", "the backup disk root is on the system volume")
        if data_dir.exists() and volume == volume_id(data_dir):
            return DiskCheck(False, "data_volume", "the backup disk root is on the PostgreSQL data volume")
    except OSError:
        return DiskCheck(False, "unreadable", "the system or data volume cannot be read")
    return DiskCheck(True, "ok", "marker, disk id and volume match")
