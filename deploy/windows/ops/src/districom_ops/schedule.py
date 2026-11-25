"""Scheduled backup tasks (W2-C6): what runs, when, under which account, and their registration.

Each task is a Task Scheduler task at the root folder, named ``DistriComBackup<Name>``, that runs without a session
as ``LocalService`` with its **own task SID** (``ProcessTokenSidType`` ``Unrestricted``): the account
``NT TASK\\<task name>``, ``S-1-5-87-…``. No password exists for it, and C3 gives each SID only what its task needs
(``services.permission_policy``). The definitions are rendered from this module, never edited by hand.

| Task | Heavy | When | Time limit |
|---|---|---|---|
| base_backup | yes | daily 15:00 (window 15:00 to 17:00) | 2 h, the end of the window |
| dump | yes | daily 13:00 | 90 min |
| offsite | yes | hourly at :40 | 2 h |
| retention | yes | daily 03:00 | 30 min |
| wal_gaps | no (read only) | hourly at :50 | 15 min |

The limit is enforced by the task itself (it cancels its work and records ``timeout``); the Task Scheduler limit is
a safety net 10 minutes later. A missed base backup is not run late, out of its window; the others are.
Registration is idempotent: a task whose definition is already the wanted one is not touched.
"""

import hashlib
import struct
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

LOCAL_SERVICE = "S-1-5-19"
TASK_PREFIX = "DistriComBackup"
SCHEMA = "http://schemas.microsoft.com/windows/2004/02/mit/task"


@dataclass(frozen=True)
class TaskSpec:
    name: str  # the task id used by districom-ops (``tasks run <name>``)
    title: str  # the Task Scheduler name suffix
    heavy: bool
    time_limit_minutes: int
    start: str  # HH:MM
    hourly: bool
    run_late: bool  # StartWhenAvailable: run at the next chance after a missed start
    description: str

    @property
    def task_name(self) -> str:
        return f"{TASK_PREFIX}{self.title}"

    @property
    def sid(self) -> str:
        return task_sid(self.task_name)


TASKS: dict[str, TaskSpec] = {
    t.name: t
    for t in (
        TaskSpec("base_backup", "Base", True, 120, "15:00", False, False, "Verified base backup into repo1"),
        TaskSpec("dump", "Dump", True, 90, "13:00", False, True, "Encrypted pg_dump and its restore test"),
        TaskSpec("offsite", "Offsite", True, 120, "00:40", True, True, "Asynchronous encrypted copy to repo2"),
        TaskSpec("retention", "Retention", True, 30, "03:00", False, True, "Retention of base backups and dumps"),
        TaskSpec("wal_gaps", "WalGaps", False, 15, "00:50", True, True, "WAL continuity check (read only)"),
    )
}
HEAVY = tuple(name for name, t in TASKS.items() if t.heavy)


def task_sid(task_name: str) -> str:
    """SID of ``NT TASK\\<task_name>``: S-1-5-87 and the SHA-1 of the upper-cased name in UTF-16LE as five
    little-endian sub-authorities, the scheme of the service SIDs (S-1-5-80). ``schtasks /showsid`` prints the same
    value; C6-2 compares both on Windows."""
    digest = hashlib.sha1(task_name.upper().encode("utf-16-le"), usedforsecurity=False).digest()
    return "S-1-5-87-" + "-".join(str(n) for n in struct.unpack("<5I", digest))


def _quote(arg: str) -> str:
    return f'"{arg}"' if (" " in arg or not arg) else arg


