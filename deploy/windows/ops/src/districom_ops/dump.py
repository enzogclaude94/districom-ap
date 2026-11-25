"""Encrypted daily ``pg_dump`` of the application database and its restore test (W2-C6, D4 and D5).

1. ``pg_dump -Fc`` as ``districom_backup`` (member of ``pg_read_all_data``, no owner of anything) into the work
   folder of the run.
2. Encrypted with ``rclone crypt``, the tool and the encryption W2-A uses for the off-site copy, with its own key
   (``config\\dump.secret``), into ``repo1\\dumps\\<label>.dump.bin``: the file is copied under a temporary name and
   renamed only when complete. Then the plain dump is deleted. Then ``<label>.json`` is written: a dump is listed
   only when both exist, so a half-written one never looks valid.
3. **Restore test** on a disposable instance (``initdb`` in the work folder, ``127.0.0.1`` and its own port, a
   random password, never the production cluster): the *published* encrypted file is decrypted, its SHA-256 must
   be the one of the plain dump, ``pg_restore --exit-on-error`` must succeed, and the tables restored must be the
   tables the dump lists. The rows of each table are counted and recorded. Only then the dump is **verified**.

Being verified means that this dump restores into a clean instance; it is not a test of the whole recovery
(base backup and WAL, C7 and C13). A dump whose restore test failed stays, marked not verified, and the task
fails so C8 can alert.
"""

import contextlib
import hashlib
import json
import secrets
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from districom_backup.offsite import REMOTE_NAME, CryptRemote
from districom_backup.repo import TMP_PREFIX, copy_durable, require_repo, write_atomic

if TYPE_CHECKING:
    from districom_ops.backup_tasks import Context

DUMPS = "dumps"
SUFFIX = ".dump.bin"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1 << 20):
            digest.update(chunk)
    return digest.hexdigest()


def dumps_dir(repo: Path) -> Path:
    return require_repo(repo) / DUMPS


def crypt_env(base: Path, password: str, rclone: Path) -> dict[str, str]:
    """The crypt remote of W2-A over a local folder, with plain file names (``<name>.bin``): the dump label stays
    readable, its content does not. The key goes only into the environment of the rclone process."""
    env = CryptRemote(str(base), password, str(rclone))._env()
    prefix = f"RCLONE_CONFIG_{REMOTE_NAME}_"
    env[f"{prefix}FILENAME_ENCRYPTION"] = "off"
    env[f"{prefix}DIRECTORY_NAME_ENCRYPTION"] = "false"
    return env


def list_dumps(repo: Path) -> list[dict[str, Any]]:
    """Dumps with both their metadata and their encrypted file, oldest first."""
    found = []
    folder = repo / DUMPS
    if not folder.is_dir():
        return []
    for meta in sorted(folder.glob("dump-*.json")):
        if not (folder / f"{meta.stem}{SUFFIX}").is_file():
            continue
        with contextlib.suppress(OSError, ValueError):
            found.append(json.loads(meta.read_text("utf-8")))
    return found


def dump_and_verify(ctx: "Context") -> None:
    from districom_ops.backup_tasks import TaskFailedError, _pg_env, read_secret

    s = ctx.settings
    if s.rclone is None:
        raise TaskFailedError("rclone is not installed: the dump cannot be encrypted")
    folder = dumps_dir(s.repo)
    folder.mkdir(exist_ok=True)
    key = read_secret(s.dump_secret)
    label = datetime.now(UTC).strftime("dump-%Y%m%dT%H%M%SZ")
    work = ctx.work
    work.mkdir(parents=True, exist_ok=True)
    plain = work / f"{label}.dump"
    pg_env = _pg_env(s)
    pg = s.pg_bin
    ctx.run(
        [str(pg / _exe("pg_dump")), "-h", "127.0.0.1", "-p", str(s.pg_port), "-U", s.backup_role, "-d",
         s.app_database, "-Fc", "--no-password", "-f", str(plain)],
        env=pg_env,
        step="pg_dump",
    )  # fmt: skip
    plain_sha = _sha256(plain)
    plain_size = plain.stat().st_size
    staging = work / "crypt"
    staging.mkdir()
    ctx.run(
        [str(s.rclone), "copyto", str(plain), f"{REMOTE_NAME}:{label}.dump"],
        env=crypt_env(staging, key, s.rclone),
        step="encrypt",
    )
    encrypted = staging / f"{label}{SUFFIX}"
    if not encrypted.is_file():
        raise TaskFailedError("rclone did not produce the encrypted dump")
    ctx.check()
    copy_durable(encrypted, folder / f"{label}{SUFFIX}")
    plain.unlink()
    meta: dict[str, Any] = {
        "label": label,
        "database": s.app_database,
        "plain_sha256": plain_sha,
        "plain_bytes": plain_size,
        "encrypted_sha256": _sha256(folder / f"{label}{SUFFIX}"),
        "created_at": datetime.now(UTC).isoformat(),
        "verified": False,
    }
    write_atomic(folder / f"{label}.json", json.dumps(meta, indent=1).encode("utf-8"))
    ctx.detail.update(label=label, created=True, verified=False, plain_bytes=plain_size)
    try:
        result = restore_test(ctx, folder, label, plain_sha, key)
    except TaskFailedError as exc:
        meta["verify_failure"] = str(exc)
        write_atomic(folder / f"{label}.json", json.dumps(meta, indent=1).encode("utf-8"))
        raise
    meta.update(verified=True, verified_at=datetime.now(UTC).isoformat(), restore_test=result)
    write_atomic(folder / f"{label}.json", json.dumps(meta, indent=1).encode("utf-8"))
    ctx.detail.update(verified="restored into a disposable instance", restore_test=result)


