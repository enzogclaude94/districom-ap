"""PostgreSQL 17 cluster for the service (W2-C2): created once, never recreated over existing data.

The cluster uses the production choices of ``docs/WINDOWS_DEPLOYMENT.md §2``: UTF-8, ICU ``es-AR``, data
checksums, SCRAM, ``127.0.0.1`` only and the safe log settings of O7, with the log collector writing to
``<root>\\logs\\postgresql``. The superuser password is random and only stored in ``config\\postgres.secret``,
readable by administrators.

**Roles (C9-B, D1 of C9, Enzo 2026-11-11):**

* the operations role of ``OPS_DATABASE_URL`` (``districom_ops``): ``LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE``,
  owner of the application database (so it can set ``ALLOW_CONNECTIONS``), member of ``pg_signal_backend`` (to end
  the application's sessions) and ``pg_read_all_stats`` (to see whether they are in a transaction). Its password
  is in ``config\\districom.env``, which only the stack and administrators read (C3), like ``DATABASE_URL``'s.
* the application role of ``DATABASE_URL``: no longer the owner of its database. It keeps ``CONNECT``,
  ``CREATE`` and ``TEMPORARY`` on it, owns the ``public`` schema and everything it creates, and is member of no
  role. ``pg_hba.conf`` lets it reach only its database: any other one (``postgres``, ``template1``) is rejected.
  The migrations keep their contract: they run as this role and create their schemas, tables and extensions.
* ``districom_backup`` (W2-C6): ``LOGIN REPLICATION`` (for ``pg_basebackup``) and member of ``pg_read_all_data``
  (for ``pg_dump``), with ``CONNECT`` on the application database. Not a superuser, owner of nothing, it cannot
  change ``ALLOW_CONNECTIONS`` nor write any table. Its random password is only in ``config\\backup-db.secret``,
  which C3 lets only administrators and the backup tasks read.
* ``postgres``: the superuser, for administrators.

WAL archiving (W2-C6) lives in ``districom-archive.conf``, which ``tasks install`` writes (it knows the release and
the repository) and ``districom.conf`` includes if it exists: a cluster without the tasks installed does not archive.

Nothing else is changed: no global revocation, no other role or database.
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
ARCHIVE_CONF = "districom-archive.conf"
BACKUP_ROLE = "districom_backup"
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
            "# W2-C6: archive_mode, archive_command and restore_command, written by tasks install.",
            f"include_if_exists = '{ARCHIVE_CONF}'",
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


def hba(install: Install) -> str:
    """First match wins: the application role reaches only its database, everything else is the initdb default."""
    user, _, database, _ = install.database
    return "\n".join(
        [
            "# Written by districom-ops (W2-C9-B). First match wins; no other role or database is restricted.",
            f"host    {database}    {user}    127.0.0.1/32    scram-sha-256",
            f"host    {database}    {user}    ::1/128         scram-sha-256",
            f"host    all    {user}    all    reject",
            f"local   all    {user}    reject",
            "local   all            all                     scram-sha-256",
            "host    all            all     127.0.0.1/32    scram-sha-256",
            "host    all            all     ::1/128         scram-sha-256",
            "local   replication    all                     scram-sha-256",
            "host    replication    all     127.0.0.1/32    scram-sha-256",
            "host    replication    all     ::1/128         scram-sha-256",
            "",
        ]
    )


def init_sql(install: Install, backup_password: str) -> str:
    user, password, database, _ = install.database
    ops_user, ops_pw, _ = install.ops_database

    def literal(text: str) -> str:
        return "'" + text.replace("'", "''") + "'"

    return (
        f'CREATE ROLE "{ops_user}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION '
        f"PASSWORD {literal(ops_pw)};\n"
        f'GRANT pg_signal_backend, pg_read_all_stats TO "{ops_user}";\n'
        f'CREATE ROLE "{user}" LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD {literal(password)};\n'
        f'CREATE DATABASE "{database}" OWNER "{ops_user}";\n'
        f'REVOKE ALL ON DATABASE "{database}" FROM PUBLIC;\n'
        f'GRANT CONNECT, CREATE, TEMPORARY ON DATABASE "{database}" TO "{user}";\n'
        f'CREATE ROLE "{BACKUP_ROLE}" LOGIN REPLICATION NOSUPERUSER NOCREATEDB NOCREATEROLE '
        f"PASSWORD {literal(backup_password)};\n"
        f'GRANT pg_read_all_data TO "{BACKUP_ROLE}";\n'
        f'GRANT CONNECT ON DATABASE "{database}" TO "{BACKUP_ROLE}";\n'
        f'\\connect "{database}"\n'
        f'ALTER SCHEMA public OWNER TO "{user}";\n'
    )


def superuser_password(install: Install) -> str:
    try:
        return install.superuser_secret.read_text("utf-8").strip()
    except OSError as exc:
        raise DatabaseError("cannot read config\\postgres.secret (run database init as administrator)") from exc


def init(install: Install) -> bool:
    """Create the cluster, the roles and the application database. Returns False when the cluster already exists:
    nothing is changed (a cluster created before C9-B keeps its old model, without the operations role: the write
    block then cannot connect, refuses to act, and a block or a recovery leaves the application blocked)."""
    data = install.pg_data
    install.postgres_logs.mkdir(parents=True, exist_ok=True)
    if (data / "PG_VERSION").exists():
        if f"include = '{CONF}'" not in (data / "postgresql.conf").read_text("utf-8"):
            raise DatabaseError("the cluster exists but was not created by districom-ops; not touching it")
        return False
    if data.exists() and any(data.iterdir()):
        raise DatabaseError("the PostgreSQL data folder is not empty and has no cluster; not touching it")
    user, _, database, port = install.database
    ops_user, ops_pw, ops_db = install.ops_database
    if not (IDENTIFIER.match(user) and IDENTIFIER.match(database)) or user == BACKUP_ROLE:
        raise DatabaseError("DATABASE_URL user and database must be lowercase identifiers")
    if not (IDENTIFIER.match(ops_user) and ops_pw and ops_db == "postgres") or ops_user in (
        user,
        "postgres",
        BACKUP_ROLE,
    ):
        raise DatabaseError("OPS_DATABASE_URL needs its own lowercase role, a password and the postgres database")

    superuser = secrets.token_urlsafe(32)
    install.superuser_secret.parent.mkdir(parents=True, exist_ok=True)
    install.superuser_secret.write_text(superuser, encoding="utf-8")
    acl.restrict(install.superuser_secret, readers=[])
    backup_password = secrets.token_urlsafe(32)
    install.backup_secret.write_text(backup_password, encoding="utf-8")
    acl.restrict(install.backup_secret, readers=[])
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
    (data / "pg_hba.conf").write_text(hba(install), encoding="utf-8")
    with (data / "postgresql.conf").open("a", encoding="utf-8") as handle:
        handle.write(f"\ninclude = '{CONF}'\n")

    pg_ctl = str(install.pg_bin("pg_ctl"))
    code = _run([pg_ctl, "-D", str(data), "-l", str(install.postgres_logs / "init-server.log"), "-w", "-t", "120",
                 "start"], out=output)  # fmt: skip
    if code != 0:
        raise DatabaseError(f"the temporary server did not start ({code}); see logs\\postgresql")
    try:
        sql = init_sql(install, backup_password)
        code = _run(
            [str(install.pg_bin("psql")), "-X", "-q", "-h", "127.0.0.1", "-p", str(port), "-U", "postgres",
             "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-f", "-"],
            env={"PGPASSWORD": superuser},
            stdin=sql,
            out=output,
        )  # fmt: skip
        if code != 0:
            raise DatabaseError(f"creating the roles and the application database failed ({code})")
    finally:
        _run([pg_ctl, "-D", str(data), "-m", "fast", "-w", "-t", "120", "stop"], out=output)
    # The service account gets its permissions from `services install`, once the service (and its SID) exists.
    log.info("database_initialized", extra={"locale": "icu es-AR", "checksums": True, "roles": "c9b"})
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


class QueryError(DatabaseError):
    """A query that did not run. ``code`` says why (``auth``, ``rejected``, ``unreachable``, ``not_accepting``,
    ``sql``, ``timeout``); the message never carries values, only the role and the database."""

    def __init__(self, code: str, role: str, dbname: str) -> None:
        super().__init__(f"query as {role} on {dbname} failed: {code}")
        self.code = code


_ERRORS = (
    ("password authentication failed", "auth"),
    ("no pg_hba.conf entry", "rejected"),
    ("pg_hba.conf rejects", "rejected"),
    ("not currently accepting connections", "not_accepting"),
    ("could not connect", "unreachable"),
    ("connection refused", "unreachable"),
    ("connection to server", "unreachable"),
)


def query(install: Install, sql: str, *, role: str, password: str, dbname: str, timeout: float = 60) -> list[str]:
    """Run ``sql`` with psql and return the non-empty lines of its unaligned output (W2-C9-B). The statement goes
    by stdin, never on the command line; the output is read only here and never logged."""
    _, _, _, port = install.database
    env = {
        **os.environ,
        "PGPASSWORD": password,
        "PGCLIENTENCODING": "UTF8",
        "PGCONNECT_TIMEOUT": "5",
        "PGAPPNAME": "districom_ops",
        "LC_MESSAGES": "C",
        "LANG": "C",
    }
    try:
        result = subprocess.run(  # noqa: S603
            [str(install.pg_bin("psql")), "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1", "-h", "127.0.0.1",
             "-p", str(port), "-U", role, "-d", dbname, "-f", "-"],
            input=sql.encode("utf-8"),
            capture_output=True,
            env=env,
            timeout=timeout,
            check=False,
        )  # fmt: skip
    except subprocess.TimeoutExpired:
        raise QueryError("timeout", role, dbname) from None
    except OSError:
        raise QueryError("unreachable", role, dbname) from None
    if result.returncode != 0:
        text = result.stderr.decode("utf-8", "replace").lower()
        code = next((c for needle, c in _ERRORS if needle in text), "sql")
        raise QueryError(code, role, dbname)
    return [line for line in result.stdout.decode("utf-8", "replace").splitlines() if line.strip()]
