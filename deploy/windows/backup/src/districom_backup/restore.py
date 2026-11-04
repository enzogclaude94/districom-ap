"""Point-in-time recovery into a new, separate instance.

Never restores over a running server: the copy goes into an empty directory, on another port,
with archiving off so it cannot write into the production repository. The result is one of:

* ``COMPLETE``: PostgreSQL reached the requested target (or, without a target, replayed every
  archived segment and the repository has no gaps) and finished recovery.
* ``INCOMPLETE``: the instance came up but cannot be trusted to hold everything requested: the
  repository has gaps and no explicit target was reached, so the copy may stop early.
* ``FAILED``: recovery did not finish (for example, the target lies beyond a gap: PostgreSQL
  stops with "recovery ended before configured recovery target was reached").
"""

import shutil
import sys
import time
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from districom_backup import pgtools
from districom_backup.base_backup import BaseBackupInfo
from districom_backup.repo import require_repo
from districom_backup.wal_gaps import GapReport, find_gaps


class RestoreStatus(StrEnum):
    COMPLETE = "COMPLETE"
    INCOMPLETE = "INCOMPLETE"
    FAILED = "FAILED"


@dataclass
class RestoreOutcome:
    status: RestoreStatus
    instance: pgtools.Instance
    gaps: GapReport
    detail: str = ""
    seconds: float = 0.0
    log_tail: list[str] = field(default_factory=list)


def _command(action: str, repo: Path, python: str | None, args: str) -> str:
    executable = pgtools.command_path(python or sys.executable)
    return f"{executable} -m districom_backup {action} --repo {pgtools.command_path(repo.resolve())} {args}"


def archive_command(repo: Path, python: str | None = None) -> str:
    """The ``archive_command`` for a repository: ``%p`` path, ``%f`` file name."""
    return _command("archive-wal", repo, python, "%p %f")


def restore_command(repo: Path, python: str | None = None) -> str:
    """The ``restore_command`` for a repository: ``%f`` file name, ``%p`` destination."""
    return _command("restore-wal", repo, python, "%f %p")


def restore(
    repo: Path,
    backup: BaseBackupInfo,
    target_dir: Path,
    *,
    port: int,
    target_time: str | None = None,
    target_name: str | None = None,
    target_lsn: str | None = None,
    bin_dir: Path | None = None,
    user: str = "postgres",
    timeout: int = 1800,
    python: str | None = None,
    archive_into: Path | None = None,
) -> RestoreOutcome:
    """Restore ``backup`` into the empty ``target_dir`` and promote it at the target.

    ``archive_into`` is for disaster recovery only: the promoted copy becomes the new primary and
    archives its new timeline (history file and segments) into that repository from the moment it
    is promoted. By default archiving is off, so a drill can never write into a repository.
    """
    if sum(t is not None for t in (target_time, target_name, target_lsn)) > 1:
        raise ValueError("give at most one recovery target")
    require_repo(repo)
    if target_dir.exists() and any(target_dir.iterdir()):
        raise FileExistsError(f"restore target is not empty: {target_dir}")
    started = time.monotonic()
    gaps = find_gaps(repo / "wal", backup.start_segment(), backup.wal_segment_size)

    shutil.copytree(backup.path(repo), target_dir, dirs_exist_ok=True)
    (target_dir / "postmaster.pid").unlink(missing_ok=True)
    if sys.platform != "win32":
        target_dir.chmod(0o700)  # PostgreSQL refuses a data directory others can read
    (target_dir / "recovery.signal").write_text("", encoding="utf-8")
    instance = pgtools.Instance(target_dir, port, bin_dir, user)
    settings = {
        "archive_mode": "'on'" if archive_into else "'off'",
        "restore_command": pgtools.conf_quote(restore_command(repo, python)),
        "recovery_target_timeline": "'latest'",
        "recovery_target_action": "'promote'",
    }
    if archive_into is not None:
        settings["archive_command"] = pgtools.conf_quote(archive_command(archive_into, python))
    # A backup of a restored server carries that server's recovery settings: clear them all first.
    # PostgreSQL applies them in order and rejects clearing one while another is set, so the chosen
    # target goes last.
    targets = {
        "recovery_target_time": target_time,
        "recovery_target_name": target_name,
        "recovery_target_lsn": target_lsn,
    }
    for key in ("recovery_target", "recovery_target_xid", *targets):
        if targets.get(key) is None:
            settings[key] = "''"
    for key, value in targets.items():
        if value is not None:
            settings[key] = pgtools.conf_quote(value)
    instance.append_conf(settings)

    has_target = any(t is not None for t in (target_time, target_name, target_lsn))
    up = instance.start(timeout=timeout, check=False)
    deadline = time.monotonic() + timeout
    promoted = False
    while up and time.monotonic() < deadline:
        if not instance.is_running():
            break
        state = instance.psql("SELECT pg_is_in_recovery()", check=False)
        if state == "f":
            promoted = True
            break
        time.sleep(0.5)
    log = instance.read_log()
    outcome = RestoreOutcome(
        status=RestoreStatus.FAILED,
        instance=instance,
        gaps=gaps,
        seconds=time.monotonic() - started,
        log_tail=log.splitlines()[-30:],
    )
    if not promoted:
        if "recovery ended before configured recovery target was reached" in log:
            outcome.detail = "recovery target not reached: the WAL needed to reach it is not in the repository"
        else:
            outcome.detail = "recovery did not finish"
        if instance.is_running():
            instance.stop("immediate")
        return outcome
    if has_target and "recovery stopping" not in log:
        outcome.detail = "server promoted without reporting that it stopped at the target"
        return outcome
    if not gaps.ok:
        outcome.status = RestoreStatus.COMPLETE if has_target else RestoreStatus.INCOMPLETE
        outcome.detail = gaps.summary()
        return outcome
    outcome.status = RestoreStatus.COMPLETE
    outcome.detail = "target reached" if has_target else "replayed all archived WAL; no gaps"
    return outcome
