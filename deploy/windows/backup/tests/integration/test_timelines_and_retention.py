"""Gaps and retention on a repository with two timelines (A-12, A-15).

Scenario, built once for the module:

1. Primary on timeline 1: base backup B1, writes, base backup B2, writes, restore point ``rp2``,
   more writes (they will be lost: the disaster happens after them).
2. Disaster recovery: B2 restored to ``rp2`` and promoted as the new primary, archiving into the
   same repository. It starts timeline 2 (``00000002.history``).
3. New primary: writes, restore point ``rp3``, base backup B3, writes, end.
"""

import shutil
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from districom_backup.base_backup import BaseBackupInfo, list_backups, take_base_backup
from districom_backup.compare import compare, fingerprint
from districom_backup.restore import RestoreStatus
from districom_backup.retention import apply_retention
from districom_backup.wal_gaps import find_gaps
from districom_backup.walnames import Segment, is_segment_name, parse_segment, segment_name
from tests.integration.support import DBNAME, Primary, copy_repo, make_primary, unique_dir, wait_for_file


@dataclass
class Scenario:
    root: Path
    repo: Path
    primary: Primary
    b1: BaseBackupInfo
    b2: BaseBackupInfo
    b3: BaseBackupInfo
    fp_rp2: str
    fp_rp3: str
    fp_end: str


@pytest.fixture(scope="module")
def scenario(work_root: Path) -> Iterator[Scenario]:
    root = unique_dir(work_root, "timelines")
    with make_primary(root) as primary:
        port = primary.instance.port
        b1 = take_base_backup(primary.repo, port=port)
        primary.write(200, "t1-a")
        primary.switch_and_wait()
        b2 = take_base_backup(primary.repo, port=port)
        primary.write(200, "t1-b")
        primary.switch_and_wait()  # a full timeline-1 segment that only timeline 1 has
        primary.write(200, "t1-c")
        fp_rp2 = primary.fingerprint()
        primary.scalar("SELECT pg_create_restore_point('rp2')", "postgres")
        primary.write(200, "t1-lost")
        primary.switch_and_wait()
        primary.instance.stop()

        outcome = primary.restore(b2, "new-primary", target_name="rp2", archive_into=primary.repo)
        assert outcome.status is RestoreStatus.COMPLETE, outcome.detail + "\n" + "\n".join(outcome.log_tail)
        new = Primary(outcome.instance, primary.repo, root)
        wait_for_file(primary.repo / "wal" / "00000002.history", 120)
        new.write(200, "t2-a")
        fp_rp3 = new.fingerprint()
        new.scalar("SELECT pg_create_restore_point('rp3')", "postgres")
        new.switch_and_wait()
        b3 = take_base_backup(primary.repo, port=new.instance.port)
        assert b3.timeline == 2
        new.write(200, "t2-b")
        fp_end = new.fingerprint()
        new.switch_and_wait()
        new.instance.stop()
        yield Scenario(root, primary.repo, primary, b1, b2, b3, fp_rp2, fp_rp3, fp_end)
    shutil.rmtree(root, ignore_errors=True)


def _fresh_copy(scenario: Scenario, name: str) -> Path:
    return copy_repo(scenario.repo, scenario.root / name)


@pytest.mark.criterion("A-12")
def test_timeline_switch_is_followed_and_not_reported_as_gap(scenario: Scenario) -> None:
    wal = scenario.repo / "wal"
    report = find_gaps(wal, scenario.b1.start_segment())
    assert report.ok, report.summary()
    assert report.latest_timeline == 2
    assert {parse_segment(n.name).timeline for n in wal.iterdir() if is_segment_name(n.name)} == {1, 2}


@pytest.mark.criterion("A-12")
def test_restore_across_the_timeline_switch_is_identical(scenario: Scenario) -> None:
    outcome = scenario.primary.restore(
        scenario.b2, "across", repo=_fresh_copy(scenario, "repo-across"), target_name="rp3"
    )
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    assert compare(scenario.fp_rp3, fingerprint(outcome.instance, DBNAME)) == ("IDENTICAL", [])
    lost = outcome.instance.psql("SELECT count(*) FROM poc.movements WHERE tag = 't1-lost'", dbname=DBNAME)
    assert lost == "0"


@pytest.mark.criterion("A-12")
def test_missing_history_file_is_detected(scenario: Scenario) -> None:
    repo = _fresh_copy(scenario, "repo-no-history")
    (repo / "wal" / "00000002.history").unlink()
    report = find_gaps(repo / "wal", scenario.b1.start_segment())
    assert not report.ok
    assert report.missing_history == [2]


