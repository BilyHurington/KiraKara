"""Reading preparation: fill Line.segments with rule readings, manual edits.

Priority (design §4.1): manual confirmed > AI > rules.  Rules never overwrite
confirmed / manual / AI segments unless the line text itself changed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from ..models import Line, LyricsDoc, Segment, Unit
from . import chinese, english, japanese
from .japanese import is_kana_text, split_morae, to_hiragana

PROTECTED_SOURCES = ("manual", "ai")


def detect_lang(text: str, hint: Optional[str] = None) -> str:
    if any(0x3041 <= ord(c) <= 0x30FF for c in text):
        return "ja"
    has_han = any(japanese.is_kanji(c) for c in text)
    if has_han:
        return hint if hint in ("ja", "zh") else "ja"
    if re.search(r"[A-Za-z]", text):
        return "en" if hint not in ("ja", "zh") else hint
    return hint or "other"


def rule_segments_for(text: str, lang: str) -> list[Segment]:
    if lang == "zh":
        return chinese.rule_segments(text)
    if lang == "en":
        return english.rule_segments(text)
    return japanese.rule_segments(text)


def _surface(line: Line) -> str:
    return "".join(s.surface for s in line.segments)


def _is_protected(seg: Segment) -> bool:
    return seg.confirmed or seg.reading_source in PROTECTED_SOURCES


@dataclass
class PrepareReport:
    prepared: list[str] = field(default_factory=list)  # line ids filled by rules
    kept: list[str] = field(default_factory=list)  # protected lines left alone
    rederived: list[str] = field(default_factory=list)  # protected but text changed -> re-derived
    messages: list[str] = field(default_factory=list)


def prepare_line(line: Line, lang_hint: Optional[str] = None, *, overwrite_rule: bool = True,
                 report: Optional[PrepareReport] = None) -> Line:
    """Fill ``line.segments`` in place and return the line."""
    report = report if report is not None else PrepareReport()
    if line.kind != "lyric" or not line.sing:
        return line
    text_changed = bool(line.segments) and _surface(line) != line.text
    protected = [s for s in line.segments if _is_protected(s)]
    if line.segments and not text_changed:
        if protected:
            # keep protected segments, re-derive only unprotected rule segments if asked
            if overwrite_rule:
                _refresh_unprotected(line, lang_hint)
            report.kept.append(line.id)
            return line
        if not overwrite_rule:
            return line
    lang = detect_lang(line.text, lang_hint)
    new_segs = rule_segments_for(line.text, lang)
    if line.segments and not text_changed:
        _reuse_ids(line.segments, new_segs)
    line.segments = new_segs
    if text_changed and protected:
        report.rederived.append(line.id)
        report.messages.append(f"{line.id}: 文本已变更，人工/AI 读音已按规则重新生成，需要重新确认")
    else:
        report.prepared.append(line.id)
    return line


def _refresh_unprotected(line: Line, lang_hint: Optional[str]) -> None:
    lang = detect_lang(line.text, lang_hint)
    out: list[Segment] = []
    for seg in line.segments:
        if _is_protected(seg) or seg.reading_source not in ("rule", "none"):
            out.append(seg)
            continue
        fresh = rule_segments_for(seg.surface, seg.lang if seg.lang in ("ja", "zh", "en") else lang)
        _reuse_ids([seg], fresh)
        out.extend(fresh)
    line.segments = out


def _reuse_ids(old: Sequence[Segment], new: Sequence[Segment]) -> None:
    """Keep ids of segments / units whose surface and unit grouping are unchanged."""
    pool = {}
    for s in old:
        pool.setdefault((s.surface, tuple(u.reading for u in s.units)), []).append(s)
    for s in new:
        key = (s.surface, tuple(u.reading for u in s.units))
        if pool.get(key):
            o = pool[key].pop(0)
            s.id = o.id
            for nu, ou in zip(s.units, o.units):
                nu.id = ou.id


def prepare_doc(doc: LyricsDoc, *, overwrite_rule: bool = True) -> PrepareReport:
    report = PrepareReport()
    for ln in doc.lines:
        prepare_line(ln, doc.language, overwrite_rule=overwrite_rule, report=report)
    return report


def units_from_spec(reading: str, units: Optional[Sequence[str]], lang: str) -> list[Unit]:
    if units is None:
        if lang == "ja" or is_kana_text(reading):
            return japanese.reading_units(to_hiragana(reading))
        return [Unit(reading=reading)]
    if "".join(units) != reading:
        raise ValueError(f"units {list(units)!r} do not join to reading {reading!r}")
    out = []
    for u in units:
        flags = []
        if lang == "ja":
            m = split_morae(u)
            flags = sorted({f for x in m for f in x.flags})
            u = to_hiragana(u)
        out.append(Unit(reading=u, flags=flags))
    return out


def _assign_surfaces(seg: Segment) -> None:
    """For pure kana segments with 1:1 unit mapping keep per-unit surfaces."""
    if is_kana_text(seg.surface) and to_hiragana(seg.surface) == "".join(u.reading for u in seg.units):
        pos = 0
        for u in seg.units:
            u.surface = seg.surface[pos:pos + len(u.reading)]
            pos += len(u.reading)
    else:
        for u in seg.units:
            u.surface = ""


def replace_units_keep_ids(seg: Segment, new_units: list[Unit]) -> None:
    """Replace units, preserving ids only when the grouping is unchanged."""
    if [u.reading for u in seg.units] == [u.reading for u in new_units]:
        for nu, ou in zip(new_units, seg.units):
            nu.id = ou.id
    seg.units = new_units
    _assign_surfaces(seg)


def set_segment_reading(line: Line, segment_id: str, reading: str, units: Optional[Sequence[str]] = None,
                        source: str = "manual", confirm: bool = True) -> Segment:
    seg = next((s for s in line.segments if s.id == segment_id), None)
    if seg is None:
        raise KeyError(segment_id)
    if seg.lang == "ja":
        reading = to_hiragana(reading)
    new_units = units_from_spec(reading, units, seg.lang)
    replace_units_keep_ids(seg, new_units)
    seg.reading = reading
    seg.reading_source = source  # type: ignore[assignment]
    seg.confirmed = confirm
    seg.uncertain = False
    return seg


def resegment_line(line: Line, segments_spec: Sequence[dict]) -> Line:
    """Replace a line's segmentation.

    ``segments_spec`` items: ``{"surface", "reading"?, "units"?, "lang"?, "confirmed"?}``.
    Surfaces must concatenate to ``line.text``.  Ids are reused for unchanged segments.
    """
    surfaces = [str(s.get("surface", "")) for s in segments_spec]
    if "".join(surfaces) != line.text:
        raise ValueError("segment surfaces do not concatenate to the line text")
    new: list[Segment] = []
    for spec in segments_spec:
        surface = spec["surface"]
        lang = spec.get("lang") or detect_lang(surface, "ja")
        reading = spec.get("reading")
        if reading:
            if lang == "ja":
                reading = to_hiragana(reading)
            units = units_from_spec(reading, spec.get("units"), lang)
            seg = Segment(surface=surface, reading=reading, lang=lang, units=units, reading_source="manual",
                          confirmed=bool(spec.get("confirmed", True)))
        else:
            seg = Segment(surface=surface, reading=None, lang=lang, units=[], reading_source="none")
        _assign_surfaces(seg)
        new.append(seg)
    _reuse_ids(line.segments, new)
    line.segments = new
    return line


def capability_warnings(doc: LyricsDoc, backend_langs: Sequence[str]) -> list[str]:
    """Warn when segments use languages the acoustic model does not cover."""
    langs = set(backend_langs)
    counts: dict[str, int] = {}
    missing: list[str] = []
    for ln in doc.sung_lines():
        for s in ln.segments:
            if s.units and s.lang not in langs:
                counts[s.lang] = counts.get(s.lang, 0) + 1
            if not s.units and s.surface.strip() and s.uncertain:
                missing.append(f"{ln.id}:{s.surface}")
    msgs = []
    names = {"zh": "中文", "en": "英文", "ja": "日语", "other": "其他语言"}
    for lang, n in sorted(counts.items()):
        msgs.append(f"{n} 个{names.get(lang, lang)}片段不在当前模型支持的语言内（{', '.join(sorted(langs))}），"
                    f"将按固定转写送入模型，结果可能不可靠；不会强制日语化")
    if missing:
        msgs.append(f"{len(missing)} 个片段缺少读音，将不参与对齐: {', '.join(missing[:10])}")
    return msgs
