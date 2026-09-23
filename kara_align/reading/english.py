"""English: one unit per word, reading = lowercase letters and apostrophes."""

from __future__ import annotations

import re
import unicodedata

from ..models import Segment, Unit

_WORD_RE = re.compile(r"[A-Za-z']+|[^A-Za-z']+")


def word_reading(word: str) -> str:
    w = unicodedata.normalize("NFKC", word).lower()
    return "".join(c for c in w if "a" <= c <= "z" or c == "'").strip("'")


def rule_segments(text: str) -> list[Segment]:
    segs: list[Segment] = []
    for m in _WORD_RE.finditer(text):
        run = m.group()
        r = word_reading(run)
        if r:
            segs.append(Segment(surface=run, reading=r, lang="en", units=[Unit(reading=r, surface=run)],
                                reading_source="rule"))
        else:
            segs.append(Segment(surface=run, reading=None, lang="en", units=[], reading_source="none"))
    return segs
