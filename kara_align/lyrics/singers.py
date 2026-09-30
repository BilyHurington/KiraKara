"""Who sings which part of the lyrics (多人演唱分色).

A line has its own singers (``Line.singers``, numbers of the karaoke style's singers) and parts
sung by someone else (``Line.singer_spans``: character ranges of ``Line.text``).  Everything is
kept by character offset, so re-segmenting a line never loses it; edits that change the text
carry the ranges over (``remap``), merging / splitting lines carries them along.

Only karaoke colours depend on this: the alignment never reads it.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from ..models import Line, LyricsDoc, SingerSpan, _singer_ids

__all__ = ["clean_ids", "fill_blanks", "normalize", "effective", "range_singers", "remap", "merged", "split", "shift_numbers",
           "Marker", "detect_markers", "ALL_WORDS"]


def clean_ids(value: Any) -> list[int]:
    return _singer_ids(value)


def fill_blanks(text: str, chars: list[tuple[int, ...]], own: tuple[int, ...]) -> list[tuple[int, ...]]:
    """Blanks (spaces, full-width spaces) are nobody's: a run of them takes the singers of the characters
    on both sides when those agree (so it never splits a part), else the line's own."""
    out = list(chars)
    i, n = 0, len(out)
    while i < n:
        if not text[i].isspace():
            i += 1
            continue
        j = i
        while j < n and text[j].isspace():
            j += 1
        left = out[i - 1] if i > 0 else None
        right = out[j] if j < n else None
        fill = left if left is not None and left == right else own
        out[i:j] = [fill] * (j - i)
        i = j
    return out


def normalize(line: Line) -> None:
    """Spans inside the text, sorted, not overlapping (a later one wins), neighbours with the same
    singers joined; a span that says the same as the line itself is dropped; blanks follow
    fill_blanks()."""
    n = len(line.text)
    line.singers = clean_ids(line.singers)
    own = tuple(line.singers)
    per_char: list[tuple[int, ...]] = [own] * n
    for sp in line.singer_spans:
        ids = tuple(clean_ids(sp.singers))
        for i in range(max(0, sp.start), min(n, sp.end)):
            per_char[i] = ids
    per_char = fill_blanks(line.text, per_char, own)
    out: list[SingerSpan] = []
    for i, ids in enumerate(per_char):
        if ids == own:
            continue
        if out and out[-1].end == i and tuple(out[-1].singers) == ids:
            out[-1].end = i + 1
        else:
            out.append(SingerSpan(start=i, end=i + 1, singers=list(ids)))
    line.singer_spans = out


def effective(line: Line) -> list[tuple[int, ...]]:
    """The singers of every character of the line (blanks: fill_blanks())."""
    out = [tuple(line.singers)] * len(line.text)
    for sp in line.singer_spans:
        for i in range(max(0, sp.start), min(len(out), sp.end)):
            out[i] = tuple(sp.singers)
    return fill_blanks(line.text, out, tuple(line.singers))


def range_singers(chars: list[tuple[int, ...]], a: int, b: int, text: Optional[str] = None) -> tuple[int, ...]:
    """The singers of characters [a, b): the ones most of them have (ties: the first character's).
    With ``text``, blank characters count only when the whole range is blank."""
    part = chars[max(0, a):max(a, b)]
    if text is not None:
        inked = [ids for ids, ch in zip(part, text[max(0, a):max(a, b)]) if not ch.isspace()]
        part = inked or part
    if not part:
        return ()
    counts: dict[tuple[int, ...], int] = {}
    for ids in part:
        counts[ids] = counts.get(ids, 0) + 1
    best = max(counts.values())
    return next(ids for ids in part if counts[ids] == best)


