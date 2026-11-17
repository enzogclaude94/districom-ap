"""W2-C9-A acceptance on the disposable Windows runner: the operations lock with real processes and real services.

Runs after ``test_c2_services.py`` in the job «Windows services (W2-C2)», which leaves the services uninstalled
and the backup disk detached: the module puts them back (``services install`` is idempotent). Windows' own file
lock (``msvcrt.locking``) and the real ``DistriComStack`` are used. A reboot of Windows with a ``blocked`` state
and Windows 11 Pro stay PENDIENTE.
"""

import json
import subprocess
import sys
import textwrap
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from districom_ops import maintenance, services, winservice
from districom_ops.maintenance import MaintenanceWindow
from districom_ops.oplock import LockBusy, OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog
from districom_ops.winservice import State
from services_tests.conftest import metrics
from services_tests.test_c2_services import (
    CONFIG,
    CTX,
    DISKS,
    RELEASE,
    ROOT,
    attach,
    cli,
    detach,
    events,
    now,
    wait_until,
)
from tests.test_c9a_coordination_units import holder, snapshot

STATE = ROOT / "state"
APP = (services.BACKEND, services.FRONTEND, services.CADDY)

pytestmark = pytest.mark.timeout(1200)


@pytest.fixture(scope="module", autouse=True)
def installed() -> Iterator[None]:
    attach(DISKS / "backup1.vhdx")
    cli("services", "install", "--release", RELEASE)
    yield
    cli("services", "stop", check=False)


def record() -> dict[str, Any]:
    return services.supervisor_state(CTX.install()) or {}


def wait_phase(wanted: str, seconds: float = 400, other_than_run: object = None) -> dict[str, Any]:
    wait_until(lambda: record().get("phase") == wanted and record().get("run") != other_than_run, seconds,
               f"the supervisor did not reach {wanted}: {record()}")  # fmt: skip
    return record()


def open_window(reason: str) -> str:
    """By the command line, as an operator does (it waits for the lock at most ``maintenance.LOCK_SECONDS``)."""
    return str(cli("maintenance", "open", "--kind", "manual", "--reason", reason).stdout.strip().splitlines()[-1])


