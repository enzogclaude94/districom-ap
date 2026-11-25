"""Paths of an installation with services (W2-C2), on top of the C1 layout. One place, so nothing guesses."""

import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlsplit

from districom_ops.artifacts import Artifact, manifest
from districom_ops.config import OpsConfig, ServicesConfig
from districom_ops.layout import Layout


@dataclass(frozen=True)
class Install:
    ops: OpsConfig
    services: ServicesConfig
    release: str | None = None

    @property
    def layout(self) -> Layout:
        return Layout(self.ops.root)

    @property
    def root(self) -> Path:
        return self.ops.root

    @property
    def runtime(self) -> Path:
        return self.root / "runtime"

    @property
    def downloads(self) -> Path:
        return self.root / "downloads"

    @property
    def service_dir(self) -> Path:
        return self.root / "services"

    @property
    def service_logs(self) -> Path:
        return self.root / "logs" / "services"

    def service_log_dir(self, name: str) -> Path:
        """Each WinSW service logs in its own folder, writable only by its account (C3)."""
        return self.service_logs / name

    @property
    def frontend_cache(self) -> Path:
        """Next.js writes ``.next\\cache`` at run time; it lives here, not in the read-only release (C3)."""
        return self.root / "state" / "frontend-cache"

    @property
    def integrity_record(self) -> Path:
        return self.root / "config" / "integrity.json"

    @property
    def python_runtime(self) -> Path:
        """The Python the release virtual environments use (``UV_PYTHON_INSTALL_DIR``)."""
        return self.runtime / "python"

    @property
    def postgres_logs(self) -> Path:
        return self.root / "logs" / "postgresql"

    @property
    def uploads(self) -> Path:
        return self.root / "data" / "uploads"

    @property
    def caddy_state(self) -> Path:
        return self.root / "state" / "caddy"

    @property
    def backend_env(self) -> Path:
        return self.root / "config" / "backend" / ".env"

    @property
    def caddyfile(self) -> Path:
        return self.root / "config" / "Caddyfile"

    @property
    def superuser_secret(self) -> Path:
        return self.root / "config" / "postgres.secret"

    @property
    def backup_secret(self) -> Path:
        """Password of ``districom_backup`` (W2-C6), written by ``database init``; read by the tasks that connect."""
        return self.root / "config" / "backup-db.secret"

    @property
    def tasks_settings(self) -> Path:
        """``config\\tasks.json`` (W2-C6), written by ``tasks install``. Its presence means the tasks are installed."""
        return self.root / "config" / "tasks.json"

    @property
    def repo1(self) -> Path:
        """The local backup repository (W2-A layout) on the backup disk."""
        return self.services.backup_disk_root / "repo1"

    @property
    def record(self) -> Path:
        return self.root / "state" / "services.json"

    @property
    def pg_data(self) -> Path:
        return self.services.pg_data_dir

    def artifact(self, name: str) -> Artifact:
        return manifest()[name]

    def pg_bin(self, tool: str) -> Path:
        suffix = ".exe" if sys.platform == "win32" else ""
        return self.artifact("postgresql").path(self.runtime) / "bin" / f"{tool}{suffix}"

    def release_dir(self) -> Path:
        if not self.release:
            raise ValueError("no release given")
        return self.root / "releases" / self.release

    @property
    def backend_python(self) -> Path:
        return self.release_dir() / "backend" / ".venv" / "Scripts" / "python.exe"

    @property
    def ops_python(self) -> Path:
        return self.release_dir() / "windows" / "ops" / ".venv" / "Scripts" / "python.exe"

    @property
    def frontend_dir(self) -> Path:
        return self.release_dir() / "frontend"

    @property
    def ops_database(self) -> tuple[str, str, str]:
        """(user, password, database) of the operations role (C9-B), from OPS_DATABASE_URL in districom.env,
        which only the stack and administrators read (C3)."""
        parts = urlsplit(self.ops.ops_database_url.reveal())
        return unquote(parts.username or ""), unquote(parts.password or ""), parts.path.lstrip("/") or "postgres"

    @property
    def database(self) -> tuple[str, str, str, int]:
        """(user, password, database, port) of the application, from DATABASE_URL."""
        parts = urlsplit(self.ops.database_url.reveal())
        return unquote(parts.username or ""), unquote(parts.password or ""), parts.path.lstrip("/"), parts.port or 5432
