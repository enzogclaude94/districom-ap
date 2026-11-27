"""W2-C10 without PostgreSQL: the closed list of records, the shape of a clean shutdown, the fixed set of WAL with
timelines, the repositories compared by content and the preconditions. The real thing (PostgreSQL 17.6, the
Windows service, repo1 and an encrypted repo2) is ``backups_tests/test_wal_shutdown_c10.py``.

The ``pg_waldump`` lines are the format of PostgreSQL 16 and 17 (the same), as observed while developing C10.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest
from districom_backup.base_backup import BaseBackupInfo
from districom_backup.offsite import CryptRemote
from districom_backup.walnames import HistoryEntry, Segment, parse_lsn

from districom_ops import wal_shutdown as ws

pytestmark = pytest.mark.criterion("C10-U")
SIZE = 16 * 1024 * 1024


def line(lsn: str, prev: str, rmgr: str, desc: str, tx: int = 0) -> str:
    return f"rmgr: {rmgr:<11} len (rec/tot):     24/    24, tx: {tx:>10}, lsn: {lsn}, prev {prev}, desc: {desc}"


SHUTDOWN = line(
    "0/03000028",
    "0/0205B5B0",
    "XLOG",
    "CHECKPOINT_SHUTDOWN redo 0/3000028; tli 1; prev tli 1; fpw true; xid 0:731; oid 16387; multi 1; offset 0; "
    "oldest xid 722 in DB 1; oldest multi 1 in DB 1; oldest/newest commit timestamp xid: 0/0; oldest running xid 0; "
    "shutdown",
)
SWITCH = line("0/0205B5B0", "0/02000028", "XLOG", "SWITCH ")
RUNNING = line(
    "0/02000028", "0/0175B5C8", "Standby", "RUNNING_XACTS nextXid 731 latestCompletedXid 730 oldestRunningXid 731"
)
FPI = line("0/02000028", "0/0174BAC0", "XLOG", "FPI_FOR_HINT , blkref #0: rel 1663/5/1259 blk 0 FPW")
HEAP = line(
    "0/02001EA8", "0/020003A0", "Heap", "INSERT+INIT off: 1, flags: 0x00, blkref #0: rel 1663/5/16384 blk 0", 731
)
COMMIT = line("0/02001EE8", "0/02001EA8", "Transaction", "COMMIT 2026-11-27 15:13:53.656745 UTC", 731)


def records(*lines: str) -> list[ws.Record]:
    return ws.parse_records("\n".join(lines))


# --- pg_waldump --------------------------------------------------------------------------------------------------


def test_records_are_parsed_without_keeping_data_out_of_the_description() -> None:
    parsed = records(FPI, HEAP, COMMIT, "pg_waldump: error: error in WAL record at 0/2001EE8: invalid record length")
    assert [(r.rmgr, r.kind, r.tx) for r in parsed] == [
        ("XLOG", "FPI_FOR_HINT", 0), ("Heap", "INSERT+INIT", 731), ("Transaction", "COMMIT", 731),
    ]  # fmt: skip
    assert parsed[1].to_json() == {"lsn": "0/2001EA8", "rmgr": "Heap", "type": "INSERT+INIT", "tx": 731}
    assert "desc" not in parsed[1].to_json()  # what result.json and the logs carry


@pytest.mark.parametrize(
    ("code", "stderr", "expected"),
    [
        (0, "", ("complete", None)),
        (
            1,
            "pg_waldump: error: error in WAL record at 0/2001EE8: invalid record length at 0/2001F10: expected at "
            "least 24, got 0",
            ("end_of_wal", None),
        ),
        (
            1,
            'pg_waldump: error: could not find file "000000010000000000000003": No such file or directory',
            ("missing", "000000010000000000000003"),
        ),
        (1, "pg_waldump: error: could not find a valid record after 0/2000000", ("end_of_wal", None)),
        (1, "pg_waldump: error: could not open directory: Permission denied", ("error", None)),
        (2, "pg_waldump: error: invalid argument", ("error", None)),
    ],
)
def test_the_end_of_a_dump_is_told_apart(code: int, stderr: str, expected: tuple[str, str | None]) -> None:
    assert ws.classify_end(code, stderr) == expected


# --- the closed list and a clean shutdown ------------------------------------------------------------------------


def test_a_clean_shutdown_passes_and_its_own_wal_is_told_apart() -> None:
    assert ws.judge_after_cut(records(SHUTDOWN), 1)[0] == []
    problems, seen = ws.judge_after_cut(records(RUNNING, SWITCH, SHUTDOWN), 1)
    assert problems == []
    assert [r["type"] for r in seen["shutdown"]] == ["SWITCH", "CHECKPOINT_SHUTDOWN"]  # the shutdown's own switch
    assert [r["type"] for r in seen["control"]] == ["RUNNING_XACTS"]
    assert seen["incompatible"] == []


def test_the_allowed_list_is_closed() -> None:
    assert frozenset({("XLOG", "SWITCH"), ("XLOG", "CHECKPOINT_SHUTDOWN"), ("Standby", "RUNNING_XACTS")}) == ws.ALLOWED
    assert ("XLOG", "FPI_FOR_HINT") not in ws.ALLOWED  # D3: a read after the cut is a FAIL
    assert ("XLOG", "RESTORE_POINT") not in ws.ALLOWED  # only before the cut


@pytest.mark.parametrize(
    ("wal", "named"),
    [
        ((FPI, SWITCH, SHUTDOWN), "XLOG/FPI_FOR_HINT at 0/2000028 (tx 0)"),
        ((HEAP, COMMIT, SWITCH, SHUTDOWN), "Heap/INSERT+INIT at 0/2001EA8 (tx 731)"),
        ((COMMIT, SWITCH, SHUTDOWN), "Transaction/COMMIT at 0/2001EE8 (tx 731)"),
        (
            (line("0/02000028", "0/0", "XLOG", "CHECKPOINT_ONLINE redo 0/2000028; tli 1;"), SHUTDOWN),
            "CHECKPOINT_ONLINE",
        ),
    ],
)
def test_any_other_record_after_the_cut_fails_and_is_named(wal: tuple[str, ...], named: str) -> None:
    problems, seen = ws.judge_after_cut(records(*wal), 1)
    assert any(named in p for p in problems), problems
    assert seen["incompatible"]


def test_without_a_shutdown_checkpoint_at_the_end_it_fails() -> None:
    assert ws.judge_after_cut([], 1)[0] == ["no WAL record after the cut: the shutdown checkpoint is missing"]
    problems, _ = ws.judge_after_cut(records(RUNNING), 1)  # an immediate shutdown: nothing after the snapshot
    assert any("last record is not the shutdown checkpoint" in p for p in problems)
    problems, _ = ws.judge_after_cut(records(SHUTDOWN, RUNNING), 1)
    assert any("last record is not the shutdown checkpoint" in p for p in problems)
    second = SHUTDOWN.replace("0/03000028", "0/03000100").replace("redo 0/3000028", "redo 0/3000100")
    problems, _ = ws.judge_after_cut(records(SHUTDOWN, second), 1)
    assert any("2 shutdown checkpoints" in p for p in problems)


def test_the_checkpoint_must_be_its_own_redo_and_on_the_timeline_of_the_cut() -> None:
    assert any("timeline 1, not 2" in p for p in ws.judge_after_cut(records(SHUTDOWN), 2)[0])
    moved = SHUTDOWN.replace("redo 0/3000028", "redo 0/2FFFFF0")
    assert any("redo is not the checkpoint" in p for p in ws.judge_after_cut(records(moved), 1)[0])


def test_the_archived_cut_segment_must_end_with_this_runs_restore_point_and_switch() -> None:
    point = records(line("0/0175B548", "0/0175B500", "XLOG", "RESTORE_POINT districom-c10-x"))
    assert ws.judge_tail(records(SWITCH), point, "districom-c10-x") == []
    assert ws.judge_tail(records(RUNNING, SWITCH), point, "districom-c10-x") == []
    assert ws.judge_tail(records(SWITCH), point, "districom-c10-other")  # another restore point
    assert ws.judge_tail(records(HEAP, SWITCH), point, "districom-c10-x")  # something in between
    assert ws.judge_tail(records(RUNNING), point, "districom-c10-x")  # no switch at the end
    assert ws.judge_tail(records(SWITCH), [], "districom-c10-x")


def test_controldata_must_show_the_clean_shutdown_at_the_last_record() -> None:
    checkpoint = records(SHUTDOWN)[0]
    good = {"Database cluster state": "shut down", "Latest checkpoint location": "0/3000028",
            "Latest checkpoint's REDO location": "0/3000028", "Latest checkpoint's TimeLineID": "1"}  # fmt: skip
    assert ws.judge_controldata(good, checkpoint, 1) == []
    assert ws.judge_controldata({}, checkpoint, 1) == ["pg_controldata could not be read"]
    assert ws.judge_controldata({**good, "Database cluster state": "in production"}, checkpoint, 1)
    assert ws.judge_controldata({**good, "Latest checkpoint location": "0/2000028"}, checkpoint, 1)
    assert ws.judge_controldata({**good, "Latest checkpoint's REDO location": "0/2000000"}, checkpoint, 1)
    assert ws.judge_controldata({**good, "Latest checkpoint's TimeLineID": "2"}, checkpoint, 1)
    assert ws.judge_controldata(good, None, 1)


# --- the fixed set -----------------------------------------------------------------------------------------------


def backup(label: str, start: str) -> BaseBackupInfo:
    return BaseBackupInfo(label, "0/0", start, int(start[:8], 16), SIZE, "2026-11-27T00:00:00+00:00")


def test_the_set_goes_from_the_newest_base_backup_to_the_cut_and_continuity_from_the_oldest() -> None:
    backups = [backup("base-a", "000000010000000000000002"), backup("base-b", "000000010000000000000005")]
    walset, problems = ws.fix_set(backups, Segment(1, 7), [], SIZE)
    assert problems == []
    assert walset is not None
    assert walset.needed == [f"0000000100000000000000{n:02X}" for n in (5, 6, 7)]
    assert walset.older == [f"0000000100000000000000{n:02X}" for n in (2, 3, 4)]
    assert walset.histories == []
    assert walset.base == "base-b"


def test_with_a_timeline_change_the_switch_segment_is_the_childs_and_the_history_files_are_in_the_set() -> None:
    history = [HistoryEntry(1, parse_lsn("0/4800000"))]  # timeline 2 starts inside segment 4
    backups = [backup("base-a", "000000010000000000000002"), backup("base-b", "000000020000000000000006")]
    walset, problems = ws.fix_set(backups, Segment(2, 7), history, SIZE)
    assert problems == []
    assert walset is not None
    assert walset.needed == ["000000020000000000000006", "000000020000000000000007"]
    assert walset.older == ["000000010000000000000002", "000000010000000000000003", "000000020000000000000004",
                            "000000020000000000000005"]  # fmt: skip
    assert walset.histories == ["00000002.history"]
    # A base backup on timeline 1 still recovers the cut on timeline 2 by following the history.
    walset, problems = ws.fix_set(backups[:1], Segment(2, 5), history, SIZE)
    assert problems == []
    assert walset is not None
    assert walset.needed == ["000000010000000000000002", "000000010000000000000003", "000000020000000000000004",
                             "000000020000000000000005"]  # fmt: skip


def test_a_base_backup_off_the_history_or_after_the_cut_is_not_used() -> None:
    other_branch = [backup("base-x", "000000030000000000000002")]
    walset, problems = ws.fix_set(other_branch, Segment(2, 7), [HistoryEntry(1, parse_lsn("0/4800000"))], SIZE)
    assert walset is None
    assert problems == ["repo1 has no base backup from which the cut can be recovered"]
    walset, problems = ws.fix_set([backup("base-late", "000000010000000000000009")], Segment(1, 7), [], SIZE)
    assert walset is None


# --- the repositories ----------------------------------------------------------------------------------------------


def wal_folder(root: Path, files: dict[str, bytes]) -> Path:
    (root / "wal").mkdir(parents=True, exist_ok=True)
    (root / ".districom-backup-repo").write_bytes(b"x")
    for name, data in files.items():
        (root / "wal" / name).write_bytes(data)
    return root


def target(tmp_path: Path, remote: Any = None) -> ws.Target:
    return ws.Target(pg_bin=tmp_path / "bin", data=tmp_path / "data", port=5432, repo1=tmp_path / "repo1",
                     diagnostics=tmp_path / "diag", stop=lambda: None, remote=cast(CryptRemote, remote))  # fmt: skip


def cut(segno: int = 7) -> ws.Cut:
    return ws.Cut("districom-c10-x", 0, (segno * SIZE) + 100, Segment(1, segno), SIZE)


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_repo1_names_a_missing_truncated_or_different_file_and_a_gap(tmp_path: Path) -> None:
    good, other = b"a" * SIZE, b"b" * SIZE
    walset = ws.WalSet("base-b", ["000000010000000000000006", "000000010000000000000007"], ["00000002.history"],
                       ["000000010000000000000004", "000000010000000000000005"], "base-a")  # fmt: skip
    wal_folder(tmp_path / "repo1", {"000000010000000000000006": good, "000000010000000000000007": other,
                                    "000000010000000000000005": good[:100]})  # fmt: skip
    problems, hashes = ws.check_repo1(target(tmp_path), cut(), walset, {"000000010000000000000007": sha(good)})
    assert problems == [
        "00000002.history missing from repo1",
        "000000010000000000000007 in repo1 differs from the server's",
        "000000010000000000000004 missing from repo1 (gap since the oldest base backup)",
        "000000010000000000000005 truncated in repo1 (100 bytes)",
    ]
    assert hashes == {"000000010000000000000006": sha(good), "000000010000000000000007": sha(other)}
    # No longer on the server: only its size tells that the copy is not whole.
    (tmp_path / "repo1" / "wal" / "000000010000000000000006").write_bytes(good[:SIZE - 1])
    problems, _ = ws.check_repo1(target(tmp_path), cut(), walset, {})
    assert f"000000010000000000000006 truncated in repo1 ({SIZE - 1} bytes)" in problems


class FolderRemote:
    """Stands for the encrypted remote: ``copy --files-from-raw`` and ``lsjson`` over a plain folder."""

    def __init__(self, folder: Path, fail: bool = False) -> None:
        self.folder, self.fail, self.calls = folder, fail, []  # type: tuple[Path, bool, list[tuple[str, ...]]]

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        if self.fail:
            return subprocess.CompletedProcess(args, 1, "", "Failed to create file system: connection refused")
        if args[0] == "lsjson":
            items = [{"Name": p.name, "Size": p.stat().st_size} for p in (self.folder / "wal").iterdir()]
            return subprocess.CompletedProcess(args, 0, json.dumps(items), "")
        listing, destination = Path(args[2]), Path(args[-1])
        for name in listing.read_text("utf-8").split():
            if (self.folder / "wal" / name).is_file():
                shutil.copyfile(self.folder / "wal" / name, destination / name)
        return subprocess.CompletedProcess(args, 0, "", "")


def test_repo2_is_downloaded_and_compared_by_content_and_keeps_only_what_the_evidence_or_a_diagnosis_needs(
    tmp_path: Path,
) -> None:
    good, bad = b"a" * SIZE, b"c" * SIZE
    walset = ws.WalSet("base-b", ["000000010000000000000006", "000000010000000000000007"], [],
                       ["000000010000000000000005"], "base-a")  # fmt: skip
    wal_folder(tmp_path / "remote", {"000000010000000000000006": good, "000000010000000000000007": bad,
                                     "000000010000000000000005": good})  # fmt: skip
    reference = {"000000010000000000000006": sha(good), "000000010000000000000007": sha(good)}
    remote = FolderRemote(tmp_path / "remote")
    problems, summary, copies = ws.check_repo2(target(tmp_path, remote), cut(), walset, reference,
                                               {"000000010000000000000006"})  # fmt: skip
    assert problems == ["000000010000000000000007 in repo2 differs from the reference"]
    assert summary == {"downloaded_and_compared": 2, "set": 2, "continuity_checked": 1}
    assert copies == [{"repo": "repo2", "path": str(tmp_path / "diag" / "repo2" / "000000010000000000000006"),
                       "sha256": sha(good)}]  # fmt: skip
    assert sorted(p.name for p in (tmp_path / "diag" / "repo2").iterdir()) == [
        "000000010000000000000006", "000000010000000000000007",  # the evidence's copy and the mismatch, kept
    ]  # fmt: skip
    assert remote.calls[0][:4] == ("copy", "--files-from-raw", remote.calls[0][2], "--no-traverse")


def test_repo2_missing_or_unreachable_fails_with_its_reason(tmp_path: Path) -> None:
    walset = ws.WalSet("base-b", ["000000010000000000000007"], [], ["000000010000000000000005"], "base-a")
    wal_folder(tmp_path / "remote", {})
    problems, _, _ = ws.check_repo2(target(tmp_path, FolderRemote(tmp_path / "remote")), cut(), walset, {}, set())
    assert problems == ["000000010000000000000007 missing from repo2",
                        "000000010000000000000005 missing from repo2 (gap since the oldest base backup)"]  # fmt: skip
    problems, _, _ = ws.check_repo2(target(tmp_path, FolderRemote(tmp_path / "remote", fail=True)), cut(), walset,
                                    {}, set())  # fmt: skip
    assert problems[0].startswith("the download from repo2 failed: Failed to create file system")
    assert "repo2's WAL listing could not be read (continuity)" in problems


def test_the_wal_after_the_cut_is_staged_from_the_server_or_repo1_and_both_copies_must_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "data" / "pg_wal").mkdir(parents=True)
    wal_folder(tmp_path / "repo1", {"000000010000000000000008": b"archived at shutdown"})
    (tmp_path / "data" / "pg_wal" / "000000010000000000000009").write_bytes(b"local")
    (tmp_path / "repo1" / "wal" / "000000010000000000000009").write_bytes(b"different")
    asked: list[list[str]] = []

    def dump(t: ws.Target, folder: Path, timeline: int, start: int, end: int | None = None) -> ws.Dump:
        staged = sorted(p.name for p in folder.iterdir())
        asked.append(staged)
        if len(staged) == 1:
            return ws.Dump(records(SWITCH), "missing", "000000010000000000000009", "")
        return ws.Dump(records(SWITCH, SHUTDOWN), "end_of_wal", None, "")

    monkeypatch.setattr(ws, "_waldump", dump)
    result, problems = ws.read_after_cut(target(tmp_path), cut(), tmp_path / "staging")
    assert asked == [["000000010000000000000008"], ["000000010000000000000008", "000000010000000000000009"]]
    assert (tmp_path / "staging" / "000000010000000000000009").read_bytes() == b"local"  # the server's copy
    assert problems == ["000000010000000000000009 after the cut differs between the server and repo1"]
    assert result.end == "end_of_wal"
    shutil.rmtree(tmp_path / "staging")
    (tmp_path / "repo1" / "wal" / "000000010000000000000008").unlink()
    _, problems = ws.read_after_cut(target(tmp_path), cut(), tmp_path / "staging")
    assert problems == ["000000010000000000000008 (WAL after the cut) is neither on the server nor in repo1"]


# --- the result ----------------------------------------------------------------------------------------------------


def test_pass_needs_every_check_and_a_missing_one_is_not_reached(tmp_path: Path) -> None:
    result = ws.Result("op", "now")
    for name in ("cut", "repo1", "shutdown", "wal_after_cut", "controldata"):
        ws._ok(result, name, [])
    assert not result.passed
    ws._ok(result, "repo2", [])
    assert result.passed
    ws._ok(result, "repo2", ["x"])
    assert result.verdict == "FAIL"
    many = ws.Result("op", "now")
    ws._ok(many, "wal_after_cut", [f"p{n}" for n in range(ws.MAX_PROBLEMS + 5)])
    assert many.checks["wal_after_cut"]["problems"][-1] == "... and 5 more"
    assert many.checks["wal_after_cut"]["ok"] is False


def test_a_cut_that_fails_reaches_no_other_step_and_still_writes_its_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(t: ws.Target, owner: str) -> ws.Cut:
        raise ws.WalShutdownError("the cluster did not answer the cut as a primary")

    stopped: list[bool] = []
    monkeypatch.setattr(ws, "take_cut", refuse)
    t = ws.Target(pg_bin=tmp_path, data=tmp_path, port=1, repo1=tmp_path, diagnostics=tmp_path / "diag",
                  stop=lambda: stopped.append(True))  # fmt: skip
    result = ws.verify(t, "op")
    assert result.verdict == "FAIL"
    assert result.checks["run"]["problems"] == ["the cluster did not answer the cut as a primary"]
    assert all(result.checks[n]["problems"] == ["not reached"] for n in ("cut", "repo1", "shutdown", "repo2"))
    assert stopped == []  # nothing is stopped when there is no cut
    assert json.loads((tmp_path / "diag" / "result.json").read_text("utf-8"))["verdict"] == "FAIL"


def test_the_operation_id_reaches_the_sql_only_as_a_plain_identifier(tmp_path: Path) -> None:
    t = ws.Target(pg_bin=tmp_path, data=tmp_path, port=1, repo1=tmp_path, diagnostics=tmp_path, stop=lambda: None)
    with pytest.raises(ws.WalShutdownError, match="plain identifier"):
        ws.take_cut(t, "x'); DROP TABLE t; --")


# --- preconditions (C10-10) ----------------------------------------------------------------------------------------


@pytest.fixture
def blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An installation with the window of ``owner`` open and its block verified, held by ``lock``."""
    from districom_ops import write_block
    from districom_ops.oplock import OperationLock
    from districom_ops.oplog import Kind
    from tests.test_services_units import make_install

    install = make_install(tmp_path / "root")
    install.layout.ensure()
    state = install.layout.state
    lock = OperationLock(state, "c10-test").acquire(timeout=5)
    owner = write_block._window(install).open(Kind.MANUAL, "prueba C10", lock)
    journal = write_block.Journal(write_block.State.BLOCKED, owner, lock.token, step=6)
    (state / write_block.JOURNAL_FILE).write_text(json.dumps(journal.to_json()), "utf-8")
    flag = {"allowed": False}
    monkeypatch.setattr(write_block.Database, "allows_connections", lambda self: flag["allowed"])
    yield install, lock, owner, flag
    lock.release()


