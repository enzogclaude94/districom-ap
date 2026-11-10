"""DistriCom as native Windows services (W2-C2, ADR 016 proposed).

Five services, each under its own virtual account ``NT SERVICE\\<name>`` (no password, not an administrator):

* ``DistriComPostgreSQL``: PostgreSQL 17 (``pg_ctl register``). Started only by the stack (or an administrator),
  after the backup disk check.
* ``DistriComBackend``: uvicorn (WinSW). Started by the stack once PostgreSQL accepts connections.
* ``DistriComFrontend``: ``node server.js`` (WinSW), SCM dependency on the backend. Started once the backend is live.
* ``DistriComCaddy``: Caddy (WinSW), SCM dependency on the frontend. Started once the frontend answers.
* ``DistriComStack``: ``districom-ops services supervise`` (WinSW). Started by Windows (automatic, delayed).

Why PostgreSQL is **not** an SCM dependency of anything and has no automatic start or SCM restart: Windows would
then start it without the backup disk check (at boot, as a dependency, or after a crash). Only the stack starts
it, and only after ``backup_disk.verify`` passes; the stack also restarts it after an unexpected stop, re-checking
the disk every time, a limited number of times. The order is enforced by readiness, not only by process start:
each service starts once the previous one answers.

WinSW restarts the backend, frontend and Caddy after a crash (``onfailure``: three times, then it stops trying;
the count resets after an hour). Stops are always in reverse order and graceful: WinSW sends Ctrl+C and waits.
"""

import hashlib
import http.client
import json
import logging
import shutil
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from districom_ops import artifacts, backup_disk, database, integrity, ntfs, permissions, winservice
from districom_ops.install import Install
from districom_ops.winservice import ServiceError, State, account, service_sid

PG = database.PG_SERVICE
BACKEND = "DistriComBackend"
FRONTEND = "DistriComFrontend"
CADDY = "DistriComCaddy"
STACK = "DistriComStack"
CHAIN = (PG, BACKEND, FRONTEND, CADDY)
WINSW = (BACKEND, FRONTEND, CADDY, STACK)
ALL = (*CHAIN, STACK)

BACKEND_PORT = 8000
FRONTEND_PORT = 3000
START_SECONDS = 180
STOP_SECONDS = 180
PG_RESTARTS = 3
PG_RESTART_WINDOW_SECONDS = 3600
log = logging.getLogger("districom_ops")


class ServicesError(Exception):
    pass


# --- service definitions -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WinswSpec:
    id: str
    name: str
    description: str
    executable: Path
    arguments: list[str]
    workdir: Path
    start_mode: Literal["Automatic", "Manual"] = "Manual"
    delayed: bool = False
    depends: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    stop_seconds: int = 60
    restart_delays: tuple[int, ...] = (5, 5, 5)


