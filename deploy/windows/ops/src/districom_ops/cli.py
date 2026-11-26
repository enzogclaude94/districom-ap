"""Command line: ``python -m districom_ops <command>`` (``districom-ops`` once installed).

Exit codes: 0 ok, 1 failure or alert, 2 refused (for example a window owned by another operation).
Every command logs through ``districom_ops.logs``: values are redacted and secrets never printed.
"""

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from districom_ops import (
    acl,
    artifacts,
    backup_disk,
    backup_tasks,
    backups,
    database,
    firewall,
    integrity,
    ntfs,
    permissions,
    schedule,
    services,
    tasklock,
    write_block,
)
from districom_ops.config import ConfigError, OpsConfig, parse_env, read_config, read_services_config
from districom_ops.install import Install
from districom_ops.layout import Layout, LayoutError
from districom_ops.logs import REDACTOR, setup_logging
from districom_ops.maintenance import MaintenanceError, MaintenanceWindow, open_stopping_tasks
from districom_ops.oplock import LockBusy
from districom_ops.oplog import Event, Kind, OperationError, OperationsLog
from districom_ops.safety import writes_resume_check
from districom_ops.winservice import ServiceError

CONFIG_ENV = "DISTRICOM_CONFIG"
DEFAULT_CONFIG = Path(r"C:\DistriCom\config\districom.env")
log = logging.getLogger("districom_ops")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="districom_ops")
    parser.add_argument("--config", type=Path, default=Path(os.environ.get(CONFIG_ENV, DEFAULT_CONFIG)))
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the installation folders that are missing")
    p.add_argument("--root", type=Path, required=True)

    sub.add_parser("check-config", help="validate the configuration without showing any value")

    m = sub.add_parser("maintenance", help="maintenance window").add_subparsers(dest="action", required=True)
    p = m.add_parser("open")
    p.add_argument("--kind", choices=[k.value for k in Kind if k is not Kind.RESUME], required=True)
    p.add_argument("--reason", required=True)
    p = m.add_parser("close")
    p.add_argument("--operation", required=True)
    p.add_argument("--reverted", action="store_true", help="end the operation as reverted instead of completed")
    m.add_parser("status")

    p = sub.add_parser("operations", help="operations record")
    p.add_argument("--incomplete", action="store_true", help="only incomplete or uncertain operations")

    sub.add_parser(
        "resume-check",
        help="read only: would it be safe to re-enable writes? (window, operations record; C9 acts on it)",
    )

    p = sub.add_parser(
        "resume",
        help="administrator: re-enable the application's writes after a write block (C9-B), audited",
    )
    p.add_argument("--operation", required=True, help="the operation that owns the write block")
    p.add_argument("--reason", required=True)

    # W2-C2: native services
    r = sub.add_parser("runtime", help="pinned runtimes").add_subparsers(dest="action", required=True)
    p = r.add_parser("fetch", help="download, verify the checksum and extract (idempotent)")
    p.add_argument("--only", nargs="*", default=None)

    d = sub.add_parser("backup-disk", help="backup disk identity").add_subparsers(dest="action", required=True)
    p = d.add_parser("prepare", help="mark a separate, empty volume once; prints the BACKUP_DISK_ID")
    p.add_argument("--root", type=Path, required=True)
    d.add_parser("verify", help="marker, disk id and volume (read only)")

    db = sub.add_parser("database", help="PostgreSQL cluster").add_subparsers(dest="action", required=True)
    db.add_parser("init", help="create the cluster once (ICU es-AR, checksums, O7 logs)")

    s = sub.add_parser("services", help="Windows services").add_subparsers(dest="action", required=True)
    p = s.add_parser("install", help="install or update the five services (idempotent)")
    p.add_argument("--release", required=True)
    s.add_parser("uninstall", help="stop in order and remove the services; data stays")
    p = s.add_parser("start", help="start through DistriComStack (or --direct, administrator)")
    p.add_argument("--direct", action="store_true")
    p.add_argument("--until", choices=list(services.CHAIN), default=services.CADDY)
    s.add_parser("stop", help="stop in reverse order")
    s.add_parser("status")
    s.add_parser("supervise", help="run by the DistriComStack service")

    # W2-C3: permissions and integrity
    a = sub.add_parser("permissions", help="NTFS permissions (C3)").add_subparsers(dest="action", required=True)
    a.add_parser("apply", help="make them exactly the policy (idempotent; refuses while a foreign link exists)")
    a.add_parser("verify", help="read the effective lists and compare them with the policy (read only)")
    f = sub.add_parser("firewall", help="Windows Firewall rules (C4)").add_subparsers(dest="action", required=True)
    f.add_parser("apply", help="make the DistriCom rules exactly the plan (idempotent; never touches other rules)")
    p = f.add_parser("verify", help="rules and profiles against the plan (read only)")
    p.add_argument(
        "--services-running",
        action="store_true",
        help="also require each internal port to listen on 127.0.0.1 with the program of its rule",
    )
    f.add_parser("remove", help="take away every DistriCom rule and nothing else")
    i = sub.add_parser("integrity", help="installed executables (C3)").add_subparsers(dest="action", required=True)
    i.add_parser("record", help="record the SHA-256 of runtimes, wrappers and the release")
    i.add_parser("verify", help="compare them with the record (read only)")

    # W2-C6: backup tasks
    t = sub.add_parser("tasks", help="scheduled backup tasks (C6)").add_subparsers(dest="action", required=True)
    t.add_parser("install", help="repo1, archiving, task settings, permissions and the scheduled tasks (idempotent)")
    t.add_parser("uninstall", help="remove the scheduled tasks; repositories, dumps and records stay")
    t.add_parser("status", help="state of every task, the running heavy task and any cancellation request")
    p = t.add_parser("start", help="administrator: start a registered task now, through the Task Scheduler")
    p.add_argument("task", choices=sorted(schedule.TASKS))
    t.add_parser("clear-cancel", help="administrator: remove a cancellation request left by an interrupted operation")
    p = t.add_parser("run", help="run by the Task Scheduler: one run of a task")
    p.add_argument("task", choices=sorted(schedule.TASKS))
    p.add_argument("--settings", type=Path, required=True)
    return parser


