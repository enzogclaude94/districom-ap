"""B-6: the backend as a real process on Windows, configured as in production.

uvicorn runs ``app.main:app`` on 127.0.0.1 with APP_ENV=production, a role that is not a superuser and owns only
its own disposable database, and random secrets. W2-C wraps this same process in a Windows service (WinSW); the
service itself is not tested here.
"""

import json
import os
import secrets
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, make_url, text

from app.core.middleware import CSRF_HEADER
from checks.conftest import BACKEND, COMPAT_URL

ROLE = "districom_w2b_app"
DATABASE = "districom_w2b_app"
ADMIN_EMAIL = "admin-w2b@districom.local"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def _base_env() -> dict[str, str]:
    """The runner's environment (Windows needs SYSTEMROOT and friends) without the test settings of conftest."""
    env = dict(os.environ)
    for key in ("DATABASE_URL", "SECRET_KEY", "APP_ENV", "ARGON2_PROFILE", "COOKIE_SECURE", "CORS_ORIGINS"):
        env.pop(key, None)
    return env


@dataclass
class Production:
    url: str
    env: dict[str, str]
    admin_password: str
    database_password: str
    secret_key: str


@pytest.fixture(scope="module")
def production(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Production]:
    database_password = secrets.token_urlsafe(24)
    admin = create_engine(COMPAT_URL, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)"))
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        conn.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD '{database_password}'"))
        conn.execute(text(f"CREATE DATABASE {DATABASE} OWNER {ROLE}"))
    url = make_url(COMPAT_URL).set(username=ROLE, password=database_password, database=DATABASE)
    url_text = url.render_as_string(hide_password=False)
    secret_key = secrets.token_urlsafe(48)
    env = {
        **_base_env(),
        "APP_ENV": "production",
        "DATABASE_URL": url_text,
        "SECRET_KEY": secret_key,
        "COOKIE_SECURE": "true",
        "CORS_ORIGINS": "",
        "ARGON2_PROFILE": "standard",
        "LOG_LEVEL": "INFO",
        "UPLOAD_DIR": str(tmp_path_factory.mktemp("uploads")),
        "PYTHONIOENCODING": "utf-8",
    }
    # Migrations as the application's own role, as the Windows install will run them.
    subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], cwd=BACKEND, env=env, check=True, timeout=600)
    admin_password = secrets.token_urlsafe(18)
    subprocess.run(
        [sys.executable, "-m", "app.cli", "create-admin", "--email", ADMIN_EMAIL, "--name", "Admin W2-B"],
        cwd=BACKEND,
        env=env,
        input=admin_password + "\n",
        text=True,
        check=True,
        timeout=120,
    )
    yield Production(url_text, env, admin_password, database_password, secret_key)
    with admin.connect() as conn:
        conn.execute(text(f"DROP DATABASE IF EXISTS {DATABASE} WITH (FORCE)"))
        conn.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
    admin.dispose()


def _app_connections() -> int:
    engine = create_engine(COMPAT_URL)
    try:
        with engine.connect() as conn:
            count = conn.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE usename = :role"), {"role": ROLE})
            return int(count or 0)
    finally:
        engine.dispose()


def _get(url: str) -> tuple[int, dict[str, Any]]:
    try:
        with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 (local http URL built here)
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


@pytest.mark.criterion("B-6")
def test_backend_serves_and_stops_cleanly(production: Production, tmp_path: Path, metrics: dict[str, Any]) -> None:
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    output = tmp_path / "uvicorn.txt"
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP  # so CTRL_BREAK reaches only uvicorn
    started = time.monotonic()
    # Output to a file, never a pipe (W2-A finding on Windows).
    with output.open("wb") as handle:
        process = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=BACKEND,
            env=production.env,
            stdin=subprocess.DEVNULL,
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    try:
        status = 0
        while time.monotonic() - started < 120:
            if process.poll() is not None:
                break
            try:
                status, _ = _get(f"{base}/api/v1/health/live")
            except OSError:
                status = 0
            if status == 200:
                break
            time.sleep(0.25)
        startup = time.monotonic() - started
        log = output.read_text(encoding="utf-8", errors="replace")
        assert status == 200, log

        ready_status, ready = _get(f"{base}/api/v1/health/ready")
        assert ready_status == 200, ready

        login_started = time.monotonic()
        request = urllib.request.Request(  # noqa: S310
            f"{base}/api/v1/auth/login",
            data=json.dumps({"email": ADMIN_EMAIL, "password": production.admin_password}).encode(),
            headers={"Content-Type": "application/json", CSRF_HEADER: "1"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            assert response.status == 200
            cookies = response.headers.get_all("Set-Cookie") or []
        login_seconds = time.monotonic() - login_started
        assert cookies, "no session cookie"
        assert all("secure" in cookie.lower() and "httponly" in cookie.lower() for cookie in cookies), cookies
        assert _app_connections() > 0

        stop_started = time.monotonic()
        if sys.platform == "win32":
            # What a Windows service wrapper sends: there is no SIGTERM on Windows.
            os.kill(process.pid, signal.CTRL_BREAK_EVENT)
        else:
            process.send_signal(signal.SIGTERM)
        exit_code = process.wait(timeout=60)
        stop_seconds = time.monotonic() - stop_started
        log = output.read_text(encoding="utf-8", errors="replace")
        assert "Finished server process" in log, log
        deadline = time.monotonic() + 30
        while _app_connections() and time.monotonic() < deadline:
            time.sleep(0.5)
        assert _app_connections() == 0
        for secret in (production.secret_key, production.database_password, production.admin_password):
            assert secret not in log
        metrics["B-6"] = {
            "arranque hasta /health/live (s)": round(startup, 1),
            "login con Argon2 estándar (s)": round(login_seconds, 2),
            "parada ordenada (s)": round(stop_seconds, 1),
            "código de salida": exit_code,
        }
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


@pytest.mark.criterion("B-6")
def test_insecure_production_config_is_refused_without_printing_secrets(production: Production) -> None:
    weak = "change-me-change-me-change-me-change-me"
    env = {**production.env, "SECRET_KEY": weak}
    completed = subprocess.run(
        [sys.executable, "-c", "import app.main"],
        cwd=BACKEND,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    output = completed.stdout + completed.stderr
    assert completed.returncode != 0
    assert "Configuración insegura" in output
    assert weak not in output
    assert production.database_password not in output
