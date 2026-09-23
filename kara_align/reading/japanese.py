"""Japanese kana handling: normalisation, mora splitting and rule readings.

Morae (拍) rules:

* 拗音 / small vowels merge with the preceding kana (きゃ, しゅ, ふぁ, ヴぁ, てぃ).
* 促音 ``っ`` is its own mora, flag ``sokuon``.
* 撥音 ``ん`` is its own mora, flag ``hatsuon``.
* long-vowel marks ``ー`` / ``〜`` / ``～`` are their own mora, flag ``long``.

Rule readings come from pykakasi.  They are context free, so every kanji
segment is marked ``uncertain`` – manual / AI readings take precedence.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from ..models import Segment, Unit

SMALL_MERGE = set("ゃゅょぁぃぅぇぉゎ")
LONG_MARKS = set("ー〜～")
SOKUON = "っ"
HATSUON = "ん"


@dataclass
class Mora:
    text: str
    flags: list[str] = field(default_factory=list)


def to_hiragana(s: str) -> str:
    """Katakana -> hiragana (ヴ -> ゔ); other characters unchanged."""
    s = unicodedata.normalize("NFKC", s)
    out = []
    for ch in s:
        code = ord(ch)
        if 0x30A1 <= code <= 0x30F6:
            out.append(chr(code - 0x60))
        else:
            out.append(ch)
    return "".join(out)


# characters inside the kana Unicode blocks that are punctuation / marks,
# not pronounced: ゠ (double hyphen), ・ (middle dot, e.g. "・・・"),
# standalone ゛ ゜
_NON_KANA = {"\u30a0", "\u30fb", "\u309b", "\u309c"}


def is_kana(ch: str) -> bool:
    code = ord(ch)
    if ch in _NON_KANA:
        return False
    return 0x3041 <= code <= 0x309F or 0x30A0 <= code <= 0x30FF or ch in LONG_MARKS


def is_kanji(ch: str) -> bool:
    code = ord(ch)
    return (0x4E00 <= code <= 0x9FFF or 0x3400 <= code <= 0x4DBF or 0xF900 <= code <= 0xFAFF
            or ch in "々〆ヶ")


def is_kana_text(s: str) -> bool:
    return bool(s) and all(is_kana(c) for c in s)


def split_morae(kana: str) -> list[Mora]:
    """Split a kana string (hiragana or katakana) into morae."""
    h = to_hiragana(kana)
    morae: list[Mora] = []
    for ch in h:
        if ch in SMALL_MERGE and morae and not morae[-1].flags:
            morae[-1].text += ch
        elif ch == SOKUON:
            morae.append(Mora(ch, ["sokuon"]))
        elif ch == HATSUON:
            morae.append(Mora(ch, ["hatsuon"]))
        elif ch in LONG_MARKS:
            morae.append(Mora("ー", ["long"]))
        else:
            morae.append(Mora(ch, []))
    return morae


def kana_units(kana_surface: str, *, with_surface: bool = True) -> list[Unit]:
    """One Unit per mora.  The unit reading is hiragana; surface keeps the original kana."""
    units: list[Unit] = []
    pos = 0
    for m in split_morae(kana_surface):
        n = len(m.text)
        surf = kana_surface[pos:pos + n] if with_surface else ""
        pos += n
        units.append(Unit(reading=m.text, surface=surf, flags=list(m.flags)))
    return units


def reading_units(reading: str) -> list[Unit]:
    """Units for a kanji reading (empty surfaces; the segment owns the surface)."""
    return [Unit(reading=m.text, surface="", flags=list(m.flags)) for m in split_morae(reading)]


_kakasi = None


def _kks():
    global _kakasi
    if _kakasi is None:
        import pykakasi

        _kakasi = pykakasi.kakasi()
    return _kakasi


_CLASS_RE = re.compile(
    # kana without the marks in _NON_KANA (゛゜゠・ are punctuation)
    r"(?P<kana>[\u3041-\u3096\u3099\u309a\u309d-\u309f\u30a1-\u30fa\u30fc-\u30ffー〜～]+)"
    r"|(?P<kanji>[一-鿿㐀-䶿豈-﫿々〆ヶ]+)"
    r"|(?P<latin>[A-Za-zＡ-Ｚａ-ｚ']+)"
    r"|(?P<digit>[0-9０-９]+)"
    r"|(?P<other>.)",
    re.S,
)


def _kanji_reading(orig: str) -> str:
    return "".join(to_hiragana(r["hira"]) for r in _kks().convert(orig))


def _split_okurigana(orig: str, hira: str) -> list[tuple[str, str]]:
    """Split a pykakasi token like ('歩い', 'あるい') into kanji / kana parts."""
    runs = [(m.lastgroup, m.group()) for m in _CLASS_RE.finditer(orig)]
    if len(runs) <= 1:
        return [(orig, hira)]
    # strip kana prefix / suffix that literally match the reading
    out_pre, out_suf = [], []
    h = hira
    while runs and runs[0][0] == "kana" and h.startswith(to_hiragana(runs[0][1])):
        k = to_hiragana(runs[0][1])
        out_pre.append((runs[0][1], k))
        h = h[len(k):]
        runs.pop(0)
    while runs and runs[-1][0] == "kana" and h.endswith(to_hiragana(runs[-1][1])) and len(runs) > 1:
        k = to_hiragana(runs[-1][1])
        out_suf.insert(0, (runs[-1][1], k))
        h = h[: len(h) - len(k)]
        runs.pop()
    middle = "".join(t for _, t in runs)
    return out_pre + ([(middle, h)] if middle else []) + out_suf


def rule_segments(text: str) -> list[Segment]:
    """Rule-based segmentation of a Japanese line.

    Surfaces of the returned segments concatenate exactly to ``text``.
    """
    segs: list[Segment] = []
    for m in _CLASS_RE.finditer(text):
        kind, run = m.lastgroup, m.group()
        if kind == "kana":
            hira = to_hiragana(run)
            segs.append(Segment(surface=run, reading=hira, lang="ja", units=kana_units(run),
                                reading_source="rule"))
        elif kind == "kanji":
            # use pykakasi on the kanji run with trailing context for better readings
            for piece, reading in _kanji_pieces(text, m.start(), run):
                segs.append(Segment(surface=piece, reading=reading, lang="ja",
                                    units=reading_units(reading) if reading else [],
                                    reading_source="rule" if reading else "none",
                                    uncertain=True))
        elif kind == "latin":
            from .english import word_reading

            r = word_reading(run)
            segs.append(Segment(surface=run, reading=r or None, lang="en",
                                units=[Unit(reading=r, surface=run)] if r else [],
                                reading_source="rule" if r else "none"))
        elif kind == "digit":
            segs.append(Segment(surface=run, reading=None, lang="ja", units=[], uncertain=True,
                                reading_source="none", note="数字：读音需要人工或 AI 补充"))
        else:
            if segs and not segs[-1].units and segs[-1].reading_source == "none" and not segs[-1].uncertain:
                segs[-1].surface += run  # merge consecutive punctuation / spaces
            else:
                segs.append(Segment(surface=run, reading=None, lang="ja", units=[], reading_source="none"))
    return segs


def _kanji_pieces(text: str, start: int, run: str) -> list[tuple[str, str]]:
    """Readings for a kanji run using pykakasi on the run plus following okurigana."""
    # include following kana so pykakasi can pick a reading (歩い -> あるい)
    tail = ""
    j = start + len(run)
    while j < len(text) and is_kana(text[j]) and len(tail) < 4:
        tail += text[j]
        j += 1
    pieces: list[tuple[str, str]] = []
    consumed = 0
    for tok in _kks().convert(run + tail):
        orig, hira = tok["orig"], to_hiragana(tok["hira"])
        for part, reading in _split_okurigana(orig, hira):
            if consumed >= len(run):
                break
            if any(is_kanji(c) for c in part):
                take = part[: len(run) - consumed]
                if take != part:  # kanji token extends past the run – should not happen
                    reading = _kanji_reading(take)
                pieces.append((take, reading if is_kana_text(reading) else ""))
                consumed += len(take)
            else:
                # kana belonging to the run cannot happen; it is the tail – stop
                if consumed >= len(run):
                    break
    if consumed < len(run):
        rest = run[consumed:]
        r = _kanji_reading(rest)
        pieces.append((rest, r if is_kana_text(r) else ""))
    return pieces