def remap(line: Line, old_text: str) -> None:
    """The text of ``line`` changed from ``old_text``: keep each character's singers where the
    character is still there (new characters take their left neighbour's)."""
    if not line.singer_spans or old_text == line.text:
        normalize(line)
        return
    old_chars = effective(Line(text=old_text, singers=line.singers, singer_spans=line.singer_spans))
    own = tuple(line.singers)
    new_chars: list[tuple[int, ...]] = [own] * len(line.text)
    sm = difflib.SequenceMatcher(a=old_text, b=line.text, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            new_chars[j1:j2] = old_chars[i1:i2]
        elif tag in ("replace", "insert"):
            src = old_chars[i1] if tag == "replace" and i1 < len(old_chars) else (old_chars[i1 - 1] if i1 else own)
            new_chars[j1:j2] = [src] * (j2 - j1)
    line.singer_spans = [SingerSpan(start=i, end=i + 1, singers=list(ids)) for i, ids in enumerate(new_chars)]
    normalize(line)


def merged(parts: list[Line], text: str) -> tuple[list[int], list[SingerSpan]]:
    """Singers of lines merged into one whose text is ``text`` (the parts' texts in order, maybe with
    joiners between them): the first line's own singers, the others' as spans where they differ."""
    own = list(parts[0].singers) if parts else []
    chars: list[tuple[int, ...]] = [tuple(own)] * len(text)
    pos = 0
    for p in parts:
        at = text.find(p.text, pos) if p.text else pos
        if at < 0:
            continue
        for i, ids in enumerate(effective(p)):
            chars[at + i] = ids
        pos = at + len(p.text)
    probe = Line(text=text, singers=own,
                 singer_spans=[SingerSpan(start=i, end=i + 1, singers=list(ids)) for i, ids in enumerate(chars)])
    normalize(probe)
    return probe.singers, probe.singer_spans


def split(line: Line, at: int, left_text: str, right_text: str) -> tuple[tuple[list[int], list[SingerSpan]],
                                                                      tuple[list[int], list[SingerSpan]]]:
    """Singers of the two halves of ``line`` split at character ``at``: ``left_text`` starts the line,
    ``right_text`` is the rest without the blanks at its start."""
    chars = effective(line)
    rest = line.text[at:]
    right_off = at + (len(rest) - len(rest.lstrip())) if right_text == rest.lstrip() else at

    def piece(text: str, off: int) -> tuple[list[int], list[SingerSpan]]:
        probe = Line(text=text, singers=list(line.singers),
                     singer_spans=[SingerSpan(start=i, end=i + 1, singers=list(chars[off + i]))
                                   for i in range(len(text)) if off + i < len(chars)])
        normalize(probe)
        return probe.singers, probe.singer_spans

    return piece(left_text, 0), piece(right_text, right_off)


def shift_numbers(doc: LyricsDoc, removed: int) -> int:
    """Singer ``removed`` is gone: it leaves every line, higher numbers move down by one.
    Returns how many lines changed."""
    def fix(ids: Iterable[int]) -> list[int]:
        return [i - 1 if i > removed else i for i in ids if i != removed]

    changed = 0
    for ln in doc.lines:
        before = (list(ln.singers), [(s.start, s.end, list(s.singers)) for s in ln.singer_spans])
        ln.singers = fix(ln.singers)
        for sp in ln.singer_spans:
            sp.singers = fix(sp.singers)
        normalize(ln)
        if before != (list(ln.singers), [(s.start, s.end, list(s.singers)) for s in ln.singer_spans]):
            changed += 1
    return changed


# ---------------------------------------------------------------------------
# singer names written into the lyrics (网易云 etc.: "A：…", "（XX）…", "【成员】…")

# words meaning "everyone"
ALL_WORDS = {"全员", "全員", "全体", "合", "合唱", "齐唱", "みんな", "all", "全部", "everyone", "tutti"}

_PREFIXES = [
    re.compile(r"^\s*[【\[［]\s*([^】\]［］\[]{1,24}?)\s*[】\]］]\s*"),
    re.compile(r"^\s*[（(]\s*([^（()）]{1,24}?)\s*[)）]\s*"),
    re.compile(r"^\s*([^\s:：（）()【】\[\]［］「」『』]{1,16}?)\s*[:：]\s*"),
]
_SEP = re.compile(r"\s*[&＆、,，/／+＋]\s*|\s+and\s+|\s*×\s*")


@dataclass
class Marker:
    line_id: str
    prefix: str  # the characters at the start of the line that name the singers (with the blanks after them)
    names: list[str]  # as written ("全员" words kept: they mean everyone)


def _names(raw: str) -> list[str]:
    return [n for n in (x.strip() for x in _SEP.split(raw)) if n]


def is_all(name: str) -> bool:
    return name.strip().lower() in ALL_WORDS


def detect_markers(doc: LyricsDoc) -> list[Marker]:
    """Lines that start with singer names.  A name must start at least two lines (or come together
    with one that does, or mean everyone), so a lyric that happens to start with "(Hey!)" once is not
    taken for a singer; the lyrics after the marker must not be empty."""
    found: list[Marker] = []
    for ln in doc.sung_lines():
        for rx in _PREFIXES:
            m = rx.match(ln.text)
            if not m or not ln.text[m.end():].strip():
                continue
            names = _names(m.group(1))
            if names and all(len(n) <= 16 for n in names):
                found.append(Marker(ln.id, ln.text[:m.end()], names))
                break
    counts: dict[str, int] = {}
    for mk in found:
        for n in set(mk.names):
            counts[n] = counts.get(n, 0) + 1
    known = {n for n, c in counts.items() if c >= 2}
    keep = [mk for mk in found
            if any(n in known for n in mk.names) or all(is_all(n) for n in mk.names)]
    return keep if any(not all(is_all(n) for n in mk.names) for mk in keep) else []


def marker_names(markers: list[Marker]) -> list[str]:
    """The singers the markers name, in order of appearance (not the "everyone" words)."""
    out: list[str] = []
    for mk in markers:
        for n in mk.names:
            if not is_all(n) and n not in out:
                out.append(n)
    return out

