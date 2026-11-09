"""W2-C3 acceptance: the definitive NTFS permissions, measured for real under each identity.

The job runs this after the W2-C2 setup tests (runtimes, backup disk, database, services, migrations), as
administrator on a disposable runner. Effective access is never inferred from the lists: a PowerShell probe tries
each operation under the identity being tested:

- each of the five service accounts, by pointing its service at the probe for a moment (same name, same SID)
  and restoring the original program afterwards;
- another service on the same machine (``NT SERVICE\\C3Outsider``), as Store Sys would be;
- a local standard user created for the test (member of Users), as a person signed in on the server would be.
"""

import ctypes
import json
import os
import secrets
import shutil
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from districom_ops import artifacts, ntfs, permissions, services, winservice
from districom_ops.winservice import State, account, service_sid
from permissions_tests.conftest import matrix, metrics
from services_tests.test_c2_services import CTX, RELEASE, ROOT, cli, disk_root, powershell, wait_until

PROBE = Path(os.environ.get("DISTRICOM_C3_PROBE", r"C:\c3-probe"))
OUTSIDER = "C3Outsider"
USER = "c3probe"
ROLES = {
    "backend": services.BACKEND,
    "frontend": services.FRONTEND,
    "caddy": services.CADDY,
    "postgresql": services.PG,
    "stack": services.STACK,
}
EVERYONE = "S-1-1-0"

pytestmark = pytest.mark.timeout(1800)


# --- identities ---------------------------------------------------------------------------------------------------

if sys.platform == "win32":
    _advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    _kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    _advapi.OpenSCManagerW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD]
    _advapi.OpenSCManagerW.restype = wintypes.HANDLE
    _advapi.OpenServiceW.argtypes = [wintypes.HANDLE, wintypes.LPCWSTR, wintypes.DWORD]
    _advapi.OpenServiceW.restype = wintypes.HANDLE
    _advapi.CloseServiceHandle.argtypes = [wintypes.HANDLE]
    _advapi.ChangeServiceConfigW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
        ctypes.c_void_p, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
    ]  # fmt: skip
    _advapi.ChangeServiceConfigW.restype = wintypes.BOOL
    _advapi.CreateProcessWithLogonW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPWSTR,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p,
    ]  # fmt: skip
    _advapi.CreateProcessWithLogonW.restype = wintypes.BOOL
    _kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _kernel.CloseHandle.argtypes = [wintypes.HANDLE]

    class _StartupInfo(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD), ("reserved", wintypes.LPWSTR), ("desktop", wintypes.LPWSTR),
            ("title", wintypes.LPWSTR), ("x", wintypes.DWORD), ("y", wintypes.DWORD), ("xsize", wintypes.DWORD),
            ("ysize", wintypes.DWORD), ("xchars", wintypes.DWORD), ("ychars", wintypes.DWORD),
            ("fill", wintypes.DWORD), ("flags", wintypes.DWORD), ("show", wintypes.WORD),
            ("reserved2", wintypes.WORD), ("reserved3", ctypes.c_void_p), ("stdin", wintypes.HANDLE),
            ("stdout", wintypes.HANDLE), ("stderr", wintypes.HANDLE),
        ]  # fmt: skip

    class _ProcessInfo(ctypes.Structure):
        _fields_ = [("process", wintypes.HANDLE), ("thread", wintypes.HANDLE), ("pid", wintypes.DWORD),
                    ("tid", wintypes.DWORD)]  # fmt: skip

    def _change_binary_path(name: str, binary_path: str) -> None:
        scm = _advapi.OpenSCManagerW(None, None, 0x0001)
        handle = _advapi.OpenServiceW(scm, name, 0x0002 | 0x0001)  # change and query config
        try:
            no_change = 0xFFFFFFFF
            ok = _advapi.ChangeServiceConfigW(
                handle, no_change, no_change, no_change, binary_path, None, None, None, None, None, None
            )
            assert ok, f"cannot change the program of {name} ({ctypes.get_last_error()})"
        finally:
            _advapi.CloseServiceHandle(handle)
            _advapi.CloseServiceHandle(scm)

    def _run_as_user(user: str, password: str, command: str, cwd: Path) -> None:
        startup = _StartupInfo()
        startup.cb = ctypes.sizeof(startup)
        info = _ProcessInfo()
        created = _advapi.CreateProcessWithLogonW(
            user, ".", password, 1, None, ctypes.create_unicode_buffer(command), 0x08000000, None, str(cwd),
            ctypes.byref(startup), ctypes.byref(info),
        )  # fmt: skip  # LOGON_WITH_PROFILE, CREATE_NO_WINDOW
        assert created, f"cannot start the probe as the local user ({ctypes.get_last_error()})"
        try:
            _kernel.WaitForSingleObject(info.process, 120_000)
        finally:
            _kernel.CloseHandle(info.process)
            _kernel.CloseHandle(info.thread)

    def _junction(target: Path, link: Path) -> None:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))