def _exe(name: str) -> str:
    import sys

    return f"{name}.exe" if sys.platform == "win32" else name


def restore_test(ctx: "Context", folder: Path, label: str, plain_sha: str, key: str) -> dict[str, Any]:
    from districom_ops.backup_tasks import TaskFailedError

    s = ctx.settings
    assert s.rclone is not None
    pg = s.pg_bin
    work = ctx.work
    check = work / "check.dump"
    ctx.run(
        [str(s.rclone), "copyto", f"{REMOTE_NAME}:{label}.dump", str(check)],
        env=crypt_env(folder, key, s.rclone),
        step="decrypt",
        tell=True,
    )
    if _sha256(check) != plain_sha:
        raise TaskFailedError("the decrypted dump differs from the dump that was taken")
    listing = ctx.run([str(pg / _exe("pg_restore")), "--list", str(check)], step="pg_restore_list")
    listed = sum(1 for line in listing.splitlines() if " TABLE " in line and " TABLE DATA " not in line
                 and not line.startswith(";"))  # fmt: skip
    data = work / "pgcheck"
    password = secrets.token_urlsafe(24)
    pwfile = work / "pgcheck.pw"
    pwfile.write_text(password, encoding="utf-8")
    env = {"PGPASSWORD": password, "PGCONNECT_TIMEOUT": "10"}
    port = str(s.restore_test_port)
    started = False
    try:
        ctx.run(
            [str(pg / _exe("initdb")), "-D", str(data), "-U", "postgres", f"--pwfile={pwfile}", "-A",
             "scram-sha-256", "--encoding=UTF8", "--locale-provider=icu", "--icu-locale=es-AR", "--locale=C"],
            step="initdb",
            tell=True,
        )  # fmt: skip
        pwfile.unlink()
        started = True
        ctx.run(
            [str(pg / _exe("pg_ctl")), "-D", str(data), "-l", str(work / "pgcheck.log"), "-o",
             f"-p {port} -c listen_addresses=127.0.0.1", "-w", "-t", "120", "start"],
            step="restore_instance_start",
            tell=True,
        )  # fmt: skip
        conn = ["-h", "127.0.0.1", "-p", port, "-U", "postgres"]
        ctx.run([str(pg / _exe("createdb")), *conn, "restore_check"], env=env, step="createdb", tell=True)
        ctx.run(
            [str(pg / _exe("pg_restore")), *conn, "-d", "restore_check", "--exit-on-error", "--no-owner",
             "--no-privileges", str(check)],
            env=env,
            step="pg_restore",
        )  # fmt: skip
        count_sql = work / "count.sql"
        count_sql.write_text(
            "SELECT format('SELECT %L || ''|'' || count(*) FROM %I.%I', n.nspname || '.' || c.relname, n.nspname, "
            "c.relname) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace WHERE c.relkind IN ('r', 'p') "
            "AND n.nspname NOT IN ('pg_catalog', 'information_schema') AND n.nspname NOT LIKE 'pg_toast%' "
            "ORDER BY 1 \\gexec\n",
            encoding="utf-8",
        )
        counted = ctx.run(
            [str(pg / _exe("psql")), "-X", "-A", "-t", "-q", "-v", "ON_ERROR_STOP=1", *conn, "-d", "restore_check",
             "-f", str(count_sql)],
            env=env,
            step="count_rows",
        )  # fmt: skip
    finally:
        if started:
            with contextlib.suppress(Exception):
                ctx_stop(pg, data)
    rows = {}
    for line in counted.splitlines():
        name, _, number = line.rpartition("|")
        if name and number.strip().isdigit():
            rows[name] = int(number)
    if len(rows) != listed:
        raise TaskFailedError(f"the dump lists {listed} tables but {len(rows)} were restored")
    shutil.rmtree(data, ignore_errors=True)
    return {"tables": len(rows), "rows": sum(rows.values()), "listed_tables": listed}


def ctx_stop(pg: Path, data: Path) -> None:
    """Stop the disposable instance (also after a cancellation: its server outlives the killed pg_ctl)."""
    import subprocess

    if not (data / "postmaster.pid").exists():
        return
    subprocess.run(  # noqa: S603
        [str(pg / _exe("pg_ctl")), "-D", str(data), "-m", "immediate", "-w", "-t", "60", "stop"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
        check=False,
    )


def apply_dump_retention(repo: Path, keep: int, check: Callable[[], None]) -> list[str]:
    """Keep the newest ``keep`` dumps and, always, the newest verified one. Metadata goes first, then the data."""
    if keep < 1:
        raise ValueError("keep at least one dump")
    dumps = list_dumps(repo)
    verified = [d["label"] for d in dumps if d.get("verified")]
    keep_labels = {d["label"] for d in dumps[-keep:]} | ({verified[-1]} if verified else set())
    removed = []
    folder = repo / DUMPS
    for item in dumps:
        label = item["label"]
        if label in keep_labels:
            continue
        check()
        (folder / f"{label}.json").unlink(missing_ok=True)
        (folder / f"{label}{SUFFIX}").unlink(missing_ok=True)
        removed.append(label)
    for orphan in folder.glob(f"dump-*{SUFFIX}"):
        # an encrypted file without metadata: a run interrupted before it was listed; never a valid dump
        if not (folder / f"{orphan.name[: -len(SUFFIX)]}.json").exists() and not orphan.name.startswith(TMP_PREFIX):
            orphan.unlink(missing_ok=True)
            removed.append(orphan.name)
    return removed
