"""Bilingual LRC as copied from lyric sites: a translation under the same time tag."""

from kara_align.lyrics.parse import parse_lyrics_text

BILINGUAL = ("[00:01.00]君と歩いた\n[00:01.00]和你一起走过\n[00:03.00]夜空の下で\n[00:03.00]在夜空下\n"
             "[00:05.00]ありがとう\n")


def test_translation_lines_become_translations_in_both_modes():
    for mode in ("lrc", "plain"):
        r = parse_lyrics_text(BILINGUAL, mode=mode)
        assert [(ln.text, ln.translation) for ln in r.doc.lines] == [
            ("君と歩いた", "和你一起走过"), ("夜空の下で", "在夜空下"), ("ありがとう", None)], mode
        assert any("检测到 2 行翻译" in w for w in r.warnings)


def test_a_single_odd_pair_or_two_japanese_lines_are_left_alone():
    one = "[00:01.00]君と歩いた\n[00:01.00]和你一起走过\n[00:03.00]夜空の下で\n"
    assert len(parse_lyrics_text(one, mode="lrc").doc.lines) == 3  # needs two pairs
    both_ja = "[00:01.00]君と歩いた\n[00:01.00]夜空の下で\n[00:03.00]ありがとう\n[00:03.00]さよなら\n"
    r = parse_lyrics_text(both_ja, mode="lrc")
    assert len(r.doc.lines) == 4 and all(ln.translation is None for ln in r.doc.lines)
