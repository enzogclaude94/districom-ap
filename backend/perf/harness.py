"""What the generator and the benchmark share: the separate database, the app in process and a query recorder.

The app reads its settings when first imported, so `use_perf_database()` must run before anything imports `app`.
"""

import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

HEADERS = {"X-Requested-With": "perf"}
PASSWORD = "perf-password-123"  # noqa: S105 - a throwaway user of the throwaway performance database


def use_perf_database() -> str:
    """Point the app at the performance database; refuse to run against anything else."""
    url = os.environ.get("PERF_DATABASE_URL", "")
    if "perf" not in url.rsplit("/", 1)[-1]:
        sys.exit("PERF_DATABASE_URL must name a separate database whose name contains 'perf' (never dev, test or E2E).")
    os.environ["DATABASE_URL"] = url
    os.environ.setdefault("SECRET_KEY", "perf-0123456789abcdef0123456789abcdef")
    os.environ.setdefault("COOKIE_SECURE", "false")
    return url


def cli(*args: str, password: str | None = None) -> None:
    """`python -m app.cli` against the performance database."""
    subprocess.run(  # noqa: S603 - fixed command, our own CLI
        [sys.executable, "-m", "app.cli", *args],
        input=f"{password}\n" if password else None,
        text=True,
        check=True,
        capture_output=True,
    )


@dataclass
class Recorder:
    """Every statement the app sends while recording: how many, and which (for EXPLAIN)."""

    recording: bool = False
    statements: list[tuple[str, Any, float]] = field(default_factory=list)

    def install(self) -> None:
        from sqlalchemy import event

        from app.db.session import get_engine

        engine = get_engine()

        @event.listens_for(engine, "before_cursor_execute")
        def _before(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
            conn.info["perf_started"] = time.perf_counter()

        @event.listens_for(engine, "after_cursor_execute")
        def _after(conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool) -> None:
            if self.recording:
                elapsed = time.perf_counter() - conn.info.pop("perf_started", time.perf_counter())
                self.statements.append((statement, parameters, elapsed))

    @contextmanager
    def record(self) -> Iterator[list[tuple[str, Any, float]]]:
        self.statements = []
        self.recording = True
        try:
            yield self.statements
        finally:
            self.recording = False


def client() -> Any:
    """The app in process, through its HTTP stack (FastAPI TestClient): no network in the measure. The request log
    is silenced: writing a line per request would be part of what is measured."""
    import logging

    from fastapi.testclient import TestClient

    from app.main import app

    for name in ("app.access", "httpx"):
        logging.getLogger(name).setLevel(logging.WARNING)
    return TestClient(app, base_url="http://perf.local", headers=HEADERS)


def login(http: Any, email: str) -> None:
    response = http.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    response.raise_for_status()
