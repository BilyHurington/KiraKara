import pytest

from kara_align.reading.japanese import rule_segments, split_morae, to_hiragana
from kara_align.reading.profiles import get_profile, kana_to_romaji


def morae(s):
    return [(m.text, m.flags) for m in split_morae(s)]


def test_yoon_merged():
    assert [m for m, _ in morae("きゃしゅちぇ")] == ["きゃ", "しゅ", "ちぇ"]
    assert [m for m, _ in morae("ふぁヴぁ")] == ["ふぁ", "ゔぁ"]


def test_sokuon_hatsuon_long():
    assert morae("かっぱ") == [("か", []), ("っ", ["sokuon"]), ("ぱ", [])]
    assert morae("さんぽ") == [("さ", []), ("ん", ["hatsuon"]), ("ぽ", [])]
    assert morae("らーめん〜") == [("ら", []), ("ー", ["long"]), ("め", []), ("ん", ["hatsuon"]), ("ー", ["long"])]


def test_katakana_normalized():
    assert to_hiragana("カタカナ") == "かたかな"
    assert [m for m, _ in morae("ティッシュ")] == ["てぃ", "っ", "しゅ"]


@pytest.mark.parametrize("text", [
    "君と歩いた道を、今日も", "ラブソング Love you 123回", "ちょっと待って！", "  空へ　飛べ ", "東京タワー",
])
def test_rule_segments_roundtrip(text):
    segs = rule_segments(text)
    assert "".join(s.surface for s in segs) == text
    for s in segs:
        if s.reading and s.lang == "ja":
            assert "".join(u.reading for u in s.units) == s.reading


def test_rule_segments_kanji_uncertain_and_kana_surface():
    segs = rule_segments("君と")
    kanji, kana = segs[0], segs[1]
    assert kanji.surface == "君" and kanji.uncertain and kanji.reading_source == "rule"
    assert all(u.surface == "" for u in kanji.units)
    assert kana.units[0].surface == "と"


def test_digits_uncertain_no_reading():
    segs = rule_segments("123回")
    assert segs[0].surface == "123" and segs[0].uncertain and not segs[0].units


def test_latin_in_japanese():
    segs = rule_segments("Love you")
    assert [s.lang for s in segs if s.units] == ["en", "en"]


def ht(readings, langs=None):
    p = get_profile("ja-hepburn")
    units = [(m.text, m.flags) for r in readings for m in split_morae(r)] if langs is None else None
    if units is None:
        return p.unit_texts(readings, langs, [[] for _ in readings])
    return p.unit_texts([u for u, _ in units], ["ja"] * len(units), [f for _, f in units])


def test_hepburn_basic():
    assert [kana_to_romaji(k) for k in ["し", "ち", "つ", "ふ", "じ", "ぢ", "づ", "を", "は"]] == \
        ["shi", "chi", "tsu", "fu", "ji", "ji", "zu", "o", "ha"]
    assert [kana_to_romaji(k) for k in ["きゃ", "しゃ", "ちゃ", "じゃ", "ふぁ", "ゔ", "にょ"]] == \
        ["kya", "sha", "cha", "ja", "fa", "vu", "nyo"]


def test_hepburn_sokuon_and_long():
    assert ht(["かっぱ"]) == ["ka", "p", "pa"]
    assert ht(["まっちゃ"]) == ["ma", "t", "cha"]
    assert ht(["がんばっ"]) == ["ga", "n", "ba", ""]  # no following consonant -> empty, reported unaligned
    assert ht(["らーめん"]) == ["ra", "a", "me", "n"]


def test_profile_other_langs_and_version():
    p = get_profile()
    assert p.version == "ja-hepburn/1"
    assert p.unit_texts(["love", "nv"], ["en", "zh"], [[], []]) == ["love", "nv"]
    with pytest.raises(KeyError):
        get_profile("nope")
