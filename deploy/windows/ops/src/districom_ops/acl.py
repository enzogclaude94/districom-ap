"""Minimal NTFS grants the services need to start (W2-C2). The definitive permissions are C3's.

Accounts are always given as SIDs (``*S-1-…``), so nothing depends on the language of Windows.
"""

import sys
from pathlib import Path

from districom_ops.winservice import ServiceError, run

ADMINISTRATORS = "S-1-5-32-544"
SYSTEM = "S-1-5-18"


def _icacls(*args: str) -> None:
    if sys.platform != "win32":
        return  # unit tests outside Windows: permissions are a Windows matter
    result = run(["icacls.exe", *args])
    if result.returncode != 0:
        raise ServiceError(f"icacls {args[0]} failed with {result.returncode}")


def grant(path: Path, sid: str, rights: str) -> None:
    """``rights``: R (read), M (modify) or F (full). Inherited by everything below a folder."""
    inherit = "(OI)(CI)" if path.is_dir() else ""
    _icacls(str(path), "/grant", f"*{sid}:{inherit}{rights}", "/T", "/C", "/Q")


def restrict(path: Path, readers: list[str]) -> None:
    """A secret file: no inherited access; administrators and SYSTEM in full, ``readers`` read only."""
    grants = [f"*{ADMINISTRATORS}:F", f"*{SYSTEM}:F", *(f"*{sid}:R" for sid in readers)]
    _icacls(str(path), "/inheritance:r", "/grant:r", *grants, "/Q")
