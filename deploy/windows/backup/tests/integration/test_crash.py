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
from typing import Any

import psycopg
import pytest

from districom_backup.base_backup import take_base_backup
from districom_backup.restore import RestoreStatus
from districom_backup.walnames import is_segment_name, parse_lsn, parse_segment
from tests.integration.support import DBNAME, Primary


def _archived_end_lsn(wal_dir_names: list[str], start_segno: int, segment_size: int) -> int:
    """End LSN of the contiguous run of archived segments that starts at the backup."""
    segnos = {parse_segment(n, segment_size).segno for n in wal_dir_names if is_segment_name(n)}
    segno = start_segno
    while segno in segnos:
        segno += 1
    return segno * segment_size


@pytest.mark.criterion("A-11")
def test_abrupt_stop_during_writes(primary: Primary, metrics: dict[str, Any]) -> None:
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    confirmed: dict[str, int] = {}
    in_flight: set[str] = set()
    stop_switching = threading.Event()

    def switcher() -> None:
        # Ship segments while writing, so part of the work is archived and part is not.
        while not stop_switching.is_set():
            try:
                with primary.connect("postgres") as conn:
                    conn.execute("SELECT pg_switch_wal()")
            except psycopg.Error:
                return
            time.sleep(1.5)

    killer = threading.Timer(8.0, primary.instance.kill)
    switch_thread = threading.Thread(target=switcher, daemon=True)
    conn = primary.connect()
    switch_thread.start()
    killer.start()
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
            confirmed[ref] = parse_lsn(str(row[0]))
            in_flight.discard(ref)
            n += 1
    finally:
        stop_switching.set()
        killer.join()
        conn.close()
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
    metrics["A-11"] = {
        "confirmed": len(confirmed),
        "archived (conservative)": len(archived),
        "recovered from repository": len(recovered),
        "confirmed but not archived at the crash": len(set(confirmed) - recovered),
        "in flight (no acknowledgement)": len(in_flight),
    }
    assert len(confirmed) > 0
    assert len(archived) > 0
