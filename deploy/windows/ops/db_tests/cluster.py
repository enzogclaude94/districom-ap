"""A disposable PostgreSQL cluster made by the real ``database.init`` (C9-B model), run with ``pg_ctl``.

Never a production database: it lives in a temporary folder and is stopped at the end of its module. Fictitious,
test-only credentials.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import IO, TYPE_CHECKING, Any

from districom_ops import database

if TYPE_CHECKING:
    import pytest
from districom_ops.config import load_config, load_services_config
from districom_ops.install import Install
from districom_ops.layout import Layout

APP_USER = "districom_app"
APP_PASSWORD = "Fict1cia-app-9f3k2LmQ"
OPS_USER = "districom_ops"
OPS_PASSWORD = "Fict1cia-ops-7Hw4pZx2"
DATABASE = "districom"
OPS_DIR = Path(__file__).resolve().parents[1]
EXE = ".exe" if sys.platform == "win32" else ""


@dataclass(frozen=True)
class LabInstall(Install):
    """An installation whose PostgreSQL binaries are ``DISTRICOM_PG_BIN`` instead of the pinned runtime."""

    pg_bin_dir: Path = Path()

    def pg_bin(self, tool: str) -> Path:
        return self.pg_bin_dir / f"{tool}{EXE}"


def workdir(factory: "pytest.TempPathFactory", name: str) -> Path:
    """A fresh folder for a cluster. On Windows, ``initdb`` runs with a restricted token (the Administrators group
    deny-only) and cannot walk pytest's private temporary folders (created owner-only): ``DISTRICOM_C9B_WORK``
    (CI: under ``RUNNER_TEMP``) gives an ordinary folder instead."""
    root = os.environ.get("DISTRICOM_C9B_WORK")
    if not root:
        return factory.mktemp(name)
    Path(root).mkdir(parents=True, exist_ok=True)
    return Path(tempfile.mkdtemp(prefix=f"{name}-", dir=root))


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def values(root: Path, port: int, app_password: str = APP_PASSWORD, ops_password: str = OPS_PASSWORD,
           ops_user: str = OPS_USER) -> dict[str, str]:  # fmt: skip
    return {
        "DISTRICOM_ROOT": str(root),
        "DISTRICOM_HOST": "districom.example.internal",
        "DATABASE_URL": f"postgresql+psycopg://{APP_USER}:{app_password}@127.0.0.1:{port}/{DATABASE}",
        "OPS_DATABASE_URL": f"postgresql://{ops_user}:{ops_password}@127.0.0.1:{port}/postgres",
        "LAN_SUBNET": "192.168.10.0/24",
        "SECRET_KEY": "k9Qv2Lr8Zt5Wx1Np7Hd3Bf6Mj4Sc0GyEuTa",
        "BACKUP_DISK_ROOT": str(root.parent / "backupdisk"),
        "BACKUP_DISK_ID": "6f1c2a7e-3b4d-4e5f-9a8b-1c2d3e4f5a6b",
        "PG_DATA_DIR": str(root / "pgdata"),
    }


def make_install(env: dict[str, str]) -> LabInstall:
    ops = load_config(env)
    return LabInstall(ops, load_services_config(env, ops), None, Path(os.environ["DISTRICOM_PG_BIN"]))


class Session:
    """A psql session kept open on stdin: each :meth:`run` sends statements and waits for their answer."""

    def __init__(self, cluster: "Cluster", role: str, password: str, dbname: str = DATABASE) -> None:
        self.err: IO[bytes] = tempfile.TemporaryFile()  # noqa: SIM115 - read by .stderr()
        self.process = subprocess.Popen(
            [str(cluster.install.pg_bin("psql")), "-X", "-q", "-A", "-t", "-h", "127.0.0.1", "-p", str(cluster.port),
             "-U", role, "-d", dbname],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.err,
            env={**os.environ, "PGPASSWORD": password, "PGCLIENTENCODING": "UTF8", "LC_MESSAGES": "C"},
        )  # fmt: skip
        if not self.run("SELECT 1;"):
            raise AssertionError(f"session of {role} did not open: {self.stderr()}")

    def run(self, sql: str, timeout: float = 60) -> bool:
        """True when every statement succeeded; False on an error or a lost connection."""
        assert self.process.stdin is not None
        assert self.process.stdout is not None
        try:
            self.process.stdin.write(f"{sql}\n\\echo __done__ :ERROR\n".encode())
            self.process.stdin.flush()
        except OSError:
            return False
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.process.stdout.readline().decode("utf-8", "replace")
            if not line:
                return False  # psql exited: the connection was lost
            if line.startswith("__done__"):
                return line.split()[-1] == "false"
        raise AssertionError("the session did not answer in time")

    def stderr(self) -> str:
        self.err.seek(0)
        return self.err.read().decode("utf-8", "replace")

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.kill()
        self.process.wait()
        self.err.close()


class Cluster:
    def __init__(self, base: Path) -> None:
        self.base = base
        self.root = base / "DistriCom"
        self.port = free_port()
        self.env = values(self.root, self.port)
        Layout(self.root).ensure()
        self.install = make_install(self.env)
        self.log = base / "server.log"
        started = time.monotonic()
        try:
            assert database.init(self.install)
        except database.DatabaseError as exc:
            init_log = self.install.postgres_logs / "init.log"
            detail = init_log.read_text("utf-8", "replace")[-4000:] if init_log.exists() else "(no init.log)"
            raise AssertionError(f"{exc}\n{detail}") from exc
        self.init_seconds = round(time.monotonic() - started, 1)
        self.start()

    def _ctl(self, *args: str) -> int:
        return database._run([str(self.install.pg_bin("pg_ctl")), "-D", str(self.install.pg_data), *args],
                             out=self.base / "pg_ctl.out")  # fmt: skip

    def start(self) -> None:
        assert self._ctl("-l", str(self.log), "-w", "-t", "120", "start") == 0, self.log.read_text("utf-8", "replace")

    def stop(self) -> None:
        assert self._ctl("-m", "fast", "-w", "-t", "120", "stop") == 0

    def superuser(self, sql: str, dbname: str = "postgres") -> list[str]:
        return database.query(self.install, sql, role="postgres", password=database.superuser_password(self.install),
                              dbname=dbname)  # fmt: skip

    def app(self, sql: str, dbname: str = DATABASE) -> list[str]:
        return database.query(self.install, sql, role=APP_USER, password=APP_PASSWORD, dbname=dbname)

    def ops(self, sql: str, dbname: str = "postgres") -> list[str]:
        return database.query(self.install, sql, role=OPS_USER, password=OPS_PASSWORD, dbname=dbname)

    def allows(self) -> bool:
        return self.superuser(f"SELECT datallowconn FROM pg_database WHERE datname = '{DATABASE}';") == ["t"]

    def client_sessions(self) -> int:
        return int(self.superuser(
            f"SELECT count(*) FROM pg_stat_activity WHERE datname = '{DATABASE}' AND backend_type = 'client backend';"
        )[0])  # fmt: skip

    def version(self) -> str:
        return self.superuser("SHOW server_version;")[0]

    def env_file(self, env: dict[str, str] | None = None) -> Path:
        path = self.base / "env.json"
        path.write_text(json.dumps(env or self.env), encoding="utf-8")
        return path


def child(
    cluster: Cluster, script: str, env: dict[str, str] | None = None, kill_at: str = ""
) -> subprocess.CompletedProcess[str]:
    """Run ``script`` in a separate Python process with ``install`` (and ``lock`` helpers) ready, dying with exit
    code 9 at ``kill_at``: ``save:<state>:<step>`` (right after that journal write), ``step:<name>`` (right after
    that operations record step) or ``allow:true`` (right after ALTER DATABASE … ALLOW_CONNECTIONS true)."""
    code = textwrap.dedent(
        f"""
        import json, os, sys
        from pathlib import Path
        from districom_ops import write_block, oplog as oplog_module
        from districom_ops.oplock import OperationLock
        from districom_ops.maintenance import MaintenanceWindow
        from districom_ops.oplog import Event, Kind, OperationsLog
        from db_tests.cluster import make_install
        install = make_install(json.loads(Path({str(cluster.env_file(env))!r}).read_text("utf-8")))
        state = install.layout.state
        kill = {kill_at!r}
        real_save = write_block._save
        def save(state_dir, journal, new_state, **changes):
            saved = real_save(state_dir, journal, new_state, **changes)
            if kill == f"save:{{saved.state.value}}:{{saved.step}}":
                os._exit(9)
            return saved
        write_block._save = save
        real_step = OperationsLog.step
        def step(self, op_id, name, **detail):
            real_step(self, op_id, name, **detail)
            if kill == f"step:{{name}}":
                os._exit(9)
        OperationsLog.step = step
        real_allow = write_block.Database.set_allow_connections
        def allow(self, allowed):
            real_allow(self, allowed)
            if kill == "allow:true" and allowed:
                os._exit(9)
        write_block.Database.set_allow_connections = allow
        window = MaintenanceWindow(state, OperationsLog(state / "operations.jsonl"))
        """
    ) + textwrap.dedent(script)
    return subprocess.run(
        [sys.executable, "-c", code], cwd=OPS_DIR, capture_output=True, text=True, timeout=300, check=False,
        env={**os.environ, "PYTHONPATH": str(OPS_DIR)},
    )  # fmt: skip


def fabricated_evidence(cluster: Cluster, folder: Path) -> dict[str, Any]:
    """Evidence of a safe close made up for the test (C10/C11 will produce the real one), true to the cluster: it
    must be stopped. The copies are two small files with their SHA-256."""
    import hashlib

    from districom_ops.write_block import _controldata

    fields = _controldata(cluster.install)
    assert fields.get("Database cluster state") == "shut down", fields
    checkpoint = fields["Latest checkpoint location"]
    folder.mkdir(parents=True, exist_ok=True)
    copies = []
    for repo in ("repo1", "repo2"):
        path = folder / f"{repo}-segment"
        path.write_bytes(f"{repo} fabricated WAL segment".encode())
        copies.append({"repo": repo, "path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    return {
        "cut_lsn": checkpoint,
        "checkpoint_lsn": checkpoint,
        "timeline": int(fields["Latest checkpoint's TimeLineID"]),
        "copies": copies,
    }
