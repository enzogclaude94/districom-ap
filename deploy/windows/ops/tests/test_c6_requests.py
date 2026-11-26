"""W2-C6 review (Enzo, 2026-11-26): cancellation requests owned by their requester (design AB kept).

Real processes and the operating system's locks, as in ``test_c6_units``. Every wait is on an observable state
(a file, a lock, a line printed by a child), never on a fixed sleep, so the order of each scenario is forced.
"""

import json
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

from districom_ops import backup_tasks, maintenance, services, tasklock
from districom_ops.backup_tasks import TaskSettings
from districom_ops.maintenance import MaintenanceError, MaintenanceWindow
from districom_ops.oplock import OperationLock
from districom_ops.oplog import Kind, OperationsLog
from tests.test_c6_units import BACKUP_SRC, SRC, make_settings
from tests.test_services_units import make_install

ENV = {**os.environ, "PYTHONPATH": os.pathsep.join([str(SRC), str(BACKUP_SRC)])}


def child(code: str) -> subprocess.Popen[str]:
    return subprocess.Popen(  # noqa: S603
        [sys.executable, "-c", textwrap.dedent(code)], stdout=subprocess.PIPE, text=True, env=ENV
    )


def first_line(process: subprocess.Popen[str], seconds: float = 60) -> str:
    """The child's next line, or a failure after ``seconds`` (a scenario that stalls fails, never hangs)."""
    assert process.stdout is not None
    stream = process.stdout
    line: list[str] = []
    reader = threading.Thread(target=lambda: line.append(stream.readline()), daemon=True)
    reader.start()
    reader.join(seconds)
    if not line:
        pytest.fail(f"the child printed nothing in {seconds} s")
    return line[0].strip()


def wait_until(condition: Callable[[], bool], seconds: float, what: str) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return
        time.sleep(0.05)
    pytest.fail(what)


def heavy_task(state: Path, gate: Path) -> subprocess.Popen[str]:
    """Holds ``backup.lock`` like a heavy task; stops when a request is valid **and** ``gate`` exists."""
    process = child(
        f"""
        import time
        from pathlib import Path
        from districom_ops import tasklock
        state, gate = Path({str(state)!r}), Path({str(gate)!r})
        lock = tasklock.backup_lock(state)
        assert lock.try_acquire()
        print("held", flush=True)
        while not (gate.exists() and tasklock.cancel_request(state) is not None):
            time.sleep(0.05)
        lock.release()
        print("released", flush=True)
        time.sleep(600)
        """
    )
    assert first_line(process) == "held"
    return process


def requester(state: Path, operation: str, done: Path | None = None, wait: float = 600) -> subprocess.Popen[str]:
    """Another operation's process: ``quiesce`` (its own request), then it keeps the request until ``done``
    exists and clears it under the operations lock. Prints the request id as soon as it is written."""
    process = child(
        f"""
        import threading, time
        from pathlib import Path
        from districom_ops import tasklock
        from districom_ops.oplock import OperationLock
        state = Path({str(state)!r})
        done = {str(done) if done else None!r}

        def announce():
            while True:
                mine = [r for r in tasklock.cancel_requests(state) if r.operation == {operation!r} and r.live]
                if mine:
                    print("requested", mine[0].request_id, flush=True)
                    return
                time.sleep(0.02)

        announcer = threading.Thread(target=announce, daemon=True)
        announcer.start()
        result = tasklock.quiesce(state, {operation!r}, "prueba", {wait!r})
        announcer.join()  # the request still stands: "requested" always comes first
        print("quiesced", result.stopped, flush=True)
        while done is None or not Path(done).exists():
            time.sleep(0.05)
        with OperationLock(state, "clear").acquire(timeout=10) as lock:
            print("cleared", result.request.clear(lock), flush=True)
        """
    )
    line = first_line(process)
    assert line.startswith("requested "), line
    return process


@pytest.fixture
def settings(tmp_path: Path) -> TaskSettings:
    return make_settings(tmp_path)


@pytest.fixture
def procs() -> Iterator[list[subprocess.Popen[str]]]:
    running: list[subprocess.Popen[str]] = []
    yield running
    for process in running:
        process.kill()
        process.wait()


def live_ids(state: Path) -> set[str]:
    return {r.request_id for r in tasklock.cancel_requests(state) if r.live}


def window(settings: TaskSettings) -> MaintenanceWindow:
    return MaintenanceWindow(settings.state, OperationsLog(settings.state / "operations.jsonl"))


# --- 1. two simultaneous requests ---------------------------------------------------------------------------------


