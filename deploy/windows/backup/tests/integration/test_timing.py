"""Timings to size the real backup window (A-17). Measured and recorded; no time limit is a criterion.

The size comes from ``DISTRICOM_POC_TIMING_MB`` (1024 in CI, smaller for a quick local run).
"""

import os
import time
from typing import Any

import pytest

from districom_backup.base_backup import take_base_backup
from districom_backup.compare import compare, fingerprint
from districom_backup.restore import RestoreStatus
from tests.integration.support import DBNAME, Primary

TARGET_MB = int(os.environ.get("DISTRICOM_POC_TIMING_MB", "128"))
ROW_BYTES = 180  # md5 text, padding and tuple overhead, measured approximately


@pytest.mark.criterion("A-17")
def test_measure_backup_and_restore_times(primary: Primary, metrics: dict[str, Any]) -> None:
    rows = TARGET_MB * 1024 * 1024 // ROW_BYTES
    started = time.monotonic()
    with primary.connect() as conn:
        conn.execute(
            "CREATE TABLE poc.bulk AS SELECT g AS id, md5(g::text) || repeat('x', 100) AS payload "
            "FROM generate_series(1, %s) AS g",
            (rows,),
        )
        conn.execute("CHECKPOINT")
    load_seconds = time.monotonic() - started
    size_mb = int(str(primary.scalar(f"SELECT pg_database_size('{DBNAME}') / 1048576")))

    started = time.monotonic()
    backup = take_base_backup(primary.repo, port=primary.instance.port)
    backup_seconds = time.monotonic() - started
    primary.write(100, "after-backup")
    expected = primary.fingerprint()
    started = time.monotonic()
    primary.switch_and_wait(timeout=1800)
    archive_wait_seconds = time.monotonic() - started

    outcome = primary.restore(backup, "timing")
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    started = time.monotonic()
    result = compare(expected, fingerprint(outcome.instance, DBNAME))
    compare_seconds = time.monotonic() - started
    assert result == ("IDENTICAL", [])

    metrics["A-17"] = {
        "database size MB": size_mb,
        "load seconds": round(load_seconds, 1),
        "base backup + pg_verifybackup seconds": round(backup_seconds, 1),
        "wait for WAL archive seconds": round(archive_wait_seconds, 1),
        "restore (copy + recovery + promotion) seconds": round(outcome.seconds, 1),
        "verify.sql comparison seconds": round(compare_seconds, 1),
    }
    assert size_mb >= TARGET_MB * 0.8
