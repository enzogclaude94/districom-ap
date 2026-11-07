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

from districom_ops.config import ConfigError, OpsConfig, read_config
from districom_ops.layout import Layout, LayoutError
from districom_ops.logs import REDACTOR, setup_logging
from districom_ops.maintenance import MaintenanceError, MaintenanceWindow
from districom_ops.oplog import Event, Kind, OperationError, OperationsLog
from districom_ops.safety import writes_resume_check

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


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args)
    except (MaintenanceError, OperationError, LayoutError) as exc:
        log.error("refused", extra={"reason": str(exc)})
        return 2