@pytest.mark.criterion("C6-RQ")
def test_two_simultaneous_requests_both_stand_until_each_owner_clears_its_own(
    settings: TaskSettings, procs: list[subprocess.Popen[str]]
) -> None:
    state = settings.state
    gate = state / "gate"
    procs.append(heavy_task(state, gate))
    done_a, done_b = state / "done-a", state / "done-b"
    procs.append(requester(state, "maintenance_day_close", done_a))
    procs.append(requester(state, "maintenance_update", done_b))
    assert len(live_ids(state)) == 2  # none replaced the other
    gate.touch()  # the task honours the requests and stops
    for process in procs[1:]:
        assert first_line(process) == "quiesced True"
    done_a.touch()
    assert first_line(procs[1]) == "cleared True"
    standing = [r for r in tasklock.cancel_requests(state) if r.live]
    assert [r.operation for r in standing] == ["maintenance_update"]  # the other obligation is not lost
    refused = backup_tasks.run_task(settings, "dump", work=lambda ctx: None)
    assert refused.status == "refused"
    assert "maintenance_update" in (refused.reason or "")
    done_b.touch()
    assert first_line(procs[2]) == "cleared True"
    assert tasklock.cancel_requests(state) == []
    assert backup_tasks.run_task(settings, "dump", work=lambda ctx: None).status == "succeeded"


# --- 2. services stop and maintenance open at the same time -------------------------------------------------------


