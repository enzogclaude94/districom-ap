import pytest

from districom_backup.walnames import (
    HistoryEntry,
    Segment,
    backup_file_segment,
    format_lsn,
    history_timeline,
    parse_history,
    parse_lsn,
    parse_segment,
    segment_name,
    segno_of_lsn,
)

MB = 1024 * 1024


def test_segment_name_round_trip_16mb() -> None:
    # 256 segments of 16 MB per log id: segment 0x1FF is log id 1, segment 0xFF.
    seg = parse_segment("0000000200000001000000FF")
    assert seg == Segment(2, 0x1FF)
    assert segment_name(seg) == "0000000200000001000000FF"


@pytest.mark.parametrize("size", [1 * MB, 16 * MB, 64 * MB, 1024 * MB])
def test_round_trip_any_segment_size(size: int) -> None:
    for segno in (0, 1, 255, 4095, 70000):
        name = segment_name(Segment(3, segno), size)
        assert parse_segment(name, size) == Segment(3, segno)


def test_segment_out_of_range_for_size() -> None:
    with pytest.raises(ValueError, match="out of range"):
        parse_segment("000000010000000000000100")  # 0x100 does not exist with 16 MB segments


def test_lsn() -> None:
    assert parse_lsn("1/A0000028") == (1 << 32) + 0xA0000028
    assert format_lsn(parse_lsn("0/16B3748")) == "0/16B3748"
    assert segno_of_lsn(parse_lsn("0/3000000")) == 3
    assert segno_of_lsn(parse_lsn("0/2FFFFFF")) == 2


def test_history_file() -> None:
    text = '1\t0/3000000\tno recovery target specified\n\n# comment\n2\t0/5000A28\tat restore point "x"\n'
    assert parse_history(text) == [HistoryEntry(1, 0x3000000), HistoryEntry(2, 0x5000A28)]
    assert history_timeline("00000003.history") == 3
    assert history_timeline("000000010000000000000003") is None


def test_backup_history_file_name() -> None:
    assert backup_file_segment("000000010000000000000002.00000028.backup") == "000000010000000000000002"
    assert backup_file_segment("000000010000000000000002") is None
