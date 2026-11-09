"""C1-2: structurally invalid operation records, and the joint window + record check before writes resume."""

import json
from pathlib import Path
from typing import Any

import pytest

from districom_ops.maintenance import MaintenanceWindow
from districom_ops.oplog import Event, Kind, OperationsLog
from districom_ops.safety import writes_resume_check

pytestmark = pytest.mark.criterion("C1-2")

AT = "2026-11-07T10:00:00.000+00:00"
SENSITIVE = "valor-que-no-debe-aparecer-20123456786"


@pytest.fixture
def oplog(tmp_path: Path) -> OperationsLog:
    return OperationsLog(tmp_path / "state" / "operations.jsonl")


@pytest.fixture
def window(tmp_path: Path, oplog: OperationsLog) -> MaintenanceWindow:
    return MaintenanceWindow(tmp_path / "state", oplog)


def _raw(oplog: OperationsLog, *lines: Any) -> None:
    with oplog.path.open("a", encoding="utf-8") as handle:
        for line in lines:
            handle.write((line if isinstance(line, str) else json.dumps(line)) + "\n")


def _started(op: str = "update-1") -> dict[str, Any]:
    return {"at": AT, "operation": op, "event": "started", "kind": "update", "reason": "actualización"}


# Valid JSON whose structure or history is wrong, for an operation that started correctly.
INVALID_FOR_OPERATION = {
    "missing_event": {"at": AT, "operation": "update-1"},
    "event_wrong_type": {"at": AT, "operation": "update-1", "event": 3},
    "unknown_event": {"at": AT, "operation": "update-1", "event": "finished"},
    "missing_at": {"operation": "update-1", "event": "step", "step": "x"},
    "at_not_a_date": {"at": SENSITIVE, "operation": "update-1", "event": "step", "step": "x"},
    "step_without_name": {"at": AT, "operation": "update-1", "event": "step"},
    "step_name_wrong_type": {"at": AT, "operation": "update-1", "event": "step", "step": {"x": SENSITIVE}},
    "duplicate_start": _started(),
    "start_unknown_kind": {**_started(), "kind": "deploy"},
}


@pytest.mark.parametrize("entry", INVALID_FOR_OPERATION.values(), ids=INVALID_FOR_OPERATION.keys())
def test_an_invalid_entry_makes_its_operation_uncertain_and_blocks_writes(
    oplog: OperationsLog, window: MaintenanceWindow, entry: dict[str, Any]
) -> None:
    oplog.path.parent.mkdir(parents=True)
    _raw(oplog, _started(), entry, {"at": AT, "operation": "update-1", "event": "completed"})
    before = oplog.path.read_bytes()
    record = oplog.read()  # never raises on structure
    assert not record.certain
    assert [p.line for p in record.problems] == [2]
    op = record.operations["update-1"]
    assert op.uncertain
    assert op.end is Event.COMPLETED  # an end is present, yet the operation is not trusted
    assert [o.id for o in record.uncertain()] == ["update-1"]
    assert record.incomplete() == []  # uncertain is reported apart, never as a plain incomplete
    check = writes_resume_check(window, 60)
    assert not check.allowed
    assert any("update-1 cannot be reconstructed" in r for r in check.reasons)
    assert all(SENSITIVE not in str(p) for p in record.problems)
    assert SENSITIVE not in check.describe()
    assert oplog.path.read_bytes() == before  # the evidence stays as it was


def test_events_after_the_end_or_of_an_unknown_operation_are_inconsistent(
    oplog: OperationsLog, window: MaintenanceWindow
) -> None:
    oplog.path.parent.mkdir(parents=True)
    _raw(
        oplog,
        _started("day_close-1"),
        {"at": AT, "operation": "day_close-1", "event": "completed"},
        {"at": AT, "operation": "day_close-1", "event": "reverted"},  # a second end
        {"at": AT, "operation": "manual-9", "event": "completed"},  # never started
    )
    record = oplog.read()
    assert [(p.line, p.code) for p in record.problems] == [(3, "event_after_end"), (4, "unknown_operation")]
    assert {o.id for o in record.uncertain()} == {"day_close-1", "manual-9"}
    assert record.operations["manual-9"].kind is None
    assert not writes_resume_check(window, 60).allowed