def render(spec: TaskSpec, python: Path, settings: Path, working_dir: Path) -> str:
    """The Task Scheduler XML (schema 1.4) of one task. Deterministic: the same inputs give the same text."""
    limit = spec.time_limit_minutes + 10
    if spec.hourly:
        trigger = (
            f"<CalendarTrigger><StartBoundary>2026-01-01T{spec.start}:00</StartBoundary>"
            "<Repetition><Interval>PT1H</Interval><Duration>P1D</Duration><StopAtDurationEnd>false</StopAtDurationEnd>"
            "</Repetition><Enabled>true</Enabled><ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>"
            "</CalendarTrigger>"
        )
    else:
        trigger = (
            f"<CalendarTrigger><StartBoundary>2026-01-01T{spec.start}:00</StartBoundary><Enabled>true</Enabled>"
            "<ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay></CalendarTrigger>"
        )
    arguments = " ".join(_quote(a) for a in ("-m", "districom_ops", "tasks", "run", spec.name, "--settings",
                                             str(settings)))  # fmt: skip
    return (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        f'<Task version="1.4" xmlns="{SCHEMA}">'
        f"<RegistrationInfo><Description>{escape(spec.description)} (DistriCom W2-C6)</Description></RegistrationInfo>"
        f"<Triggers>{trigger}</Triggers>"
        '<Principals><Principal id="Author">'
        f"<UserId>{LOCAL_SERVICE}</UserId><LogonType>ServiceAccount</LogonType><RunLevel>LeastPrivilege</RunLevel>"
        "<ProcessTokenSidType>Unrestricted</ProcessTokenSidType>"
        "</Principal></Principals>"
        "<Settings><MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>"
        "<DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>"
        "<StopIfGoingOnBatteries>false</StopIfGoingOnBatteries><AllowHardTerminate>true</AllowHardTerminate>"
        f"<StartWhenAvailable>{str(spec.run_late).lower()}</StartWhenAvailable>"
        "<RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>"
        "<IdleSettings><StopOnIdleEnd>false</StopOnIdleEnd><RestartOnIdle>false</RestartOnIdle></IdleSettings>"
        "<AllowStartOnDemand>true</AllowStartOnDemand><Enabled>true</Enabled><Hidden>false</Hidden>"
        "<RunOnlyIfIdle>false</RunOnlyIfIdle><WakeToRun>false</WakeToRun>"
        f"<ExecutionTimeLimit>PT{limit}M</ExecutionTimeLimit>"
        f"<Priority>{7 if spec.heavy else 6}</Priority></Settings>"
        f'<Actions Context="Author"><Exec><Command>{escape(str(python))}</Command>'
        f"<Arguments>{escape(arguments)}</Arguments><WorkingDirectory>{escape(str(working_dir))}</WorkingDirectory>"
        "</Exec></Actions></Task>\n"
    )


Runner = Callable[[list[str]], subprocess.CompletedProcess[str]]


def _schtasks(args: list[str]) -> subprocess.CompletedProcess[str]:
    if sys.platform != "win32":
        raise RuntimeError("the Task Scheduler exists only on Windows")
    return subprocess.run(  # noqa: S603
        ["schtasks.exe", *args],  # noqa: S607 - a Windows system tool, resolved like sc.exe and icacls.exe
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


class ScheduleError(Exception):
    pass


def _why(result: subprocess.CompletedProcess[str]) -> str:
    """What schtasks said (its own message: no secrets ever reach its command line), on one short line."""
    text = " ".join((result.stderr + " " + result.stdout).split())
    return f"{result.returncode}: {text[:300]}" if text else str(result.returncode)


def _definition_file(state_tasks: Path, spec: TaskSpec) -> Path:
    return state_tasks / "definitions" / f"{spec.task_name}.xml"


def register(state_tasks: Path, python: Path, settings: Path, working_dir: Path, run: Runner = _schtasks) -> list[str]:
    """Create or update every task; returns what changed. A task whose registered definition equals the wanted one
    (the file written at its last registration, and the task still present) is left as it is."""
    changes = []
    for spec in TASKS.values():
        wanted = render(spec, python, settings, working_dir)
        definition = _definition_file(state_tasks, spec)
        present = run(["/query", "/tn", spec.task_name]).returncode == 0
        current = definition.read_text("utf-16") if definition.is_file() else None
        if present and current == wanted:
            continue
        definition.parent.mkdir(parents=True, exist_ok=True)
        definition.write_text(wanted, encoding="utf-16")
        result = run(["/create", "/tn", spec.task_name, "/xml", str(definition), "/f"])
        if result.returncode != 0:
            raise ScheduleError(f"registering {spec.task_name} failed ({_why(result)})")
        changes.append(f"{spec.task_name}: {'updated' if present else 'created'}")
    return changes


def unregister(run: Runner = _schtasks) -> list[str]:
    removed = []
    for spec in TASKS.values():
        if run(["/query", "/tn", spec.task_name]).returncode != 0:
            continue
        if run(["/delete", "/tn", spec.task_name, "/f"]).returncode != 0:
            raise ScheduleError(f"removing {spec.task_name} failed")
        removed.append(spec.task_name)
    return removed


def shown_sid(spec: TaskSpec, run: Runner = _schtasks) -> str | None:
    """The SID Windows itself computes for the task account (``schtasks /showsid``)."""
    import re

    result = run(["/showsid", "/tn", spec.task_name])
    found = re.search(r"S-1-5-87(?:-\d+)+", result.stdout)
    return found.group(0) if result.returncode == 0 and found else None


def check_sids(run: Runner = _schtasks) -> list[str]:
    """Every task SID used by the C3 policy must be the one Windows computes; problems name the task."""
    problems = []
    for spec in TASKS.values():
        shown = shown_sid(spec, run)
        if shown != spec.sid:
            problems.append(f"{spec.task_name}: Windows computes {shown}, the policy uses {spec.sid}")
    return problems


def start_now(name: str, run: Runner = _schtasks) -> None:
    """``schtasks /run``: start a registered task now, through the Task Scheduler and its account."""
    spec = TASKS[name]
    result = run(["/run", "/tn", spec.task_name])
    if result.returncode != 0:
        raise ScheduleError(f"starting {spec.task_name} failed ({_why(result)})")