def popen_cli(*args: str) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [sys.executable, "-m", "districom_ops", "--config", str(CONFIG), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def lock_is_free() -> bool:
    try:
        # Windows frees the locks of a terminated process "depending on available resources": a bounded wait.
        OperationLock(STATE, "probe").acquire(timeout=5).release()
    except LockBusy:
        return False
    return True


def lock_holder() -> dict[str, Any]:
    """Who holds the lock, proven by the lock itself: a probe is refused, and the refusal names the owner."""
    try:
        OperationLock(STATE, "probe").acquire().release()
    except LockBusy as exc:
        return exc.holder or {}
    raise AssertionError("nobody holds the operations lock")


def maintenance_open_process(reason: str, pause_inside: bool = False, go: Path | None = None) -> subprocess.Popen[str]:
    """The real ``maintenance open`` of the command line, in its own process, at a point the test controls.

    ``pause_inside``: it stops inside ``open``, holding the lock, after recording its operation and before
    creating the window; it prints ``inside <pid>`` and goes on only when the test writes a line to its stdin.
    ``go``: it waits for that file to exist, so several processes are released at the same instant."""
    code = textwrap.dedent(
        f"""
        import os, sys, time
        from pathlib import Path
        from districom_ops import cli
        from districom_ops.maintenance import MaintenanceWindow
        go = {str(go) if go else None!r}
        if {pause_inside!r}:
            real = MaintenanceWindow._create
            def paused(self, data):
                print("inside", os.getpid(), data["operation_id"], flush=True)
                sys.stdin.readline()
                return real(self, data)
            MaintenanceWindow._create = paused
        while go and not Path(go).exists():
            time.sleep(0.001)
        sys.exit(cli.main(["--config", {str(CONFIG)!r}, "maintenance", "open", "--kind", "manual",
                           "--reason", {reason!r}]))
        """
    )
    return subprocess.Popen(
        [sys.executable, "-c", code],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )


def oplog_ops() -> list[str]:
    return [op.id for op in OperationsLog(STATE / "operations.jsonl").read().operations.values()]


def app_down() -> bool:
    return all(winservice.status(name).state is State.STOPPED for name in APP)


@pytest.mark.criterion("C9A-1")
@pytest.mark.criterion("C9A-2")
def test_the_lock_between_real_windows_processes_and_a_killed_owner() -> None:
    process = holder(STATE, "day_close")
    try:
        result = cli("services", "start", check=False)
        assert result.returncode == 2
        assert "day_close by process" in result.stderr
        assert cli("maintenance", "open", "--kind", "manual", "--reason", "x", check=False).returncode == 2
    finally:
        process.kill()
        process.wait()
    assert lock_is_free(), "Windows did not free the lock of a killed process"

    process = holder(STATE, "manual", open_window=True)
    before = snapshot(STATE)
    process.kill()
    process.wait()
    assert lock_is_free()
    assert snapshot(STATE) == before, "the death of the owner changed the persistent state"
    window = json.loads((STATE / "maintenance.json").read_text("utf-8"))
    cli("maintenance", "close", "--operation", window["operation_id"], "--reverted")


@pytest.mark.criterion("C9A-3")
def test_two_real_processes_open_maintenance_at_once_and_only_one_gets_it() -> None:
    """The point of the race is held, not hoped for: the first process is stopped inside ``open``, with its
    operation recorded and before its window exists (where the C1 code let a second opening in)."""
    cli("services", "stop")
    first = maintenance_open_process("primera", pause_inside=True)
    assert first.stdout is not None
    assert first.stdin is not None
    inside = first.stdout.readline().split()
    assert inside[:1] == ["inside"], first.stderr.read() if first.stderr else ""
    first_pid, first_op = int(inside[1]), inside[2]
    assert lock_holder()["operation"] == "maintenance_open"
    assert lock_holder()["pid"] == first_pid
    assert not (STATE / "maintenance.json").exists()
    before = snapshot(STATE)

    second = maintenance_open_process("segunda")
    _, err = second.communicate(timeout=120)
    assert second.returncode == 2, err
    assert f"maintenance_open by process {first_pid}" in err, "the refusal did not name the first opening"
    assert snapshot(STATE) == before, "the refused opening changed the window or the operations record"
    assert lock_holder()["pid"] == first_pid, "the owner record changed"

    out, err = first.communicate("\n", timeout=120)
    assert first.returncode == 0, err
    assert out.strip().splitlines()[-1] == first_op
    window = json.loads((STATE / "maintenance.json").read_text("utf-8"))
    assert window["operation_id"] == first_op
    record = OperationsLog(STATE / "operations.jsonl").read()
    assert [op.id for op in record.incomplete()] == [first_op], "a second owner was recorded"
    assert lock_is_free()
    cli("maintenance", "close", "--operation", first_op, "--reverted")

    # Released at the same instant, three times: always one window and one new operation, never two.
    for attempt in range(3):
        go = STATE.parent / f"c9a3-go-{attempt}"
        go.unlink(missing_ok=True)
        known = set(oplog_ops())
        racers = [maintenance_open_process(f"a la vez {attempt}-{n}", go=go) for n in range(2)]
        time.sleep(3)  # both interpreters loaded and waiting on the file
        go.write_text("go", encoding="utf-8")
        results = [p.communicate(timeout=120) for p in racers]
        go.unlink()
        codes = sorted(p.returncode for p in racers)
        assert codes == [0, 2], results
        winner = results[[p.returncode for p in racers].index(0)][0].strip().splitlines()[-1]
        new = [op for op in oplog_ops() if op not in known]
        assert new == [winner], f"operations started: {new}"
        assert json.loads((STATE / "maintenance.json").read_text("utf-8"))["operation_id"] == winner
        cli("maintenance", "close", "--operation", winner, "--reverted")
    metrics["C9A-3: aperturas simultáneas en Windows con una sola ventana"] = "1 controlada + 3 a la vez"


@pytest.mark.criterion("C9A-9")
@pytest.mark.criterion("C9A-10")
def test_services_start_and_the_supervisor_never_wait_for_each_other_and_no_window_slips_in() -> None:
    """While ``services start`` runs, keep trying to open a window without waiting for the lock. A window can only
    get in before the supervisor took the lock, and then the supervisor must answer ``blocked``, never ``running``."""
    started = time.monotonic()
    starter = popen_cli("services", "start")
    refused, opened_with = 0, None
    while starter.poll() is None and opened_with is None:
        try:
            lock = OperationLock(STATE, "manual").acquire()
        except LockBusy:
            refused += 1
        else:
            try:
                request = json.loads((STATE / services.START_REQUEST).read_text("utf-8")).get("nonce")
                answered = record().get("request") == request and record().get("phase") != "starting"
                window = MaintenanceWindow(STATE, OperationsLog(STATE / "operations.jsonl"))
                opened_with = (window.open(Kind.MANUAL, "durante el arranque", lock), answered)
            finally:
                lock.release()
        time.sleep(0.05)
    output = starter.communicate(timeout=900)[0]
    final = record()
    metrics["services start con intentos de mantenimiento (s)"] = round(time.monotonic() - started, 1)
    metrics["intentos de abrir una ventana rechazados durante el arranque"] = refused
    if opened_with is not None:
        window_id, answered = opened_with
        if not answered:  # it got in before the supervisor decided: the application must not start
            assert starter.returncode != 0, output
            assert final["phase"] == "blocked"
            assert app_down()
        cli("maintenance", "close", "--operation", window_id, "--reverted")
        if final["phase"] != "running":
            cli("services", "stop")
            cli("services", "start")
    else:
        assert starter.returncode == 0, output
    assert record()["phase"] == "running"
    assert lock_is_free()


@pytest.mark.criterion("C9A-10")
def test_maintenance_is_refused_while_the_supervisor_holds_the_lock_to_decide_and_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The supervisor is held at a known point with the lock: the backup disk is detached, so it waits for it
    (up to ``BACKUP_DISK_WAIT_SECONDS``) after taking the lock and before its check. Then the disk comes back and
    the attempts go on through the start of the chain, until the phase is recorded."""
    cli("services", "stop")
    detach(DISKS / "backup1.vhdx")
    since = now()
    known = set(oplog_ops())
    starter = popen_cli("services", "start")
    try:
        wait_until(lambda: bool(events(since, "backup_disk_waiting")), 120, "the supervisor did not wait for the disk")
        owner = lock_holder()
        assert owner["operation"] == "supervisor_start", owner
        assert owner["pid"] == record()["pid"], "the lock is not held by this run of the supervisor"
        assert record()["phase"] == "starting"
        before = snapshot(STATE)
        refused = cli("maintenance", "open", "--kind", "manual", "--reason", "con el supervisor decidiendo",
                      check=False)  # fmt: skip
        assert refused.returncode == 2, refused.stderr
        assert f"supervisor_start by process {owner['pid']}" in refused.stderr
        assert snapshot(STATE) == before, "the refused opening changed the persistent state"
        assert record()["phase"] == "starting"
        assert winservice.status(services.PG).state is State.STOPPED, "nothing starts before the check"
    finally:
        attach(DISKS / "backup1.vhdx")

    monkeypatch.setattr(maintenance, "LOCK_SECONDS", 0)  # this process's attempts do not wait for the lock
    window = MaintenanceWindow(STATE, OperationsLog(STATE / "operations.jsonl"))
    during_chain, late = 0, None
    while record().get("phase") == "starting" and late is None:
        try:
            late = window.open(Kind.MANUAL, "con la cadena arrancando")
        except LockBusy:
            if winservice.status(services.PG).state is State.RUNNING:
                during_chain += 1  # refused with the chain already under way
        time.sleep(0.2)
    if late is not None:  # it got the lock: only possible once the supervisor recorded its phase
        assert record()["phase"] != "starting", "the lock was free before the supervisor recorded its phase"
        known.add(late)
        cli("maintenance", "close", "--operation", late, "--reverted")
    output = starter.communicate(timeout=900)[0]
    assert starter.returncode == 0, output
    assert during_chain >= 1, "no attempt was made while the chain was starting"
    assert record()["phase"] == "running"
    install = CTX.install()
    assert all(winservice.status(name).state is State.RUNNING for name in services.CHAIN)
    assert services.ready(install, services.CADDY)
    assert not (STATE / "maintenance.json").exists()
    assert set(oplog_ops()) == known, "a refused opening left an operation behind"
    assert lock_is_free()
    metrics["C9A-10: aperturas rechazadas con la cadena arrancando"] = during_chain


@pytest.mark.criterion("C9A-7")
def test_two_simultaneous_starts_one_starts_and_the_other_is_refused() -> None:
    cli("services", "stop")
    first, second = popen_cli("services", "start"), popen_cli("services", "start")
    outputs = [p.communicate(timeout=900)[0] for p in (first, second)]
    codes = sorted([first.returncode, second.returncode])
    assert codes == [0, 2], outputs
    refused = outputs[[first.returncode, second.returncode].index(2)]
    assert "refused" in refused
    assert record()["phase"] == "running"
    cli("services", "start")  # with the stack running and its record answered, a start confirms the live run


@pytest.mark.criterion("C9A-12")
def test_postgresql_is_not_restarted_with_the_lock_held_or_a_window_open() -> None:
    assert record()["phase"] == "running"
    process = holder(STATE, "day_close")
    try:
        winservice.stop(services.PG)
        winservice.wait_for(services.PG, {State.STOPPED}, 120)
        time.sleep(15)  # three polls of the supervisor
        assert winservice.status(services.PG).state is State.STOPPED, "restarted while the lock was held"
    finally:
        process.kill()
        process.wait()
    window_id = open_window("PostgreSQL detenido a propósito")
    time.sleep(15)
    assert winservice.status(services.PG).state is State.STOPPED, "restarted with a window open"
    cli("maintenance", "close", "--operation", window_id, "--reverted")
    wait_until(lambda: winservice.status(services.PG).state is State.RUNNING, 120, "never restarted afterwards")

    # No maintenance.json, the lock free, the supervisor watching, and a critical operation without end that may
    # have stopped PostgreSQL itself before dying: PostgreSQL stays stopped until the record is resolved.
    oplog = OperationsLog(STATE / "operations.jsonl")
    op_id = oplog.start(Kind.DAY_CLOSE, "cierre cortado")
    assert not (STATE / "maintenance.json").exists()
    since = now()
    winservice.stop(services.PG)
    winservice.wait_for(services.PG, {State.STOPPED}, 120)
    time.sleep(15)
    assert winservice.status(services.PG).state is State.STOPPED, "restarted with an incomplete operation"
    assert lock_is_free()
    assert record()["phase"] == "running", "the supervisor stopped watching"
    held = [e for e in events(since, "postgresql_not_restarted") if "has no end" in str(e.get("reason"))]
    assert len(held) == 1, "logged once per cause"
    oplog.finish(op_id, Event.COMPLETED)
    wait_until(lambda: winservice.status(services.PG).state is State.RUNNING, 120, "never restarted once resolved")


@pytest.mark.criterion("C9A-4")
@pytest.mark.criterion("C9A-5")
@pytest.mark.criterion("C9A-6")
def test_an_open_window_blocks_the_app_across_restarts_of_the_stack() -> None:
    cli("services", "stop")
    window_id = open_window("mantenimiento manual")
    refused = cli("services", "start", check=False)
    assert refused.returncode == 2
    assert "services start refused" in refused.stderr
    assert cli("services", "start", "--direct", check=False).returncode == 2
    winservice.start(services.STACK)  # as Windows does at boot, without services start
    first = wait_phase("blocked")
    assert "maintenance by manual" in str(first["reason"])
    assert winservice.status(services.PG).state is State.RUNNING
    assert app_down()
    assert cli("services", "start", check=False).returncode == 2
    cli("services", "stop")  # stopping a blocked installation stays possible
    winservice.start(services.STACK)
    wait_phase("blocked", other_than_run=first["run"])
    assert app_down(), "a restart of the stack lifted blocked"
    cli("services", "stop")
    cli("maintenance", "close", "--operation", window_id, "--reverted")
    cli("services", "start")
    assert record()["phase"] == "running"


@pytest.mark.criterion("C9A-11")
def test_a_killed_services_start_and_a_killed_supervisor() -> None:
    cli("services", "stop")
    starter = popen_cli("services", "start")
    wait_until(lambda: winservice.status(services.STACK).state is State.RUNNING, 120, "the stack did not start")
    starter.kill()  # services start dies after starting the stack: the supervisor goes on alone
    starter.wait()
    wait_phase("running")

    cli("services", "stop")
    starter = popen_cli("services", "start")
    wait_until(lambda: record().get("phase") == "starting" and record().get("pid"), 120, "no starting phase")
    killed = record()
    subprocess.run(["taskkill.exe", "/F", "/T", "/PID", str(killed["pid"])], capture_output=True, check=False)  # noqa: S607
    output = starter.communicate(timeout=900)[0]
    final = record()
    metrics["services start con el supervisor matado"] = f"salida {starter.returncode}, fase {final.get('phase')}"
    if starter.returncode == 0:  # Windows restarted the stack and its new run answered this very start
        assert final["run"] != killed["run"], output
        assert final["request"] == killed["request"]
        assert final["phase"] == "running"
    else:  # no PASS: the killed run never recorded running
        assert not (final.get("run") == killed["run"] and final.get("phase") == "running"), output
    # Windows may restart the stack 60 s after the kill (its recovery actions): let it settle before stopping.
    time.sleep(75)

    def settled() -> bool:
        stopped = winservice.status(services.STACK).state is State.STOPPED
        return stopped or record().get("phase") in ("running", "blocked", "failed", "stopped")

    wait_until(settled, 600, "the stack never settled after the kill")
    cli("services", "stop")
    assert lock_is_free(), "the lock of the killed supervisor was not freed"
    cli("services", "start")


@pytest.mark.criterion("C9A-13")
def test_an_old_record_never_passes() -> None:
    cli("services", "stop")
    install = CTX.install()
    services._write_json(STATE / services.START_REQUEST, {"nonce": "viejo"})
    services._write_json(STATE / services.SUPERVISOR_STATE,
                         {"run": "r0", "request": "viejo", "service_pid": 1, "pid": 1, "phase": "running"})  # fmt: skip
    detach(DISKS / "backup1.vhdx")  # the new supervisor waits for the disk: it cannot answer running
    try:
        with pytest.raises(services.ServicesError, match="in time"):
            services.start_stack(install, timeout=20)
        assert record()["request"] not in ("viejo", None)
    finally:
        attach(DISKS / "backup1.vhdx")
    wait_phase("running")  # once the disk is back, the supervisor of this start answers
    cli("services", "stop")
