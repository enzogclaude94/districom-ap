"""Shared pieces of the integration tests: a primary with archiving, fictitious data and waits."""

import re
import shutil
import socket
import sys
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import psycopg

from districom_backup import pgtools
from districom_backup.base_backup import BaseBackupInfo
from districom_backup.compare import fingerprint
from districom_backup.repo import init_repo
from districom_backup.restore import RestoreOutcome, archive_command, restore
from districom_backup.walnames import DEFAULT_SEGMENT_SIZE, parse_lsn, segno_of_lsn

DBNAME = "pocdb"

# Fictitious data only (Enzo, 2026-11-04). public.alembic_version and settlements.ledger_entries
# exist because deploy/sql/verify.sql reads them; everything else lives in the "poc" schema.
SCHEMA_SQL = """
CREATE TABLE public.alembic_version (version_num varchar(32) PRIMARY KEY);
INSERT INTO public.alembic_version VALUES ('w2a-poc');
CREATE SCHEMA settlements;
CREATE TABLE settlements.ledger_entries (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL,
    amount numeric(14,2) NOT NULL,
    tag text NOT NULL
);
CREATE SCHEMA poc;
CREATE TABLE poc.accounts (id bigint PRIMARY KEY, name text NOT NULL);
CREATE TABLE poc.movements (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id bigint NOT NULL REFERENCES poc.accounts,
    amount numeric(14,2) NOT NULL,
    tag text NOT NULL,
    client_ref text UNIQUE
);
INSERT INTO poc.accounts VALUES
    (1, 'Caja reparto ficticia'), (2, 'Banco ficticio'), (3, 'Cheques ficticios'), (4, 'Pendientes ficticios');
"""


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


@dataclass
class Primary:
    instance: pgtools.Instance
    repo: Path
    root: Path
    restored: list[pgtools.Instance] = field(default_factory=list)

    def connect(self, dbname: str = DBNAME) -> psycopg.Connection[tuple[object, ...]]:
        return psycopg.connect(
            host="127.0.0.1", port=self.instance.port, user=self.instance.user, dbname=dbname, autocommit=True
        )

    def scalar(self, sql: str, dbname: str = DBNAME) -> object:
        with self.connect(dbname) as conn:
            row = conn.execute(sql).fetchone()
            assert row is not None
            return row[0]

    def write(self, rows: int, tag: str) -> None:
        """Business-like writes: movements and their ledger entries, in small transactions."""
        with self.connect() as conn:
            for i in range(rows):
                with conn.transaction():
                    account = 1 + i % 4
                    amount = (i % 97) + 0.25
                    conn.execute(
                        "INSERT INTO poc.movements (account_id, amount, tag) VALUES (%s, %s, %s)",
                        (account, amount, tag),
                    )
                    conn.execute(
                        "INSERT INTO settlements.ledger_entries (account_id, amount, tag) VALUES (%s, %s, %s)",
                        (account, amount, tag),
                    )

    def fingerprint(self) -> str:
        return fingerprint(self.instance, DBNAME)

    def switch_and_wait(self, timeout: float = 180) -> str:
        """Close the current WAL segment and wait until it is in the repository. Returns its name."""
        name = str(self.scalar("SELECT pg_walfile_name(pg_switch_wal())", "postgres"))
        wait_for_file(self.repo / "wal" / name, timeout, self.archiver_report)
        return name

    def wait_lsn_archived(self, lsn: str, timeout: float = 180) -> list[str]:
        """Switch and wait until every segment up to the one holding ``lsn`` is archived."""
        self.switch_and_wait(timeout)
        name = str(self.scalar(f"SELECT pg_walfile_name('{lsn}')", "postgres"))
        wait_for_file(self.repo / "wal" / name, timeout, self.archiver_report)
        return sorted(p.name for p in (self.repo / "wal").iterdir())

    def archiver_report(self) -> str:
        """What the archiver was doing, for a wait that timed out: tells a slow archiver from a failing one."""
        lines = []
        try:
            with self.connect("postgres") as conn:
                row = conn.execute(
                    "SELECT archived_count, last_archived_wal, last_archived_time, failed_count, last_failed_wal,"
                    " last_failed_time, pg_walfile_name(pg_current_wal_lsn()) FROM pg_stat_archiver"
                ).fetchone()
            assert row is not None
            archived_count, last, last_at, failed, last_failed, failed_at, current = row
            lines.append(
                f"pg_stat_archiver: archived={archived_count} last={last} at {last_at}; "
                f"failed={failed} last_failed={last_failed} at {failed_at}; current={current}"
            )
        except psycopg.Error as exc:
            lines.append(f"pg_stat_archiver unavailable: {type(exc).__name__}")
        archived = sorted(p.name for p in (self.repo / "wal").iterdir())
        lines.append(f"repository wal: {len(archived)} files, last {archived[-3:]}")
        log = self.instance.log_file
        if log.exists():
            interesting = [
                line.rstrip()
                for line in log.read_text(encoding="utf-8", errors="replace").splitlines()
                if re.search(r"archiv|FATAL|ERROR|WARNING|PANIC", line)
            ]
            lines.append("server log (archiver, errors), last 20:")
            lines.extend(interesting[-20:])
        return "\n".join(lines)

    def restore(
        self,
        backup: BaseBackupInfo,
        name: str,
        *,
        repo: Path | None = None,
        target_time: str | None = None,
        target_name: str | None = None,
        target_lsn: str | None = None,
        archive_into: Path | None = None,
    ) -> RestoreOutcome:
        """Restore into ``<root>/restored-<name>`` on a free port; stopped when the test ends."""
        outcome = restore(
            repo or self.repo,
            backup,
            self.root / f"restored-{name}",
            port=free_port(),
            target_time=target_time,
            target_name=target_name,
            target_lsn=target_lsn,
            archive_into=archive_into,
        )
        self.restored.append(outcome.instance)
        return outcome


