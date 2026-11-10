"""Paths of an installation with services (W2-C2), on top of the C1 layout. One place, so nothing guesses."""

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
    def record(self) -> Path:
        return self.root / "state" / "services.json"

    @property
    def pg_data(self) -> Path:
        return self.services.pg_data_dir

    def artifact(self, name: str) -> Artifact:
        return manifest()[name]

    def pg_bin(self, tool: str) -> Path:
        return self.artifact("postgresql").path(self.runtime) / "bin" / f"{tool}.exe"

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
    def database(self) -> tuple[str, str, str, int]:
        """(user, password, database, port) of the application, from DATABASE_URL."""
        parts = urlsplit(self.ops.database_url.reveal())
        return unquote(parts.username or ""), unquote(parts.password or ""), parts.path.lstrip("/"), parts.port or 5432