def test_c10_starts_only_for_the_owner_of_the_window_and_of_the_block_with_no_task(blocked: Any) -> None:
    from districom_ops import tasklock

    install, lock, owner, _ = blocked
    heavy = ws.preconditions(install, lock, owner)
    try:
        assert heavy.held  # no heavy task starts while C10 works
        assert tasklock.heavy_task_running(install.layout.state)
    finally:
        heavy.release()
    with pytest.raises(ws.WalShutdownError, match="own operation"):
        ws.preconditions(install, lock, "manual-other")


def test_c10_does_not_start_without_its_block_or_with_the_database_open(blocked: Any) -> None:
    from districom_ops import write_block

    install, lock, owner, flag = blocked
    path = install.layout.state / write_block.JOURNAL_FILE
    for journal in (write_block.Journal(write_block.State.NONE),
                    write_block.Journal(write_block.State.BLOCKED, owner, "another-lock", step=6),
                    write_block.Journal(write_block.State.BLOCKING, owner, lock.token, step=4)):  # fmt: skip
        path.write_text(json.dumps(journal.to_json()), "utf-8")
        with pytest.raises(ws.WalShutdownError, match="write block of its own operation"):
            ws.preconditions(install, lock, owner)
    path.write_text(json.dumps(write_block.Journal(write_block.State.BLOCKED, owner, lock.token, step=6).to_json()))
    flag["allowed"] = True
    with pytest.raises(ws.WalShutdownError, match="still accepts connections"):
        ws.preconditions(install, lock, owner)


def test_c10_does_not_start_with_a_live_request_or_a_heavy_task(blocked: Any) -> None:
    from districom_ops import tasklock

    install, lock, owner, _ = blocked
    state = install.layout.state
    request = tasklock._write_request(state, lock, "services_stop", "services stop")  # another operation's, live
    try:
        with pytest.raises(ws.WalShutdownError, match="services_stop asked the backup tasks to stop"):
            ws.preconditions(install, lock, owner)
    finally:
        request.clear(lock)
    task = tasklock.backup_lock(state)
    assert task.try_acquire()
    try:
        with pytest.raises(ws.WalShutdownError, match="heavy backup task is running"):
            ws.preconditions(install, lock, owner)
    finally:
        task.release()
    request = tasklock._write_request(state, lock, "dead_requester", "x")
    request.abandon()  # its requester died: an orphan, reconciled, not a refusal
    ws.preconditions(install, lock, owner).release()
    assert tasklock.cancel_requests(state) == []


def test_c10_needs_the_operations_lock(blocked: Any) -> None:
    install, lock, owner, _ = blocked
    lock.release()
    with pytest.raises(ws.WalShutdownError, match="operations lock"):
        ws.preconditions(install, lock, owner)
