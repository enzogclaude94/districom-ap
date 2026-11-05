"""Command line: ``python -m districom_backup <command>``.

``archive-wal`` and ``restore-wal`` are what PostgreSQL runs (``archive_command`` and
``restore_command``); the rest are operator and test commands. Exit codes: 0 success, 1 failure,
2 refused (conflict or gap), so PostgreSQL retries archiving on any non-zero code.
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import asdict
from pathlib import Path

from districom_backup import offsite
from districom_backup.base_backup import is_valid_backup, list_backups, read_info, take_base_backup
from districom_backup.repo import RepoUnavailableError, init_repo, wal_dir
from districom_backup.restore import RestoreStatus, restore
from districom_backup.retention import apply_retention
from districom_backup.wal_archive import ArchiveConflictError, archive_wal, restore_wal
from districom_backup.wal_gaps import find_gaps

log = logging.getLogger("districom_backup")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="districom_backup")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init-repo", help="prepare an empty repository")
    p.add_argument("--repo", type=Path, required=True)

    p = sub.add_parser("archive-wal", help="archive_command: <path %%p> <name %%f>")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("path", type=Path)
    p.add_argument("name")

    p = sub.add_parser("restore-wal", help="restore_command: <name %%f> <path %%p>")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("name")
    p.add_argument("path", type=Path)

    p = sub.add_parser("base-backup", help="take and verify a base backup")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--port", type=int, default=5432)
    p.add_argument("--user", default="postgres")

    p = sub.add_parser("verify", help="pg_verifybackup on a stored base backup")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("label")

    p = sub.add_parser("wal-gaps", help="check WAL continuity from the oldest (or given) base backup")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--label")

    p = sub.add_parser("restore", help="PITR into a new instance")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--label", required=True)
    p.add_argument("--target-dir", type=Path, required=True)
    p.add_argument("--port", type=int, required=True)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--target-time")
    group.add_argument("--target-name")
    group.add_argument("--target-lsn")

    p = sub.add_parser("retention", help="keep the newest N base backups and what they need")
    p.add_argument("--repo", type=Path, required=True)
    p.add_argument("--keep", type=int, required=True)

    for name in ("offsite-push", "offsite-pull"):
        p = sub.add_parser(name, help="rclone crypt copy; key in DISTRICOM_OFFSITE_PASSWORD")
        p.add_argument("--remote", required=True, help="base of the crypt remote (folder or bucket path)")
        p.add_argument("--rclone", default="rclone")
        if name == "offsite-push":
            p.add_argument("--repo", type=Path, required=True)
        else:
            p.add_argument("--destination", type=Path, required=True)
    return parser


def _remote(args: argparse.Namespace) -> offsite.CryptRemote:
    password = os.environ.get("DISTRICOM_OFFSITE_PASSWORD")
    if not password:
        raise SystemExit("set DISTRICOM_OFFSITE_PASSWORD")
    return offsite.CryptRemote(args.remote, password, args.rclone)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="districom_backup %(levelname)s %(message)s", stream=sys.stderr)
    args = _parser().parse_args(argv)
    try:
        return _dispatch(args)
    except ArchiveConflictError as error:
        log.error("%s", error)
        return 2
    except RepoUnavailableError as error:
        log.error("%s", error)
        return 1
    except Exception as error:  # the command line reports every failure as a non-zero exit
        log.error("%s: %s", type(error).__name__, error)
        return 1


def _dispatch(args: argparse.Namespace) -> int:
    match args.command:
        case "init-repo":
            init_repo(args.repo)
        case "archive-wal":
            result = archive_wal(args.path, args.repo, args.name)
            log.info("%s %s", args.name, result)
        case "restore-wal":
            return 0 if restore_wal(args.repo, args.name, args.path) else 1
        case "base-backup":
            info = take_base_backup(args.repo, port=args.port, user=args.user)
            print(json.dumps(asdict(info)))
        case "verify":
            ok, message = is_valid_backup(read_info(args.repo, args.label).path(args.repo))
            print(message)
            return 0 if ok else 2
        case "wal-gaps":
            backups = list_backups(args.repo)
            backup = read_info(args.repo, args.label) if args.label else (backups[0] if backups else None)
            if backup is None:
                raise RuntimeError("no base backup in the repository")
            report = find_gaps(wal_dir(args.repo), backup.start_segment(), backup.wal_segment_size)
            print(report.summary())
            return 0 if report.ok else 2
        case "restore":
            outcome = restore(
                args.repo,
                read_info(args.repo, args.label),
                args.target_dir,
                port=args.port,
                target_time=args.target_time,
                target_name=args.target_name,
                target_lsn=args.target_lsn,
            )
            print(f"{outcome.status}: {outcome.detail}")
            return {RestoreStatus.COMPLETE: 0, RestoreStatus.INCOMPLETE: 2}.get(outcome.status, 1)
        case "retention":
            print(json.dumps(asdict(apply_retention(args.repo, args.keep)), indent=1))
        case "offsite-push":
            print(offsite.push(args.repo, _remote(args)).name)
        case "offsite-pull":
            print(offsite.pull(_remote(args), args.destination).name)
    return 0