else:

    def _change_binary_path(name: str, binary_path: str) -> None:
        raise NotImplementedError("Windows only")

    def _run_as_user(user: str, password: str, command: str, cwd: Path) -> None:
        raise NotImplementedError("Windows only")

    def _junction(target: Path, link: Path) -> None:
        raise NotImplementedError("Windows only")


def _grant(path: Path, sid: str, rights: str) -> None:
    """Adds an inheritable allow entry by SID with advapi32 (icacls cannot name a service SID before the service
    exists). Only for the probe's own folders, outside the installation."""
    mask = {"RX": ntfs.READ_EXECUTE, "M": ntfs.MODIFY}[rights]
    current = ntfs.read(path)
    wanted = ntfs.Ace(sid, mask, ntfs.OBJECT_INHERIT | ntfs.CONTAINER_INHERIT)
    if wanted not in current.explicit:
        ntfs.write(path, current.protected, (*current.explicit, wanted))


def _probe_service_files(name: str, checks: Path, out: Path) -> str:
    """A WinSW copy named like the service, that runs the probe; returns the program to point the service at."""
    folder = PROBE / "svc" / name
    folder.mkdir(parents=True, exist_ok=True)
    winsw = artifacts.manifest()["winsw"].path(ROOT / "runtime")
    shutil.copyfile(winsw, folder / f"{name}.exe")
    logs = PROBE / "svclogs" / name
    logs.mkdir(parents=True, exist_ok=True)
    _grant(logs, service_sid(name), "M")
    arguments = subprocess.list2cmdline(
        ["-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(PROBE / "probe.ps1"),
         "-Checks", str(checks), "-Out", str(out)]
    )  # fmt: skip
    (folder / f"{name}.xml").write_text(
        "<service>\n"
        f"  <id>{name}</id><name>{name}</name><description>W2-C3 probe</description>\n"
        "  <executable>powershell.exe</executable>\n"
        f"  <arguments>{arguments}</arguments>\n"
        f"  <logpath>{logs}</logpath>\n"
        "</service>\n",
        encoding="utf-8",
    )
    return str(folder / f"{name}.exe")


def _wait_result(out: Path, seconds: float) -> dict[str, str]:
    wait_until(lambda: out.exists() and out.stat().st_size > 0, seconds, f"the probe wrote no result ({out.name})")
    time.sleep(0.5)
    data: dict[str, str] = json.loads(out.read_text("utf-8-sig"))
    return data


def _out_file(who: str, sid: str) -> Path:
    folder = PROBE / "out" / who
    folder.mkdir(parents=True, exist_ok=True)
    _grant(folder, sid, "M")
    out = folder / "result.json"
    out.unlink(missing_ok=True)
    return out


def probe_as_service(name: str, checks: Path) -> dict[str, str]:
    """Point the service at the probe for one run, under its own account, then restore its program."""
    out = _out_file(name, service_sid(name))
    original = winservice.config(name)
    assert original is not None
    assert winservice.status(name).state is State.STOPPED, f"{name} must be stopped to be probed"
    program = _probe_service_files(name, checks, out)
    _change_binary_path(name, f'"{program}"')
    try:
        winservice.start(name)
        result = _wait_result(out, 120)
        winservice.stop(name)
        winservice.wait_for(name, {State.STOPPED}, 60)
    finally:
        _change_binary_path(name, original.binary_path)
    restored = winservice.config(name)
    assert restored == original, f"{name} was not restored"
    return result


def probe_as_outsider(checks: Path) -> dict[str, str]:
    out = _out_file(OUTSIDER, service_sid(OUTSIDER))
    program = Path(_probe_service_files(OUTSIDER, checks, out))
    if winservice.config(OUTSIDER) is None:
        assert winservice.run([str(program), "install"]).returncode == 0
        winservice.sc("config", OUTSIDER, "obj=", account(OUTSIDER))
    winservice.start(OUTSIDER)
    result = _wait_result(out, 120)
    winservice.stop(OUTSIDER)
    winservice.wait_for(OUTSIDER, {State.STOPPED, State.MISSING}, 60)
    return result


