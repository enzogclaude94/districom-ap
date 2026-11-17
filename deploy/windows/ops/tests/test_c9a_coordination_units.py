"""W2-C9-A coordination of operations, on any platform (``docs/WINDOWS_W2C_PLAN.md``, C9-A).

The lock is exercised with real processes (``subprocess``); the start protocol with a fake Service Control
Manager whose ``DistriComStack`` runs the real ``services.supervise`` in a thread, so ``services.start_stack`` and
the supervisor really meet on the lock. The same criteria run against real Windows services in
``services_tests/test_c9a_coordination.py``.
"""

import os
import subprocess
import sys
import textwrap
import threading
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from districom_ops import backup_disk, maintenance, services, winservice
from districom_ops.install import Install
from districom_ops.maintenance import MaintenanceError, MaintenanceWindow
from districom_ops.oplock import LockBusy, OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog
from districom_ops.services import START_REQUEST, SUPERVISOR_STATE, ServicesError
from districom_ops.winservice import State, Status
from tests.test_services_units import make_install

STACK_PID = 5151
SRC = Path(__file__).resolve().parents[1] / "src"
BACKUP_SRC = Path(__file__).resolve().parents[2] / "backup" / "src"


# --- real processes holding the lock ----------------------------------------------------------------------------


def holder(state: Path, operation: str, open_window: bool = False) -> subprocess.Popen[str]:
    """A separate Python process that takes the lock (and optionally opens a window), says so, then waits."""
    code = textwrap.dedent(
        f"""
        import sys, time
        from pathlib import Path
        from districom_ops.oplock import OperationLock
        from districom_ops.maintenance import MaintenanceWindow
        from districom_ops.oplog import Kind, OperationsLog
        state = Path({str(state)!r})
        lock = OperationLock(state, {operation!r}).acquire()
        if {open_window!r}:
            MaintenanceWindow(state, OperationsLog(state / "operations.jsonl")).open(Kind.MANUAL, "prueba", lock)
        print("held", flush=True)
        time.sleep(600)
        """
    )
    env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(BACKUP_SRC)])}
    process = subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", code], stdout=subprocess.PIPE, text=True, env=env
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "held"
    return process


def snapshot(state: Path) -> dict[str, bytes]:
    """Every persistent file of ``state`` except the lock itself and the informational holder record."""
    skip = {"operations.lock", "operations.lock.json"}
    return {p.name: p.read_bytes() for p in sorted(state.iterdir()) if p.is_file() and p.name not in skip}


@pytest.mark.criterion("C9A-1")
def test_the_lock_is_exclusive_between_processes_and_names_its_owner(tmp_path: Path) -> None:
    process = holder(tmp_path, "day_close")
    try:
        with pytest.raises(LockBusy, match="day_close by process"):
            OperationLock(tmp_path, "services_start").acquire()
        started = time.monotonic()
        with pytest.raises(LockBusy):
            OperationLock(tmp_path, "services_start").acquire(timeout=0.3)
        assert 0.3 <= time.monotonic() - started < 5, "the wait is bounded"
    finally:
        process.kill()
        process.wait()


@pytest.mark.criterion("C9A-1")
def test_a_second_handle_in_the_same_process_is_refused_too(tmp_path: Path) -> None:
    with OperationLock(tmp_path, "first").acquire(), pytest.raises(LockBusy, match="first"):
        OperationLock(tmp_path, "second").acquire()
    OperationLock(tmp_path, "third").acquire().release()


@pytest.mark.criterion("C9A-2")
def test_a_dead_owner_frees_the_lock_and_leaves_the_persistent_state_as_it_was(tmp_path: Path) -> None:
    process = holder(tmp_path, "manual_maintenance", open_window=True)
    before = snapshot(tmp_path)
    assert "maintenance.json" in before
    process.kill()
    process.wait()
    # Windows frees the locks of a terminated process "depending on available resources": a bounded wait.
    lock = OperationLock(tmp_path, "after").acquire(timeout=5)
    lock.release()
    assert snapshot(tmp_path) == before, "the death of the owner changed the persistent state"
    record = OperationsLog(tmp_path / "operations.jsonl").read()
    assert [op.kind for op in record.incomplete()] == ["manual"], "the interrupted operation stays incomplete"


# --- maintenance open ------------------------------------------------------------------------------------------


