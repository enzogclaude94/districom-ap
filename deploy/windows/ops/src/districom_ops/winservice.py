"""Windows Service Control Manager, read and control through ``advapi32`` (ctypes: locale independent).

Creating, changing and deleting services goes through WinSW, ``pg_ctl`` and ``sc.exe``, whose exit codes are
checked; their localized text output is never parsed, except ``sc sdshow`` (an SDDL string, not localized).
"""

import hashlib
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from enum import IntEnum


class ServiceError(Exception):
    pass


class State(IntEnum):
    MISSING = 0
    STOPPED = 1
    START_PENDING = 2
    STOP_PENDING = 3
    RUNNING = 4
    CONTINUE_PENDING = 5
    PAUSE_PENDING = 6
    PAUSED = 7


class StartType(IntEnum):
    AUTO = 2
    DEMAND = 3
    DISABLED = 4


@dataclass(frozen=True)
class Status:
    state: State
    pid: int
    exit_code: int


@dataclass(frozen=True)
class Config:
    binary_path: str
    start_type: int
    dependencies: tuple[str, ...]
    account: str
    display_name: str


def service_sid(name: str) -> str:
    """SID of the virtual account ``NT SERVICE\\<name>``: S-1-5-80 and the SHA-1 of the upper-cased name in
    UTF-16LE, as five little-endian sub-authorities (the same value ``sc showsid`` prints)."""
    digest = hashlib.sha1(name.upper().encode("utf-16-le"), usedforsecurity=False).digest()
    return "S-1-5-80-" + "-".join(str(n) for n in struct.unpack("<5I", digest))


def account(name: str) -> str:
    return f"NT SERVICE\\{name}"


def run(args: list[str], timeout: float = 120) -> subprocess.CompletedProcess[str]:
    """Short-lived tools only (sc, WinSW install, pg_ctl register): their output can safely go to a pipe."""
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603


def sc(*args: str) -> None:
    result = run(["sc.exe", *args])
    if result.returncode != 0:
        raise ServiceError(f"sc {args[0]} {args[1] if len(args) > 1 else ''} failed with {result.returncode}")


def grant_control(name: str, sid: str) -> bool:
    """Let ``sid`` query, start and stop service ``name`` (and nothing else). Returns False if it already could."""
    result = run(["sc.exe", "sdshow", name])
    sddl = "".join(result.stdout.split())
    if result.returncode != 0 or not sddl.startswith("D:"):
        raise ServiceError(f"cannot read the security descriptor of {name}")
    ace = f"(A;;CCLCSWRPWPLORC;;;{sid})"
    if ace in sddl:
        return False
    dacl, sep, sacl = sddl.partition("S:")
    sc("sdset", name, dacl + ace + sep + sacl)
    return True