def _user_sid() -> str:
    return powershell(
        f"(New-Object Security.Principal.NTAccount('{USER}')).Translate([Security.Principal.SecurityIdentifier]).Value"
    )


def probe_as_user(checks: Path, password: str) -> dict[str, str]:
    out = _out_file(USER, _user_sid())
    command = subprocess.list2cmdline(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File",
         str(PROBE / "probe.ps1"), "-Checks", str(checks), "-Out", str(out)]
    )  # fmt: skip
    _run_as_user(USER, password, command, PROBE)
    return _wait_result(out, 10)


# --- the expected access --------------------------------------------------------------------------------------


def _one(pattern: str, base: Path) -> Path:
    found = sorted(base.glob(pattern))
    assert found, f"{pattern} not found"
    return found[0]


def checks() -> list[tuple[str, Path, str, set[str]]]:
    """(id, path, operation, roles that must be allowed). Every other identity must be denied."""
    install = CTX.install()
    release = ROOT / "releases" / RELEASE
    runtime = ROOT / "runtime"
    manifest = artifacts.manifest()
    pg_bin = manifest["postgresql"].path(runtime) / "bin"
    rows: list[tuple[str, Path, str, set[str]]] = [
        ("config\\ (listar)", ROOT / "config", "read", set()),
        ("config\\districom.env (leer)", ROOT / "config" / "districom.env", "read", {"stack"}),
        ("config\\districom.env (escribir)", ROOT / "config" / "districom.env", "write", set()),
        ("config\\backend\\.env (leer)", install.backend_env, "read", {"backend"}),
        ("config\\backend\\.env (escribir)", install.backend_env, "write", set()),
        ("config\\Caddyfile (leer)", install.caddyfile, "read", {"caddy"}),
        ("config\\postgres.secret (leer)", install.superuser_secret, "read", set()),
        ("config\\integrity.json (leer)", install.integrity_record, "read", set()),
        ("pgdata\\PG_VERSION (leer)", install.pg_data / "PG_VERSION", "read", {"postgresql"}),
        ("pgdata\\ (crear)", install.pg_data, "create", {"postgresql"}),
        ("pgdata\\ (atributos)", install.pg_data, "attributes", {"postgresql", "stack"}),
        ("releases\\ (atributos)", ROOT / "releases", "attributes", {"backend", "frontend", "stack"}),
        ("releases\\ (listar)", ROOT / "releases", "read", set()),
        ("releases\\…\\backend\\alembic.ini (leer)", release / "backend" / "alembic.ini", "read", {"backend"}),
        ("releases\\…\\backend\\alembic.ini (escribir)", release / "backend" / "alembic.ini", "write", set()),
        ("releases\\…\\backend\\ (crear)", release / "backend", "create", set()),
        ("releases\\…\\backend\\app (crear)", release / "backend" / "app", "create", set()),
        ("releases\\ (crear)", ROOT / "releases", "create", set()),
        ("releases\\…\\frontend\\server.js (leer)", release / "frontend" / "server.js", "read", {"frontend"}),
        ("releases\\…\\frontend\\server.js (escribir)", release / "frontend" / "server.js", "write", set()),
        ("releases\\…\\frontend\\.next\\cache (crear)", release / "frontend" / ".next" / "cache", "create",
         {"frontend"}),
        ("releases\\…\\frontend\\node_modules\\next (crear, junction)",
         release / "frontend" / "node_modules" / "next", "create", set()),
        ("releases\\…\\windows\\ops (leer)", release / "windows" / "ops", "read", {"stack"}),
        ("runtime\\python\\…\\python.exe (leer)", _one("**/python.exe", install.python_runtime), "read",
         {"backend", "stack"}),
        ("runtime\\python\\…\\python.exe (escribir)", _one("**/python.exe", install.python_runtime), "write", set()),
        ("runtime\\postgresql\\…\\postgres.exe (leer)", pg_bin / "postgres.exe", "read", {"postgresql", "stack"}),
        ("runtime\\postgresql\\…\\postgres.exe (escribir)", pg_bin / "postgres.exe", "write", set()),
        ("runtime\\caddy\\caddy.exe (leer)", manifest["caddy"].path(runtime), "read", {"caddy"}),
        ("runtime\\caddy\\caddy.exe (escribir)", manifest["caddy"].path(runtime), "write", set()),
        ("runtime\\node\\node.exe (leer)", manifest["node"].path(runtime) / "node.exe", "read", {"frontend"}),
        ("runtime\\node\\node.exe (escribir)", manifest["node"].path(runtime) / "node.exe", "write", set()),
        ("runtime\\ (crear)", runtime, "create", set()),
        ("services\\DistriComBackend.exe (leer)", ROOT / "services" / "DistriComBackend.exe", "read", {"backend"}),
        ("services\\DistriComBackend.exe (escribir)", ROOT / "services" / "DistriComBackend.exe", "write", set()),
        ("services\\DistriComStack.xml (escribir)", ROOT / "services" / "DistriComStack.xml", "write", set()),
        ("services\\ (crear)", ROOT / "services", "create", set()),
        ("data\\uploads (crear)", install.uploads, "create", {"backend"}),
        ("logs\\ops.log (escribir)", ROOT / "logs" / "ops.log", "write", {"stack"}),
        ("logs\\ (crear)", ROOT / "logs", "create", {"stack"}),
        ("logs\\postgresql (crear)", install.postgres_logs, "create", {"postgresql"}),
        *((f"logs\\services\\{name} (crear)", install.service_log_dir(name), "create", {role})
          for role, name in ROLES.items() if name in services.WINSW),
        ("state\\ (crear)", ROOT / "state", "create", {"stack"}),
        ("state\\caddy (listar)", install.caddy_state, "read", {"caddy"}),
        ("state\\caddy (crear)", install.caddy_state, "create", {"caddy"}),
        ("state\\frontend-cache (crear)", install.frontend_cache, "create", {"frontend"}),
        ("downloads\\ (listar)", ROOT / "downloads", "read", set()),
        ("raíz (crear)", ROOT, "create", set()),
        ("disco de backup: marca (leer)", disk_root() / ".districom-backup-disk", "read", {"stack"}),
        ("disco de backup: marca (escribir)", disk_root() / ".districom-backup-disk", "write", set()),
    ]  # fmt: skip
    return rows


