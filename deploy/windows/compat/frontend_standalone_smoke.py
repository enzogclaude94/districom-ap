"""B-8: the production frontend (Next.js standalone) on Windows, as the Windows service will run it.

Run after ``NEXT_OUTPUT=standalone API_INTERNAL_URL=http://127.0.0.1:<backend port> pnpm build`` in ``frontend/``.
It assembles the standalone folder as the Linux image does (``.next/static`` and ``public``), starts the backend
with uvicorn and ``node server.js`` on 127.0.0.1, and checks pages, security headers and the ``/api`` rewrite.

Usage: python frontend_standalone_smoke.py <frontend dir> <backend dir> <backend port> <frontend port> <results dir>
The backend reads DATABASE_URL and the rest of its settings from the environment. Standard library only.
"""

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

EXPECTED_HEADERS = {
    "content-security-policy": (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'; object-src 'none'; report-uri /api/v1/csp-report"
    ),
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def fetch(url: str) -> tuple[int, dict[str, str], bytes]:
    try:
        with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 (local URL built here)
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as error:
        return error.code, {k.lower(): v for k, v in error.headers.items()}, error.read()


def wait_for(url: str, process: subprocess.Popen[bytes], seconds: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if process.poll() is not None:
            raise RuntimeError(f"{url}: the process exited with {process.returncode}")
        try:
            status, _, _ = fetch(url)
            if status == 200:
                return time.monotonic() - started
        except OSError:
            pass
        time.sleep(0.5)
    raise TimeoutError(f"{url} did not answer 200 within {seconds} s")


def start(args: list[str], cwd: Path, env: dict[str, str], log: Path) -> subprocess.Popen[bytes]:
    # Output to a file, never a pipe: on Windows a pipe inherited by a long-running child never ends (W2-A).
    with log.open("wb") as handle:
        return subprocess.Popen(
            args, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT
        )


def main() -> int:
    frontend, backend, backend_port, frontend_port, results = sys.argv[1:6]
    frontend_dir, backend_dir, results_dir = Path(frontend), Path(backend), Path(results)
    results_dir.mkdir(parents=True, exist_ok=True)
    standalone = frontend_dir / ".next" / "standalone"
    if not (standalone / "server.js").is_file():
        print("no standalone build: run the build with NEXT_OUTPUT=standalone first", file=sys.stderr)
        return 1
    # The same assembly as frontend/Dockerfile.
    shutil.copytree(frontend_dir / ".next" / "static", standalone / ".next" / "static", dirs_exist_ok=True)
    if (frontend_dir / "public").is_dir():
        shutil.copytree(frontend_dir / "public", standalone / "public", dirs_exist_ok=True)

    env = dict(os.environ)
    processes: list[subprocess.Popen[bytes]] = []
    checks: dict[str, object] = {}
    ok = False
    try:
        api = start(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", backend_port],
            backend_dir,
            env,
            results_dir / "b8-backend.log",
        )
        processes.append(api)
        wait_for(f"http://127.0.0.1:{backend_port}/api/v1/health/live", api, 120)
        node = shutil.which("node")
        if node is None:
            raise RuntimeError("node is not on PATH")
        web = start(
            [node, "server.js"],
            standalone,
            {**env, "NODE_ENV": "production", "PORT": frontend_port, "HOSTNAME": "127.0.0.1"},
            results_dir / "b8-frontend.log",
        )
        processes.append(web)
        base = f"http://127.0.0.1:{frontend_port}"
        checks["arranque de node server.js (s)"] = round(wait_for(f"{base}/login", web, 120), 1)

        status, headers, body = fetch(f"{base}/login")
        checks["/login"] = status
        assert status == 200
        assert b"<html" in body.lower()
        for name, value in EXPECTED_HEADERS.items():
            assert headers.get(name) == value, f"{name}: {headers.get(name)!r}"
        checks["headers de seguridad"] = "iguales a next.config.ts"
        assert "x-powered-by" not in headers

        status, headers, body = fetch(f"{base}/api/v1/health")
        checks["/api/v1/health por el rewrite"] = status
        assert status == 200
        assert json.loads(body).get("db") == "ok", body

        static = next((standalone / ".next" / "static").rglob("*.js"), None)
        assert static is not None
        # .next/static/<file> is served at /_next/static/<file>.
        relative = static.relative_to(standalone / ".next").as_posix()
        status, _, _ = fetch(f"{base}/_next/{relative}")
        checks["archivo estático"] = status
        assert status == 200
        ok = True
    except Exception as error:
        checks["error"] = f"{type(error).__name__}: {error}"
    finally:
        for process in reversed(processes):
            process.terminate()
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                process.kill()
    checks["resultado"] = "PASS" if ok else "FAIL"
    (results_dir / "b8-standalone.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(checks, indent=2, ensure_ascii=False))
    if not ok:
        for log in ("b8-backend.log", "b8-frontend.log"):
            path = results_dir / log
            if path.exists():
                print(f"--- {log}\n{path.read_text(encoding='utf-8', errors='replace')[-4000:]}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