@pytest.mark.criterion("C6-RQ")
def test_services_stop_and_maintenance_open_together_never_let_a_backup_in(
    settings: TaskSettings, procs: list[subprocess.Popen[str]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both ask, both wait outside the lock, both go on once the task confirmed; in all that time no task starts,
    and the window is opened with no heavy task running."""
    install = make_install(settings.root)
    state = settings.state
    assert install.layout.state == state
    gate = state / "gate"
    procs.append(heavy_task(state, gate))
    stopped: list[bool] = []
    monkeypatch.setattr(services, "stop_all", lambda inst: stopped.append(not tasklock.heavy_task_running(state)))
    results: dict[str, Any] = {}

    def stop() -> None:
        services.stop_services(install, cancel_wait_seconds=60)
        results["stop"] = True

    def open_window() -> None:
        results["op"] = maintenance.open_stopping_tasks(window(settings), Kind.UPDATE, "actualización", 60)

    threads = [threading.Thread(target=stop), threading.Thread(target=open_window)]
    for thread in threads:
        thread.start()
    wait_until(lambda: len(live_ids(state)) == 2, 20, "the two operations did not both ask")
    attempts: list[str] = []
    watching = threading.Event()

    def watch() -> None:
        while not watching.is_set():
            attempts.append(backup_tasks.run_task(settings, "dump", work=lambda ctx: None).status)
            time.sleep(0.05)

    watcher = threading.Thread(target=watch)
    watcher.start()
    gate.touch()
    for thread in threads:
        thread.join(60)
        assert not thread.is_alive(), "an operation did not finish"
    time.sleep(0.5)
    watching.set()
    watcher.join()
    assert results.get("stop") is True
    assert stopped == [True]  # services stopped with the task already gone
    assert window(settings).status(60).window is not None
    assert window(settings).status(60).window.operation_id == results["op"]  # type: ignore[union-attr]
    assert attempts
    assert set(attempts) == {"refused"}  # no backup started while either asked, nor after the window opened
    assert live_ids(state) == set()


# --- 3. nobody clears another's request ---------------------------------------------------------------------------


@pytest.mark.criterion("C6-RQ")
def test_no_operation_can_clear_a_request_that_belongs_to_another(
    settings: TaskSettings, procs: list[subprocess.Popen[str]]
) -> None:
    state = settings.state
    procs.append(requester(state, "maintenance_day_close", state / "never"))
    theirs = live_ids(state)
    assert len(theirs) == 1
    with OperationLock(state, "mine").acquire() as lock:
        mine = tasklock._write_request(state, lock, "maintenance_update", "mía")
    win = window(settings)
    op = win.open(Kind.MANUAL, "otra")
    win.close(op)  # closing a window reconciles only orphans
    with OperationLock(state, "clear").acquire() as lock:
        assert tasklock.reconcile(state, lock) == []  # ``tasks clear-cancel``: a live request is never removed
        assert mine.clear(lock) is True  # the owner clears its own...
        assert mine.clear(lock) is False  # ...once
    assert live_ids(state) == theirs  # the other operation's request is intact
    assert backup_tasks.run_task(settings, "dump", work=lambda ctx: None).status == "refused"


# --- 4. the requester dies while it waits -------------------------------------------------------------------------


@pytest.mark.criterion("C6-RQ")
def test_a_requester_that_dies_while_waiting_leaves_an_orphan_that_is_reconciled(
    settings: TaskSettings, procs: list[subprocess.Popen[str]]
) -> None:
    state = settings.state
    gate = state / "gate"
    task = heavy_task(state, gate)
    procs.append(task)
    dying = requester(state, "maintenance_update")
    (request_id,) = live_ids(state)
    dying.kill()  # during the bounded wait: it never cleared anything
    dying.wait()
    orphans = [r for r in tasklock.cancel_requests(state) if not r.live]
    assert [r.request_id for r in orphans] == [request_id]  # the operating system says its owner is gone
    assert tasklock.cancel_request(state) is None  # an orphan stops nobody
    assert tasklock.heavy_task_running(state)  # the task was not stopped by a dead requester
    task.kill()
    task.wait()
    outcome = backup_tasks.run_task(settings, "dump", work=lambda ctx: None)
    assert outcome.status == "succeeded"  # the start reconciled the orphan, then found nothing against it
    assert tasklock.cancel_requests(state) == []
    assert list(tasklock.requests_dir(state).iterdir()) == []


# --- 5. a heavy task that tries to start while another operation is asking ---------------------------------------


@pytest.mark.criterion("C6-RQ")
def test_a_heavy_task_does_not_start_while_an_operation_is_asking(
    settings: TaskSettings, procs: list[subprocess.Popen[str]]
) -> None:
    state = settings.state
    gate = state / "gate"
    procs.append(heavy_task(state, gate))
    done = state / "done"
    procs.append(requester(state, "maintenance_day_close", done))
    ran: list[str] = []
    for task in ("base_backup", "dump", "offsite", "retention", "wal_gaps"):
        outcome = backup_tasks.run_task(settings, task, work=lambda ctx: ran.append("any"))
        assert outcome.status == "refused", (task, outcome)
        assert "maintenance_day_close asked the backup tasks to stop" in (outcome.reason or "")
    gate.touch()
    assert first_line(procs[1]) == "quiesced True"
    assert backup_tasks.run_task(settings, "dump", work=lambda ctx: ran.append("dump")).status == "refused"
    done.touch()
    assert first_line(procs[1]) == "cleared True"
    assert ran == []
    assert backup_tasks.run_task(settings, "dump", work=lambda ctx: ran.append("dump")).status == "succeeded"
    assert ran == ["dump"]


# --- 6. a persistent request left incomplete (a restart, a crash while writing) -----------------------------------


@pytest.mark.criterion("C6-RQ")
def test_incomplete_requests_after_a_restart_are_reconciled_and_a_live_unreadable_one_still_blocks(
    settings: TaskSettings, procs: list[subprocess.Popen[str]]
) -> None:
    state = settings.state
    folder = tasklock.requests_dir(state)
    folder.mkdir(parents=True)
    # Left by processes of an earlier boot: a description without owner, an owner without description, an
    # unreadable description whose owner is gone.
    (folder / "aaaa.json").write_text(json.dumps({"operation": "maintenance_update", "reason": "x"}))
    (folder / "bbbb.owner").write_bytes(b"")
    (folder / "cccc.json").write_text("{")
    (folder / "cccc.owner").write_bytes(b"")
    # And one live request whose description cannot be read: its owner still holds it (fail safe: it blocks).
    holder = child(
        f"""
        import time
        from pathlib import Path
        from districom_ops import tasklock
        lock = tasklock.FileLock(Path({str(folder / "dddd.owner")!r}))
        assert lock.try_acquire()
        Path({str(folder / "dddd.json")!r}).write_text("{{")
        print("held", flush=True)
        time.sleep(600)
        """
    )
    procs.append(holder)
    assert first_line(holder) == "held"
    status = backup_tasks.status(settings)
    assert {r["request_id"]: r["live"] for r in status["cancel_requests"]} == {
        "aaaa": False,
        "cccc": False,
        "dddd": True,
    }
    refused = backup_tasks.run_task(settings, "dump", work=lambda ctx: None)
    assert refused.status == "refused"
    assert "unreadable cancellation request" in (refused.reason or "")
    assert sorted(p.name for p in folder.iterdir()) == ["dddd.json", "dddd.owner"]  # orphans gone, live kept
    holder.kill()
    holder.wait()
    assert backup_tasks.run_task(settings, "dump", work=lambda ctx: None).status == "succeeded"
    assert list(folder.iterdir()) == []


# --- maintenance open is refused while a heavy task runs, whoever asks -------------------------------------------


@pytest.mark.criterion("C6-RQ")
def test_maintenance_never_opens_with_a_heavy_task_running_even_with_another_request_standing(
    settings: TaskSettings, procs: list[subprocess.Popen[str]]
) -> None:
    state = settings.state
    procs.append(heavy_task(state, state / "never"))
    procs.append(requester(state, "maintenance_day_close", state / "never"))
    with pytest.raises(MaintenanceError, match="did not stop within"):
        maintenance.open_stopping_tasks(window(settings), Kind.UPDATE, "actualización", wait_seconds=1)
    with pytest.raises(MaintenanceError, match="heavy backup task is still running"):
        window(settings).open(Kind.MANUAL, "directo")
    assert window(settings).status(60).window is None
    assert [r.operation for r in tasklock.cancel_requests(state) if r.live] == ["maintenance_day_close"]


@pytest.mark.criterion("C6-RQ")
def test_services_stop_stops_even_when_the_request_cannot_be_written(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """C9-A kept: an unsafe state never prevents the stop (here the requests folder is a file)."""
    install = make_install(tmp_path / "DistriCom")
    tasklock.tasks_dir(install.layout.state).mkdir(parents=True)
    tasklock.requests_dir(install.layout.state).write_text("not a folder")
    stopped: list[bool] = []
    monkeypatch.setattr(services, "stop_all", lambda inst: stopped.append(True))
    services.stop_services(install, cancel_wait_seconds=1)
    assert stopped == [True]
