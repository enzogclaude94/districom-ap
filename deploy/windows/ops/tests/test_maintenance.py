"""C1-2: maintenance window owned by an operation, and the operations record."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from districom_ops.maintenance import MaintenanceError, MaintenanceWindow, WindowState
from districom_ops.oplog import Event, Kind, OperationError, OperationsLog
from districom_ops.safety import writes_resume_check


@pytest.fixture
def oplog(tmp_path: Path) -> OperationsLog:
    return OperationsLog(tmp_path / "state" / "operations.jsonl")


@pytest.fixture
def window(tmp_path: Path, oplog: OperationsLog) -> MaintenanceWindow:
    return MaintenanceWindow(tmp_path / "state", oplog)


@pytest.mark.criterion("C1-2")
def test_a_window_belongs_to_one_operation_and_only_it_closes_it(
    window: MaintenanceWindow, oplog: OperationsLog
) -> None:
    assert window.status(60).state is WindowState.CLOSED
    op_id = window.open(Kind.DAY_CLOSE, "cierre de jornada")
    status = window.status(60)
    assert status.state is WindowState.OPEN
    assert status.window is not None
    assert (status.window.operation_id, status.window.kind) == (op_id, Kind.DAY_CLOSE)
    assert not writes_resume_check(window, 60).allowed
    with pytest.raises(MaintenanceError, match=op_id):
        window.open(Kind.UPDATE, "otra")
    with pytest.raises(MaintenanceError, match="belongs to"):
        window.close("update-20260101T000000Z-abcdef")
    assert window.status(60).state is WindowState.OPEN
    window.close(op_id)
    assert writes_resume_check(window, 60).allowed
    op = oplog.read().operations[op_id]
    assert [e["event"] for e in op.events] == ["started", "step", "step", "completed"]
    assert [e.get("step") for e in op.events if e["event"] == "step"] == [
        "maintenance_window_opened",
        "maintenance_window_closed",
    ]


@pytest.mark.criterion("C1-2")
def test_expiry_alerts_but_never_closes_the_window_or_allows_writes(window: MaintenanceWindow) -> None:
    op_id = window.open(Kind.UPDATE, "actualización v1.2.0")
    before = window.path.read_bytes()
    later = datetime.now(UTC) + timedelta(hours=5)
    status = window.status(60, now=later)
    assert status.state is WindowState.OVERDUE
    assert status.alert
    assert not writes_resume_check(window, 60, now=later).allowed
    assert status.describe().startswith("FAIL")
    assert op_id in status.describe()
    assert window.path.read_bytes() == before  # reading the status never changes the window
    assert window.status(60, now=later + timedelta(days=30)).state is WindowState.OVERDUE


@pytest.mark.criterion("C1-2")
def test_an_unreadable_window_is_open_with_unknown_owner(window: MaintenanceWindow) -> None:
    window.path.parent.mkdir(parents=True)
    window.path.write_text("{ torn", encoding="utf-8")
    status = window.status(60)
    assert status.state is WindowState.UNREADABLE
    assert status.alert
    assert not writes_resume_check(window, 60).allowed
    with pytest.raises(MaintenanceError):
        window.open(Kind.MANUAL, "intento")
    with pytest.raises(MaintenanceError, match="unreadable"):
        window.close("anything")


@pytest.mark.criterion("C1-2")
def test_a_reverted_operation_ends_as_reverted(window: MaintenanceWindow, oplog: OperationsLog) -> None:
    op_id = window.open(Kind.DAY_CLOSE, "cierre")
    oplog.step(op_id, "writes_blocked")
    window.close(op_id, Event.REVERTED, cause="timeout waiting for transactions")
    op = oplog.read().operations[op_id]
    assert op.end is Event.REVERTED
    assert not op.incomplete
    with pytest.raises(OperationError, match="already ended"):
        oplog.step(op_id, "late")


@pytest.mark.criterion("C1-2")
def test_an_operation_without_end_is_incomplete_and_survives_a_torn_line(oplog: OperationsLog) -> None:
    done = oplog.start(Kind.MANUAL, "revisión")
    oplog.finish(done, Event.COMPLETED)
    pending = oplog.start(Kind.UPDATE, "actualización")
    oplog.step(pending, "backup_taken", label="pre-update-1")
    with oplog.path.open("a", encoding="utf-8") as handle:
        handle.write('{"at": "2026-11-06", "operation": "upd')  # a crash in the middle of a write
    record = oplog.read()
    assert [op.id for op in record.incomplete()] == [pending]
    assert record.torn_lines == [5]
    lines = oplog.path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 5  # nothing rewritten or lost
    assert json.loads(lines[0])["operation"] == done


@pytest.mark.criterion("C1-2")
def test_operations_need_a_reason_and_a_known_id(oplog: OperationsLog) -> None:
    with pytest.raises(OperationError):
        oplog.start(Kind.MANUAL, "  ")
    with pytest.raises(OperationError, match="unknown"):
        oplog.step("manual-x", "step")
    op_id = oplog.start(Kind.MANUAL, "ok")
    with pytest.raises(OperationError):
        oplog.finish(op_id, Event.STEP)


@pytest.mark.criterion("C1-2")
def test_maintenance_through_the_command_line(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> None:
    from districom_ops.cli import main
    from tests.test_layout_and_config import valid

    root = tmp_path / "DistriCom"
    config = tmp_path / "districom.env"
    config.write_text("\n".join(f"{k}={v}" for k, v in valid(root).items()), encoding="utf-8")
    assert main(["init", "--root", str(root)]) == 0
    base = ["--config", str(config), "maintenance"]
    assert main([*base, "status"]) == 0
    capfd.readouterr()
    assert main([*base, "open", "--kind", "manual", "--reason", "prueba"]) == 0
    op_id = capfd.readouterr().out.strip()
    assert op_id.startswith("manual-")
    assert main([*base, "open", "--kind", "update", "--reason", "otra"]) == 2
    assert main([*base, "close", "--operation", "update-x"]) == 2
    assert main([*base, "status"]) == 0
    assert main(["--config", str(config), "operations", "--incomplete"]) == 0
    assert op_id in capfd.readouterr().out
    assert main([*base, "close", "--operation", op_id]) == 0
    assert main(["--config", str(config), "operations", "--incomplete"]) == 0
    assert capfd.readouterr().out == ""
