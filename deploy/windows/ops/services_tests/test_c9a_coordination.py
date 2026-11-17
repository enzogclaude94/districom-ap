"""W2-C9-A acceptance on the disposable Windows runner: the operations lock with real processes and real services.

Runs after ``test_c2_services.py`` in the job «Windows services (W2-C2)», which leaves the services uninstalled
and the backup disk detached: the module puts them back (``services install`` is idempotent). Windows' own file
lock (``msvcrt.locking``) and the real ``DistriComStack`` are used. A reboot of Windows with a ``blocked`` state
and Windows 11 Pro stay PENDIENTE.
"""

import json
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import pytest

from districom_ops import services, winservice
from districom_ops.maintenance import MaintenanceWindow
from districom_ops.oplock import LockBusy, OperationLock
from districom_ops.oplog import Kind, OperationsLog
from districom_ops.winservice import State
from services_tests.conftest import metrics
from services_tests.test_c2_services import CONFIG, CTX, DISKS, RELEASE, ROOT, attach, cli, detach, wait_until
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
