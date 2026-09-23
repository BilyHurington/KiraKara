import pytest

from kara_align.lyrics import apply_pairs, merge_lines, pair_track, parse_lyrics_text, split_line
from kara_align.models import Segment, Unit

ORIG = "[00:01.00]一行目\n[00:05.00]二行目\n[00:09.00]三行目\n[00:20.00]二行目\n"


def doc():
    return parse_lyrics_text(ORIG, mode="lrc").doc


def test_pair_exact_and_nearest():
    d = doc()
    prev = pair_track(d, "[00:01.00]line one\n[00:05.20]line two\n[00:30.00]stray", tolerance_ms=500)
    got = {p.line_id: (p.text, p.method) for p in prev.pairs}
    assert got["L0001"] == ("line one", "time")
    assert got["L0002"] == ("line two", "nearest")
    assert [t.text for t in prev.unmatched_texts] == ["stray"]
    assert set(prev.unmatched_line_ids) == {"L0003", "L0004"}


def test_pair_by_order_when_track_untimed():
    d = doc()
    prev = pair_track(d, "one\ntwo\nthree\nfour", kind="romanization")
    assert [p.method for p in prev.pairs] == ["order"] * 4
    new = apply_pairs(d, prev.as_pairs(), kind="romanization")
    assert new.lines[3].romanization == "four"
    assert d.lines[3].romanization is None  # original untouched


def test_apply_pairs_unknown_id():
    with pytest.raises(KeyError):
        apply_pairs(doc(), [("nope", "x")])


def test_merge_keeps_first_start_and_provenance():
    d = doc()
    m = merge_lines(d, ["L0002", "L0003"])
    assert len(m.lines) == 3
    merged = m.lines[1]
    assert merged.text == "二行目三行目"
    assert merged.imported_start_ms == 5000
    assert merged.source.merged_from == ["L0002", "L0003"]
    with pytest.raises(ValueError):
        merge_lines(d, ["L0001", "L0003"])


def test_split_no_invented_time():
    d = doc()
    s = split_line(d, "L0002", 1)
    left, right = s.lines[1], s.lines[2]
    assert (left.text, right.text) == ("二", "行目")
    assert left.imported_start_ms == 5000
    assert right.imported_start_ms is None
    assert left.source.split_from == right.source.split_from == "L0002"


def test_split_keeps_segments_on_boundary():
    d = doc()
    d.lines[0].segments = [
        Segment(surface="一", reading="いち", units=[Unit(reading="い"), Unit(reading="ち")]),
        Segment(surface="行目", reading="ぎょうめ", units=[Unit(reading="ぎょ"), Unit(reading="う"), Unit(reading="め")]),
    ]
    s = split_line(d, "L0001", 1)
    assert [seg.surface for seg in s.lines[0].segments] == ["一"]
    assert [seg.surface for seg in s.lines[1].segments] == ["行目"]
    s2 = split_line(d, "L0001", 2)
    assert s2.lines[0].segments == [] and s2.lines[1].segments == []
