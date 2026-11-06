"""Abrupt stop during writes (A-11): confirmed, archived and recoverable transactions.

The three sets are measured, not assumed:

* confirmed: the client received the COMMIT acknowledgement;
* archived: the commit lies in WAL segments that were already in the repository when the server
  died (decided conservatively with the LSN read right after each commit);
* recovered: present in a copy restored only from the backup and that repository snapshot.

Expected: every archived transaction is recovered; after a restart on the same machine every
confirmed transaction is still there (local durability); confirmed but not yet archived
transactions are reported, because a total loss of the machine would lose them (no RPO zero).
"""

import shutil
import threading
import time
from pathlib import Path
from typing import Any

import psycopg
import pytest

from districom_backup.base_backup import take_base_backup
from districom_backup.restore import RestoreStatus
from districom_backup.walnames import is_segment_name, parse_lsn, parse_segment
from tests.integration.support import DBNAME, Primary, make_primary


def _archived_end_lsn(wal_dir_names: list[str], start_segno: int, segment_size: int) -> int:
    """End LSN of the contiguous run of archived segments that starts at the backup."""
    segnos = {parse_segment(n, segment_size).segno for n in wal_dir_names if is_segment_name(n)}
    segno = start_segno
    while segno in segnos:
        segno += 1
    return segno * segment_size


# Generous: archiving one segment takes well under a second; this only bounds a broken archiver.
ARCHIVE_TIMEOUT_S = 180.0


@pytest.mark.criterion("A-11")
def test_abrupt_stop_during_writes(primary: Primary, metrics: dict[str, Any]) -> None:
    _crash_during_writes(primary, metrics, "A-11")


@pytest.mark.criterion("A-11")
def test_abrupt_stop_during_writes_with_a_slow_archiver(workdir: Path, metrics: dict[str, Any]) -> None:
    """Regression: with a fixed 8 s window, a slow runner archived nothing before the crash (2026-11-06,
    371 confirmed and 0 archived). Each archive_command here takes 3 s longer; the test must still hold."""
    with make_primary(workdir, archive_delay_s=3) as primary:
        _crash_during_writes(primary, metrics, "A-11 slow archiver")