if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)

    class _StatusProcess(ctypes.Structure):
        _fields_ = [(f, wintypes.DWORD) for f in (
            "type", "state", "accepted", "exit_code", "specific_exit_code", "checkpoint", "wait_hint", "pid", "flags"
        )]  # fmt: skip

    class _ServiceStatus(ctypes.Structure):
        _fields_ = [(f, wintypes.DWORD) for f in (
            "type", "state", "accepted", "exit_code", "specific_exit_code", "checkpoint", "wait_hint"
        )]  # fmt: skip

    class _QueryConfig(ctypes.Structure):
        _fields_ = [
            ("type", wintypes.DWORD),
            ("start_type", wintypes.DWORD),
            ("error_control", wintypes.DWORD),
            ("binary_path", ctypes.c_void_p),
            ("load_order_group", ctypes.c_void_p),
            ("tag", wintypes.DWORD),
            ("dependencies", ctypes.c_void_p),
            ("start_name", ctypes.c_void_p),
            ("display_name", ctypes.c_void_p),
        ]

    _OpenSCManagerW = _advapi.OpenSCManagerW
    _OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _OpenSCManagerW.restype = wintypes.HANDLE
    _OpenServiceW = _advapi.OpenServiceW
    _OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    _OpenServiceW.restype = wintypes.HANDLE
    _CloseServiceHandle = _advapi.CloseServiceHandle
    _CloseServiceHandle.argtypes = [wintypes.HANDLE]
    _QueryServiceStatusEx = _advapi.QueryServiceStatusEx
    _QueryServiceStatusEx.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)
    ]  # fmt: skip
    _QueryServiceStatusEx.restype = wintypes.BOOL
    _QueryServiceConfigW = _advapi.QueryServiceConfigW
    _QueryServiceConfigW.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    _QueryServiceConfigW.restype = wintypes.BOOL
    _StartServiceW = _advapi.StartServiceW
    _StartServiceW.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p]
    _StartServiceW.restype = wintypes.BOOL
    _ControlService = _advapi.ControlService
    _ControlService.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(_ServiceStatus)]
    _ControlService.restype = wintypes.BOOL

    SC_MANAGER_CONNECT = 0x0001
    SERVICE_QUERY_CONFIG = 0x0001
    SERVICE_QUERY_STATUS = 0x0004
    SERVICE_START = 0x0010
    SERVICE_STOP = 0x0020
    ERROR_SERVICE_DOES_NOT_EXIST = 1060
    ERROR_SERVICE_ALREADY_RUNNING = 1056
    ERROR_SERVICE_NOT_ACTIVE = 1062
    ERROR_INSUFFICIENT_BUFFER = 122

    class _Handle:
        def __init__(self, name: str, access: int) -> None:
            self.scm = _OpenSCManagerW(None, None, SC_MANAGER_CONNECT)
            if not self.scm:
                raise ServiceError(f"cannot open the service manager ({ctypes.get_last_error()})")
            self.handle = _OpenServiceW(self.scm, name, access)
            self.error = 0 if self.handle else ctypes.get_last_error()

        def __enter__(self) -> "_Handle":
            return self

        def __exit__(self, *exc: object) -> None:
            if self.handle:
                _CloseServiceHandle(self.handle)
            _CloseServiceHandle(self.scm)

    def status(name: str) -> Status:
        with _Handle(name, SERVICE_QUERY_STATUS) as h:
            if h.error == ERROR_SERVICE_DOES_NOT_EXIST:
                return Status(State.MISSING, 0, 0)
            if not h.handle:
                raise ServiceError(f"cannot open service {name} ({h.error})")
            info = _StatusProcess()
            needed = wintypes.DWORD()
            if not _QueryServiceStatusEx(h.handle, 0, ctypes.byref(info), ctypes.sizeof(info), ctypes.byref(needed)):
                raise ServiceError(f"cannot query service {name} ({ctypes.get_last_error()})")
            return Status(State(info.state), int(info.pid), int(info.exit_code))

    def _wide(pointer: int | None) -> str:
        return ctypes.wstring_at(pointer) if pointer else ""

    def _multi(pointer: int | None) -> tuple[str, ...]:
        items: list[str] = []
        while pointer:
            item = ctypes.wstring_at(pointer)
            if not item:
                break
            items.append(item)
            pointer += (len(item) + 1) * ctypes.sizeof(ctypes.c_wchar)
        return tuple(items)

    def config(name: str) -> Config | None:
        with _Handle(name, SERVICE_QUERY_CONFIG) as h:
            if h.error == ERROR_SERVICE_DOES_NOT_EXIST:
                return None
            if not h.handle:
                raise ServiceError(f"cannot open service {name} ({h.error})")
            needed = wintypes.DWORD()
            _QueryServiceConfigW(h.handle, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            if not _QueryServiceConfigW(h.handle, buffer, needed, ctypes.byref(needed)):
                raise ServiceError(f"cannot read the configuration of {name} ({ctypes.get_last_error()})")
            info = _QueryConfig.from_buffer(buffer)
            return Config(
                binary_path=_wide(info.binary_path),
                start_type=int(info.start_type),
                dependencies=_multi(info.dependencies),
                account=_wide(info.start_name),
                display_name=_wide(info.display_name),
            )

    def start(name: str) -> None:
        with _Handle(name, SERVICE_START) as h:
            if not h.handle:
                raise ServiceError(f"cannot open service {name} to start it ({h.error})")
            if not _StartServiceW(h.handle, 0, None):
                error = ctypes.get_last_error()
                if error != ERROR_SERVICE_ALREADY_RUNNING:
                    raise ServiceError(f"service {name} did not start (error {error})")

    def stop(name: str) -> None:
        with _Handle(name, SERVICE_STOP) as h:
            if h.error == ERROR_SERVICE_DOES_NOT_EXIST:
                return
            if not h.handle:
                raise ServiceError(f"cannot open service {name} to stop it ({h.error})")
            info = _ServiceStatus()
            if not _ControlService(h.handle, 1, ctypes.byref(info)):
                error = ctypes.get_last_error()
                if error != ERROR_SERVICE_NOT_ACTIVE:
                    raise ServiceError(f"service {name} did not accept the stop (error {error})")

else:

    def _unsupported(name: str) -> ServiceError:
        return ServiceError(f"Windows services are only available on Windows ({name})")

    def status(name: str) -> Status:
        raise _unsupported(name)

    def config(name: str) -> Config | None:
        raise _unsupported(name)

    def start(name: str) -> None:
        raise _unsupported(name)

    def stop(name: str) -> None:
        raise _unsupported(name)


def wait_for(name: str, wanted: set[State], seconds: float) -> Status:
    deadline = time.monotonic() + seconds
    while True:
        current = status(name)
        if current.state in wanted:
            return current
        if time.monotonic() >= deadline:
            raise ServiceError(f"service {name} is {current.state.name}, not {'/'.join(s.name for s in wanted)}")
        time.sleep(0.5)
