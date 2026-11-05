"""Gap detection on synthetic repositories (A-12): timelines, history files and segment sizes."""

from pathlib import Path

import pytest

from districom_backup.wal_gaps import find_gaps
from districom_backup.walnames import Segment, segment_name

MB = 1024 * 1024


def _repo(tmp_path: Path, segments: list[tuple[int, int]], histories: dict[int, str], size: int = 16 * MB) -> Path:
    wal = tmp_path / "wal"
    wal.mkdir()
    for tli, segno in segments:
        (wal / segment_name(Segment(tli, segno), size)).write_bytes(b"")
    for tli, text in histories.items():
        (wal / f"{tli:08X}.history").write_text(text, encoding="utf-8")
    (wal / "000000010000000000000002.00000028.backup").write_text("x", encoding="utf-8")
    (wal / "000000010000000000000003.partial").write_bytes(b"")
    return wal


@pytest.mark.criterion("A-12")
def test_contiguous_single_timeline(tmp_path: Path) -> None:
    wal = _repo(tmp_path, [(1, n) for n in range(1, 9)], {})
    report = find_gaps(wal, Segment(1, 2))
    assert report.ok, report.summary()
    assert report.last_segment == "000000010000000000000008"


@pytest.mark.criterion("A-12")
def test_missing_segment_in_the_middle(tmp_path: Path) -> None:
    wal = _repo(tmp_path, [(1, n) for n in range(1, 9) if n != 5], {})
    report = find_gaps(wal, Segment(1, 2))
    assert not report.ok
    assert report.missing_segments == ["000000010000000000000005"]
    assert "WAL GAP" in report.summary()


@pytest.mark.criterion("A-12")
def test_segments_before_the_backup_do_not_matter(tmp_path: Path) -> None:
    wal = _repo(tmp_path, [(1, n) for n in range(4, 9)], {})
    assert find_gaps(wal, Segment(1, 4)).ok


@pytest.mark.criterion("A-12")
def test_timeline_switch_is_not_a_gap(tmp_path: Path) -> None:
    # Timeline 1 until 0/5000A28 (inside segment 5); timeline 2 continues from segment 5.
    # Timeline 1 kept going (segments 6-7) on the old primary: irrelevant for timeline 2.
    segments = [(1, n) for n in range(2, 8)] + [(2, n) for n in range(5, 10)]
    wal = _repo(tmp_path, segments, {2: "1\t0/5000A28\tat restore point\n"})
    report = find_gaps(wal, Segment(1, 2))
    assert report.ok, report.summary()
    assert report.latest_timeline == 2
    assert report.last_segment == "000000020000000000000009"


@pytest.mark.criterion("A-12")
def test_switch_exactly_on_a_segment_boundary(tmp_path: Path) -> None:
    segments = [(1, n) for n in range(2, 5)] + [(2, n) for n in range(5, 7)]
    wal = _repo(tmp_path, segments, {2: "1\t0/5000000\tno recovery target specified\n"})
    assert find_gaps(wal, Segment(1, 2)).ok


@pytest.mark.criterion("A-12")
def test_gap_on_the_parent_timeline_before_the_switch(tmp_path: Path) -> None:
    segments = [(1, n) for n in (2, 3, 5)] + [(2, n) for n in range(5, 8)]
    wal = _repo(tmp_path, segments, {2: "1\t0/5000A28\tx\n"})
    report = find_gaps(wal, Segment(1, 2))
    assert report.missing_segments == ["000000010000000000000004"]


@pytest.mark.criterion("A-12")
def test_switch_segment_is_satisfied_by_either_timeline(tmp_path: Path) -> None:
    # Segment 5 holds the switch: the copy on timeline 2 starts with timeline 1's WAL.
    segments = [(1, n) for n in (2, 3, 4)] + [(2, n) for n in range(5, 8)]
    wal = _repo(tmp_path, segments, {2: "1\t0/5000A28\tx\n"})
    assert find_gaps(wal, Segment(1, 2)).ok
    (wal / segment_name(Segment(2, 5))).unlink()
    assert find_gaps(wal, Segment(1, 2)).missing_segments == ["000000020000000000000005"]
    (wal / segment_name(Segment(1, 5))).write_bytes(b"")
    assert find_gaps(wal, Segment(1, 2)).ok


@pytest.mark.criterion("A-12")
def test_gap_on_the_new_timeline(tmp_path: Path) -> None:
    segments = [(1, n) for n in range(2, 6)] + [(2, n) for n in (5, 6, 8)]
    wal = _repo(tmp_path, segments, {2: "1\t0/5000A28\tx\n"})
    assert find_gaps(wal, Segment(1, 2)).missing_segments == ["000000020000000000000007"]


@pytest.mark.criterion("A-12")
def test_missing_history_file_is_a_gap(tmp_path: Path) -> None:
    segments = [(1, n) for n in range(2, 6)] + [(2, n) for n in range(5, 8)]
    wal = _repo(tmp_path, segments, {})
    report = find_gaps(wal, Segment(1, 2))
    assert not report.ok
    assert report.missing_history == [2]


@pytest.mark.criterion("A-12")
def test_three_timelines(tmp_path: Path) -> None:
    segments = [(1, n) for n in range(2, 5)] + [(2, n) for n in range(4, 7)] + [(3, n) for n in range(6, 9)]
    histories = {2: "1\t0/4000100\tx\n", 3: "1\t0/4000100\tx\n2\t0/6000200\ty\n"}
    wal = _repo(tmp_path, segments, histories)
    assert find_gaps(wal, Segment(1, 2)).ok
    (wal / segment_name(Segment(2, 5))).unlink()
    assert find_gaps(wal, Segment(1, 2)).missing_segments == ["000000020000000000000005"]


@pytest.mark.criterion("A-12")
def test_backup_on_an_abandoned_timeline_is_reported(tmp_path: Path) -> None:
    # Timeline 3 branched from timeline 1: a backup taken on timeline 2 cannot follow it.
    segments = [(1, n) for n in range(2, 5)] + [(2, n) for n in range(4, 7)] + [(3, n) for n in range(4, 7)]
    histories = {2: "1\t0/4000100\tx\n", 3: "1\t0/4000200\ty\n"}
    wal = _repo(tmp_path, segments, histories)
    report = find_gaps(wal, Segment(2, 5))
    assert not report.ok
    assert "not an ancestor" in report.summary()


def test_other_segment_sizes(tmp_path: Path) -> None:
    size = 1 * MB  # 4096 segments per log id
    wal = _repo(tmp_path, [(1, n) for n in range(4090, 4100)], {}, size)
    assert find_gaps(wal, Segment(1, 4090), size).ok
    (wal / segment_name(Segment(1, 4096), size)).unlink()
    assert find_gaps(wal, Segment(1, 4090), size).missing_segments == ["000000010000000100000000"]
