"""Fixed, deterministic transliteration profiles (unit reading -> model text).

The AI never decides tokenizer spelling; it only supplies kana readings.  A
profile maps each Unit to the text the acoustic model is fed, one output
string per unit so token positions map back to units.

``ja-hepburn`` (version ``ja-hepburn/1``):

* lowercase Hepburn: し shi, ち chi, つ tsu, ふ fu, じ/ぢ ji, づ zu, を o,
  きゃ kya, しゃ sha, ちゃ cha, じゃ ja, ふぁ fa, ゔ vu ...
* readings are expected to be *pronunciations*: particle は must be given as
  わ by the AI / user; the profile writes は as ``ha``.
* 促音 っ -> the geminate consonant of the next unit (っか -> ``k``, っち ->
  ``t``, っしゃ -> ``s``).  When no consonant follows (end of line, before a
  vowel or ん) the sokuon becomes an empty string: such a unit gets no tokens
  and the alignment runner reports it as ``unaligned`` (it is never silently
  dropped from the result).
* 撥音 ん -> ``n``.
* long mark ー -> the previous unit's final vowel (empty if none).
* ``en`` units: lowercase letters (apostrophes dropped).
* ``zh`` units: tone-less pinyin letters, ü written as ``v``.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Sequence

from .japanese import to_hiragana

_BASE = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}

# explicit digraph table (checked before the generic rules)
_DIGRAPH = {
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho", "しぇ": "she",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho", "ちぇ": "che",
    "じゃ": "ja", "じゅ": "ju", "じょ": "jo", "じぇ": "je",
    "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "ふゅ": "fyu",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du",
    "つぁ": "tsa", "つぃ": "tsi", "つぇ": "tse", "つぉ": "tso",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "いぇ": "ye", "しぃ": "shi",
}
_YOON = {"ゃ": "a", "ゅ": "u", "ょ": "o"}
_SMALL_V = {"ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o", "ゎ": "a"}
_VOWELS = "aeiou"


def kana_to_romaji(mora: str) -> str:
    """Romaji for a single mora (without sokuon / long handling)."""
    m = to_hiragana(mora)
    if m in _DIGRAPH:
        return _DIGRAPH[m]
    if m in _BASE:
        return _BASE[m]
    if len(m) >= 2 and m[0] in _BASE:
        head = _BASE[m[0]]
        rest = m[1:]
        if rest[0] in _YOON:  # きゃ -> kya, にょ -> nyo
            return head[:-1] + "y" + _YOON[rest[0]] + "".join(_BASE.get(c, "") for c in rest[1:])
        if rest[0] in _SMALL_V:  # くぁ -> kwa-ish: replace vowel
            return head[:-1] + _SMALL_V[rest[0]] + "".join(_BASE.get(c, "") for c in rest[1:])
        return "".join(kana_to_romaji(c) for c in m)
    return "".join(_BASE.get(c, "") for c in m)


def _geminate(next_text: str) -> str:
    if not next_text or next_text[0] in _VOWELS or next_text == "n":
        return ""
    if next_text.startswith("ch"):
        return "t"
    return next_text[0]


_LETTERS_RE = re.compile(r"[a-z]+")


def _letters(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).lower().replace("ü", "v")
    return "".join(_LETTERS_RE.findall(s))


class JaHepburnProfile:
    name = "ja-hepburn"
    version = "ja-hepburn/1"

    def unit_texts(self, readings: Sequence[str], langs: Sequence[str], flags: Sequence[Sequence[str]]) -> list[str]:
        n = len(readings)
        base: list[str] = []
        for r, lang, fl in zip(readings, langs, flags):
            if lang in ("en", "zh", "other") and not _looks_kana(r):
                base.append(_letters(r))
            elif "sokuon" in fl or to_hiragana(r) == "っ":
                base.append("\0sokuon")
            elif "long" in fl or r in ("ー", "〜", "～"):
                base.append("\0long")
            else:
                base.append(kana_to_romaji(r))
        out = list(base)
        for i in range(n):
            if base[i] == "\0long":
                prev = next((out[j] for j in range(i - 1, -1, -1) if out[j] and out[j][0] != "\0"), "")
                v = prev[-1] if prev and prev[-1] in _VOWELS else ("n" if prev == "n" else "")
                out[i] = v
        for i in range(n):
            if out[i] == "\0sokuon":
                # only a directly following unit may supply the geminate
                nxt = out[i + 1] if i + 1 < n and out[i + 1] and out[i + 1][0] != "\0" else ""
                out[i] = _geminate(nxt)
        return out


def _looks_kana(s: str) -> bool:
    from .japanese import is_kana_text

    return is_kana_text(s)


_PROFILES = {"ja-hepburn": JaHepburnProfile}


def get_profile(name: str = "ja-hepburn"):
    try:
        return _PROFILES[name]()
    except KeyError:
        raise KeyError(f"unknown transliteration profile: {name!r} (available: {sorted(_PROFILES)})") from None


def available_profiles() -> list[str]:
    return sorted(_PROFILES)
