"""Pairing of extra tracks (translation / romanization) and line merge / split.

Pairing only produces a *preview*; applying takes an explicit, user editable
list of ``(line_id, text)``.  Merge and split keep provenance and never
invent times for lines that had no anchor of their own.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable, Literal, Optional

from ..models import Line, LineSource, LyricsDoc, new_id
from .lrc import parse_lrc
from .parse import detect_format, normalize_text

TrackKind = Literal["translation", "romanization"]


@dataclass
class PairItem:
    line_id: str
    line_text: str
    text: str
    method: Literal["time", "nearest", "order"]
    delta_ms: Optional[int] = None


@dataclass
class TrackLine:
    time_ms: Optional[int]
    text: str


@dataclass
class PairPreview:
    kind: TrackKind
    pairs: list[PairItem] = field(default_factory=list)
    unmatched_line_ids: list[str] = field(default_factory=list)
    unmatched_texts: list[TrackLine] = field(default_factory=list)

    def as_pairs(self) -> list[tuple[str, str]]:
        return [(p.line_id, p.text) for p in self.pairs]


def _track_lines(text: str) -> list[TrackLine]:
    norm = normalize_text(text)
    if detect_format(norm) == "lrc":
        parsed = parse_lrc(norm)
        out = [TrackLine(e.time_ms, e.text) for e in parsed.entries if e.text]
        out += [TrackLine(None, u.text) for u in parsed.untimed if u.text]
        return out
    return [TrackLine(None, ln.strip()) for ln in norm.split("\n") if ln.strip()]


def pair_track(doc: LyricsDoc, track_text: str, *, kind: TrackKind = "translation",
               tolerance_ms: int = 500) -> PairPreview:
    """Match track lines to lyric lines: exact time, then nearest time, then order."""
    targets = [ln for ln in doc.lines if ln.kind == "lyric"]
    track = _track_lines(track_text)
    preview = PairPreview(kind=kind)
    used_line: set[str] = set()
    used_track: set[int] = set()
    matched: dict[str, PairItem] = {}

    # 1. exact time
    for ti, tl in enumerate(track):
        if tl.time_ms is None:
            continue
        for ln in targets:
            if ln.id not in used_line and ln.imported_start_ms == tl.time_ms:
                matched[ln.id] = PairItem(ln.id, ln.text, tl.text, "time", 0)
                used_line.add(ln.id)
                used_track.add(ti)
                break
    # 2. nearest time within tolerance
    for ti, tl in enumerate(track):
        if ti in used_track or tl.time_ms is None:
            continue
        best: Optional[Line] = None
        best_d = tolerance_ms + 1
        for ln in targets:
            if ln.id in used_line or ln.imported_start_ms is None:
                continue
            d = abs(ln.imported_start_ms - tl.time_ms)
            if d < best_d:
                best, best_d = ln, d
        if best is not None:
            matched[best.id] = PairItem(best.id, best.text, tl.text, "nearest", tl.time_ms - best.imported_start_ms)
            used_line.add(best.id)
            used_track.add(ti)
    # 3. order: only between items that cannot be matched by time
    rest_lines = [ln for ln in targets if ln.id not in used_line]
    rest_track = [(ti, tl) for ti, tl in enumerate(track) if ti not in used_track]
    if any(tl.time_ms is not None for _, tl in rest_track) and any(
            ln.imported_start_ms is not None for ln in rest_lines):
        rest_track = [(ti, tl) for ti, tl in rest_track if tl.time_ms is None]
        rest_lines = [ln for ln in rest_lines if ln.imported_start_ms is None]
    for (ti, tl), ln in zip(rest_track, rest_lines):
        matched[ln.id] = PairItem(ln.id, ln.text, tl.text, "order")
        used_line.add(ln.id)
        used_track.add(ti)

    preview.pairs = [matched[ln.id] for ln in targets if ln.id in matched]
    preview.unmatched_line_ids = [ln.id for ln in targets if ln.id not in used_line]
    preview.unmatched_texts = [tl for ti, tl in enumerate(track) if ti not in used_track]
    return preview


def apply_pairs(doc: LyricsDoc, pairs: Iterable[tuple[str, str]], kind: TrackKind = "translation") -> LyricsDoc:
    """Return a copy of ``doc`` with the confirmed pairs applied."""
    new = doc.model_copy(deep=True)
    by_id = {ln.id: ln for ln in new.lines}
    for line_id, text in pairs:
        if line_id not in by_id:
            raise KeyError(f"没有歌词行 {line_id}")
        setattr(by_id[line_id], kind, text)
    return new


def _joiner(a: str, b: str) -> str:
    return " " if a and b and re.match(r"[A-Za-z0-9]", a[-1]) and re.match(r"[A-Za-z0-9]", b[0]) else ""


def merge_lines(doc: LyricsDoc, ids: list[str]) -> LyricsDoc:
    """Merge consecutive lines into one; the first start is kept, provenance recorded."""
    if len(ids) < 2:
        raise ValueError("至少选择两行才能合并")
    positions = [next(i for i, ln in enumerate(doc.lines) if ln.id == lid) for lid in ids]
    if positions != list(range(positions[0], positions[0] + len(ids))):
        raise ValueError("只能合并按顺序相邻的行")
    new = doc.model_copy(deep=True)
    parts = new.lines[positions[0]: positions[-1] + 1]
    first = parts[0]
    text, spaced = first.text, False
    for p in parts[1:]:
        j = _joiner(text, p.text)
        spaced = spaced or bool(j)
        text += j + p.text
    # segments stay valid only when their surfaces still concatenate to the text
    keep_segments = all(p.segments for p in parts) and not spaced
    merged = Line(
        id=new_id("L"),
        text=text,
        kind="lyric" if any(p.kind == "lyric" for p in parts) else first.kind,
        sing=any(p.sing for p in parts),
        segments=[s for p in parts for s in p.segments] if keep_segments else [],
        imported_start_ms=first.imported_start_ms,
        imported_end_ms=parts[-1].imported_end_ms,
        anchor=first.anchor,
        translation=" ".join(p.translation for p in parts if p.translation) or None,
        romanization=" ".join(p.romanization for p in parts if p.romanization) or None,
        voice=first.voice,
        source=LineSource(origin=first.source.origin, source_id=first.source.source_id,
                          raw_index=first.source.raw_index, merged_from=list(ids)),
    )
    new.lines[positions[0]: positions[-1] + 1] = [merged]
    return new


def split_line(doc: LyricsDoc, line_id: str, at_char: int) -> LyricsDoc:
    """Split a line at a character offset.

    The first part keeps the imported start and anchor; the second part gets
    *no* time (it is never interpolated).  Segments are kept only when the
    split falls on a segment boundary; otherwise both parts need readings again.
    """
    idx = next((i for i, ln in enumerate(doc.lines) if ln.id == line_id), None)
    if idx is None:
        raise KeyError(line_id)
    new = doc.model_copy(deep=True)
    line = new.lines[idx]
    if not 0 < at_char < len(line.text):
        raise ValueError("拆分位置必须在该行文字内部")
    left_text, right_text = line.text[:at_char], line.text[at_char:]
    left_segs, right_segs, pos = [], [], 0
    boundary_ok = False
    for s in line.segments:
        (left_segs if pos < at_char else right_segs).append(s)
        pos += len(s.surface)
        if pos == at_char:
            boundary_ok = True
    if not boundary_ok or pos != len(line.text):
        left_segs, right_segs = [], []
    src = dict(origin=line.source.origin, source_id=line.source.source_id,
               raw_index=line.source.raw_index, split_from=line.id)
    left = line.model_copy(update=dict(id=new_id("L"), text=left_text.rstrip(), segments=left_segs,
                                       imported_end_ms=None, source=LineSource(**src)))
    right = Line(id=new_id("L"), text=right_text.lstrip(), kind=line.kind, sing=line.sing,
                 segments=right_segs, imported_start_ms=None, imported_end_ms=line.imported_end_ms,
                 voice=line.voice, source=LineSource(**src))
    if left.text != left_text or right.text != right_text:
        left.segments, right.segments = [], []
    new.lines[idx: idx + 1] = [left, right]
    return new