def _old_open(window: MaintenanceWindow, kind: Kind, reason: str, between: Callable[[], None]) -> str:
    """``maintenance open`` as merged in C1: check, then write (``between`` runs in the gap)."""
    from districom_backup.repo import write_atomic

    if window.path.exists():
        raise MaintenanceError("already open")
    between()
    op_id = window.oplog.start(kind, reason)
    write_atomic(window.path, f'{{"operation_id": "{op_id}", "kind": "{kind.value}", "reason": "x", '
                              f'"opened_at": "2026-11-17T00:00:00+00:00"}}'.encode())  # fmt: skip
    return op_id


@pytest.mark.criterion("C9A-3")
def test_the_race_of_maintenance_open_is_reproduced_and_fixed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(maintenance, "LOCK_SECONDS", 0.2)
    oplog = OperationsLog(tmp_path / "operations.jsonl")
    old = MaintenanceWindow(tmp_path, oplog)
    second: list[str] = []
    first = _old_open(old, Kind.UPDATE, "a", lambda: second.append(_old_open(old, Kind.MANUAL, "b", lambda: None)))
    # Without the fix: two openings pass the check; the window names one owner and the other never ends.
    assert len(second) == 1
    assert len(oplog.read().incomplete()) == 2
    assert first

    state = tmp_path / "fixed"
    state.mkdir()
    window = MaintenanceWindow(state, OperationsLog(state / "operations.jsonl"))
    real_start = window.oplog.start
    refused: list[Exception] = []

    def start_in_the_gap(kind: Kind, reason: str) -> str:
        try:
            MaintenanceWindow(state, OperationsLog(state / "operations.jsonl")).open(Kind.MANUAL, "en el medio")
        except (LockBusy, MaintenanceError) as exc:
            refused.append(exc)
        return real_start(kind, reason)

    window.oplog.start = start_in_the_gap  # type: ignore[method-assign]
    op_id = window.open(Kind.UPDATE, "actualización")
    assert len(refused) == 1, "the second opening was not refused"
    assert isinstance(refused[0], LockBusy)
    record = window.oplog.read()
    assert [op.id for op in record.incomplete()] == [op_id], "only one operation was started"


@pytest.mark.criterion("C9A-3")
def test_the_window_file_is_created_exclusively_even_with_the_lock_bypassed(tmp_path: Path) -> None:
    window = MaintenanceWindow(tmp_path, OperationsLog(tmp_path / "operations.jsonl"))
    lock = OperationLock(tmp_path, "c11").acquire()
    window.open(Kind.DAY_CLOSE, "cierre", lock)
    before = window.path.read_bytes()
    with pytest.raises(MaintenanceError, match="appeared"):
        window._create({"operation_id": "x", "kind": "manual", "reason": "x", "opened_at": "2026-11-17T00:00:00"})
    assert window.path.read_bytes() == before
    with pytest.raises(MaintenanceError, match="operations lock"):
        window.close("anything", Event.COMPLETED, OperationLock(tmp_path, "not held"))
    lock.release()


# --- a fake Service Control Manager whose stack runs the real supervisor ------------------------------------------


class SCM:
    """Services of the fake machine. ``DistriComStack`` runs ``services.supervise`` in a thread."""

    def __init__(self, install: Install) -> None:
        self.install = install
        self.states = dict.fromkeys(services.ALL, State.STOPPED)
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.exit_code: int | None = None
        self.before_supervisor: Callable[[], object] = lambda: None
        self.during_chain: Callable[[str], object] = lambda name: None
        self.pg_starts = 0
        self.lock = threading.Lock()

    def status(self, name: str) -> Status:
        state = self.states[name]
        return Status(state, STACK_PID if name == services.STACK and state is State.RUNNING else 0, 0)

    def start(self, name: str) -> None:
        if name != services.STACK:
            self.states[name] = State.RUNNING
            return
        if self.states[name] is not State.STOPPED:
            raise winservice.ServiceError("the service is already running")
        self.states[name] = State.RUNNING
        self.stop_event = threading.Event()

        def run() -> None:
            self.before_supervisor()  # in the stack's process, before the supervisor
            try:
                self.exit_code = services.supervise(self.install, self.stop_event, poll_seconds=0.01)
            except SystemExit:
                self.exit_code = -1  # the process died: nothing more of the supervisor runs
            self.states[services.STACK] = State.STOPPED

        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()

    def stop(self, name: str) -> None:
        if name == services.STACK:
            self.stop_stack()
        self.states[name] = State.STOPPED

    def stop_stack(self) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(10)
            assert not self.thread.is_alive(), "the supervisor did not stop"
        self.states[services.STACK] = State.STOPPED

    def start_chain(self, install: Install, until: str = services.CADDY) -> None:
        for name in services.CHAIN:
            self.during_chain(name)
            if name == services.PG:
                self.pg_starts += 1
            self.states[name] = State.RUNNING
            if name == until:
                return

    def stop_chain(self, install: Install) -> None:
        for name in services.CHAIN:
            self.states[name] = State.STOPPED

    def start_pg(self, install: Install, name: str) -> None:
        assert name == services.PG
        self.pg_starts += 1
        self.states[name] = State.RUNNING