def _config(path: Path) -> OpsConfig:
    config = read_config(path)
    REDACTOR.register(*config.secrets())
    return config


def _run_task(args: argparse.Namespace) -> int:
    """``tasks run``: reads only ``config\\tasks.json`` and its own secrets, never ``districom.env``."""
    try:
        settings = backup_tasks.TaskSettings.read(args.settings)
    except backup_tasks.TaskSettingsError as exc:
        setup_logging(None)
        log.error("refused", extra={"reason": str(exc)})
        return 2
    setup_logging(settings.log_dir(args.task))
    outcome = backup_tasks.run_task(settings, args.task)
    _print({"task": outcome.task, "status": outcome.status, "reason": outcome.reason})
    return {"succeeded": 0, "refused": 2}.get(outcome.status, 1)


def _run(args: argparse.Namespace) -> int:
    if args.command == "tasks" and args.action == "run":
        return _run_task(args)
    if args.command == "init":
        layout = Layout(args.root)
        setup_logging(layout.logs if layout.logs.is_dir() else None)
        created = layout.ensure()
        setup_logging(layout.logs)
        log.info("layout_ready", extra={"root": str(layout.root), "folders_created": [p.name for p in created]})
        return 0

    try:
        config = _config(args.config)
    except ConfigError as exc:
        setup_logging(None)
        log.error("config_invalid", extra={"problems": [str(p) for p in exc.problems]})
        return 1
    layout = Layout(config.root)
    setup_logging(layout.logs if layout.logs.is_dir() else None)
    if args.command == "check-config":
        missing = layout.missing()
        if missing:
            log.error("layout_incomplete", extra={"missing": missing})
            return 1
        log.info("config_ok", extra={"root": str(layout.root), "host": config.host})
        return 0

    if args.command in (
        "runtime", "backup-disk", "database", "services", "permissions", "integrity", "firewall", "resume", "tasks"
    ):  # fmt: skip
        return _services(args, config)

    oplog = OperationsLog(layout.state / "operations.jsonl")
    if args.command == "operations":
        record = oplog.read()
        ops = [op for op in record.operations.values() if op.incomplete or op.uncertain or not args.incomplete]
        for op in ops:
            item = {"id": op.id, "kind": op.kind, "reason": REDACTOR.text(op.reason), "end": op.end}
            sys.stdout.write(json.dumps({**item, "uncertain": op.uncertain}) + "\n")
        if record.torn_lines:
            log.warning("operations_record_torn_lines", extra={"lines": record.torn_lines})
        if not record.certain:
            log.error("operations_record_invalid", extra={"problems": [str(p) for p in record.problems]})
            return 1
        return 0

    window = MaintenanceWindow(layout.state, oplog)
    if args.command == "resume-check":
        check = writes_resume_check(window, config.maintenance_max_minutes)
        (log.info if check.allowed else log.error)(
            "writes_resume_check", extra={"allowed": check.allowed, "detail": check.describe()}
        )
        return 0 if check.allowed else 1
    if args.action == "open":
        # W2-C6 (design AB): the heavy backup tasks are asked to stop first, with a bounded wait outside the lock.
        op_id = open_stopping_tasks(window, Kind(args.kind), args.reason, config.backup_cancel_wait_seconds)
        log.info("maintenance_opened", extra={"operation": op_id, "kind": args.kind})
        sys.stdout.write(op_id + "\n")
        return 0
    if args.action == "close":
        window.close(args.operation, Event.REVERTED if args.reverted else Event.COMPLETED)
        log.info("maintenance_closed", extra={"operation": args.operation})
        return 0
    status = window.status(config.maintenance_max_minutes)
    (log.error if status.alert else log.info)(
        "maintenance_status", extra={"state": status.state, "detail": status.describe()}
    )
    return 1 if status.alert else 0


