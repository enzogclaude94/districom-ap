"""W2-C9-B units that need no PostgreSQL: the journal's state machine, the recovery context of
``writes_resume_check`` (C9-X1 to C9-X6), the evidence form, the model's SQL and ``pg_hba.conf``, and the
supervisor's use of the journal (C9-8, C9-R). The block itself runs on a real PostgreSQL in ``db_tests/``.
"""

import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest

from districom_ops import cli, database, ntfs, services, winservice, write_block
from districom_ops.maintenance import MaintenanceError, MaintenanceWindow
from districom_ops.oplock import LockContext, LockContextError, OperationLock
from districom_ops.oplog import Event, Kind, OperationsLog
from districom_ops.safety import ResumeCheck, start_blockers, writes_resume_check
from districom_ops.winservice import State as Service
from districom_ops.write_block import TRANSITIONS, JournalError, State, WriteBlockError
from tests.test_c9a_coordination_units import SCM, app_down, phase, phase_is, scm, start  # noqa: F401 - scm: fixture
from tests.test_services_units import make_install


def window(state: Path) -> MaintenanceWindow:
    return MaintenanceWindow(state, OperationsLog(state / "operations.jsonl"))


def check(state: Path, context: Any = None) -> ResumeCheck:
    return writes_resume_check(window(state), 60, context=context)


# --- the journal ---------------------------------------------------------------------------------------------


@pytest.mark.criterion("C9-4")
def test_the_state_machine_is_closed(tmp_path: Path) -> None:
    for origin in State:
        for target in State:
            journal = write_block.Journal(origin, "op", "token", before=State.BLOCKED, resume="r")
            if target is origin or target in TRANSITIONS[origin]:
                write_block._save(tmp_path, journal, target)
            else:
                with pytest.raises(JournalError, match="not in the state machine"):
                    write_block._save(tmp_path, journal, target)
    # What the plan's table says, and only that.
    assert TRANSITIONS[State.NONE] == {State.BLOCKING}
    assert TRANSITIONS[State.CLOSED_COMPLETE] == {State.RESUMING}
    assert State.NONE not in TRANSITIONS[State.BLOCKED], "a verified block is only lifted by revert or resume"


@pytest.mark.criterion("C9-4")
@pytest.mark.parametrize(
    "content",
    ["{roto", "[]", '{"state": "otro"}', '{"state": "blocked"}', '{"state": "resuming", "owner": "x"}',
     '{"state": "blocked", "owner": "x", "step": "seis"}'],
)  # fmt: skip
def test_an_unreadable_journal_blocks(tmp_path: Path, content: str) -> None:
    (tmp_path / write_block.JOURNAL_FILE).write_text(content, "utf-8")
    with pytest.raises(JournalError):
        write_block.read_journal(tmp_path)
    assert "unreadable" in str(write_block.journal_blocker(tmp_path))
    assert any("unreadable" in r for r in start_blockers(window(tmp_path), 60))


@pytest.mark.criterion("C9-8")
def test_any_state_but_none_keeps_the_application_down(tmp_path: Path) -> None:
    assert write_block.read_journal(tmp_path).state is State.NONE  # no file
    assert write_block.journal_blocker(tmp_path) is None
    for state in State:
        if state is State.NONE:
            continue
        journal = write_block.Journal(state, "day_close-1", "t", before=State.BLOCKED, resume="r")
        (tmp_path / write_block.JOURNAL_FILE).write_text(json.dumps(journal.to_json()), "utf-8")
        assert f"write block {state.value}" in str(write_block.journal_blocker(tmp_path))
        assert start_blockers(window(tmp_path), 60), state
        candidate = write_block.journal_blocker(tmp_path, candidate_ok=True)
        assert (candidate is None) is (state is State.CLOSED_COMPLETE), "only closed_complete is a candidate"


# --- C9-X1 to C9-X6: the one exception of writes_resume_check ----------------------------------------------------


@pytest.fixture
def resume_op(tmp_path: Path) -> tuple[OperationLock, str]:
    lock = OperationLock(tmp_path, "resume").acquire()
    return lock, OperationsLog(tmp_path / "operations.jsonl").start(Kind.RESUME, "recuperación")


@pytest.mark.criterion("C9-X1")
def test_without_context_an_active_resume_says_no(tmp_path: Path, resume_op: tuple[OperationLock, str]) -> None:
    lock, op = resume_op
    result = check(tmp_path)
    assert not result.allowed
    assert any(op in r for r in result.reasons)
    assert check(tmp_path, lock.context(op)).allowed  # the same record, with the real lock: the one exception
    lock.release()