@pytest.fixture
def scm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[SCM]:
    install = make_install(tmp_path)
    install.layout.state.mkdir(parents=True, exist_ok=True)
    fake = SCM(install)
    monkeypatch.setattr(winservice, "status", fake.status)
    monkeypatch.setattr(winservice, "start", fake.start)
    monkeypatch.setattr(winservice, "stop", fake.stop)
    monkeypatch.setattr(winservice, "wait_for", lambda name, wanted, seconds: fake.status(name))
    monkeypatch.setattr(winservice, "config", lambda name: object())
    monkeypatch.setattr(maintenance, "LOCK_SECONDS", 0.2)
    monkeypatch.setattr(services, "start_chain", fake.start_chain)
    monkeypatch.setattr(services, "stop_chain", fake.stop_chain)
    monkeypatch.setattr(services, "_start", fake.start_pg)
    monkeypatch.setattr(services, "check_disk", lambda install: backup_disk.DiskCheck(True, "ok", ""))
    monkeypatch.setattr(services, "ready", lambda install, name: fake.states[name] is State.RUNNING)
    monkeypatch.setattr(services, "_own_service_pid", lambda: STACK_PID)
    monkeypatch.setattr(services, "LOCK_SECONDS", 0.5)
    monkeypatch.setattr(services, "SUPERVISOR_LOCK_SECONDS", 0.5)
    yield fake
    fake.stop_stack()


def start(fake: SCM, timeout: float = 5) -> None:
    services.start_stack(fake.install, poll_seconds=0.01, timeout=timeout)


def window(fake: SCM) -> MaintenanceWindow:
    state = fake.install.layout.state
    return MaintenanceWindow(state, OperationsLog(state / "operations.jsonl"))


def phase(fake: SCM) -> dict[str, Any]:
    record = services.supervisor_state(fake.install)
    assert record is not None
    return record


def phase_is(fake: SCM, wanted: str) -> bool:
    record = services.supervisor_state(fake.install)
    return record is not None and record.get("phase") == wanted


def app_down(fake: SCM) -> bool:
    return all(fake.states[n] is State.STOPPED for n in (services.BACKEND, services.FRONTEND, services.CADDY))


def in_the_gap(fake: SCM) -> None:
    """A maintenance window opened after ``services start`` let go of the lock and before the supervisor took it."""
    state = fake.install.layout.state
    deadline = time.monotonic() + 5
    while True:
        try:
            lock = OperationLock(state, "manual").acquire()
            break
        except LockBusy:
            assert time.monotonic() < deadline, "services start never let go of the lock"
            time.sleep(0.01)
    try:
        window(fake).open(Kind.MANUAL, "en el hueco", lock)
    finally:
        lock.release()


def wait(condition: Callable[[], bool], seconds: float = 5) -> None:
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "condition not reached"
        time.sleep(0.01)