@pytest.mark.criterion("A-12")
def test_gap_on_timeline_2_is_detected_and_never_identical(scenario: Scenario) -> None:
    repo = _fresh_copy(scenario, "repo-gap")
    wal = repo / "wal"
    tl2 = sorted(n.name for n in wal.iterdir() if is_segment_name(n.name) and parse_segment(n.name).timeline == 2)
    assert len(tl2) >= 3
    victim = tl2[len(tl2) // 2]
    (wal / victim).unlink()

    report = find_gaps(wal, scenario.b2.start_segment())
    assert victim in report.missing_segments

    to_end = scenario.primary.restore(scenario.b2, "gap-end", repo=repo)
    # PostgreSQL itself stops quietly at the hole and promotes; the gap check is what refuses COMPLETE.
    assert to_end.status is RestoreStatus.INCOMPLETE, to_end.detail
    assert victim in to_end.detail
    assert compare(scenario.fp_end, fingerprint(to_end.instance, DBNAME))[0] == "DIFFERENT"
    to_end.instance.stop("immediate")


@pytest.mark.criterion("A-12")
def test_gap_before_a_restore_point_makes_the_target_unreachable(scenario: Scenario) -> None:
    repo = _fresh_copy(scenario, "repo-gap-tl1")
    wal = repo / "wal"
    first = scenario.b2.start_segment().segno
    # The segment after B2's start on timeline 1 holds writes before rp2 and exists on no other
    # timeline (the switch happened in a later segment).
    victim = segment_name(Segment(1, first + 1), scenario.b2.wal_segment_size)
    assert (wal / victim).exists()
    (wal / victim).unlink()
    assert victim in find_gaps(wal, scenario.b2.start_segment()).missing_segments
    outcome = scenario.primary.restore(scenario.b2, "gap-before-target", repo=repo, target_name="rp3")
    assert outcome.status is RestoreStatus.FAILED
    assert "not reached" in outcome.detail or "did not finish" in outcome.detail


@pytest.mark.criterion("A-15")
def test_retention_keeps_what_the_kept_backups_need(scenario: Scenario) -> None:
    repo = _fresh_copy(scenario, "repo-retention-2")
    wal = repo / "wal"
    histories_before = sorted(p.name for p in wal.glob("*.history"))
    report = apply_retention(repo, keep=2)
    assert report.removed_backups == [scenario.b1.label]
    assert [b.label for b in list_backups(repo)] == [scenario.b2.label, scenario.b3.label]
    assert sorted(p.name for p in wal.glob("*.history")) == histories_before
    oldest = scenario.b2.start_segment().segno
    remaining = [parse_segment(p.name).segno for p in wal.iterdir() if is_segment_name(p.name)]
    assert min(remaining) == oldest
    assert report.removed_wal, "older segments should have been removed"
    assert find_gaps(wal, scenario.b2.start_segment()).ok

    b2 = scenario.primary.restore(scenario.b2, "kept-b2", repo=repo, target_name="rp3")
    assert b2.status is RestoreStatus.COMPLETE, b2.detail
    assert compare(scenario.fp_rp3, fingerprint(b2.instance, DBNAME)) == ("IDENTICAL", [])
    b2.instance.stop()
    b3 = scenario.primary.restore(scenario.b3, "kept-b3", repo=repo)
    assert b3.status is RestoreStatus.COMPLETE, b3.detail
    assert compare(scenario.fp_end, fingerprint(b3.instance, DBNAME)) == ("IDENTICAL", [])


@pytest.mark.criterion("A-15")
def test_retention_of_one_backup_on_the_new_timeline(scenario: Scenario) -> None:
    repo = _fresh_copy(scenario, "repo-retention-1")
    apply_retention(repo, keep=1)
    assert [b.label for b in list_backups(repo)] == [scenario.b3.label]
    assert (repo / "wal" / "00000002.history").is_file()
    assert find_gaps(repo / "wal", scenario.b3.start_segment()).ok
    outcome = scenario.primary.restore(scenario.b3, "only-b3", repo=repo)
    assert outcome.status is RestoreStatus.COMPLETE, outcome.detail
    assert compare(scenario.fp_end, fingerprint(outcome.instance, DBNAME)) == ("IDENTICAL", [])


@pytest.mark.criterion("A-15")
def test_retention_refuses_to_keep_nothing(scenario: Scenario) -> None:
    with pytest.raises(ValueError, match="at least one"):
        apply_retention(_fresh_copy(scenario, "repo-keep-0"), keep=0)
    shutil.rmtree(scenario.root / "repo-keep-0")