def _crash_during_writes(primary: Primary, metrics: dict[str, Any], key: str) -> None:
    """The crash happens only once both sides exist: confirmed transactions already archived, and
    confirmed transactions not archived yet. Both are established by observing the repository, not
    by waiting a number of seconds, so a slow runner changes how long it takes, never what is tested."""
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    segment_size = backup.wal_segment_size
    start_segno = backup.start_segment().segno
    wal_dir = primary.repo / "wal"
    confirmed: dict[str, int] = {}
    in_flight: set[str] = set()
    lock = threading.Lock()
    first_confirmed = threading.Event()
    beyond_archived = threading.Event()
    threshold: list[int] = []  # LSN after the last switch; set once by the killer
    stop_switching = threading.Event()
    precondition: dict[str, Any] = {}

    def switcher() -> None:
        # Ship segments while writing, so archiving is in progress when the server dies.
        while not stop_switching.is_set():
            try:
                with primary.connect("postgres") as conn:
                    conn.execute("SELECT pg_switch_wal()")
            except psycopg.Error:
                return
            stop_switching.wait(1.5)

    def killer() -> None:
        try:
            # 1. Some confirmed transactions are in the repository (observed, not assumed).
            if not first_confirmed.wait(ARCHIVE_TIMEOUT_S):
                precondition["error"] = "no transaction was confirmed"
                return
            started = time.monotonic()
            with lock:
                target = max(confirmed.values())
            with primary.connect("postgres") as conn:
                conn.execute("SELECT pg_switch_wal()")
            while (end := _archived_end_lsn([p.name for p in wal_dir.iterdir()], start_segno, segment_size)) < target:
                if time.monotonic() - started > ARCHIVE_TIMEOUT_S:
                    report = primary.archiver_report()
                    precondition["error"] = f"no confirmed transaction archived within {ARCHIVE_TIMEOUT_S} s\n{report}"
                    return
                time.sleep(0.1)
            precondition["seconds until archived"] = round(time.monotonic() - started, 1)
            precondition["archived end before the crash"] = end
            # 2. No more switches, so the open segment cannot reach the repository before the crash
            #    (archive_timeout is 60 s from the last switch; the kill follows within milliseconds).
            stop_switching.set()
            switch_thread.join()
            with primary.connect("postgres") as conn:
                row = conn.execute("SELECT pg_current_wal_lsn()::text").fetchone()
            assert row is not None
            threshold.append(parse_lsn(str(row[0])))
            # 3. Some transaction is confirmed in that open segment.
            if not beyond_archived.wait(ARCHIVE_TIMEOUT_S):
                precondition["error"] = "no transaction was confirmed after the last switch"
                return
        except Exception as exc:  # reported by the test, never lost in the thread
            precondition["error"] = f"killer failed: {exc!r}"
        finally:
            stop_switching.set()
            primary.instance.kill()  # also on a failed precondition: the writer loop must end

    kill_thread = threading.Thread(target=killer, daemon=True)
    switch_thread = threading.Thread(target=switcher, daemon=True)
    conn = primary.connect()
    switch_thread.start()
    kill_thread.start()
    n = 0
    try:
        while True:
            ref = f"tx-{n:06d}"
            in_flight.add(ref)
            try:
                conn.execute(
                    "INSERT INTO poc.movements (account_id, amount, tag, client_ref) VALUES (1, 1, 'crash', %s)", (ref,)
                )
                row = conn.execute("SELECT pg_current_wal_lsn()::text").fetchone()
            except psycopg.Error:
                break
            assert row is not None
            lsn = parse_lsn(str(row[0]))
            with lock:
                confirmed[ref] = lsn
            in_flight.discard(ref)
            first_confirmed.set()
            if threshold and lsn > threshold[0]:
                beyond_archived.set()
            n += 1
    finally:
        stop_switching.set()
        kill_thread.join()
        conn.close()
    assert "error" not in precondition, precondition.get("error")
    assert not primary.instance.is_running()
    snapshot = primary.root / "repo1-at-crash"
    shutil.copytree(primary.repo, snapshot)  # what was really outside the server at that instant

    archived_end = _archived_end_lsn(
        [p.name for p in (snapshot / "wal").iterdir()], backup.start_segment().segno, backup.wal_segment_size
    )
    archived = {ref for ref, lsn in confirmed.items() if lsn <= archived_end}

    # Local durability: the same machine restarts and replays its own WAL.
    primary.instance.start()
    local = set(
        primary.instance.psql("SELECT string_agg(client_ref, ',') FROM poc.movements", dbname=DBNAME).split(",")
    )
    assert set(confirmed) <= local, f"{len(set(confirmed) - local)} confirmed transactions lost after restart"
    primary.instance.stop()

    outcome = primary.restore(backup, "crash", repo=snapshot)
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    text = outcome.instance.psql(
        "SELECT coalesce(string_agg(client_ref, ','), '') FROM poc.movements WHERE tag = 'crash'", dbname=DBNAME
    )
    recovered = set(filter(None, text.split(",")))

    assert archived <= recovered, f"{len(archived - recovered)} archived transactions not recovered"
    assert recovered <= set(confirmed) | in_flight, "the copy holds transactions that were never committed"
    metrics[key] = {
        "confirmed": len(confirmed),
        "archived (conservative)": len(archived),
        "recovered from repository": len(recovered),
        "confirmed but not archived at the crash": len(set(confirmed) - recovered),
        "in flight (no acknowledgement)": len(in_flight),
        **precondition,
    }
    # Guaranteed by the precondition, and checked again on what was really in the repository at the crash.
    assert len(confirmed) > 0
    assert len(archived) > 0
    assert set(confirmed) - archived, "every confirmed transaction was archived: the crash proves nothing"