@pytest.mark.criterion("C9-X2")
@pytest.mark.parametrize("forged", ["resume-id", True, 1, object()])
def test_a_hand_made_context_is_refused(tmp_path: Path, resume_op: tuple[OperationLock, str], forged: Any) -> None:
    lock, op = resume_op
    assert not check(tmp_path, forged).allowed
    with pytest.raises(LockContextError):
        LockContext(object(), lock, op)  # only OperationLock.context() makes one
    other = OperationLock(tmp_path, "resume")  # a lock object that never took the lock
    with pytest.raises(LockContextError):
        other.context(op)
    lock.release()


@pytest.mark.criterion("C9-X3")
def test_a_context_whose_lock_was_released_or_is_another_process_is_refused(
    tmp_path: Path, resume_op: tuple[OperationLock, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    lock, op = resume_op
    context = lock.context(op)
    pid = os.getpid()
    monkeypatch.setattr(os, "getpid", lambda: pid + 1)
    assert "another process" in check(tmp_path, context).reasons[0]
    monkeypatch.undo()
    lock.release()
    assert "no longer held" in check(tmp_path, context).reasons[0]
    later = OperationLock(tmp_path, "resume").acquire()  # the lock taken again, by another object: still refused
    assert not check(tmp_path, context).allowed
    later.release()


@pytest.mark.criterion("C9-X3")
def test_a_context_bound_to_another_operation_is_refused(tmp_path: Path, resume_op: tuple[OperationLock, str]) -> None:
    lock, op = resume_op
    first = lock.context(op)
    lock.context("resume-otra")  # the holder record now names another operation
    assert "not held for this operation" in check(tmp_path, first).reasons[0]
    lock.release()


@pytest.mark.criterion("C9-X4")
@pytest.mark.parametrize("other", [Kind.UPDATE, Kind.DAY_CLOSE, Kind.MANUAL, Kind.ROLLBACK, Kind.RESUME, "uncertain"])
def test_with_context_any_other_open_operation_still_says_no(
    tmp_path: Path, resume_op: tuple[OperationLock, str], other: Any
) -> None:
    lock, op = resume_op
    log = OperationsLog(tmp_path / "operations.jsonl")
    if other == "uncertain":
        ended = log.start(Kind.UPDATE, "a")
        log.finish(ended, Event.COMPLETED)
        with log.path.open("a", encoding="utf-8") as f:  # an event after its end: that operation is uncertain
            f.write(json.dumps({"at": "2026-11-17T00:00:00+00:00", "operation": ended, "event": "step",
                                "step": "x"}) + "\n")  # fmt: skip
    else:
        log.start(other, "otra operación")  # a second resume, not the owner of the lock, counts too
    assert not check(tmp_path, lock.context(op)).allowed
    lock.release()


@pytest.mark.criterion("C9-X5")
@pytest.mark.parametrize("line", ['{"roto"', '{"operation": "x", "event": "raro", "at": "2026-11-17"}', "[1]"])
def test_with_context_an_invalid_line_still_says_no(
    tmp_path: Path, resume_op: tuple[OperationLock, str], line: str
) -> None:
    lock, op = resume_op
    log = OperationsLog(tmp_path / "operations.jsonl")
    with log.path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    log.step(op, "después")  # the line is now in the middle: not a torn last line
    assert not check(tmp_path, lock.context(op)).allowed
    lock.release()


@pytest.mark.criterion("C9-X5")
def test_with_context_an_open_or_overdue_window_still_says_no(
    tmp_path: Path, resume_op: tuple[OperationLock, str]
) -> None:
    lock, op = resume_op
    owner = window(tmp_path).open(Kind.MANUAL, "ventana", lock)
    assert not check(tmp_path, lock.context(op)).allowed
    window(tmp_path).close(owner, Event.REVERTED, lock)
    assert check(tmp_path, lock.context(op)).allowed
    lock.release()


@pytest.mark.criterion("C9-X6")
@pytest.mark.parametrize("case", ["day_close", "update", "ended", "missing"])
def test_the_skipped_operation_must_be_the_active_resume(tmp_path: Path, case: str) -> None:
    log = OperationsLog(tmp_path / "operations.jsonl")
    lock = OperationLock(tmp_path, "resume").acquire()
    if case in ("day_close", "update"):
        op = log.start(Kind(case), "no es un resume")
    elif case == "ended":
        op = log.start(Kind.RESUME, "terminado")
        log.finish(op, Event.COMPLETED)
    else:
        op = "resume-que-no-existe"
    result = check(tmp_path, lock.context(op))
    assert not result.allowed
    assert any("recovery context refused" in r or "has no end" in r for r in result.reasons)
    lock.release()


def test_a_resume_never_opens_a_window_nor_is_offered_by_the_command_line(tmp_path: Path) -> None:
    with pytest.raises(MaintenanceError, match="never opens"):
        window(tmp_path).open(Kind.RESUME, "no")
    with pytest.raises(SystemExit):
        cli._parser().parse_args(["maintenance", "open", "--kind", "resume", "--reason", "x"])


# --- the evidence, the model -------------------------------------------------------------------------------------

GOOD = {
    "cut_lsn": "0/3000060",
    "checkpoint_lsn": "0/30000D8",
    "timeline": 1,
    "copies": [{"repo": "repo1", "path": "a", "sha256": "a" * 64}, {"repo": "repo2", "path": "b", "sha256": "b" * 64}],
}


@pytest.mark.criterion("C9-R")
@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"cut_lsn": "0/40000D8"}, "after the shutdown"),
        ({"checkpoint_lsn": "nada"}, "not an LSN"),
        ({"timeline": 0}, "timeline"),
        ({"timeline": True}, "timeline"),
        ({"copies": []}, "no copies"),
        ({"copies": [GOOD["copies"][0]]}, "repo1 and repo2"),  # type: ignore[index]
        ({"copies": [{"repo": "repo1", "path": "a"}]}, "without path"),
        ({"copies": [{"repo": "repo1", "path": "a", "sha256": "XYZ"}, GOOD["copies"][1]]}, "malformed"),  # type: ignore[index]
    ],
)
def test_the_evidence_form_is_strict(change: dict[str, Any], problem: str) -> None:
    assert write_block.evidence_problems(GOOD) == []
    assert any(problem in p for p in write_block.evidence_problems({**GOOD, **change}))
    assert write_block.evidence_problems(None) == ["evidence missing"]


