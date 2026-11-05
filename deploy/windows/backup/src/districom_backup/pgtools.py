"""PostgreSQL binaries and instances controlled with ``pg_ctl``.

The binaries come from ``DISTRICOM_PG_BIN`` (for example ``C:\\Program Files\\PostgreSQL\\17\\bin``)
or an explicit directory. Instances always listen on ``127.0.0.1`` with TCP, so the same code
works on Windows (no Unix sockets) and on Linux.
"""

import contextlib
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

PG_BIN_ENV = "DISTRICOM_PG_BIN"


class PgToolError(RuntimeError):
    """A PostgreSQL command failed."""


def pg_bin_dir(explicit: Path | None = None) -> Path:
    if explicit is not None:
        return explicit
    value = os.environ.get(PG_BIN_ENV)
    if not value:
        raise PgToolError(f"set {PG_BIN_ENV} to the PostgreSQL bin directory")
    return Path(value)


def tool(name: str, bin_dir: Path | None = None) -> str:
    suffix = ".exe" if sys.platform == "win32" else ""
    path = pg_bin_dir(bin_dir) / f"{name}{suffix}"
    if not path.is_file():
        raise PgToolError(f"{name} not found in {path.parent}")
    return str(path)


def run(args: list[str], *, check: bool = True, timeout: float = 600, env: dict[str, str] | None = None) -> str:
    """Run a binary without a shell and return its stdout. stderr is included in the error."""
    completed = subprocess.run(args, capture_output=True, text=True, timeout=timeout, env=env, check=False)
    if check and completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip()
        raise PgToolError(f"{Path(args[0]).name} exited {completed.returncode}: {message}")
    return completed.stdout


def conf_quote(value: str) -> str:
    """Quote a value for postgresql.conf (single quotes doubled)."""
    return "'" + value.replace("'", "''") + "'"


def conf_path(path: Path) -> str:
    """A path for postgresql.conf. Forward slashes avoid backslash escapes; Windows accepts them."""
    return conf_quote(path.resolve().as_posix())


def command_path(path: Path | str) -> str:
    """A path inside archive_command/restore_command, double-quoted for cmd.exe and sh alike."""
    return '"' + Path(path).as_posix() + '"'


@dataclass
class Instance:
    """A PostgreSQL data directory controlled with pg_ctl on a TCP port of 127.0.0.1."""

    data_dir: Path
    port: int
    bin_dir: Path | None = None
    user: str = "postgres"

    @property
    def log_file(self) -> Path:
        return self.data_dir.parent / f"{self.data_dir.name}.log"

    def initdb(self, *, extra: list[str] | None = None) -> None:
        run(
            [
                tool("initdb", self.bin_dir),
                "-D",
                str(self.data_dir),
                "-U",
                self.user,
                "-A",
                "trust",
                "--encoding=UTF8",
                *(extra or []),
            ]
        )

    def append_conf(self, settings: dict[str, str], file: str = "postgresql.auto.conf") -> None:
        """Append settings (already quoted values). Later lines win in PostgreSQL configuration."""
        lines = "".join(f"{key} = {value}\n" for key, value in settings.items())
        with (self.data_dir / file).open("a", encoding="utf-8") as handle:
            handle.write("\n# districom_backup\n" + lines)

    def start(self, *, timeout: int = 300, check: bool = True) -> bool:
        """Start and wait. With ``check=False`` return whether the server came up.

        pg_ctl's output goes to a file, never to a pipe: the server it launches inherits those
        handles, and on Windows a pipe held open by the running server never reaches end-of-file,
        so reading pg_ctl's output would wait forever.
        """
        output = self.data_dir.parent / f"{self.data_dir.name}.pg_ctl.txt"
        with output.open("w", encoding="utf-8") as handle:
            completed = subprocess.run(
                [
                    tool("pg_ctl", self.bin_dir),
                    "-D",
                    str(self.data_dir),
                    "-l",
                    str(self.log_file),
                    "-o",
                    self._server_options(),
                    "-w",
                    "-t",
                    str(timeout),
                    "start",
                ],
                stdin=subprocess.DEVNULL,
                stdout=handle,
                stderr=subprocess.STDOUT,
                timeout=timeout + 60,
                check=False,
            )
        if completed.returncode != 0 and check:
            message = output.read_text(encoding="utf-8", errors="replace")
            raise PgToolError(f"pg_ctl start failed: {message}\n{self.read_log()}")
        return completed.returncode == 0

    def _server_options(self) -> str:
        options = f"-p {self.port} -c listen_addresses=127.0.0.1"
        if sys.platform != "win32":
            # TCP only, like on Windows; also avoids needing a writable socket directory.
            options += " -c unix_socket_directories=''"
        return options

    def stop(self, mode: str = "fast", *, timeout: int = 300) -> None:
        if not self.is_running():
            return
        run(
            [tool("pg_ctl", self.bin_dir), "-D", str(self.data_dir), "-m", mode, "-w", "-t", str(timeout), "stop"],
            timeout=timeout + 60,
        )

    def postmaster_pid(self) -> int | None:
        pid_file = self.data_dir / "postmaster.pid"
        try:
            first = pid_file.read_text(encoding="utf-8").splitlines()[0]
        except (FileNotFoundError, IndexError):
            return None
        return int(first)

    def is_running(self) -> bool:
        completed = subprocess.run(
            [tool("pg_ctl", self.bin_dir), "-D", str(self.data_dir), "status"],
            capture_output=True,
            text=True,
            check=False,
        )
        return completed.returncode == 0

    def kill(self) -> None:
        """Crash the server: kill the postmaster and every backend at once, without a checkpoint."""
        pid = self.postmaster_pid()
        if pid is None:
            raise PgToolError("server is not running")
        if sys.platform == "win32":
            run(["taskkill", "/F", "/T", "/PID", str(pid)], check=False)
        else:
            children = run(["pgrep", "-P", str(pid)], check=False).split()
            for target in [pid, *map(int, children)]:
                with contextlib.suppress(ProcessLookupError):
                    os.kill(target, signal.SIGKILL)
        deadline = time.monotonic() + 60
        while self.is_running() and time.monotonic() < deadline:
            time.sleep(0.2)

    def psql(self, sql: str, *, dbname: str = "postgres", check: bool = True) -> str:
        return run(
            [
                tool("psql", self.bin_dir),
                "-X",
                "-q",
                "-At",
                "-v",
                "ON_ERROR_STOP=1",
                "-h",
                "127.0.0.1",
                "-p",
                str(self.port),
                "-U",
                self.user,
                "-d",
                dbname,
                "-c",
                sql,
            ],
            check=check,
            timeout=300,
        ).strip()

    def read_log(self) -> str:
        try:
            return self.log_file.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return ""