# --- C9A-4 to C9A-13 -----------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9A-4")
@pytest.mark.parametrize("unsafe", ["open", "overdue", "unreadable", "incomplete", "uncertain"])
def test_start_is_refused_with_an_unsafe_state(scm: SCM, unsafe: str) -> None:
    w = window(scm)
    if unsafe in ("open", "overdue"):
        w.open(Kind.MANUAL, "mantenimiento")
    if unsafe == "overdue":
        scm.install.ops.__dict__["maintenance_max_minutes"] = 0
    if unsafe == "unreadable":
        w.path.write_text("{roto", encoding="utf-8")
    if unsafe == "incomplete":
        w.oplog.start(Kind.UPDATE, "actualización cortada")
    if unsafe == "uncertain":
        w.oplog.path.write_text('{"no": "válido"}\n{"tampoco"\n', encoding="utf-8")
    with pytest.raises(ServicesError, match="services start refused"):
        start(scm)
    assert scm.states[services.STACK] is State.STOPPED, "nothing was started"
    with pytest.raises(ServicesError, match="services start --direct refused"):
        services.start_direct(scm.install)
    assert scm.pg_starts == 0
    services.stop_services(scm.install)  # stopping stays possible: it only moves towards safety


@pytest.mark.criterion("C9A-4")
def test_start_and_stop_are_refused_while_another_operation_holds_the_lock(scm: SCM) -> None:
    with OperationLock(scm.install.layout.state, "update").acquire():
        with pytest.raises(ServicesError, match="update by process"):
            start(scm)
        with pytest.raises(ServicesError, match="update by process"):
            services.stop_services(scm.install)
    start(scm)
    assert phase(scm)["phase"] == "running"


@pytest.mark.criterion("C9A-5")
def test_an_unsafe_state_at_the_supervisor_leaves_postgresql_up_and_the_app_down(scm: SCM) -> None:
    window(scm).oplog.start(Kind.DAY_CLOSE, "cierre interrumpido")
    winservice.start(services.STACK)  # as Windows does at boot: no services start, no request
    wait(lambda: phase_is(scm, "blocked"))
    assert "has no end" in phase(scm)["reason"]
    assert scm.states[services.PG] is State.RUNNING
    assert app_down(scm)


@pytest.mark.criterion("C9A-5")
def test_services_start_reports_blocked_as_a_failure(scm: SCM) -> None:
    scm.before_supervisor = lambda: in_the_gap(scm)

    with pytest.raises(ServicesError, match="blocked"):
        start(scm)
    assert "maintenance by manual" in phase(scm)["reason"]
    assert app_down(scm)


@pytest.mark.criterion("C9A-6")
def test_blocked_survives_a_restart_of_the_stack_a_restart_of_postgresql_and_an_expired_window(scm: SCM) -> None:
    window(scm).open(Kind.UPDATE, "actualización")
    scm.install.ops.__dict__["maintenance_max_minutes"] = 0  # the window expires at once
    winservice.start(services.STACK)
    wait(lambda: phase_is(scm, "blocked"))
    first = phase(scm)["run"]
    scm.stop_stack()
    winservice.start(services.STACK)
    wait(lambda: phase_is(scm, "blocked") and phase(scm)["run"] != first)
    assert "overdue" in phase(scm)["reason"]
    assert app_down(scm)
    with pytest.raises(ServicesError, match="refused"):
        start(scm)
    assert app_down(scm)


@pytest.mark.criterion("C9A-7")
def test_two_simultaneous_starts_one_starts_and_the_other_fails_at_once(scm: SCM) -> None:
    release = threading.Event()
    scm.during_chain = lambda name: release.wait(5) if name == services.BACKEND else None
    results: dict[str, str] = {}

    def run(who: str) -> None:
        try:
            start(scm)
            results[who] = "ok"
        except ServicesError as exc:
            results[who] = str(exc)

    first = threading.Thread(target=run, args=("first",))
    first.start()
    wait(lambda: scm.states[services.PG] is State.RUNNING)  # the first start is under way
    started = time.monotonic()
    run("second")
    assert time.monotonic() - started < 2, "the second start waited"
    assert "refused" in results["second"]
    release.set()
    first.join(10)
    assert results["first"] == "ok"
    assert phase(scm)["phase"] == "running"


@pytest.mark.criterion("C9A-7")
def test_a_start_with_the_stack_already_running_confirms_with_the_live_record_only(scm: SCM) -> None:
    start(scm)
    assert phase(scm)["phase"] == "running"
    start(scm)  # no new start: the record of the live service is the one that counts
    services._write_json(scm.install.layout.state / SUPERVISOR_STATE, {**phase(scm), "service_pid": 1})
    with pytest.raises(ServicesError, match="in time"):
        start(scm, timeout=0.3)  # a record of another run of the stack never passes