class Results:
    """Measured once (it takes a few minutes), then each criterion checks its part."""

    def __init__(self) -> None:
        self.measured: dict[str, dict[str, str]] = {}
        self.expected: dict[str, set[str]] = {}


def _identity_label(role: str) -> str:
    return {"outsider": "servicio ajeno", "user": "usuario local"}.get(role, role)


@pytest.fixture(scope="module")
def measured() -> Results:
    PROBE.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).parent / "probe.ps1", PROBE / "probe.ps1")
    _grant(PROBE, EVERYONE, "RX")
    rows = checks()
    for _, path, _, _ in rows:
        assert path.exists() or ntfs.is_link(path), f"the check needs {path}"
    spec = PROBE / "checks.json"
    spec.write_text(json.dumps([{"id": i, "path": str(p), "op": op} for i, p, op, _ in rows]), encoding="utf-8")
    results = Results()
    results.expected = {i: allowed for i, _, _, allowed in rows}

    cli("services", "stop")
    password = "Aa1!" + secrets.token_urlsafe(18)
    CTX.secrets.append(password)
    subprocess.run(["net.exe", "user", USER, password, "/add", "/y"], capture_output=True, check=True)  # noqa: S607
    started = time.monotonic()
    try:
        for role, name in ROLES.items():
            results.measured[role] = probe_as_service(name, spec)
        results.measured["outsider"] = probe_as_outsider(spec)
        results.measured["user"] = probe_as_user(spec, password)
    finally:
        subprocess.run(["net.exe", "user", USER, "/delete", "/y"], capture_output=True, check=False)  # noqa: S607
        if winservice.config(OUTSIDER) is not None:
            winservice.run([str(PROBE / "svc" / OUTSIDER / f"{OUTSIDER}.exe"), "uninstall"])
    metrics["sondas bajo 7 identidades (s)"] = round(time.monotonic() - started, 1)
    for check_id, allowed in results.expected.items():
        row = {}
        for role, result in results.measured.items():
            got = result.get(check_id, "absent")
            mark = "✓" if got == "allowed" else "✗" if got == "denied" else got
            if (got == "allowed") != (role in allowed):
                mark += " !"
            row[_identity_label(role)] = mark
        matrix[check_id] = row
    return results


