"""W2-C2 acceptance: the five Windows services on a disposable runner, in order (each test needs the previous).

The job stages a release with ``stage_release.py`` and runs this as administrator, with fictitious secrets
generated here. The backup disk is a virtual disk (VHDX) attached and detached with Windows' own tools: removing
it is a **simulation** of a disconnected disk, and the real one stays PENDIENTE.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from districom_ops import artifacts, database, services, winservice
from districom_ops.config import load_config, load_services_config, parse_env
from districom_ops.install import Install
from districom_ops.winservice import State, account
from services_tests.conftest import metrics

ROOT = Path(os.environ.get("DISTRICOM_C2_ROOT", r"C:\DistriCom"))
RELEASE = os.environ.get("DISTRICOM_C2_RELEASE", "ci")
DISKS = Path(os.environ.get("DISTRICOM_C2_DISKS", r"C:\c2-disks"))
LETTER = "R"
HOST = "districom.test"
# Long enough for a disk image to mount on a CI runner (Mount-DiskImage took > 12 s in run 6).
DISK_WAIT_SECONDS = 90
CONFIG = ROOT / "config" / "districom.env"

pytestmark = pytest.mark.timeout(900)


@dataclass
class Context:
    disk_id: str = ""
    secrets: list[str] = field(default_factory=list)

    def install(self) -> Install:
        values = parse_env(CONFIG.read_text("utf-8-sig"))
        ops = load_config(values)
        return Install(ops, load_services_config(values, ops), RELEASE)


CTX = Context()


def cli(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [sys.executable, "-m", "districom_ops", "--config", str(CONFIG), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUTF8": "1"},
        timeout=900,
        check=False,
    )
    for secret in CTX.secrets:
        assert secret not in result.stdout + result.stderr, "a secret reached the command output"
    if check and result.returncode != 0:
        pytest.fail(f"districom-ops {' '.join(args)} exited {result.returncode}\n{result.stdout}\n{result.stderr}")
    return result


def powershell(command: str) -> str:
    result = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],  # noqa: S607
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        pytest.fail(f"powershell failed: {command}\n{result.stdout}\n{result.stderr}")
    return result.stdout.strip()


def diskpart(script: str) -> None:
    path = DISKS / "diskpart.txt"
    path.write_text(script, encoding="ascii")
    result = subprocess.run(["diskpart.exe", "/s", str(path)], capture_output=True, text=True, timeout=300, check=False)  # noqa: S607
    if result.returncode != 0:
        pytest.fail(f"diskpart failed\n{script}\n{result.stdout}")


def disk_root() -> Path:
    return Path(f"{LETTER}:\\")


def create_disk(name: str) -> Path:
    vhd = DISKS / f"{name}.vhdx"
    diskpart(
        f'create vdisk file="{vhd}" maximum=128 type=expandable\n'
        f'select vdisk file="{vhd}"\nattach vdisk\nconvert mbr\ncreate partition primary\n'
        f"format fs=ntfs quick label=DCBACKUP\nassign letter={LETTER}\n"
    )
    wait_until(lambda: disk_root().exists(), 30, "the virtual disk did not get its letter")
    return vhd


def detach(vhd: Path) -> None:
    powershell(f"Dismount-DiskImage -ImagePath '{vhd}' | Out-Null")
    wait_until(lambda: not disk_root().exists(), 30, "the virtual disk is still mounted")


def attach(vhd: Path) -> None:
    powershell(f"Mount-DiskImage -ImagePath '{vhd}' | Out-Null")
    deadline = time.monotonic() + 20
    while not disk_root().exists() and time.monotonic() < deadline:
        time.sleep(0.5)
    if not disk_root().exists():
        powershell(
            f"$n = (Get-DiskImage -ImagePath '{vhd}' | Get-Disk).Number; "
            f"Get-Partition -DiskNumber $n | Where-Object Type -ne 'Reserved' | "
            f"Set-Partition -NewDriveLetter {LETTER}"
        )
    wait_until(lambda: disk_root().exists(), 30, "the virtual disk did not get its letter back")


def wait_until(condition: Any, seconds: float, message: str) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            pytest.fail(message)
        time.sleep(0.5)


def events(since: datetime, name: str | None = None) -> list[dict[str, Any]]:
    path = ROOT / "logs" / "ops.log"
    found = []
    for line in path.read_text("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if datetime.fromisoformat(entry["at"]) >= since and (name is None or entry.get("event") == name):
            found.append(entry)
    return found


def now() -> datetime:
    return datetime.now(UTC)


def watch_pg_never_starts(seconds: float) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        state = winservice.status(services.PG).state
        assert state is State.STOPPED, f"PostgreSQL went {state.name} without the backup disk"
        time.sleep(0.25)


def superuser_sql(sql: str) -> str:
    install = CTX.install()
    _, _, _, port = install.database
    result = subprocess.run(
        [str(install.pg_bin("psql")), "-X", "-A", "-t", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
         "-d", "postgres", "-c", sql],
        capture_output=True,
        text=True,
        env={**os.environ, "PGPASSWORD": database.superuser_password(install)},
        timeout=60,
        check=True,
    )  # fmt: skip
    return result.stdout.strip()


@pytest.fixture(scope="module", autouse=True)
def disks() -> None:
    DISKS.mkdir(parents=True, exist_ok=True)


def _port_free(port: int) -> bool:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", port))  # noqa: S104 - the same bind Caddy does
    except OSError:
        return False
    finally:
        probe.close()
    return True


def _holders(port: int) -> str:
    """Who listens on ``port`` (netstat), so the results say why the standard ports were not used."""
    command = ["netstat.exe", "-ano", "-p", "TCP"]
    out = subprocess.run(command, capture_output=True, text=True, check=False).stdout
    pids = {line.split()[-1] for line in out.splitlines() if f":{port} " in line and "LISTENING" in line}
    return ", ".join(sorted(pids)) or "ninguno visible (puerto reservado o excluido)"


def caddy_ports() -> tuple[int, int]:
    """80/443 as in production when the runner leaves them free; otherwise 8080/8443, recorded in the results."""
    if _port_free(80) and _port_free(443):
        metrics["puertos de Caddy"] = "80 y 443 (los de producción)"
        return 80, 443
    metrics["puertos de Caddy"] = f"8080 y 8443: el runner ocupa 80 (PID {_holders(80)}) o 443 (PID {_holders(443)})"
    return 8080, 8443


# --- C2-1 -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C2-1")
def test_runtimes_are_verified_and_a_tampered_download_is_refused() -> None:
    cli("init", "--root", str(ROOT))
    # A first configuration (completed below): the runtime commands only need the C1 part.
    app_password, ops_password = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    secret_key = secrets.token_urlsafe(48)
    CTX.secrets = [app_password, ops_password, secret_key]
    CONFIG.write_text(
        "\n".join(
            [
                f"DISTRICOM_ROOT={ROOT}",
                f"DISTRICOM_HOST={HOST}",
                f"DATABASE_URL=postgresql://districom_app:{app_password}@127.0.0.1:5432/districom",
                f"OPS_DATABASE_URL=postgresql://districom_ops:{ops_password}@127.0.0.1:5432/postgres",
                "LAN_SUBNET=192.168.10.0/24",
                f"SECRET_KEY={secret_key}",
                f"BACKUP_DISK_ROOT={disk_root()}",
                f"BACKUP_DISK_WAIT_SECONDS={DISK_WAIT_SECONDS}",
                *(f"{key}={port}" for key, port in zip(("HTTP_PORT", "HTTPS_PORT"), caddy_ports(), strict=True)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    started = time.monotonic()
    cli("runtime", "fetch")
    metrics["descarga y verificación de los 4 runtimes (s)"] = round(time.monotonic() - started, 1)
    since = now()
    cli("runtime", "fetch")
    assert all(not e["changed"] for e in events(since, "runtime_ready")), "a second fetch changed something"
    assert len(events(since, "runtime_ready")) == 4

    winsw = artifacts.manifest()["winsw"]
    downloaded = ROOT / "downloads" / winsw.url.rsplit("/", 1)[-1]
    tampered = DISKS / "tampered" / downloaded.name
    tampered.parent.mkdir(parents=True, exist_ok=True)
    data = bytearray(downloaded.read_bytes())
    data[len(data) // 2] ^= 0xFF
    tampered.write_bytes(bytes(data))
    with pytest.raises(artifacts.ArtifactError, match="mismatch"):
        artifacts.install(winsw, tampered, DISKS / "tampered-runtime")
    assert not tampered.exists()
    assert not winsw.home(DISKS / "tampered-runtime").exists()


@pytest.mark.criterion("C2-1")
def test_disk_database_and_services_install_idempotently() -> None:
    create_disk("backup1")
    prepared = cli("backup-disk", "prepare", "--root", str(disk_root()))
    CTX.disk_id = prepared.stdout.strip().splitlines()[-1]
    with CONFIG.open("a", encoding="utf-8") as handle:
        handle.write(f"BACKUP_DISK_ID={CTX.disk_id}\n")
    cli("backup-disk", "verify")

    started = time.monotonic()
    since = now()
    cli("database", "init")
    metrics["database init (s)"] = round(time.monotonic() - started, 1)
    cli("database", "init")
    assert [e["changed"] for e in events(since, "database_init")] == [True, False]

    started = time.monotonic()
    first = json.loads(cli("services", "install", "--release", RELEASE).stdout.strip().splitlines()[-1])
    metrics["services install (s)"] = round(time.monotonic() - started, 1)
    assert first["changes"], "the first install changed nothing"
    definitions = {name: (ROOT / "services" / f"{name}.xml").read_bytes() for name in services.WINSW}
    configs = {name: winservice.config(name) for name in services.ALL}
    second = json.loads(cli("services", "install", "--release", RELEASE).stdout.strip().splitlines()[-1])
    assert second["changes"] == [], second
    assert {name: (ROOT / "services" / f"{name}.xml").read_bytes() for name in services.WINSW} == definitions
    assert {name: winservice.config(name) for name in services.ALL} == configs

    present = {name: cfg for name, cfg in configs.items() if cfg is not None}
    assert set(present) == set(services.ALL)
    for name, cfg in present.items():
        assert cfg.account.lower() == account(name).lower(), (name, cfg.account)  # never LocalSystem
    assert present[services.STACK].start_type == 2  # automatic (delayed)
    for name in services.CHAIN:
        assert present[name].start_type == 3, name  # manual: only the stack starts them
    deps = {name: cfg.dependencies for name, cfg in present.items()}
    # pg_ctl register adds the Windows RPC service; PostgreSQL depends on nothing of DistriCom.
    assert not set(deps[services.PG]) & set(services.ALL)
    assert deps[services.BACKEND] == ()
    assert deps[services.FRONTEND] == (services.BACKEND,)
    assert deps[services.CADDY] == (services.FRONTEND,)
    for name in services.ALL:
        assert not any(d == services.PG for d in deps[name])
    report = json.loads(cli("services", "status").stdout.strip().splitlines()[-1])
    assert report["backup_disk"] == {"ok": True, "code": "ok"}
    metrics["cuentas de servicio"] = ", ".join(sorted(cfg.account for cfg in present.values()))


def test_migrations_with_postgresql_alone() -> None:
    """Setup for C2-2: the schema, migrated as the application role (C12 will own this step)."""
    cli("services", "start", "--direct", "--until", services.PG)
    install = CTX.install()
    backend = install.release_dir() / "backend"
    # The same settings the service reads from config\backend\.env (single-quoted literal values).
    settings = {k: v.strip("'") for k, v in parse_env(install.backend_env.read_text("utf-8")).items()}
    result = subprocess.run(
        [str(install.backend_python), "-m", "alembic", "-c", str(backend / "alembic.ini"), "upgrade", "head"],
        cwd=backend,
        capture_output=True,
        text=True,
        env={**os.environ, **settings, "PYTHONUTF8": "1"},
        timeout=900,
        check=False,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    cli("services", "stop")
    assert winservice.status(services.PG).state is State.STOPPED


# --- C2-2 / C2-3 ------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C2-2")
@pytest.mark.criterion("C2-3")
def test_the_stack_starts_everything_in_order_and_ready_answers_through_caddy() -> None:
    since = now()
    started = time.monotonic()
    cli("services", "start")
    metrics["arranque completo por DistriComStack hasta Caddy (s)"] = round(time.monotonic() - started, 1)
    install = CTX.install()
    status, body = services.https_get(install, "/api/v1/health/ready")
    assert status == 200, body
    assert json.loads(body) == {"status": "ready", "db": "ok", "migrations": "ok"}
    page, _ = services.https_get(install, "/")
    assert page < 500
    for name in services.ALL:
        assert winservice.status(name).state is State.RUNNING, name

    sequence = [
        (e["event"], e["service"])
        for e in events(since)
        if e.get("event") in ("service_starting", "service_ready") and e.get("service") in services.CHAIN
    ]
    expected = [(event, name) for name in services.CHAIN for event in ("service_starting", "service_ready")]
    assert sequence == expected, sequence
    assert events(since, "backup_disk_waiting") == []


@pytest.mark.criterion("C2-3")
def test_windows_never_starts_postgresql_as_a_dependency() -> None:
    cli("services", "stop")
    for name in services.ALL:
        assert winservice.status(name).state is State.STOPPED, name
    winservice.start(services.CADDY)  # an administrator starting Caddy by hand: SCM starts its dependencies first
    winservice.wait_for(services.CADDY, {State.RUNNING}, 180)
    assert winservice.status(services.FRONTEND).state is State.RUNNING
    assert winservice.status(services.BACKEND).state is State.RUNNING
    watch_pg_never_starts(5)
    services.stop_chain(CTX.install())


# --- C2-4 -------------------------------------------------------------------------------------------------------


def _kill_backend() -> int:
    wrapper = winservice.status(services.BACKEND).pid
    assert wrapper, "the backend has no process"
    child = powershell(
        f"(Get-CimInstance Win32_Process -Filter 'ParentProcessId={wrapper}' | Select-Object -First 1).ProcessId"
    )
    assert child.isdigit(), f"no child process under WinSW {wrapper}"
    subprocess.run(["taskkill.exe", "/F", "/T", "/PID", child], capture_output=True, timeout=60, check=True)  # noqa: S607
    return wrapper


@pytest.mark.criterion("C2-4")
def test_a_killed_backend_is_restarted_three_times_then_left_stopped() -> None:
    cli("services", "start")
    install = CTX.install()
    times = []
    for attempt in range(1, 4):
        before = _kill_backend()
        killed = time.monotonic()

        def restarted(before: int = before) -> bool:
            current = winservice.status(services.BACKEND)
            return (
                current.state is State.RUNNING
                and current.pid not in (0, before)
                and services.ready(install, services.BACKEND)
            )

        wait_until(restarted, 120, f"the backend was not restarted after kill {attempt}")
        times.append(round(time.monotonic() - killed, 1))
    metrics["reinicio del backend tras matarlo (s)"] = times
    _kill_backend()
    wait_until(lambda: winservice.status(services.BACKEND).state is State.STOPPED, 60, "backend still running")
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:  # the fourth failure: WinSW stops trying
        assert winservice.status(services.BACKEND).state is State.STOPPED
        time.sleep(0.5)
    assert winservice.status(services.PG).state is State.RUNNING  # nothing else was touched


# --- C2-5 -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C2-5")
def test_the_stop_is_ordered_and_postgresql_shuts_down_cleanly() -> None:
    cli("services", "start", "--direct")  # brings the backend back after C2-4
    install = CTX.install()
    assert services.https_get(install, "/api/v1/health/ready")[0] == 200
    assert (database.app_connections(install) or 0) > 0  # the backend holds pooled sessions
    since = now()
    started = time.monotonic()
    cli("services", "stop")
    metrics["parada ordenada completa (s)"] = round(time.monotonic() - started, 1)
    for name in services.ALL:
        assert winservice.status(name).state is State.STOPPED, name

    stopped = [e["service"] for e in events(since, "service_stopped")]
    # The stack stops the chain in reverse order; the command logs the stack itself once it has exited.
    assert stopped == [services.CADDY, services.FRONTEND, services.BACKEND, services.PG, services.STACK], stopped
    sessions = events(since, "postgresql_sessions_before_stop")
    assert [e["application_sessions"] for e in sessions] == [0]

    backend_logs = sorted((install.service_logs).glob(f"{services.BACKEND}*.log"), key=lambda p: p.stat().st_mtime)
    assert any("Finished server process" in p.read_text("utf-8", errors="replace") for p in backend_logs[-3:])
    pg_logs = sorted(install.postgres_logs.glob("postgresql-*.log"), key=lambda p: p.stat().st_mtime)
    assert pg_logs
    assert "database system is shut down" in pg_logs[-1].read_text("utf-8", errors="replace")
    control = subprocess.run(
        [str(install.pg_bin("pg_controldata")), "-D", str(install.pg_data)],
        capture_output=True,
        text=True,
        env={**os.environ, "LC_ALL": "C", "LANG": "C"},
        timeout=60,
        check=True,
    )
    state = next(line for line in control.stdout.splitlines() if line.startswith("Database cluster state"))
    assert state.endswith("shut down"), state


# --- C2-6 -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C2-6")
def test_without_the_disk_postgresql_is_not_started_and_the_reason_is_logged() -> None:
    cli("services", "stop")  # each case starts from everything stopped
    detach(DISKS / "backup1.vhdx")
    since = now()
    result = cli("services", "start", "--direct", check=False)
    assert result.returncode == 2
    watch_pg_never_starts(3)
    refused = events(since, "postgresql_start_refused")
    assert [e["reason"] for e in refused] == ["missing"]
    assert winservice.status(services.BACKEND).state is State.STOPPED  # nothing after it started either


@pytest.mark.criterion("C2-6")
def test_a_copied_marker_on_another_disk_with_the_same_letter_is_refused() -> None:
    attach(DISKS / "backup1.vhdx")
    copied = (disk_root() / ".districom-backup-disk").read_bytes()
    detach(DISKS / "backup1.vhdx")
    create_disk("impostor")  # another disk, given the same letter, with an exact copy of the marker
    (disk_root() / ".districom-backup-disk").write_bytes(copied)
    since = now()
    result = cli("services", "start", "--direct", check=False)
    assert result.returncode == 2
    watch_pg_never_starts(3)
    assert [e["reason"] for e in events(since, "postgresql_start_refused")] == ["volume_mismatch"]
    assert cli("backup-disk", "verify", check=False).returncode == 1
    detach(DISKS / "impostor.vhdx")


@pytest.mark.criterion("C2-6")
def test_a_disk_that_mounts_late_is_waited_for() -> None:
    cli("services", "stop")  # each case starts from everything stopped
    since = now()
    winservice.start(services.STACK)
    watch_pg_never_starts(8)
    attach(DISKS / "backup1.vhdx")
    install = CTX.install()
    wait_until(lambda: services.ready(install, services.CADDY), 300, "the services did not start after the disk")
    assert events(since, "backup_disk_waiting"), "the stack did not wait for the disk"
    assert events(since, "postgresql_start_refused") == []
    cli("services", "stop")


@pytest.mark.criterion("C2-6")
def test_the_stack_gives_up_after_the_wait_and_records_why() -> None:
    cli("services", "stop")  # each case starts from everything stopped
    detach(DISKS / "backup1.vhdx")
    since = now()
    winservice.start(services.STACK)
    deadline = time.monotonic() + DISK_WAIT_SECONDS + 60
    while winservice.status(services.STACK).state is not State.STOPPED:
        assert winservice.status(services.PG).state is State.STOPPED
        assert time.monotonic() < deadline, "the stack did not give up"
        time.sleep(0.5)
    watch_pg_never_starts(2)
    refused = events(since, "postgresql_start_refused")
    assert [e["reason"] for e in refused] == ["missing"]
    assert winservice.status(services.STACK).exit_code != 0


# --- C2-7 -------------------------------------------------------------------------------------------------------


@pytest.mark.criterion("C2-7")
def test_uninstall_leaves_no_service_or_process_and_keeps_the_data() -> None:
    install = CTX.install()
    removed = json.loads(cli("services", "uninstall").stdout.strip().splitlines()[-1])["removed"]
    assert sorted(removed) == sorted(services.ALL)
    for name in services.ALL:
        assert winservice.status(name).state is State.MISSING, name
    leftovers = powershell(
        "Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -like '"
        + str(ROOT)
        + "\\*' } | ForEach-Object { $_.Name + ' ' + $_.ProcessId }"
    )
    assert leftovers == "", leftovers
    assert (install.pg_data / "PG_VERSION").exists()
    assert CONFIG.exists()
    again = json.loads(cli("services", "uninstall").stdout.strip().splitlines()[-1])["removed"]
    assert again == []
