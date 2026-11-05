"""Continuous archiving on a real PostgreSQL (A-1, A-5)."""

import time
from pathlib import Path
from typing import Any

import pytest

from districom_backup.base_backup import take_base_backup
from districom_backup.compare import fingerprint
from districom_backup.repo import replace_with_retry
from districom_backup.restore import RestoreStatus
from districom_backup.wal_gaps import find_gaps
from districom_backup.walnames import Segment, segment_name
from tests.integration.support import DBNAME, Primary, segment_of, wait_for_file


@pytest.mark.criterion("A-1")
def test_archive_timeout_ships_a_segment_without_a_manual_switch(primary: Primary, metrics: dict[str, Any]) -> None:
    """With archive_timeout = 60 s a trickle of writes reaches the repository on its own."""
    take_base_backup(primary.repo, port=primary.instance.port)
    with primary.connect() as conn:
        conn.execute("INSERT INTO poc.movements (account_id, amount, tag) VALUES (1, 10, 'trickle')")
        row = conn.execute("SELECT pg_walfile_name(pg_current_wal_lsn())").fetchone()
        assert row is not None
        segment = str(row[0])
        started = time.monotonic()
        while not (primary.repo / "wal" / segment).exists():
            assert time.monotonic() - started < 90, "archive_timeout did not ship the segment within 90 s"
            conn.execute("INSERT INTO poc.movements (account_id, amount, tag) VALUES (2, 1, 'trickle')")
            time.sleep(1)
    metrics["A-1 archive_timeout seconds"] = round(time.monotonic() - started, 1)


@pytest.mark.criterion("A-1")
def test_everything_up_to_a_known_lsn_is_archived_and_recoverable(primary: Primary) -> None:
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    primary.write(300, "before-lsn")
    lsn = str(primary.scalar("SELECT pg_current_wal_lsn()"))
    expected = primary.fingerprint()
    primary.write(50, "after-lsn")
    primary.wait_lsn_archived(lsn)

    wal = primary.repo / "wal"
    first, last = backup.start_segment().segno, segment_of(lsn)
    for segno in range(first, last + 1):
        name = segment_name(Segment(backup.timeline, segno), backup.wal_segment_size)
        assert (wal / name).is_file(), f"{name} missing"
        assert (wal / name).stat().st_size == backup.wal_segment_size, f"{name} incomplete"
    assert find_gaps(wal, backup.start_segment()).ok
    failed = primary.scalar("SELECT failed_count FROM pg_stat_archiver", "postgres")
    assert failed == 0
    # pg_wal is not required to be empty: PostgreSQL keeps recycled and current segments there.

    outcome = primary.restore(backup, "lsn", target_lsn=lsn)
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail + "\n" + "\n".join(outcome.log_tail)
    assert fingerprint(outcome.instance, DBNAME) == expected


@pytest.mark.criterion("A-5")
def test_repository_unavailable_postgresql_keeps_wal_and_catches_up(primary: Primary) -> None:
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    primary.switch_and_wait()
    offline = primary.root / "repo1-unplugged"
    replace_with_retry(primary.repo, offline)  # the backup disk disappears

    primary.write(100, "while-offline")
    pending = str(primary.scalar("SELECT pg_walfile_name(pg_switch_wal())", "postgres"))
    deadline = time.monotonic() + 120
    while primary.scalar("SELECT failed_count FROM pg_stat_archiver", "postgres") == 0:
        assert time.monotonic() < deadline, "the archiver never reported the failure"
        time.sleep(0.5)
    data = primary.instance.data_dir / "pg_wal"
    assert (data / pending).is_file(), "PostgreSQL must keep the segment it could not archive"
    assert (data / "archive_status" / f"{pending}.ready").is_file()
    assert not Path(primary.repo).exists(), "nothing may be written where the disk used to be"

    replace_with_retry(offline, primary.repo)  # the disk comes back
    wait_for_file(primary.repo / "wal" / pending, 180)
    expected = primary.fingerprint()
    primary.switch_and_wait()
    assert find_gaps(primary.repo / "wal", backup.start_segment()).ok

    outcome = primary.restore(backup, "after-outage")
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    assert fingerprint(outcome.instance, DBNAME) == expected