@pytest.mark.parametrize(
    "line",
    ["[1, 2]", '"texto"', json.dumps({"at": AT, "event": "completed"}), json.dumps({"record": "otro", "line": 1})],
    ids=["array", "string", "without_operation", "unknown_record"],
)
def test_an_invalid_line_without_a_known_operation_makes_the_whole_record_uncertain(
    oplog: OperationsLog, window: MaintenanceWindow, line: str
) -> None:
    op_id = oplog.start(Kind.MANUAL, "revisión")
    oplog.finish(op_id, Event.COMPLETED)
    _raw(oplog, line)
    record = oplog.read()
    assert not record.certain
    assert record.uncertain() == []  # no operation to blame: the record itself is in doubt
    assert record.operations[op_id].end is Event.COMPLETED
    check = writes_resume_check(window, 60)
    assert not check.allowed
    assert "invalid line(s): 3" in check.describe()


def test_an_unparsable_line_in_the_middle_is_corruption_not_a_torn_line(
    oplog: OperationsLog, window: MaintenanceWindow
) -> None:
    op_id = oplog.start(Kind.MANUAL, "revisión")
    _raw(oplog, '{"at": "2026-11-07", "operation": "upd')  # terminated by hand: not what a crash leaves
    oplog.finish(op_id, Event.COMPLETED)
    record = oplog.read()
    assert record.torn_lines == []
    assert [(p.line, p.code) for p in record.problems] == [(2, "unreadable_line")]
    assert not writes_resume_check(window, 60).allowed


