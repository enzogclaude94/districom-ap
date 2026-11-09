"""NTFS access control lists, read and written through ``advapi32`` (ctypes: SIDs and masks, never localized text).

Only allow entries are written. Reading returns every entry, so a deny entry or an unknown type is visible to the
checks instead of being ignored. Outside Windows the functions raise: the policy logic above them is tested with a
fake backend.
"""

import os
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

ACCESS_ALLOWED = 0
OBJECT_INHERIT = 0x01
CONTAINER_INHERIT = 0x02
INHERIT_ONLY = 0x08
INHERITED = 0x10

READ = 0x120089  # FILE_GENERIC_READ
READ_EXECUTE = 0x1200A9  # FILE_GENERIC_READ | FILE_GENERIC_EXECUTE
MODIFY = 0x1301BF  # read, write, execute and delete; never change permissions, take ownership or delete children
FULL = 0x1F01FF
WRITE_BITS = 0x000D0156  # write data, append, write EA/attributes, delete, delete child, WRITE_DAC, WRITE_OWNER
GENERIC_ALL, GENERIC_EXECUTE, GENERIC_WRITE, GENERIC_READ = 0x10000000, 0x20000000, 0x40000000, 0x80000000


class NtfsError(Exception):
    pass


@dataclass(frozen=True, order=True)
class Ace:
    sid: str
    mask: int
    flags: int = 0
    type: int = ACCESS_ALLOWED

    @property
    def inherited(self) -> bool:
        return bool(self.flags & INHERITED)

    def explicit(self) -> "Ace":
        return Ace(self.sid, self.mask, self.flags & ~INHERITED, self.type)


@dataclass(frozen=True)
class Dacl:
    protected: bool
    aces: tuple[Ace, ...]
    null: bool = False  # no DACL at all: everyone has full access

    @property
    def explicit(self) -> tuple[Ace, ...]:
        return tuple(a for a in self.aces if not a.inherited)


def generic_to_file(mask: int) -> int:
    """Generic rights as the file rights they mean (inherit-only entries can keep them unmapped)."""
    mapped = mask & ~(GENERIC_ALL | GENERIC_EXECUTE | GENERIC_WRITE | GENERIC_READ)
    if mask & GENERIC_ALL:
        mapped |= FULL
    if mask & GENERIC_READ:
        mapped |= READ
    if mask & GENERIC_EXECUTE:
        mapped |= 0x1200A0
    if mask & GENERIC_WRITE:
        mapped |= 0x120116
    return mapped


def is_link(path: Path) -> bool:
    """A symbolic link or a junction (any reparse point that is a directory link). Never followed."""
    try:
        info = os.lstat(path)
    except OSError:
        return False
    attributes = getattr(info, "st_file_attributes", 0)
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT) or stat.S_ISLNK(info.st_mode)