def _unexpected(results: Results, ids: list[str] | None = None) -> list[str]:
    wrong = []
    for check_id, allowed in results.expected.items():
        if ids is not None and check_id not in ids:
            continue
        for role, result in results.measured.items():
            got = result.get(check_id, "absent")
            if got not in ("allowed", "denied") or (got == "allowed") != (role in allowed):
                wrong.append(f"{check_id} as {role}: {got}")
    return wrong


# --- C3-1 -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C3-1")
def test_each_service_runs_with_its_own_virtual_account() -> None:
    raw = powershell(
        "Get-CimInstance Win32_Service -Filter \"Name LIKE 'DistriCom%'\" | "
        "Select-Object Name, StartName | ConvertTo-Json -Compress"
    )
    found = {item["Name"]: item["StartName"] for item in json.loads(raw)}
    assert set(found) == set(services.ALL)
    for name, start_name in found.items():
        assert start_name.lower() == account(name).lower(), (name, start_name)
    metrics["cuentas (Win32_Service)"] = ", ".join(f"{n}={s}" for n, s in sorted(found.items()))


# --- C3-4 -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C3-4")
def test_applying_twice_changes_nothing() -> None:
    install = CTX.install()
    policy = services.permission_policy(install)
    started = time.monotonic()
    before = permissions.snapshot(policy)
    metrics["objetos con ACL leídas"] = len(before)
    metrics["lectura de todas las ACL (s)"] = round(time.monotonic() - started, 1)
    started = time.monotonic()
    changes = json.loads(cli("permissions", "apply").stdout.strip().splitlines()[-1])["changes"]
    metrics["permissions apply sin cambios (s)"] = round(time.monotonic() - started, 1)
    assert changes == []
    assert permissions.snapshot(policy) == before
    second = json.loads(cli("services", "install", "--release", RELEASE).stdout.strip().splitlines()[-1])
    assert second["changes"] == [], second
    assert permissions.snapshot(policy) == before
    assert cli("permissions", "verify", check=False).returncode == 0


@pytest.mark.criterion("C3-4")
def test_stray_grants_are_reported_and_corrected() -> None:
    install = CTX.install()
    policy = services.permission_policy(install)
    before = permissions.snapshot(policy)
    target = install.release_dir() / "backend" / "alembic.ini"
    for path, sid, rights in ((target, "S-1-5-32-545", "M"), (install.caddyfile, EVERYONE, "R"),
                              (install.uploads, service_sid(services.FRONTEND), "M")):  # fmt: skip
        result = subprocess.run(["icacls.exe", str(path), "/grant", f"*{sid}:{rights}", "/Q"],  # noqa: S607
                                capture_output=True, check=False)  # fmt: skip
        assert result.returncode == 0
    verified = cli("permissions", "verify", check=False)
    assert verified.returncode == 1
    problems = json.loads(verified.stdout.strip().splitlines()[-1])["problems"]
    assert any("alembic.ini" in p and "broad_access" in p for p in problems), problems
    assert any("Caddyfile" in p for p in problems), problems
    assert any("uploads" in p for p in problems), problems
    changes = json.loads(cli("permissions", "apply").stdout.strip().splitlines()[-1])["changes"]
    assert changes
    assert cli("permissions", "verify", check=False).returncode == 0
    assert permissions.snapshot(policy) == before


@pytest.mark.criterion("C3-4")
def test_a_junction_out_of_the_release_stops_apply_and_nothing_outside_changes() -> None:
    install = CTX.install()
    outside = PROBE / "outside"
    outside.mkdir(parents=True, exist_ok=True)
    (outside / "sentinel.txt").write_text("outside", encoding="utf-8")
    sentinel = {p: ntfs.read(p) for p in (outside, outside / "sentinel.txt")}
    link = install.frontend_dir / "node_modules" / "c3-foreign"
    _junction(outside, link)
    try:
        refused = cli("permissions", "apply", check=False)
        assert refused.returncode == 2, refused.stdout + refused.stderr
        assert "foreign_link" in refused.stderr + refused.stdout
        assert cli("permissions", "verify", check=False).returncode == 1
        assert {p: ntfs.read(p) for p in sentinel} == sentinel, "permissions changed outside the installation"
    finally:
        link.rmdir()
    assert json.loads(cli("permissions", "apply").stdout.strip().splitlines()[-1])["changes"] == []