@pytest.mark.criterion("C9A-9")
def test_no_deadlock_between_services_start_and_the_supervisor(scm: SCM, monkeypatch: pytest.MonkeyPatch) -> None:
    waited: list[float] = []
    real_acquire = OperationLock.acquire

    def timed(self: OperationLock, timeout: float = 0, poll_seconds: float = 0.2) -> OperationLock:
        started = time.monotonic()
        try:
            return real_acquire(self, timeout, poll_seconds)
        finally:
            if self.operation == "supervisor_start":
                waited.append(time.monotonic() - started)

    monkeypatch.setattr(OperationLock, "acquire", timed)
    started = time.monotonic()
    start(scm)
    assert phase(scm)["phase"] == "running"
    assert waited
    assert waited[0] < 1, "the supervisor waited for the lock longer than services start needs to ask for it"
    assert time.monotonic() - started < 2


@pytest.mark.criterion("C9A-10")
def test_no_maintenance_can_begin_while_the_supervisor_decides_and_starts(scm: SCM) -> None:
    attempts: list[str] = []

    def try_maintenance(name: str) -> None:
        try:
            window(scm).open(Kind.MANUAL, f"durante {name}")
            attempts.append("opened")
        except (LockBusy, MaintenanceError):
            attempts.append("refused")

    scm.during_chain = try_maintenance
    start(scm)
    assert attempts == ["refused"] * len(services.CHAIN)
    assert phase(scm)["phase"] == "running"
    window(scm).open(Kind.MANUAL, "después")  # once the phase is recorded, the lock is free again


@pytest.mark.criterion("C9A-11")
def test_services_start_dying_after_starting_the_stack_leaves_the_supervisor_alone(
    scm: SCM, monkeypatch: pytest.MonkeyPatch
) -> None:
    def start_then_die(name: str) -> None:
        scm.start(name)
        if name == services.STACK:
            raise KeyboardInterrupt("services start killed")

    monkeypatch.setattr(winservice, "start", start_then_die)
    with pytest.raises(KeyboardInterrupt):
        start(scm)
    wait(lambda: phase_is(scm, "running"))
    OperationLock(scm.install.layout.state, "after").acquire().release()  # nobody holds it


@pytest.mark.criterion("C9A-11")
def test_the_supervisor_dying_while_starting_fails_services_start_and_frees_the_lock(scm: SCM) -> None:
    def crash(name: str) -> None:
        if name == services.FRONTEND:
            scm.states[services.STACK] = State.STOPPED  # the stack's process died mid-start
            raise SystemExit("the supervisor's process was killed")  # nothing in the supervisor catches it

    scm.during_chain = crash
    with pytest.raises(ServicesError, match="stopped while starting"):
        start(scm)
    assert phase(scm)["phase"] == "starting", "no PASS and no phase it did not reach"
    OperationLock(scm.install.layout.state, "after").acquire().release()


@pytest.mark.criterion("C9A-12")
def test_postgresql_is_not_restarted_while_the_lock_is_held_or_a_window_is_open(scm: SCM) -> None:
    start(scm)
    starts = scm.pg_starts
    lock = OperationLock(scm.install.layout.state, "day_close").acquire()
    scm.states[services.PG] = State.STOPPED  # stopped on purpose by the operation
    time.sleep(0.3)
    assert scm.states[services.PG] is State.STOPPED
    op_id = window(scm).open(Kind.DAY_CLOSE, "cierre", lock)
    lock.release()
    time.sleep(0.3)
    assert scm.states[services.PG] is State.STOPPED, "restarted with a window open"
    window(scm).close(op_id)
    wait(lambda: scm.states[services.PG] is State.RUNNING)  # an unexpected stop, nothing in the way: restarted
    assert scm.pg_starts == starts + 1


@pytest.mark.criterion("C9A-13")
def test_an_old_supervisor_record_never_passes_even_around_the_lock(scm: SCM) -> None:
    services._write_json(scm.install.layout.state / START_REQUEST, {"nonce": "viejo"})
    services._write_json(scm.install.layout.state / SUPERVISOR_STATE,
                         {"run": "r0", "request": "viejo", "service_pid": STACK_PID, "phase": "running"})  # fmt: skip
    scm.before_supervisor = lambda: in_the_gap(scm)
    with pytest.raises(ServicesError, match="blocked"):
        start(scm)  # the old running record (same pid, old nonce) did not count
    assert phase(scm)["request"] != "viejo"