def link_target(path: Path) -> Path:
    raw = os.readlink(path)
    for prefix in ("\\\\?\\", "\\??\\"):
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
    target = Path(raw)
    return Path(os.path.normpath(target if target.is_absolute() else path.parent / target))


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)

    _GetNamedSecurityInfoW = _advapi.GetNamedSecurityInfoW
    _GetNamedSecurityInfoW.argtypes = [
        wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]  # fmt: skip
    _GetNamedSecurityInfoW.restype = wintypes.DWORD
    _SetNamedSecurityInfoW = _advapi.SetNamedSecurityInfoW
    _SetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p,
    ]  # fmt: skip
    _SetNamedSecurityInfoW.restype = wintypes.DWORD
    _GetSecurityDescriptorControl = _advapi.GetSecurityDescriptorControl
    _GetSecurityDescriptorControl.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)
    ]  # fmt: skip
    _GetSecurityDescriptorControl.restype = wintypes.BOOL
    _GetAce = _advapi.GetAce
    _GetAce.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    _GetAce.restype = wintypes.BOOL
    _ConvertSidToStringSidW = _advapi.ConvertSidToStringSidW
    _ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
    _ConvertSidToStringSidW.restype = wintypes.BOOL
    _ConvertStringSidToSidW = _advapi.ConvertStringSidToSidW
    _ConvertStringSidToSidW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p)]
    _ConvertStringSidToSidW.restype = wintypes.BOOL
    _InitializeAcl = _advapi.InitializeAcl
    _InitializeAcl.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD]
    _InitializeAcl.restype = wintypes.BOOL
    _AddAccessAllowedAceEx = _advapi.AddAccessAllowedAceEx
    _AddAccessAllowedAceEx.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
    _AddAccessAllowedAceEx.restype = wintypes.BOOL
    _GetLengthSid = _advapi.GetLengthSid
    _GetLengthSid.argtypes = [ctypes.c_void_p]
    _GetLengthSid.restype = wintypes.DWORD
    _LocalFree = _kernel.LocalFree
    _LocalFree.argtypes = [ctypes.c_void_p]
    _LocalFree.restype = ctypes.c_void_p

    class _AclHeader(ctypes.Structure):
        _fields_ = [("revision", ctypes.c_ubyte), ("sbz1", ctypes.c_ubyte), ("size", wintypes.WORD),
                    ("count", wintypes.WORD), ("sbz2", wintypes.WORD)]  # fmt: skip

    class _AceHeader(ctypes.Structure):
        _fields_ = [("type", ctypes.c_ubyte), ("flags", ctypes.c_ubyte), ("size", wintypes.WORD),
                    ("mask", wintypes.DWORD)]  # fmt: skip

    SE_FILE_OBJECT = 1
    DACL_SECURITY_INFORMATION = 0x4
    PROTECTED_DACL = 0x80000000
    UNPROTECTED_DACL = 0x20000000
    SE_DACL_PROTECTED = 0x1000
    ACL_REVISION = 2
    _ALLOWED_TYPES = (0, 1)  # allowed, denied: the SID starts right after the mask

    def read(path: Path) -> Dacl:
        dacl, descriptor = ctypes.c_void_p(), ctypes.c_void_p()
        error = _GetNamedSecurityInfoW(
            str(path), SE_FILE_OBJECT, DACL_SECURITY_INFORMATION, None, None, ctypes.byref(dacl), None,
            ctypes.byref(descriptor),
        )  # fmt: skip
        if error:
            raise NtfsError(f"cannot read the permissions of {path.name} (error {error})")
        try:
            control, revision = wintypes.WORD(), wintypes.DWORD()
            if not _GetSecurityDescriptorControl(descriptor, ctypes.byref(control), ctypes.byref(revision)):
                raise NtfsError(f"cannot read the permissions of {path.name} ({ctypes.get_last_error()})")
            protected = bool(control.value & SE_DACL_PROTECTED)
            if not dacl.value:
                return Dacl(protected, (), null=True)
            header = _AclHeader.from_address(dacl.value)
            aces = []
            for index in range(header.count):
                pointer = ctypes.c_void_p()
                if not _GetAce(dacl, index, ctypes.byref(pointer)):
                    raise NtfsError(f"cannot read an entry of {path.name}")
                ace = _AceHeader.from_address(pointer.value or 0)
                sid = "?"
                if ace.type in _ALLOWED_TYPES:
                    text = wintypes.LPWSTR()
                    if _ConvertSidToStringSidW((pointer.value or 0) + 8, ctypes.byref(text)):
                        sid = text.value or "?"
                        _LocalFree(text)
                aces.append(Ace(sid, generic_to_file(ace.mask), ace.flags, ace.type))
            return Dacl(protected, tuple(aces))
        finally:
            _LocalFree(descriptor)

    def write(path: Path, protected: bool, aces: tuple[Ace, ...]) -> None:
        """Replace the explicit entries of ``path``; inherited ones come from the parent unless ``protected``.
        Windows propagates inheritable entries to what is below a folder."""
        sids = []
        try:
            for ace in aces:
                sid = ctypes.c_void_p()
                if not _ConvertStringSidToSidW(ace.sid, ctypes.byref(sid)):
                    raise NtfsError(f"invalid SID {ace.sid}")
                sids.append(sid)
            size = 8 + sum(8 + _GetLengthSid(sid) for sid in sids)
            buffer = ctypes.create_string_buffer(size)
            if not _InitializeAcl(buffer, size, ACL_REVISION):
                raise NtfsError(f"cannot build the permissions of {path.name}")
            for ace, sid in zip(aces, sids, strict=True):
                if not _AddAccessAllowedAceEx(buffer, ACL_REVISION, ace.flags & ~INHERITED, ace.mask, sid):
                    raise NtfsError(f"cannot build the permissions of {path.name}")
            information = DACL_SECURITY_INFORMATION | (PROTECTED_DACL if protected else UNPROTECTED_DACL)
            error = _SetNamedSecurityInfoW(str(path), SE_FILE_OBJECT, information, None, None, buffer, None)
            if error:
                raise NtfsError(f"cannot set the permissions of {path.name} (error {error})")
        finally:
            for sid in sids:
                _LocalFree(sid)

else:

    def read(path: Path) -> Dacl:
        raise NtfsError("NTFS permissions are only available on Windows")

    def write(path: Path, protected: bool, aces: tuple[Ace, ...]) -> None:
        raise NtfsError("NTFS permissions are only available on Windows")