def _account() -> str:
    """Who runs a manual resume, for the audit: the Windows SID (no name, no other personal data)."""
    if sys.platform == "win32":
        return acl.current_user_sid()
    return f"uid:{os.getuid()}"


def _is_admin() -> bool:
    if sys.platform != "win32":
        return os.getuid() == 0
    import ctypes

    return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _resume(args: argparse.Namespace, install: Install) -> int:
    if not _is_admin():
        log.error("refused", extra={"reason": "resume needs an administrator (Remote Desktop, elevated)"})
        return 2
    with services.operations_lock(install, "resume").acquire(timeout=services.LOCK_SECONDS) as lock:
        result = write_block.resume(install, lock, args.reason, target=args.operation, account=_account())
    log.info("resume_done", extra={"operation": result.operation, "resumed": result.resumed, "detail": result.detail})
    _print({"operation": result.operation, "resumed": result.resumed})
    return 0


def _print(data: object) -> None:
    sys.stdout.write(json.dumps(data, ensure_ascii=False, default=str) + "\n")


def _services(args: argparse.Namespace, config: OpsConfig) -> int:
    if args.command == "runtime":
        manifest = artifacts.manifest()
        names = args.only or list(manifest)
        runtime, downloads = config.root / "runtime", config.root / "downloads"
        for name in names:
            changed = artifacts.fetch(manifest[name], downloads, runtime)
            log.info("runtime_ready", extra={"artifact": name, "version": manifest[name].version, "changed": changed})
        return 0
    if args.command == "backup-disk" and args.action == "prepare":
        raw = parse_env(args.config.read_text(encoding="utf-8-sig")).get("PG_DATA_DIR")
        disk_id = backup_disk.prepare(args.root, Path(raw) if raw else config.root / "pgdata")
        log.info("backup_disk_prepared", extra={"root": str(args.root)})
        sys.stdout.write(disk_id + "\n")
        return 0
    try:
        services_config = read_services_config(args.config, config)
    except ConfigError as exc:
        log.error("config_invalid", extra={"problems": [str(p) for p in exc.problems]})
        return 1
    REDACTOR.register(*services_config.secrets())
    release = getattr(args, "release", None)
    if release is None and (config.root / "state" / "services.json").is_file():
        release = json.loads((config.root / "state" / "services.json").read_text("utf-8")).get("release")
    install = Install(config, services_config, release)
    if args.command == "backup-disk":
        check = services.check_disk(install)
        (log.info if check.ok else log.error)("backup_disk_check", extra={"code": check.code, "detail": check.detail})
        return 0 if check.ok else 1
    if args.command == "resume":
        return _resume(args, install)
    if args.command == "tasks":
        return _tasks(args, install)
    if args.command == "database":
        changed = database.init(install)
        log.info("database_init", extra={"changed": changed})
        return 0
    if args.command == "firewall" and args.action == "remove":
        return _firewall(args.action, install, False)  # reversible always, even without a release
    if args.command in ("permissions", "integrity", "firewall") and install.release is None:
        log.error("refused", extra={"reason": "no release recorded in state\\services.json: run services install"})
        return 2
    if args.command == "permissions":
        policy = services.permission_policy(install)
        if args.action == "apply":
            _print({"changes": permissions.apply(policy)})
            return 0
        problems = permissions.verify(policy)
        for problem in problems[:50]:
            extra = {"path": problem.path, "code": problem.code, "detail": problem.detail}
            log.error("permissions_problem", extra=extra)
        log.info("permissions_verified", extra={"problems": len(problems)})
        _print({"problems": [p.describe() for p in problems]})
        return 1 if problems else 0
    if args.command == "firewall":
        return _firewall(args.action, install, getattr(args, "services_running", False))
    if args.command == "integrity":
        if args.action == "record":
            _print({"changed": integrity.record(install)})
            return 0
        report = integrity.verify(install)
        summary = {"files": report.files, "changed": report.changed[:20], "missing": report.missing[:20],
                   "added": report.added[:20]}  # fmt: skip
        (log.info if report.ok else log.error)("integrity_verified", extra={"ok": report.ok, **{
            k: len(v) for k, v in (("changed", report.changed), ("missing", report.missing), ("added", report.added))
        }})  # fmt: skip
        _print(summary)
        return 0 if report.ok else 1
    if args.action == "install":
        _print({"changes": services.install_services(install)})
        return 0
    if args.action == "uninstall":
        with services.operations_lock(install, "services_uninstall").acquire(timeout=services.LOCK_SECONDS):
            _print({"removed": services.uninstall_services(install)})
        return 0
    if args.action == "start":
        if args.direct:
            services.start_direct(install, args.until)
        else:
            services.start_stack(install)
        return 0
    if args.action == "stop":
        services.stop_services(install, install.ops.backup_cancel_wait_seconds)
        return 0
    if args.action == "status":
        _print(services.status_report(install))
        return 0
    return services.supervise(install)