def test_a_torn_last_line_is_sealed_by_the_next_event_and_stays_recognisable(
    oplog: OperationsLog, window: MaintenanceWindow
) -> None:
    op_id = oplog.start(Kind.UPDATE, "actualización")
    with oplog.path.open("a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-11-07", "operation": "upd')  # a crash in the middle of a write
    assert oplog.read().torn_lines == [2]
    oplog.step(op_id, "backup_taken")  # after the restart
    oplog.finish(op_id, Event.REVERTED)
    lines = oplog.path.read_text(encoding="utf-8").splitlines()
    assert lines[1] == '{"at": "2026-11-07", "operation": "upd'  # evidence kept byte for byte
    assert json.loads(lines[2]) | {"at": None} == {"at": None, "record": "torn_line_sealed", "line": 2}
    record = oplog.read()
    assert record.certain
    assert record.torn_lines == [2]
    assert [e["event"] for e in record.operations[op_id].events] == ["started", "step", "reverted"]
    assert writes_resume_check(window, 60).allowed


def test_a_whole_last_line_without_newline_is_kept_and_not_glued(oplog: OperationsLog) -> None:
    op_id = oplog.start(Kind.MANUAL, "revisión")
    data = oplog.path.read_bytes()
    oplog.path.write_bytes(data[:-1])  # the crash hit just before the newline
    oplog.finish(op_id, Event.COMPLETED)
    record = oplog.read()
    assert record.certain
    assert record.operations[op_id].end is Event.COMPLETED


def test_invalid_utf8_is_unreadable(oplog: OperationsLog, window: MaintenanceWindow) -> None:
    op_id = oplog.start(Kind.MANUAL, "revisión")
    with oplog.path.open("ab") as handle:
        handle.write(b'{"at": "\xff\xfe", "operation": "x", "event": "step"}\n')
    oplog.finish(op_id, Event.COMPLETED)
    assert [p.code for p in oplog.read().problems] == ["unreadable_line"]
    assert not writes_resume_check(window, 60).allowed


# Joint check: a missing maintenance.json is not enough.


@pytest.mark.parametrize("kind", [Kind.DAY_CLOSE, Kind.UPDATE, Kind.ROLLBACK])
def test_no_window_but_an_incomplete_critical_operation_blocks_writes(
    window: MaintenanceWindow, oplog: OperationsLog, kind: Kind
) -> None:
    op_id = window.open(kind, "operación crítica")
    oplog.step(op_id, "writes_blocked")
    # crash between removing the window and writing the end (MaintenanceWindow.close order)
    oplog.step(op_id, "maintenance_window_closed")
    window.path.unlink()
    assert not window.path.exists()
    assert window.status(60).alert is False  # the window alone looks fine
    check = writes_resume_check(window, 60)
    assert not check.allowed
    assert check.reasons == (f"operation {op_id} ({kind.value}) has no end",)


def test_no_window_ever_but_an_operation_without_end_blocks_writes(
    window: MaintenanceWindow, oplog: OperationsLog
) -> None:
    op_id = oplog.start(Kind.DAY_CLOSE, "cierre sin ventana")
    assert not window.path.exists()
    check = writes_resume_check(window, 60)
    assert not check.allowed
    assert op_id in check.describe()
    oplog.finish(op_id, Event.FAILED)
    assert writes_resume_check(window, 60).allowed  # every operation ended, even if it failed


def test_a_window_whose_owner_already_ended_or_is_missing_blocks_writes(
    window: MaintenanceWindow, oplog: OperationsLog
) -> None:
    op_id = window.open(Kind.UPDATE, "actualización")
    oplog.finish(op_id, Event.COMPLETED)  # ended without closing its window
    check = writes_resume_check(window, 60)
    assert not check.allowed
    assert any("already ended" in r for r in check.reasons)
    oplog.path.unlink()  # and with the record gone, the owner is unknown
    check = writes_resume_check(window, 60)
    assert any("not in the operations record" in r for r in check.reasons)


def test_an_unreadable_operations_record_blocks_writes(window: MaintenanceWindow, oplog: OperationsLog) -> None:
    oplog.path.mkdir(parents=True)  # exists, but cannot be read as a file
    check = writes_resume_check(window, 60)
    assert not check.allowed
    assert "operations record unreadable" in check.describe()


def test_clean_state_allows_and_the_check_changes_nothing(window: MaintenanceWindow, oplog: OperationsLog) -> None:
    assert writes_resume_check(window, 60).allowed  # fresh installation: nothing ever ran
    op_id = window.open(Kind.DAY_CLOSE, "cierre")
    window.close(op_id)
    before = oplog.path.read_bytes()
    check = writes_resume_check(window, 60)
    assert check.allowed
    assert check.reasons == ()
    assert oplog.path.read_bytes() == before
    assert not window.path.exists()


def test_resume_check_and_operations_through_the_command_line(
    tmp_path: Path, capfd: pytest.CaptureFixture[str]
) -> None:
    from districom_ops.cli import main
    from tests.test_layout_and_config import valid

    root = tmp_path / "DistriCom"
    config = tmp_path / "districom.env"
    config.write_text("\n".join(f"{k}={v}" for k, v in valid(root).items()), encoding="utf-8")
    assert main(["init", "--root", str(root)]) == 0
    cfg = ["--config", str(config)]
    assert main([*cfg, "resume-check"]) == 0
    assert main([*cfg, "maintenance", "open", "--kind", "update", "--reason", "prueba"]) == 0
    op_id = capfd.readouterr().out.strip()
    assert main([*cfg, "resume-check"]) == 1
    oplog = OperationsLog(root / "state" / "operations.jsonl")
    oplog.step(op_id, "maintenance_window_closed")
    (root / "state" / "maintenance.json").unlink()
    assert main([*cfg, "resume-check"]) == 1  # no window, operation without end
    _raw(oplog, {"at": AT, "operation": op_id, "event": "step"})
    capfd.readouterr()
    assert main([*cfg, "operations", "--incomplete"]) == 1
    out = capfd.readouterr().out
    assert json.loads(out.splitlines()[0])["uncertain"] is True
