"""PostgreSQL 17 cluster for the service (W2-C2): created once, never recreated over existing data.

The cluster uses the production choices of ``docs/WINDOWS_DEPLOYMENT.md §2``: UTF-8, ICU ``es-AR``, data
checksums, SCRAM, ``127.0.0.1`` only and the safe log settings of O7, with the log collector writing to
``<root>\\logs\\postgresql``. The superuser password is random and only stored in ``config\\postgres.secret``,
readable by administrators. The application role owns only its own database.
"""

import logging
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

from districom_ops import acl
from districom_ops.install import Install

PG_SERVICE = "DistriComPostgreSQL"
CONF = "districom.conf"
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")
log = logging.getLogger("districom_ops")


class DatabaseError(Exception):
    pass


def settings(install: Install) -> str:
    _, _, _, port = install.database
    log_dir = install.postgres_logs.as_posix()
    return "\n".join(
        [
            "# Written by districom-ops (W2-C2). The O7 safe log settings: no statements, no parameters.",
            "listen_addresses = '127.0.0.1'",
            f"port = {port}",
            "max_connections = 100",
            "logging_collector = on",
            f"log_directory = '{log_dir}'",
            "log_filename = 'postgresql-%Y-%m-%d.log'",
            "log_rotation_age = 1d",
            "log_rotation_size = 100MB",
            "log_error_verbosity = terse",
            "log_min_error_statement = panic",
            "log_statement = none",
            "log_min_duration_statement = -1",
            "log_min_duration_sample = -1",
            "log_transaction_sample_rate = 0",
            "log_duration = off",
            "log_parameter_max_length = 0",
            "log_parameter_max_length_on_error = 0",
            "",
        ]
    )


def _run(args: list[str], *, env: dict[str, str] | None = None, stdin: str | None = None, out: Path) -> int:
    """Output to a file, never a pipe: pg_ctl start leaves the server holding inherited handles (W2-A)."""
    with out.open("ab") as handle:
        process = subprocess.run(  # noqa: S603
            args,
            input=stdin.encode("utf-8") if stdin is not None else None,
            stdout=handle,
            stderr=subprocess.STDOUT,
            env={**os.environ, **(env or {}), "PGCLIENTENCODING": "UTF8"},
            timeout=300,
            check=False,
        )
    return process.returncode


def superuser_password(install: Install) -> str:
    try:
        return install.superuser_secret.read_text("utf-8").strip()
    except OSError as exc:
        raise DatabaseError("cannot read config\\postgres.secret (run database init as administrator)") from exc


def init(install: Install) -> bool:
    """Create the cluster and the application role and database. Returns False when the cluster already exists:
    nothing is changed."""
    data = install.pg_data
    install.postgres_logs.mkdir(parents=True, exist_ok=True)
    if (data / "PG_VERSION").exists():
        if f"include = '{CONF}'" not in (data / "postgresql.conf").read_text("utf-8"):
            raise DatabaseError("the cluster exists but was not created by districom-ops; not touching it")
        return False
    if data.exists() and any(data.iterdir()):
        raise DatabaseError("the PostgreSQL data folder is not empty and has no cluster; not touching it")
    user, password, database, port = install.database
    if not (IDENTIFIER.match(user) and IDENTIFIER.match(database)):
        raise DatabaseError("DATABASE_URL user and database must be lowercase identifiers")

    superuser = secrets.token_urlsafe(32)
    install.superuser_secret.parent.mkdir(parents=True, exist_ok=True)
    install.superuser_secret.write_text(superuser, encoding="utf-8")
    acl.restrict(install.superuser_secret, readers=[])
    output = install.postgres_logs / "init.log"
    # initdb runs with a restricted token: the Administrators group only denies, and an elevated administrator's
    # files are owned by that group. The password file is therefore readable by administrators, SYSTEM and this
    # user's own SID, nobody else, and is deleted right after.
    pwfile = install.root / "config" / "initdb.pw"
    pwfile.write_text(superuser, encoding="utf-8")
    try:
        if sys.platform == "win32":
            acl.restrict(pwfile, readers=[acl.current_user_sid()])
        code = _run(
            [
                str(install.pg_bin("initdb")), "-D", str(data), "-U", "postgres", f"--pwfile={pwfile}",
                "-A", "scram-sha-256", "--encoding=UTF8", "--locale-provider=icu", "--icu-locale=es-AR",
                "--locale=C", "--data-checksums",
            ],
            out=output,
        )  # fmt: skip
    finally:
        pwfile.unlink(missing_ok=True)
    if code != 0:
        raise DatabaseError(f"initdb failed ({code}); see logs\\postgresql\\init.log")
    (data / CONF).write_text(settings(install), encoding="utf-8")
    with (data / "postgresql.conf").open("a", encoding="utf-8") as handle:
        handle.write(f"\ninclude = '{CONF}'\n")

    pg_ctl = str(install.pg_bin("pg_ctl"))
    code = _run([pg_ctl, "-D", str(data), "-l", str(install.postgres_logs / "init-server.log"), "-w", "-t", "120",
                 "start"], out=output)  # fmt: skip
    if code != 0:
        raise DatabaseError(f"the temporary server did not start ({code}); see logs\\postgresql")
    try:
        quoted = password.replace("'", "''")
        sql = (
            f"CREATE ROLE \"{user}\" LOGIN PASSWORD '{quoted}';\n"
            f'CREATE DATABASE "{database}" OWNER "{user}";\n'
            f'REVOKE ALL ON DATABASE "{database}" FROM PUBLIC;\n'
        )
        code = _run(
            [str(install.pg_bin("psql")), "-X", "-q", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
             "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-f", "-"],
            env={"PGPASSWORD": superuser},
            stdin=sql,
            out=output,
        )  # fmt: skip
        if code != 0:
            raise DatabaseError(f"creating the application role and database failed ({code})")
    finally:
        _run([pg_ctl, "-D", str(data), "-m", "fast", "-w", "-t", "120", "stop"], out=output)
    # The service account gets its permissions from `services install`, once the service (and its SID) exists.
    log.info("database_initialized", extra={"locale": "icu es-AR", "checksums": True})
    return True


def ready(install: Install) -> bool:
    _, _, _, port = install.database
    result = subprocess.run(  # noqa: S603
        [str(install.pg_bin("pg_isready")), "-q", "-h", "127.0.0.1", "-p", str(port), "-t", "3"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
        check=False,
    )
    return result.returncode == 0


def app_connections(install: Install) -> int | None:
    """Sessions of the application role other than this one; None when it cannot be asked (server down)."""
    user, password, name, port = install.database
    sql = "SELECT count(*) FROM pg_stat_activity WHERE usename = current_user AND pid <> pg_backend_pid();"
    try:
        result = subprocess.run(  # noqa: S603
            [str(install.pg_bin("psql")), "-X", "-A", "-t", "-h", "127.0.0.1", "-p", str(port), "-U", user,
             "-d", name, "-f", "-"],
            input=sql.encode("utf-8"),
            capture_output=True,
            env={**os.environ, "PGPASSWORD": password, "PGCLIENTENCODING": "UTF8", "PGCONNECT_TIMEOUT": "5"},
            timeout=30,
            check=False,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = result.stdout.decode("utf-8", "replace").strip()
    return int(text) if result.returncode == 0 and text.isdigit() else None
