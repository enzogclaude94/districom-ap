"""``districom-ops tasks install`` and ``tasks uninstall`` (W2-C6): what an administrator runs once, and again
after an update, to put the backups of W2-A into operation. Idempotent: a second run changes nothing.

1. ``repo1`` on the verified backup disk (W2-A layout, ``init_repo``).
2. ``districom-archive.conf`` in the PostgreSQL data: ``archive_mode``, ``archive_timeout`` and the
   ``archive_command`` / ``restore_command`` of W2-A, run by the PostgreSQL service with the Python of the
   release. ``archive_mode`` needs a restart of PostgreSQL: the command says so when the file changed.
3. ``config\\tasks.json``: the settings of the tasks, without secrets.
4. The secret files: ``backup-db.secret`` (from ``database init``), ``dump.secret`` (the key of the dumps) and,
   when ``OFFSITE_REMOTE`` is set, ``offsite.secret``. The administrator writes the two keys and keeps a copy
   **outside the server**: without them neither the dumps nor repo2 can be read (pending, physical procedure).
5. The permissions of C3, now with the task accounts and the PostgreSQL archiver (``services.permission_policy``).
6. The Task Scheduler tasks (``schedule.register``).

All of it under ``operations.lock`` (a short operation).
"""

import logging
import sys
from pathlib import Path

from districom_backup import restore as w2a_restore
from districom_backup.repo import init_repo

from districom_ops import backup_tasks, database, permissions, schedule
from districom_ops.config import TasksConfig, load_tasks_config, parse_env
from districom_ops.install import Install

log = logging.getLogger("districom_ops")
MIN_KEY_LENGTH = 32
ARCHIVE_TIMEOUT_SECONDS = 300  # a segment is archived at least every 5 minutes, even with little traffic


class BackupsError(Exception):
    pass


def archive_settings(install: Install) -> str:
    python = str(install.ops_python)
    return "\n".join(
        [
            "# Written by districom-ops tasks install (W2-C6). WAL archiving into repo1 with districom_backup (W2-A).",
            "archive_mode = on",
            f"archive_timeout = {ARCHIVE_TIMEOUT_SECONDS}",
            "archive_command = " + _conf(w2a_restore.archive_command(install.repo1, python)),
            "restore_command = " + _conf(w2a_restore.restore_command(install.repo1, python)),
            "",
        ]
    )


def _conf(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def task_settings(install: Install, tasks: TasksConfig) -> backup_tasks.TaskSettings:
    _, _, database_name, port = install.database
    rclone = install.artifact("rclone").path(install.runtime) if "rclone" in _artifacts(install) else None
    return backup_tasks.TaskSettings(
        root=install.root,
        repo=install.repo1,
        pg_bin=install.pg_bin("pg_ctl").parent,
        pg_port=port,
        app_database=database_name,
        backup_role=database.BACKUP_ROLE,
        python=install.ops_python,
        rclone=rclone,
        offsite_remote=tasks.offsite_remote,
        keep_base=tasks.keep_base,
        keep_dumps=tasks.keep_dumps,
        restore_test_port=tasks.restore_test_port,
        maintenance_max_minutes=install.ops.maintenance_max_minutes,
    )


def _artifacts(install: Install) -> set[str]:
    from districom_ops.artifacts import manifest

    return set(manifest())


def _write_if_changed(path: Path, text: str) -> bool:
    data = text.encode("utf-8")
    if path.is_file() and path.read_bytes() == data:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    from districom_backup.repo import write_atomic

    write_atomic(path, data)
    return True


def _check_key(path: Path, name: str) -> str | None:
    try:
        value = path.read_text("utf-8").strip()
    except OSError:
        return f"{name} missing: write the key in config\\{path.name} (and keep a copy outside the server)"
    if len(value) < MIN_KEY_LENGTH or len(set(value)) < 12:
        return f"{name} too weak: {MIN_KEY_LENGTH}+ random characters"
    return None


def install_tasks(install: Install, config_path: Path) -> list[str]:
    from districom_ops import services  # services imports this module's neighbours; imported here to avoid a cycle

    if install.release is None:
        raise BackupsError("no release recorded in state\\services.json: run services install first")
    disk = services.check_disk(install)
    if not disk.ok:
        raise BackupsError(f"the backup disk does not verify ({disk.code}): repo1 is not created")
    if not (install.pg_data / "PG_VERSION").is_file():
        raise BackupsError("no PostgreSQL cluster: run database init first")
    tasks = load_tasks_config(parse_env(config_path.read_text(encoding="utf-8-sig")), install.database[3])
    problems = []
    if not install.backup_secret.is_file():
        problems.append("config\\backup-db.secret missing: the cluster was created before W2-C6 (database init)")
    settings = task_settings(install, tasks)
    for path, name, needed in (
        (settings.dump_secret, "the dump key", True),
        (settings.offsite_secret, "the off-site key", tasks.offsite_remote is not None),
    ):
        problem = _check_key(path, name) if needed else None
        if problem:
            problems.append(problem)
    if settings.rclone is None or not settings.rclone.is_file():
        problems.append("rclone is not in the runtimes: run runtime fetch")
    if sys.platform == "win32":
        problems += schedule.check_sids()  # the permissions below are only right with Windows' own task SIDs
    if problems:
        raise BackupsError("; ".join(problems))

    changes: list[str] = []
    with services.operations_lock(install, "tasks_install").acquire(timeout=services.LOCK_SECONDS):
        if not (install.repo1 / ".districom-backup-repo").is_file():
            init_repo(install.repo1)
            changes.append("repo1 created on the backup disk")
        if _write_if_changed(install.pg_data / database.ARCHIVE_CONF, archive_settings(install)):
            changes.append(f"{database.ARCHIVE_CONF} written: restart PostgreSQL (services stop and start) to apply")
        if _write_if_changed(install.tasks_settings, settings.to_json()):
            changes.append("config\\tasks.json written")
        for folder in (settings.tasks, *(settings.log_dir(name) for name in schedule.TASKS)):
            folder.mkdir(parents=True, exist_ok=True)
        changes += permissions.apply(services.permission_policy(install))
        changes += schedule.register(
            settings.tasks, install.ops_python, install.tasks_settings, install.release_dir() / "windows" / "ops"
        )
    log.info("tasks_installed", extra={"changes": changes})
    return changes


def uninstall_tasks(install: Install) -> list[str]:
    """Remove the scheduled tasks. Repositories, dumps, records and the archiving stay (remove the archiving by
    deleting ``districom-archive.conf`` and restarting PostgreSQL: never automatic, the WAL history matters)."""
    from districom_ops import services

    with services.operations_lock(install, "tasks_uninstall").acquire(timeout=services.LOCK_SECONDS):
        return schedule.unregister()
