"""B-10: pieces of the Linux deployment that Windows reuses as they are.

- ``deploy/sql/verify.sql`` (restore drills and rollbacks) against a database migrated to head.
- The safe log settings of O7: an error keeps its message but never the row values nor the statement.
- ``deploy/caddy/Caddyfile`` with the official Caddy for Windows, pointing at 127.0.0.1 instead of Docker names.
"""

import os
import secrets
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, make_url, text

from checks.conftest import COMPAT_URL, REPO

VERIFY_SQL = REPO / "deploy" / "sql" / "verify.sql"
CADDYFILE = REPO / "deploy" / "caddy" / "Caddyfile"


def _required_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        pytest.fail(f"{name} must be set (the CI job sets it); a check that silently skips proves nothing")
    return value


def _psql(sql_file: Path) -> str:
    url = make_url(COMPAT_URL)
    suffix = ".exe" if os.name == "nt" else ""
    psql = Path(_required_env("DISTRICOM_PG_BIN")) / f"psql{suffix}"
    completed = subprocess.run(
        [
            str(psql),
            "-X",
            "-h",
            str(url.host),
            "-p",
            str(url.port or 5432),
            "-U",
            str(url.username),
            "-d",
            str(url.database),
            "-f",
            str(sql_file),
        ],
        env={**os.environ, "PGPASSWORD": str(url.password), "PGCLIENTENCODING": "UTF8"},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout


@pytest.mark.criterion("B-10")
def test_verify_sql_runs_and_is_deterministic(engine: Engine, metrics: dict[str, Any]) -> None:
    with engine.connect() as conn:
        head = conn.scalar(text("SELECT version_num FROM public.alembic_version"))
    first = _psql(VERIFY_SQL)
    second = _psql(VERIFY_SQL)
    assert first == second
    lines = first.splitlines()
    assert f"alembic_version {head}" in lines
    assert any(line.startswith("structure_md5 ") for line in lines)
    metrics["B-10 verify.sql"] = {"líneas": len(lines), "versión": head}


@pytest.mark.criterion("B-10")
def test_safe_log_settings_keep_values_out_of_the_log(engine: Engine) -> None:
    log = Path(_required_env("DISTRICOM_PG_LOG"))
    with engine.connect() as conn:
        settings: dict[str, str] = dict(
            conn.execute(
                text(
                    "SELECT name, setting FROM pg_settings WHERE name IN"
                    " ('log_error_verbosity', 'log_min_error_statement', 'log_statement', 'log_parameter_max_length',"
                    " 'log_parameter_max_length_on_error')"
                )
            ).all()
        )
    assert settings == {
        "log_error_verbosity": "terse",
        "log_min_error_statement": "panic",
        "log_statement": "none",
        "log_parameter_max_length": "0",
        "log_parameter_max_length_on_error": "0",
    }

    literal = f"w2b-fictitious-{secrets.token_hex(8)}"
    with engine.connect() as conn:
        conn.execute(text("CREATE TEMP TABLE w2b_log_probe (value text PRIMARY KEY)"))
        conn.execute(text("INSERT INTO w2b_log_probe VALUES (:v)"), {"v": literal})
        with pytest.raises(Exception, match="duplicate key"):
            conn.execute(text(f"INSERT INTO w2b_log_probe VALUES ('{literal}')"))
        conn.rollback()
    content = log.read_text(encoding="utf-8", errors="replace")
    assert 'duplicate key value violates unique constraint "w2b_log_probe_pkey"' in content
    assert literal not in content


@pytest.mark.criterion("B-10")
def test_caddyfile_validates_with_caddy_for_windows(tmp_path: Path, metrics: dict[str, Any]) -> None:
    caddy = _required_env("DISTRICOM_CADDY")
    # On Windows there is no Docker network: the same file, with the upstreams on 127.0.0.1 (W2-C will take them from
    # variables). Nothing else changes.
    adapted = (
        CADDYFILE.read_text(encoding="utf-8")
        .replace("reverse_proxy backend:8000", "reverse_proxy 127.0.0.1:8000")
        .replace("reverse_proxy frontend:3000", "reverse_proxy 127.0.0.1:3000")
    )
    assert adapted.count("127.0.0.1:") == 2
    config = tmp_path / "Caddyfile"
    config.write_text(adapted, encoding="utf-8")

    def validate(mode: str) -> subprocess.CompletedProcess[str]:
        env = {
            **os.environ,
            "DISTRICOM_HOST": "districom.example.test",
            "CADDY_TLS_MODE": mode,
            "CLOUDFLARE_API_TOKEN": "not-a-real-token",
        }
        return subprocess.run(
            [caddy, "validate", "--config", str(config), "--adapter", "caddyfile"],
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )

    internal = validate("internal")
    assert internal.returncode == 0, internal.stderr
    # The official build has no Cloudflare DNS module; production needs a build with it (W2-C, as the Linux image).
    cloudflare = validate("cloudflare")
    metrics["B-10 Caddyfile"] = {
        "modo internal": "válido",
        "modo cloudflare con el binario oficial": "válido"
        if cloudflare.returncode == 0
        else "requiere un binario con caddy-dns/cloudflare",
    }