@pytest.mark.criterion("C9-H1")
def test_the_model_sql_and_pg_hba(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom")
    user, _, name, _ = install.database
    ops_user, _, _ = install.ops_database
    sql = database.init_sql(install)
    assert f'CREATE DATABASE "{name}" OWNER "{ops_user}";' in sql
    assert f'GRANT pg_signal_backend, pg_read_all_stats TO "{ops_user}";' in sql
    assert f'GRANT CONNECT, CREATE, TEMPORARY ON DATABASE "{name}" TO "{user}";' in sql
    assert f'ALTER SCHEMA public OWNER TO "{user}";' in sql
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in sql
    assert not re.search(rf'GRANT [a-z_, ]+ TO "{user}"', sql), "the application role joins no role"
    assert 'REVOKE ALL ON DATABASE "postgres"' not in sql  # only its own database
    assert f'REVOKE ALL ON DATABASE "{name}" FROM PUBLIC;' in sql
    lines = [line.split() for line in database.hba(install).splitlines() if line and not line.startswith("#")]
    assert lines[0] == ["host", name, user, "127.0.0.1/32", "scram-sha-256"]
    assert lines[2] == ["host", "all", user, "all", "reject"]
    assert all(rule[2] != user or rule[1] == name or rule[-1] == "reject" for rule in lines)


@pytest.mark.criterion("C9-H1")
def test_the_stack_reads_only_the_control_file_of_the_data(tmp_path: Path) -> None:
    install = make_install(tmp_path / "DistriCom")
    policy = services.permission_policy(install)
    control = next(r for r in policy.rules if r.path == install.pg_data / "global" / "pg_control")
    stack = winservice.service_sid(services.STACK)
    assert control.grants == ((stack, ntfs.READ),)
    data = next(r for r in policy.rules if r.path == install.pg_data)
    assert [sid for sid, _ in data.grants] == [winservice.service_sid(services.PG)]


@pytest.mark.criterion("C9-H1")
def test_the_operations_credential_reaches_neither_the_backend_nor_a_command_line(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Enzo 2026-11-20: OPS_DATABASE_URL stays in config\\districom.env (Stack and administrators, C3). The
    backend does not get it, and the password never goes in arguments: psql gets it by its environment."""
    install = make_install(tmp_path / "DistriCom")
    ops_user, ops_password, _ = install.ops_database
    assert ops_password
    backend = services.backend_env(install)
    assert "OPS_DATABASE_URL" not in backend
    assert ops_password not in backend
    assert ops_user not in backend
    for name, spec in services.specs(install).items():
        rendered = services.render(spec, install.root / "logs").decode("utf-8")
        assert ops_password not in rendered, name
    seen: list[Any] = []

    def run(argv: list[str], **kw: Any) -> Any:
        seen.append((argv, kw["env"]))
        raise OSError("no psql here")

    monkeypatch.setattr("districom_ops.database.subprocess.run", run)
    with pytest.raises(WriteBlockError, match="unreachable"):
        write_block.Database(install).allows_connections()
    argv, env = seen[0]
    assert all(ops_password not in arg for arg in argv)
    assert env["PGPASSWORD"] == ops_password


# --- the supervisor and services start with a journal (fake SCM, real supervisor) ----------------------------------


def write_journal(fake: SCM, state: State) -> None:
    journal = write_block.Journal(state, "day_close-1", "t", step=6)
    (fake.install.layout.state / write_block.JOURNAL_FILE).write_text(json.dumps(journal.to_json()), "utf-8")


@pytest.mark.criterion("C9-8")
@pytest.mark.parametrize("state", [State.BLOCKING, State.BLOCKED, State.REVERTING])
def test_a_block_not_lifted_refuses_services_start_and_blocks_the_supervisor(scm: SCM, state: State) -> None:  # noqa: F811
    write_journal(scm, state)
    with pytest.raises(services.ServicesError, match="write block"):
        start(scm)
    winservice.start(services.STACK)  # Windows' boot: the supervisor alone
    deadline = time.monotonic() + 5
    while not phase_is(scm, "blocked"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    assert "write block" in phase(scm)["reason"]
    assert app_down(scm)


@pytest.mark.criterion("C9-X8")
def test_the_supervisor_with_a_resuming_journal_and_connections_allowed_secures_and_blocks(
    scm: SCM,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    journal = write_block.Journal(State.RESUMING, "day_close-1", "t", step=6, before=State.CLOSED_COMPLETE,
                                  resume="resume-1")  # fmt: skip
    path = scm.install.layout.state / write_block.JOURNAL_FILE
    path.write_text(json.dumps(journal.to_json()), "utf-8")
    flag: dict[str, Any] = {"allowed": True, "set_false": 0}

    def allows(self: Any) -> bool:
        return bool(flag["allowed"])

    def set_allow(self: Any, allowed: bool) -> None:
        assert not allowed, "the supervisor never enables connections"
        assert scm.states[services.PG] is Service.RUNNING
        flag["allowed"] = allowed
        flag["set_false"] += 1

    monkeypatch.setattr(write_block.Database, "allows_connections", allows)
    monkeypatch.setattr(write_block.Database, "set_allow_connections", set_allow)
    resumed: list[Any] = []
    monkeypatch.setattr(write_block, "resume", lambda *a, **k: resumed.append(k))
    with pytest.raises(services.ServicesError, match="write block"):
        start(scm)  # services start refuses a resuming journal
    winservice.start(services.STACK)  # a restart of the supervisor alone, as after Windows' boot
    deadline = time.monotonic() + 5
    while not phase_is(scm, "blocked"):
        assert time.monotonic() < deadline
        time.sleep(0.01)
    record = phase(scm)
    assert "write block resuming" in record["reason"]
    assert "interrupted resume: allow_connections_put_back_to_false" in record["reason"]
    assert flag == {"allowed": False, "set_false": 1}
    assert app_down(scm), "backend, frontend or Caddy started with an unresolved journal"
    assert resumed == [], "an automatic recovery from resuming"
    assert write_block.read_journal(scm.install.layout.state) == journal, "the supervisor changed the journal"


@pytest.mark.criterion("C9-R")
def test_a_closed_complete_journal_goes_to_the_supervisor_which_resumes_or_blocks(
    scm: SCM,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    write_journal(scm, State.CLOSED_COMPLETE)
    calls: list[dict[str, Any]] = []

    def refused(install: Any, lock: OperationLock, reason: str, **kw: Any) -> None:
        assert lock.held, "the recovery runs with the supervisor's lock"
        calls.append(kw)
        raise WriteBlockError("evidence of the close: the shutdown checkpoint is not the recorded one")

    monkeypatch.setattr(write_block, "resume", refused)
    with pytest.raises(services.ServicesError, match="blocked"):
        start(scm)  # services start lets it through; the supervisor answers blocked
    assert calls[0]["automatic"] is True
    assert "automatic recovery refused" in phase(scm)["reason"]
    assert scm.states[services.PG] is Service.RUNNING
    assert app_down(scm)
    services.stop_services(scm.install)

    def resumed(install: Any, lock: OperationLock, reason: str, **kw: Any) -> write_block.ResumeResult:
        kw["start_postgresql"]()  # PostgreSQL alone, after the evidence
        assert app_down(scm), "the application started before the writes were resumed"
        (install.layout.state / write_block.JOURNAL_FILE).write_text(
            json.dumps(write_block.Journal(State.NONE).to_json()), "utf-8"
        )
        return write_block.ResumeResult("resume-1", True, "writes resumed")

    monkeypatch.setattr(write_block, "resume", resumed)
    start(scm)
    assert phase(scm)["phase"] == "running"


def test_resume_needs_an_administrator(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "_is_admin", lambda: False)
    install = make_install(tmp_path / "DistriCom")
    args = cli._parser().parse_args(["resume", "--operation", "day_close-1", "--reason", "x"])
    assert cli._resume(args, install) == 2
    assert not (install.layout.state / "operations.jsonl").exists(), "something was recorded"
