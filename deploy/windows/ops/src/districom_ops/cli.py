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

from districom_ops import artifacts, backup_disk, database, firewall, integrity, ntfs, permissions, services
from districom_ops.config import ConfigError, OpsConfig, parse_env, read_config, read_services_config
from districom_ops.install import Install
from districom_ops.layout import Layout, LayoutError
from districom_ops.logs import REDACTOR, setup_logging
from districom_ops.maintenance import MaintenanceError, MaintenanceWindow
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
    p.add_argument("--kind", choices=[k.value for k in Kind], required=True)
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
    return parser


def _config(path: Path) -> OpsConfig:
    config = read_config(path)
    REDACTOR.register(*config.secrets())
    return config


def _run(args: argparse.Namespace) -> int:
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

    if args.command in ("runtime", "backup-disk", "database", "services", "permissions", "integrity", "firewall"):
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
        op_id = window.open(Kind(args.kind), args.reason)
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
        _print({"removed": services.uninstall_services(install)})
        return 0
    if args.action == "start":
        if args.direct:
            services.start_chain(install, args.until)
        else:
            services.start_stack(install)
        return 0
    if args.action == "stop":
        services.stop_all(install)
        return 0
    if args.action == "status":
        _print(services.status_report(install))
        return 0
    return services.supervise(install)


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
    ) as exc:
        log.error("refused", extra={"reason": str(exc)})
        return 2