def specs(install: Install) -> dict[str, WinswSpec]:
    release = install.release_dir()
    caddy = install.artifact("caddy").path(install.runtime)
    node = install.artifact("node").path(install.runtime) / "node.exe"
    config_file = install.layout.config_file
    return {
        BACKEND: WinswSpec(
            id=BACKEND,
            name="DistriCom backend",
            description="DistriCom API (uvicorn on 127.0.0.1:8000). Started by DistriComStack.",
            executable=install.backend_python,
            arguments=[
                "-m",
                "uvicorn",
                "app.main:app",
                "--app-dir",
                str(release / "backend"),
                "--host",
                "127.0.0.1",
                "--port",
                str(BACKEND_PORT),
                "--proxy-headers",
                "--forwarded-allow-ips",
                "127.0.0.1",
                "--timeout-graceful-shutdown",
                "30",
            ],
            # pydantic reads `.env` from the working directory: the secrets stay in config\, not in the release.
            workdir=install.backend_env.parent,
            env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
        ),
        FRONTEND: WinswSpec(
            id=FRONTEND,
            name="DistriCom frontend",
            description="DistriCom web (Next.js standalone on 127.0.0.1:3000). Started by DistriComStack.",
            executable=node,
            arguments=["server.js"],
            workdir=install.frontend_dir,
            depends=(BACKEND,),
            env={"NODE_ENV": "production", "HOSTNAME": "127.0.0.1", "PORT": str(FRONTEND_PORT)},
        ),
        CADDY: WinswSpec(
            id=CADDY,
            name="DistriCom Caddy",
            description=(
                f"DistriCom entry point (HTTPS :{install.services.https_port}, HTTP :{install.services.http_port}). "
                "Started by DistriComStack."
            ),
            executable=caddy,
            arguments=["run", "--config", str(install.caddyfile), "--adapter", "caddyfile"],
            workdir=install.caddy_state,
            depends=(FRONTEND,),
            env={
                "DISTRICOM_HOST": install.ops.host,
                "CADDY_TLS_MODE": "internal",  # C5 switches to the Cloudflare build
                "CADDY_HTTP_PORT": str(install.services.http_port),
                "CADDY_HTTPS_PORT": str(install.services.https_port),
                "XDG_DATA_HOME": str(install.caddy_state / "data"),
                "XDG_CONFIG_HOME": str(install.caddy_state / "config"),
            },
        ),
        STACK: WinswSpec(
            id=STACK,
            name="DistriCom",
            description="Checks the backup disk and starts PostgreSQL, backend, frontend and Caddy in order.",
            executable=install.ops_python,
            arguments=["-m", "districom_ops", "--config", str(config_file), "services", "supervise"],
            workdir=install.root / "state",
            start_mode="Automatic",
            delayed=True,
            env={"PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
            stop_seconds=600,
            restart_delays=(60, 60, 60),
        ),
    }


def render(spec: WinswSpec, logpath: Path) -> bytes:
    """The WinSW 2.12 XML of a service; ElementTree escapes every value."""
    root = ET.Element("service")

    def add(tag: str, text: str | None = None, **attributes: str) -> ET.Element:
        element = ET.SubElement(root, tag, attributes)
        if text is not None:
            element.text = text
        return element

    add("id", spec.id)
    add("name", spec.name)
    add("description", spec.description)
    add("executable", str(spec.executable))
    add("arguments", subprocess.list2cmdline(spec.arguments))
    add("workingdirectory", str(spec.workdir))
    add("startmode", spec.start_mode)
    if spec.delayed:
        add("delayedAutoStart", "true")
    for dependency in spec.depends:
        add("depend", dependency)
    for name, value in sorted(spec.env.items()):
        add("env", name=name, value=value)
    add("logpath", str(logpath))
    log_element = add("log", mode="roll-by-size")
    ET.SubElement(log_element, "sizeThreshold").text = "10240"
    ET.SubElement(log_element, "keepFiles").text = "8"
    for delay in spec.restart_delays:
        add("onfailure", action="restart", delay=f"{delay} sec")
    add("onfailure", action="none")  # after the last restart WinSW stops trying: the monitor alerts instead
    add("resetfailure", "1 hour")
    add("stoptimeout", f"{spec.stop_seconds} sec")
    add("stopparentprocessfirst", "true")
    ET.indent(root)
    document: bytes = ET.tostring(root, encoding="utf-8", xml_declaration=True)
    return document + b"\n"


def backend_env(install: Install) -> str:
    """``config\\backend\\.env``: what the backend reads in production. Values are single-quoted (literal)."""
    url = install.ops.database_url.reveal()
    scheme, _, rest = url.partition("://")
    url = f"postgresql+psycopg://{rest}" if scheme in ("postgres", "postgresql") else url
    values = {
        "APP_ENV": "production",
        "DATABASE_URL": url,
        "SECRET_KEY": install.services.secret_key.reveal(),
        "COOKIE_SECURE": "true",
        "CORS_ORIGINS": "",
        "ARGON2_PROFILE": "standard",
        "LOG_LEVEL": "INFO",
        "UPLOAD_DIR": str(install.uploads),
    }
    lines = ["# Written by districom-ops services install (W2-C2). Do not edit: change districom.env instead."]
    lines += [f"{key}='{value}'" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def pg_binary_path(install: Install) -> str:
    return f'"{install.pg_bin("pg_ctl")}" runservice -N "{PG}" -D "{install.pg_data}" -w -t {START_SECONDS}'


# --- install / uninstall ---------------------------------------------------------------------------------------


def _write_if_changed(path: Path, content: bytes) -> bool:
    if path.exists() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_name(path.name + ".new")
    partial.write_bytes(content)
    partial.replace(path)
    return True


def _check_release(install: Install) -> None:
    required = {
        "backend python": install.backend_python,
        "backend app": install.release_dir() / "backend" / "app" / "main.py",
        "frontend server.js": install.frontend_dir / "server.js",
        "ops python": install.ops_python,
    }
    missing = [name for name, path in required.items() if not path.exists()]
    if missing:
        raise ServicesError(f"release {install.release} is incomplete: {', '.join(missing)}")
    for name in ("winsw", "postgresql", "caddy", "node"):
        if not artifacts.verified(install.artifact(name), install.runtime):
            raise ServicesError(f"runtime {name} is missing or not verified: run `runtime fetch` first")
    if not (install.pg_data / "PG_VERSION").exists():
        raise ServicesError("the PostgreSQL cluster does not exist: run `database init` first")


def _register_pg(install: Install) -> bool:
    wanted_account = account(PG)
    current = winservice.config(PG)
    if current is not None:
        same = current.binary_path.lower() == pg_binary_path(install).lower()
        if same and current.account.lower() == wanted_account.lower() and current.start_type == 3:
            return False
        if winservice.status(PG).state is not State.STOPPED:
            raise ServicesError(f"{PG} must be stopped to change its registration")
        _pg_ctl(install, ["unregister", "-N", PG])
        winservice.wait_for(PG, {State.MISSING}, 60)
    _pg_ctl(
        install,
        ["register", "-N", PG, "-U", wanted_account, "-D", str(install.pg_data), "-S", "demand", "-w", "-t",
         str(START_SECONDS)],
    )  # fmt: skip
    winservice.sc("description", PG, "DistriCom PostgreSQL 17. Started only by DistriComStack after the disk check.")
    winservice.sc("failure", PG, "reset=", "0", "actions=", "")  # no SCM restarts: the stack re-checks the disk
    return True


def _pg_ctl(install: Install, args: list[str]) -> None:
    result = winservice.run([str(install.pg_bin("pg_ctl")), *args])
    if result.returncode != 0:
        raise ServicesError(f"pg_ctl {args[0]} failed with {result.returncode}")


def _install_winsw(install: Install, spec: WinswSpec) -> list[str]:
    changes: list[str] = []
    exe = install.service_dir / f"{spec.id}.exe"
    winsw = install.artifact("winsw").path(install.runtime)
    if _write_if_changed(exe, winsw.read_bytes()):
        changes.append(f"{spec.id}: wrapper copied")
    xml_changed = _write_if_changed(
        install.service_dir / f"{spec.id}.xml", render(spec, install.service_log_dir(spec.id))
    )
    current = winservice.config(spec.id)
    if current is None:
        result = winservice.run([str(exe), "install"])
        if result.returncode != 0:
            raise ServicesError(f"WinSW install of {spec.id} failed with {result.returncode}")
        changes.append(f"{spec.id}: installed")
    elif xml_changed:
        result = winservice.run([str(exe), "refresh"])
        if result.returncode != 0:
            raise ServicesError(f"WinSW refresh of {spec.id} failed with {result.returncode}")
        changes.append(f"{spec.id}: definition updated")
    current = winservice.config(spec.id)
    if current is None or current.account.lower() != account(spec.id).lower():
        winservice.sc("config", spec.id, "obj=", account(spec.id))
        changes.append(f"{spec.id}: runs as {account(spec.id)}")
    return changes


def permission_policy(install: Install) -> permissions.Policy:
    """The C3 policy of this installation. The backup disk marker is included only when the disk verifies."""
    runtimes = {
        "python": (install.python_runtime, [BACKEND, STACK]),
        "postgresql": (install.artifact("postgresql").home(install.runtime), [PG, STACK]),
        "caddy": (install.artifact("caddy").home(install.runtime), [CADDY]),
        "node": (install.artifact("node").home(install.runtime), [FRONTEND]),
    }
    marker = None
    if check_disk(install).ok:
        marker = install.services.backup_disk_root / backup_disk.MARKER
    return permissions.policy(
        root=install.root,
        release=install.release_dir() if install.release else None,
        pg_data=install.pg_data,
        runtimes=runtimes,
        winsw=list(WINSW),
        names={"backend": BACKEND, "frontend": FRONTEND, "caddy": CADDY, "postgresql": PG, "stack": STACK},
        backup_marker=marker,
    )


def _link_frontend_cache(install: Install) -> bool:
    """``<release>\\frontend\\.next\\cache`` → ``state\\frontend-cache`` (a junction), so the release stays read-only.
    A cache folder left by the build is removed: it is only a cache."""
    link = install.frontend_dir / ".next" / "cache"
    target = install.frontend_cache
    if ntfs.is_link(link):
        if ntfs.link_target(link) == target:
            return False
        link.rmdir() if sys.platform == "win32" else link.unlink()
    elif link.exists():
        shutil.rmtree(link)
    link.parent.mkdir(parents=True, exist_ok=True)
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        link.symlink_to(target, target_is_directory=True)
    return True


def install_services(install: Install) -> list[str]:
    """Idempotent: a second run with the same release and configuration reports no change and touches nothing."""
    _check_release(install)
    changes: list[str] = []
    folders = [install.service_dir, install.uploads, install.caddy_state, install.frontend_cache,
               install.backend_env.parent, install.postgres_logs,
               *(install.service_log_dir(name) for name in WINSW)]  # fmt: skip
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
    if _link_frontend_cache(install):
        changes.append("frontend .next\\cache linked to state\\frontend-cache")
    if _write_if_changed(install.backend_env, backend_env(install).encode("utf-8")):
        changes.append("config\\backend\\.env written")
    template = (Path(__file__).parent / "templates" / "Caddyfile").read_bytes()
    if _write_if_changed(install.caddyfile, template):
        changes.append("config\\Caddyfile written")
    if _register_pg(install):
        changes.append(f"{PG}: registered")
    all_specs = specs(install)
    for name in WINSW:
        changes += _install_winsw(install, all_specs[name])

    if integrity.record(install):
        changes.append("config\\integrity.json written")
    # Definitive permissions (C3): exactly the policy, nothing inherited from C:\\. Last, so that every file
    # written above (including the integrity record) is covered.
    changes += permissions.apply(permission_policy(install))
    for name in CHAIN:
        if winservice.grant_control(name, service_sid(STACK)):
            changes.append(f"{name}: {STACK} may start and stop it")

    record = {
        "release": install.release,
        "artifacts": {name: install.artifact(name).version for name in ("winsw", "postgresql", "caddy", "node")},
        "definitions": {
            name: hashlib.sha256((install.service_dir / f"{name}.xml").read_bytes()).hexdigest() for name in WINSW
        },
        "postgresql": pg_binary_path(install),
    }
    if _write_if_changed(install.record, json.dumps(record, indent=2).encode("utf-8")):
        changes.append("state\\services.json written")
    log.info("services_installed", extra={"release": install.release, "changes": changes})
    return changes


def uninstall_services(install: Install) -> list[str]:
    """Stop everything in order and remove the five services. Data, configuration and logs stay."""
    removed: list[str] = []
    stop_all(install)
    for name in (STACK, CADDY, FRONTEND, BACKEND):
        if winservice.config(name) is None:
            continue
        exe = install.service_dir / f"{name}.exe"
        result = winservice.run([str(exe), "uninstall"]) if exe.exists() else winservice.run(["sc.exe", "delete", name])
        if result.returncode != 0:
            raise ServicesError(f"removing {name} failed with {result.returncode}")
        removed.append(name)
    if winservice.config(PG) is not None:
        _pg_ctl(install, ["unregister", "-N", PG])
        removed.append(PG)
    for name in ALL:
        winservice.wait_for(name, {State.MISSING}, 60)
    install.record.unlink(missing_ok=True)
    for name in WINSW:
        for suffix in (".exe", ".xml"):
            (install.service_dir / f"{name}{suffix}").unlink(missing_ok=True)
    log.info("services_uninstalled", extra={"removed": removed})
    return removed


# --- readiness ---------------------------------------------------------------------------------------------------


def http_status(port: int, path: str, timeout: float = 5) -> int:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("GET", path)
        return connection.getresponse().status
    finally:
        connection.close()


def caddy_ca(install: Install) -> Path:
    return install.caddy_state / "data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"


def _caddy_context(install: Install) -> ssl.SSLContext:
    """Verified against Caddy's own CA when this account can read it (administrators, the tests). The stack cannot:
    Caddy's keys sit next to that certificate and are Caddy's only (C3). For the stack this is a readiness probe
    on 127.0.0.1, not a trust decision, so it then checks only that TLS answers."""
    try:
        return ssl.create_default_context(cafile=str(caddy_ca(install)))
    except PermissionError:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        return context


def https_get(install: Install, path: str, timeout: float = 10) -> tuple[int, bytes]:
    """Through Caddy on 127.0.0.1 (HTTPS port) with the configured name, checking the certificate with Caddy's CA."""
    port = install.services.https_port
    context = _caddy_context(install)
    raw = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    connection = http.client.HTTPConnection(install.ops.host, port, timeout=timeout)
    try:
        connection.sock = context.wrap_socket(raw, server_hostname=install.ops.host)
        host = install.ops.host if port == 443 else f"{install.ops.host}:{port}"
        connection.request("GET", path, headers={"Host": host})
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def ready(install: Install, name: str) -> bool:
    try:
        if name == PG:
            return database.ready(install)
        if name == BACKEND:
            return http_status(BACKEND_PORT, "/api/v1/health/live") == 200
        if name == FRONTEND:
            return http_status(FRONTEND_PORT, "/") < 500
        if name == CADDY:
            return https_get(install, "/api/v1/health/live")[0] == 200
    except (OSError, http.client.HTTPException, ssl.SSLError):
        return False
    raise ValueError(name)


# --- start / stop -----------------------------------------------------------------------------------------------


def check_disk(install: Install) -> backup_disk.DiskCheck:
    return backup_disk.verify(install.services.backup_disk_root, install.services.backup_disk_id, install.pg_data)


def _start(install: Install, name: str) -> None:
    if name == PG:
        disk = check_disk(install)
        if not disk.ok:
            log.error("postgresql_start_refused", extra={"reason": disk.code, "detail": disk.detail})
            raise ServicesError(disk.describe())
    current = winservice.status(name)
    if current.state is State.MISSING:
        raise ServicesError(f"{name} is not installed")
    started = time.monotonic()
    if current.state is not State.RUNNING:
        log.info("service_starting", extra={"service": name})
        winservice.start(name)
        winservice.wait_for(name, {State.RUNNING, State.STOPPED}, START_SECONDS)
    deadline = started + START_SECONDS
    while not ready(install, name):
        state = winservice.status(name).state
        if state is not State.RUNNING and state is not State.START_PENDING:
            raise ServicesError(f"{name} stopped while starting ({state.name})")
        if time.monotonic() > deadline:
            raise ServicesError(f"{name} did not become ready in {START_SECONDS} s")
        time.sleep(1)
    log.info("service_ready", extra={"service": name, "seconds": round(time.monotonic() - started, 1)})


def start_chain(install: Install, until: str = CADDY) -> None:
    """PostgreSQL (after the disk check), backend, frontend and Caddy; each one after the previous is ready."""
    for name in CHAIN:
        _start(install, name)
        if name == until:
            return


def _stop(name: str) -> None:
    current = winservice.status(name)
    if current.state in (State.MISSING, State.STOPPED):
        return
    log.info("service_stopping", extra={"service": name})
    started = time.monotonic()
    winservice.stop(name)
    winservice.wait_for(name, {State.STOPPED}, STOP_SECONDS)
    log.info("service_stopped", extra={"service": name, "seconds": round(time.monotonic() - started, 1)})


def stop_chain(install: Install) -> None:
    """Reverse order: Caddy, frontend, backend, and PostgreSQL last, once no application session is left."""
    for name in reversed(CHAIN):
        if name == PG and winservice.status(PG).state is State.RUNNING:
            deadline = time.monotonic() + 30
            sessions = database.app_connections(install)
            while sessions and time.monotonic() < deadline:
                time.sleep(1)
                sessions = database.app_connections(install)
            log.info("postgresql_sessions_before_stop", extra={"application_sessions": sessions})
        _stop(name)


def stop_all(install: Install) -> None:
    if winservice.config(STACK) is not None:
        _stop(STACK)  # the stack stops the chain itself, in order
    stop_chain(install)


def start_stack(install: Install) -> None:
    """The normal start: the stack service, which checks the disk and starts the chain. Waits until Caddy answers."""
    winservice.start(STACK)
    deadline = time.monotonic() + install.services.backup_disk_wait_seconds + START_SECONDS * len(CHAIN)
    while not ready(install, CADDY):
        if winservice.status(STACK).state is State.STOPPED:
            raise ServicesError(f"{STACK} stopped while starting: see logs\\ops.log")
        if time.monotonic() > deadline:
            raise ServicesError("the services did not become ready in time")
        time.sleep(1)


def status_report(install: Install) -> dict[str, object]:
    services: dict[str, object] = {}
    for name in ALL:
        current = winservice.status(name)
        cfg = winservice.config(name)
        services[name] = {
            "state": current.state.name,
            "account": cfg.account if cfg else None,
            "start_type": cfg.start_type if cfg else None,
            "depends": list(cfg.dependencies) if cfg else [],
        }
    disk = check_disk(install)
    return {"services": services, "backup_disk": {"ok": disk.ok, "code": disk.code}}


# --- supervisor (the DistriComStack service) -------------------------------------------------------------------


def supervise(install: Install, stop: threading.Event | None = None, poll_seconds: float = 5) -> int:
    """Run by ``DistriComStack``. Exit codes: 0 stopped on request, 3 backup disk unavailable, 4 start failed.

    Waits up to ``BACKUP_DISK_WAIT_SECONDS`` for the disk (it may mount after the services start), then starts
    the chain in order and watches PostgreSQL: after an unexpected stop it re-checks the disk and restarts it, at
    most ``PG_RESTARTS`` times an hour. A disk lost while running is a critical log entry; PostgreSQL keeps running
    (ADR 013) and the monitor (C8) alerts. On Ctrl+C (service stop) the chain stops in reverse order."""
    stop = stop or threading.Event()

    def on_signal(signum: int, frame: object) -> None:
        stop.set()

    for name in ("SIGINT", "SIGBREAK", "SIGTERM"):
        if hasattr(signal, name) and threading.current_thread() is threading.main_thread():
            signal.signal(getattr(signal, name), on_signal)

    log.info("supervisor_started", extra={"release": install.release})
    deadline = time.monotonic() + install.services.backup_disk_wait_seconds
    disk = check_disk(install)
    while not disk.ok:
        if time.monotonic() >= deadline or stop.is_set():
            log.error("postgresql_start_refused", extra={"reason": disk.code, "detail": disk.detail})
            return 3
        log.warning("backup_disk_waiting", extra={"reason": disk.code})
        stop.wait(poll_seconds)
        disk = check_disk(install)
    try:
        start_chain(install)
    except (ServicesError, ServiceError) as exc:
        log.error("services_start_failed", extra={"reason": str(exc)})
        stop_chain(install)
        return 4
    log.info("services_running")

    restarts: list[float] = []
    disk_ok = True
    next_disk_check = time.monotonic() + 60
    while not stop.wait(poll_seconds):
        if time.monotonic() >= next_disk_check:
            next_disk_check = time.monotonic() + 60
            disk = check_disk(install)
            if disk.ok != disk_ok:
                (log.info if disk.ok else log.critical)(
                    "backup_disk_back" if disk.ok else "backup_disk_lost", extra={"reason": disk.code}
                )
                disk_ok = disk.ok
        if winservice.status(PG).state is not State.STOPPED:
            continue
        now = time.monotonic()
        restarts = [t for t in restarts if now - t < PG_RESTART_WINDOW_SECONDS]
        log.error("postgresql_stopped_unexpectedly", extra={"restarts_last_hour": len(restarts)})
        if len(restarts) >= PG_RESTARTS:
            log.critical("postgresql_not_restarted", extra={"reason": "restart limit reached"})
            stop.wait(60)
            continue
        restarts.append(now)
        try:
            _start(install, PG)
            log.info("postgresql_restarted")
        except (ServicesError, ServiceError) as exc:
            log.critical("postgresql_not_restarted", extra={"reason": str(exc)})
    log.info("supervisor_stopping")
    stop_chain(install)
    log.info("supervisor_stopped")
    return 0