def wait_for_file(path: Path, timeout: float, diagnose: Callable[[], str] | None = None) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        if time.monotonic() > deadline:
            detail = f"\n{diagnose()}" if diagnose else ""
            raise TimeoutError(f"{path.name} did not appear in {path.parent} within {timeout} s{detail}")
        time.sleep(0.25)


def segment_of(lsn: str, segment_size: int = DEFAULT_SEGMENT_SIZE) -> int:
    return segno_of_lsn(parse_lsn(lsn), segment_size)


ICU_ARGS = ["--locale-provider=icu", "--icu-locale=es-AR", "--locale=C", "--data-checksums"]


@contextmanager
def make_primary(root: Path, *, archive_timeout: str = "60s", archive_delay_s: float = 0) -> Iterator[Primary]:
    """``archive_delay_s`` makes every archive_command that much slower, like a loaded runner or disk."""
    repo = init_repo(root / "repo1")
    command = archive_command(repo)
    if archive_delay_s:
        sleep = f'{pgtools.command_path(sys.executable)} -c "import time; time.sleep({archive_delay_s})"'
        command = f"{sleep} && {command}"
    instance = pgtools.Instance(root / "primary", free_port())
    instance.initdb(extra=ICU_ARGS)
    instance.append_conf(
        {
            "wal_level": "'replica'",
            "archive_mode": "'on'",
            "archive_command": pgtools.conf_quote(command),
            "archive_timeout": pgtools.conf_quote(archive_timeout),
            "max_wal_senders": "10",
            "fsync": "on",
            "synchronous_commit": "on",
        }
    )
    instance.start()
    primary = Primary(instance, repo, root)
    try:
        instance.psql(f"CREATE DATABASE {DBNAME}")
        with primary.connect() as conn:
            conn.execute(SCHEMA_SQL)
        yield primary
    finally:
        for other in primary.restored:
            if other.is_running():
                other.stop("immediate")
        if instance.is_running():
            instance.stop("immediate")


def copy_repo(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def unique_dir(root: Path, name: str) -> Path:
    base = re.sub(r"[^A-Za-z0-9_-]+", "-", name)[:60]
    path, n = root / base, 1
    while path.exists():
        n += 1
        path = root / f"{base}-{n}"
    path.mkdir()
    return path
