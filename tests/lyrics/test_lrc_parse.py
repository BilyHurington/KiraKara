import pytest

from kara_align.lyrics import (
    LyricsFormatError,
    LyricsModeError,
    detect_format,
    format_lrc,
    format_lrc_time,
    has_valid_times,
    parse_lrc,
    parse_lyrics_text,
)

LRC = """﻿[ti:テスト]
[ar:Someone]
[length:03:21.50]
[offset:+500]
[00:01.00]作词 : 誰か
[00:12.30]君と歩いた
[00:20.00][01:10.00]サビの歌詞
[00:30.5]
[00:35:40]もう一度
[01:00.000]サビの歌詞
"""


def test_multi_tags_and_repeats_are_separate_instances():
    p = parse_lrc(LRC)
    texts = [(e.time_ms, e.text, e.tag_index) for e in p.entries]
    assert (20_000, "サビの歌詞", 0) in texts
    assert (70_000, "サビの歌詞", 1) in texts
    assert (60_000, "サビの歌詞", 0) in texts
    assert sum(1 for t in texts if t[1] == "サビの歌詞") == 3
    assert [e.time_ms for e in p.entries] == sorted(e.time_ms for e in p.entries)


def test_time_formats():
    p = parse_lrc("[00:30.5]a\n[00:35:40]b\n[01:00.123]c\n[02:00]d\n[100:00.00]e")
    assert [e.time_ms for e in p.entries] == [30_500, 35_400, 60_123, 120_000, 6_000_000]


def test_offset_sign_normalization():
    p = parse_lrc(LRC)
    assert p.offset_raw == "+500"
    assert p.offset_ms == 500
    assert p.embedded_shift_ms == -500
    doc = parse_lyrics_text(LRC, mode="lrc").doc
    assert doc.embedded_offset_raw == "+500"
    assert doc.embedded_shift_ms == -500
    assert "earlier" in doc.embedded_offset_note
    neg = parse_lyrics_text("[offset:-250]\n[00:01.00]a", mode="lrc").doc
    assert neg.embedded_shift_ms == 250


def test_enhanced_word_tags_stripped_but_kept():
    p = parse_lrc("[00:01.00]<00:01.00>君<00:01.50>と<00:02.00>")
    e = p.entries[0]
    assert e.text == "君と"
    assert [(w.time_ms, w.text) for w in e.word_tags] == [(1000, "君"), (1500, "と"), (2000, "")]


def test_lrc_mode_doc():
    res = parse_lyrics_text(LRC, mode="lrc")
    doc = res.doc
    assert res.detected == "lrc"
    assert doc.meta.title == "テスト"
    assert doc.meta.duration_ms == 201_500
    kinds = [(ln.text, ln.kind, ln.sing, ln.imported_start_ms) for ln in doc.lines]
    assert kinds[0] == ("作词 : 誰か", "meta", False, 1000)
    blank = [ln for ln in doc.lines if ln.kind == "blank"]
    assert len(blank) == 1 and not blank[0].sing and blank[0].imported_start_ms == 30_500
    assert [ln.id for ln in doc.lines] == [f"L{i:04d}" for i in range(1, len(doc.lines) + 1)]
    assert doc.language == "ja"
    chorus = [ln for ln in doc.lines if ln.text == "サビの歌詞"]
    assert len({ln.id for ln in chorus}) == 3
    assert {ln.source.tag_index for ln in chorus} == {0, 1}


def test_plain_mode_drops_times_with_warning():
    res = parse_lyrics_text(LRC, mode="plain")
    assert all(ln.imported_start_ms is None for ln in res.doc.lines)
    assert not any(ln.kind == "blank" for ln in res.doc.lines)
    assert any("ignored" in w for w in res.warnings)
    # repeated chorus still kept (3 instances)
    assert sum(1 for ln in res.doc.lines if ln.text == "サビの歌詞") == 3


def test_lrc_mode_requires_times():
    with pytest.raises(LyricsModeError):
        parse_lyrics_text("君と歩いた\nもう一度", mode="lrc")
    with pytest.raises(LyricsModeError):
        parse_lyrics_text("[ti:x]\n[00:01.00]\n[00:02.00]", mode="lrc")


def test_untimed_line_in_lrc_kept_in_raw_position():
    res = parse_lyrics_text("[00:01.00]a\nb\n[00:03.00]c", mode="lrc")
    assert [(ln.text, ln.imported_start_ms) for ln in res.doc.lines] == [("a", 1000), ("b", None), ("c", 3000)]
    assert any("without a time" in w for w in res.warnings)


def test_plain_text_and_credits():
    res = parse_lyrics_text("作詞：A\nLyrics by: B\n词：C\n君と\n\n歩いた\n", mode="plain")
    assert [ln.kind for ln in res.doc.lines] == ["meta", "meta", "meta", "lyric", "lyric"]
    assert res.doc.sung_lines()[0].text == "君と"


def test_paste_vs_upload_equivalent():
    a = parse_lyrics_text(LRC, mode="lrc", origin="paste").doc
    b = parse_lyrics_text(LRC.replace("\n", "\r\n"), mode="lrc", origin="upload", filename="x.lrc").doc
    assert a.text_revision() == b.text_revision()
    assert [ln.id for ln in a.lines] == [ln.id for ln in b.lines]
    assert b.lines[0].source.origin == "upload"


def test_detect_format():
    assert detect_format(LRC) == "lrc"
    assert detect_format("hello\nworld") == "plain"
    assert detect_format('{"format": "kara-align/project", "version": 1}') == "json-project"
    assert detect_format('{"format": "kara-align/reading-patch"}') == "json-reading-patch"
    assert detect_format('{"format": "kara-align/alignment"}') == "json-alignment"
    assert detect_format('{"format": "kara-align/prepared"}') == "json-prepared"
    assert detect_format('{"x": 1}') == "unknown"
    assert detect_format("[ti:abc]\n[00:01.00]x") == "lrc"
    with pytest.raises(LyricsFormatError):
        parse_lyrics_text('{"format": "kara-align/project"}', mode="plain")


def test_language_detection():
    assert parse_lyrics_text("我爱你", mode="plain").doc.language == "zh"
    assert parse_lyrics_text("hello world", mode="plain").doc.language == "en"


def test_has_valid_times():
    assert has_valid_times(parse_lrc("[00:01.00]a"))
    assert not has_valid_times(parse_lrc("[00:01.00]\nplain"))


def test_format_time_rounding():
    assert format_lrc_time(12345) == "00:12.35"
    assert format_lrc_time(12344) == "00:12.34"
    assert format_lrc_time(59_995) == "01:00.00"
    assert format_lrc_time(12345, "ms") == "00:12.345"
    with pytest.raises(ValueError):
        format_lrc_time(-1)


def test_format_roundtrip_no_offset():
    out = format_lrc([(1000, "a"), (None, "b")], meta={"ti": "t", "offset": "500"})
    assert "[offset" not in out
    p = parse_lrc(out)
    assert p.entries[0].time_ms == 1000 and p.untimed[0].text == "b"


@pytest.mark.parametrize("text,meta", [
    ("人声录音 : 利伟明", True), ("制作人 : 钱雷", True), ("Mixed by: X", True), ("作曲：Y", True),
    ("君と歩いた", False), ("Hello: world of love and all the other things", False), ("時は: 今", False),
])
def test_credit_detection(text, meta):
    from kara_align.lyrics.parse import is_credit_line
    assert is_credit_line(text) is meta