# --- C3-5 -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C3-5")
def test_no_secret_and_no_object_is_open_to_users_or_everyone() -> None:
    install = CTX.install()
    policy = services.permission_policy(install)
    problems = permissions.verify(policy)
    assert problems == [], [p.describe() for p in problems[:20]]
    readers = {
        install.layout.config_file: {service_sid(services.STACK)},
        install.backend_env: {service_sid(services.BACKEND)},
        install.caddyfile: {service_sid(services.CADDY)},
        install.superuser_secret: set(),
        install.integrity_record: set(),
    }
    for path, allowed in readers.items():
        sids = {ace.sid for ace in ntfs.read(path).aces}
        assert sids - {permissions.ADMINISTRATORS, permissions.SYSTEM} == allowed, (path.name, sids)
    # the root does not inherit from C:\\ (whose default lets signed-in users write)
    assert ntfs.read(ROOT).protected
    assert not set(permissions.BROAD) & {a.sid for a in ntfs.read(ROOT).aces}


@pytest.mark.criterion("C3-5")
@pytest.mark.criterion("C3-2")
def test_the_outsider_and_the_local_user_reach_nothing(measured: Results) -> None:
    for role in ("outsider", "user"):
        allowed = [i for i, got in measured.measured[role].items() if got == "allowed" and i != "identity"]
        assert allowed == [], (role, allowed)
    assert _unexpected(measured) == [], _unexpected(measured)


# --- C3-2 / C3-3 / C3-E ------------------------------------------------------------------------------------------


@pytest.mark.criterion("C3-2")
def test_each_identity_has_exactly_the_expected_access(measured: Results) -> None:
    for role, name in ROLES.items():
        assert measured.measured[role]["identity"] == service_sid(name), role
    assert _unexpected(measured) == [], _unexpected(measured)


@pytest.mark.criterion("C3-3")
def test_the_backend_cannot_write_releases(measured: Results) -> None:
    ids = [i for i in measured.expected if i.startswith("releases\\") and ("escribir" in i or "crear" in i)]
    for role in measured.measured:
        for check_id in ids:
            if check_id.endswith(".next\\cache (crear)"):
                continue  # the cache link leads to state\\frontend-cache, outside the release
            assert measured.measured[role][check_id] == "denied", (role, check_id)
    assert _unexpected(measured, ids) == []


@pytest.mark.criterion("C3-E")
def test_nobody_but_administrators_can_alter_executables(measured: Results) -> None:
    ids = [i for i in measured.expected if (".exe" in i or ".xml" in i) and "escribir" in i]
    assert len(ids) >= 6
    for role in measured.measured:
        for check_id in ids:
            assert measured.measured[role][check_id] == "denied", (role, check_id)


@pytest.mark.criterion("C3-E")
def test_the_integrity_record_detects_a_changed_executable() -> None:
    started = time.monotonic()
    report = json.loads(cli("integrity", "verify").stdout.strip().splitlines()[-1])
    metrics["integrity verify (s)"] = round(time.monotonic() - started, 1)
    metrics["archivos con hash registrado"] = report["files"]
    assert report["changed"] == report["missing"] == report["added"] == []
    target = ROOT / "services" / f"{services.BACKEND}.xml"
    original = target.read_bytes()
    target.write_bytes(original + b"<!-- tampered -->")
    try:
        tampered = cli("integrity", "verify", check=False)
        assert tampered.returncode == 1
        assert json.loads(tampered.stdout.strip().splitlines()[-1])["changed"] == [str(target.relative_to(ROOT))]
    finally:
        target.write_bytes(original)
    assert cli("integrity", "verify").returncode == 0


# --- C3-S -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C3-S")
def test_the_services_work_with_the_definitive_permissions(measured: Results) -> None:
    """After the probes (each service was pointed back at its program): start, answer through Caddy, stop."""
    install = CTX.install()
    started = time.monotonic()
    cli("services", "start")
    metrics["arranque con las ACL de C3 (s)"] = round(time.monotonic() - started, 1)
    status, body = services.https_get(install, "/api/v1/health/ready")
    assert status == 200, body[:200]
    page, _ = services.https_get(install, "/")
    assert page < 500
    cli("services", "stop")
    for name in services.ALL:
        assert winservice.status(name).state is State.STOPPED, name
    assert permissions.verify(services.permission_policy(install)) == []
