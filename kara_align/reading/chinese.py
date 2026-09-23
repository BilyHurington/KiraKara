"""Chinese: one unit per hanzi with a tone-less pinyin reading (pypinyin).

``ü`` is written as ``v`` (pypinyin NORMAL style), e.g. 女 -> ``nv``.
Polyphonic characters are marked uncertain with the alternatives as candidates.
"""

from __future__ import annotations

import re

from ..models import Segment, Unit
from .japanese import is_kanji

_RUN_RE = re.compile(r"(?P<han>[一-鿿㐀-䶿]+)|(?P<latin>[A-Za-z']+)|(?P<other>.)", re.S)


def rule_segments(text: str) -> list[Segment]:
    from pypinyin import Style, lazy_pinyin, pinyin

    segs: list[Segment] = []
    for m in _RUN_RE.finditer(text):
        kind, run = m.lastgroup, m.group()
        if kind == "han":
            readings = lazy_pinyin(run, style=Style.NORMAL)
            for ch, r in zip(run, readings):
                alts = pinyin(ch, style=Style.NORMAL, heteronym=True)[0]
                alts = [a for a in dict.fromkeys(alts) if a != r]
                segs.append(Segment(surface=ch, reading=r, lang="zh", units=[Unit(reading=r, surface=ch)],
                                    reading_source="rule", uncertain=bool(alts), candidates=alts))
        elif kind == "latin":
            from .english import word_reading

            r = word_reading(run)
            segs.append(Segment(surface=run, reading=r or None, lang="en",
                                units=[Unit(reading=r, surface=run)] if r else [],
                                reading_source="rule" if r else "none"))
        else:
            if segs and not segs[-1].units and segs[-1].reading_source == "none":
                segs[-1].surface += run
            else:
                segs.append(Segment(surface=run, reading=None, lang="zh", units=[], reading_source="none"))
    return segs


def has_hanzi_only(text: str) -> bool:
    return any(is_kanji(c) for c in text)
