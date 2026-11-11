"""``services start`` waits for the supervisor's signal of *this* start (the C2 race, Enzo 2026-11-10).

The race seen in CI: Caddy already answered the administrator's verified check while the supervisor had not yet
recorded its own ``service_ready`` for Caddy, so ``services start`` returned too early. Here the supervisor is
scripted step by step (each poll of ``start_stack`` is one step), so the race is reproduced deterministically.
"""

import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from districom_ops import backup_disk, services, winservice
from districom_ops.install import Install
from districom_ops.services import START_REQUEST, SUPERVISOR_STATE, ServicesError
from districom_ops.winservice import State, Status
from tests.test_services_units import make_install

STACK_PID = 4242


class World:
    """The stack service, Caddy's answer and the supervisor's records, advanced one poll at a time."""

    def __init__(self, install: Install, state: State = State.STOPPED) -> None:
        self.install = install
        self.state = state
        self.caddy_answers = False
        self.log: list[str] = []
        self.script: list[Callable[[], None]] = []
        self.polls = 0

    def status(self, name: str) -> Status:
        return Status(self.state, STACK_PID if self.state is State.RUNNING else 0, 0)

    def start(self, name: str) -> None:
        self.state = State.RUNNING
        self.log.append("stack_started")

    def ready(self, install: Install, name: str) -> bool:
        assert name == services.CADDY
        return self.caddy_answers

    def sleep(self, seconds: float) -> None:
        self.polls += 1
        if self.script:
            self.script.pop(0)()

    def record(self, phase: str, request: object = "current", service_pid: int = STACK_PID) -> None:
        state = self.install.layout.state
        if request == "current":
            request = services._read_json(state / START_REQUEST)["nonce"]  # type: ignore[index]
        services._write_json(state / SUPERVISOR_STATE, {"run": "r", "request": request, "pid": 1,
                                                        "service_pid": service_pid, "phase": phase})  # fmt: skip
        self.log.append(f"supervisor_{phase}")


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> World:
    install = make_install(tmp_path)
    install.layout.state.mkdir(parents=True, exist_ok=True)
    w = World(install)
    monkeypatch.setattr(winservice, "status", w.status)
    monkeypatch.setattr(winservice, "start", w.start)
    monkeypatch.setattr(services, "ready", w.ready)
    monkeypatch.setattr(time, "sleep", w.sleep)
    return w


def test_caddy_answering_first_is_not_enough_the_supervisor_must_say_running(world: World) -> None:
    def caddy_up() -> None:
        world.caddy_answers = True
        world.log.append("caddy_answers")

    world.script = [
        lambda: world.record("starting"),
        caddy_up,  # the race: the verified check passes here, before the supervisor's word
        lambda: world.log.append("still_waiting"),
        lambda: world.record("running"),  # after its service_ready for Caddy
    ]
    services.start_stack(world.install, poll_seconds=0, timeout=60)
    assert world.log[-1] == "supervisor_running", world.log
    assert "still_waiting" in world.log


def test_a_record_left_by_an_earlier_run_is_never_taken(world: World) -> None:
    services._write_json(world.install.layout.state / SUPERVISOR_STATE,
                         {"run": "old", "request": "an-earlier-nonce", "phase": "running"})  # fmt: skip
    world.caddy_answers = True
    world.script = [lambda: None] * 3
    with pytest.raises(ServicesError, match="in time"):
        services.start_stack(world.install, poll_seconds=0, timeout=0.05)
    assert world.polls >= 1


def test_a_failed_start_reported_by_the_supervisor_fails_at_once(world: World) -> None:
    world.script = [lambda: world.record("starting"), lambda: world.record("failed")]
    with pytest.raises(ServicesError, match="could not start"):
        services.start_stack(world.install, poll_seconds=0, timeout=60)


def test_the_stack_stopping_while_starting_fails(world: World) -> None:
    def stopped() -> None:
        world.state = State.STOPPED

    world.script = [lambda: world.record("starting"), stopped]
    with pytest.raises(ServicesError, match="stopped while starting"):
        services.start_stack(world.install, poll_seconds=0, timeout=60)


def test_without_a_signal_it_fails_after_the_timeout(world: World) -> None:
    world.caddy_answers = True
    with pytest.raises(ServicesError, match="in time"):
        services.start_stack(world.install, poll_seconds=0, timeout=0.05)


def test_an_already_running_stack_counts_only_with_the_record_of_the_live_service(world: World) -> None:
    world.state = State.RUNNING
    world.caddy_answers = True
    world.record("running", request="x", service_pid=999)  # an earlier run's record
    world.script = [lambda: world.record("running", request="x", service_pid=STACK_PID)]
    services.start_stack(world.install, poll_seconds=0, timeout=60)
    assert "stack_started" not in world.log
    assert world.polls == 1


def test_each_start_writes_a_new_nonce(world: World) -> None:
    world.caddy_answers = True
    nonces = []
    for _ in range(2):
        world.state = State.STOPPED
        world.script = [lambda: world.record("running")]
        services.start_stack(world.install, poll_seconds=0, timeout=60)
        nonces.append(services._read_json(world.install.layout.state / START_REQUEST)["nonce"])  # type: ignore[index]
    assert nonces[0] != nonces[1]


def test_the_supervisor_records_the_request_and_its_phases(world: World, monkeypatch: pytest.MonkeyPatch) -> None:
    services._write_json(world.install.layout.state / START_REQUEST, {"nonce": "n1"})
    phases: list[str] = []
    real = services._record_phase

    def spy(install: Install, run: dict[str, object], phase: str, reason: str | None = None) -> None:
        real(install, run, phase, reason)
        phases.append(phase)
        if phase == "running":
            stop.set()

    stop = threading.Event()
    monkeypatch.setattr(services, "_record_phase", spy)
    monkeypatch.setattr(services, "check_disk", lambda install: backup_disk.DiskCheck(True, "ok", ""))
    monkeypatch.setattr(services, "start_chain", lambda install: world.log.append("chain_ready"))
    monkeypatch.setattr(services, "stop_chain", lambda install: None)
    assert services.supervise(world.install, stop, poll_seconds=0) == 0
    assert phases == ["starting", "running", "stopping", "stopped"]
    record = services.supervisor_state(world.install)
    assert record is not None
    assert record["request"] == "n1"
    assert record["phase"] == "stopped"