def _tasks(args: argparse.Namespace, install: Install) -> int:
    if args.action == "install":
        if not _is_admin():
            log.error("refused", extra={"reason": "tasks install needs an administrator"})
            return 2
        _print({"changes": backups.install_tasks(install, args.config)})
        return 0
    if args.action == "uninstall":
        _print({"removed": backups.uninstall_tasks(install)})
        return 0
    if not install.tasks_settings.is_file():
        log.error("refused", extra={"reason": "the backup tasks are not installed (tasks install)"})
        return 2
    settings = backup_tasks.TaskSettings.read(install.tasks_settings)
    if args.action == "status":
        _print(backup_tasks.status(settings))
        return 0
    if args.action == "start":
        schedule.start_now(args.task)
        log.info("backup_task_start_requested", extra={"task": args.task})
        return 0
    with services.operations_lock(install, "tasks_clear_cancel").acquire(timeout=services.LOCK_SECONDS) as lock:
        removed = tasklock.reconcile(install.layout.state, lock)
        standing = [r for r in tasklock.cancel_requests(install.layout.state) if r.live]
    # Only orphans go: a valid request belongs to an operation that is still running and clears it itself.
    log.info("cancel_requests_reconciled", extra={"removed": [r.request_id for r in removed],
                                                  "standing": [r.operation for r in standing]})  # fmt: skip
    return 0


def _firewall(action: str, install: Install, services_running: bool) -> int:
    if action == "remove":
        removed = firewall.remove()
        log.info("firewall_removed", extra={"rules": removed})
        _print({"removed": removed})
        return 0
    plan = services.firewall_plan(install)
    if action == "apply":
        changes = firewall.apply(plan)
        log.info("firewall_applied", extra={"changes": changes})
        _print({"changes": changes})
        return 0
    problems, warnings = firewall.verify(plan, services_running=services_running)
    for item in problems:
        log.error("firewall_problem", extra={"rule": item.name, "code": item.code, "detail": item.detail})
    for item in warnings:
        log.warning("firewall_warning", extra={"rule": item.name, "code": item.code, "detail": item.detail})
    _print({"problems": [p.describe() for p in problems], "warnings": [w.describe() for w in warnings]})
    return 1 if problems else 0


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except (
        MaintenanceError,
        OperationError,
        LayoutError,
        services.ServicesError,
        ServiceError,
        artifacts.ArtifactError,
        backup_disk.BackupDiskError,
        database.DatabaseError,
        permissions.PermissionsError,
        ntfs.NtfsError,
        firewall.FirewallError,
        LockBusy,
        write_block.WriteBlockError,
        backups.BackupsError,
        schedule.ScheduleError,
        tasklock.TaskLockError,
    ) as exc:
        log.error("refused", extra={"reason": str(exc)})
        return 2
