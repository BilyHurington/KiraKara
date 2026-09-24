"""Export formats.

``alignment.json`` is the complete standard output; every other format may
lose information and returns explicit loss warnings.  Times written here are
already absolute on the original audio timeline — exports never add an offset
again.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from typing import Optional

from ..models import (
    FMT_PREPARED, SCHEMA_VERSION, AlignmentResult, Calibration, LyricsDoc, Project, UnitTiming,
)

EXPORT_FORMATS = {
    "alignment": ("alignment.json", "application/json", "标准结果（完整信息）"),
    "prepared": ("prepared.json", "application/json", "可编辑的歌词与读音"),
    "project": ("project.json", "application/json", "完整项目文件"),
    "csv": ("alignment.csv", "text/csv", "逐单元 CSV"),
    "lrc-line": ("aligned-line.lrc", "text/plain", "模型对齐后聚合的行级 LRC"),
    "lrc-unit": ("aligned-unit.lrc", "text/plain", "模型对齐后的逐单元（增强）LRC"),
    "lrc-calibrated": ("calibrated.lrc", "text/plain", "仅校准原锚点的 LRC（已清除 offset）"),
    "karaoke-ass": ("karaoke.ass", "text/plain", "卡拉OK字幕 ASS（使用“卡拉OK字幕”页的样式）"),
}


@dataclass
class ExportOutput:
    filename: str
    media_type: str
    content: str
    warnings: list[str] = field(default_factory=list)


def fmt_lrc_ts(ms: int) -> str:
    """``[mm:ss.xx]`` body; centiseconds rounded half-up from integer ms."""
    if ms < 0:
        raise ValueError("LRC 不能表示负时间")
    cs = (ms + 5) // 10
    m, rem = divmod(cs, 6000)
    s, c = divmod(rem, 100)
    return f"{m:02d}:{s:02d}.{c:02d}"


def _lines_payload(doc: LyricsDoc) -> list[dict]:
    return [ln.model_dump(mode="json") for ln in doc.lines]


def export_alignment(project: Project, result: AlignmentResult) -> ExportOutput:
    payload = result.model_dump(mode="json")
    payload["lyrics"] = {
        "language": project.lyrics.language,
        "meta": project.lyrics.meta.model_dump(mode="json"),
        "lines": _lines_payload(project.lyrics),
    }
    payload["audio"] = [a.model_dump(mode="json", exclude={"path"}) for a in project.audio]
    warnings = []
    if result.stale:
        warnings.append(f"该结果已过期：{result.stale_reason or '输入已修改'}")
    if not result.coverage.full:
        warnings.append("该结果仅覆盖部分歌词（见 coverage）")
    return ExportOutput("alignment.json", "application/json",
                        json.dumps(payload, ensure_ascii=False, indent=2), warnings)


def export_prepared(project: Project) -> ExportOutput:
    doc = project.lyrics
    payload = {
        "format": FMT_PREPARED,
        "version": SCHEMA_VERSION,
        "language": doc.language,
        "meta": doc.meta.model_dump(mode="json"),
        "embedded_offset_raw": doc.embedded_offset_raw,
        "embedded_shift_ms": doc.embedded_shift_ms,
        "embedded_offset_note": doc.embedded_offset_note,
        "calibration": project.calibration.model_dump(mode="json"),
        "lines": _lines_payload(doc),
    }
    return ExportOutput("prepared.json", "application/json", json.dumps(payload, ensure_ascii=False, indent=2))


def _unit_index(project: Project) -> dict[str, tuple[int, str, str]]:
    idx = {}
    for li, ln in enumerate(project.lyrics.lines):
        for seg in ln.segments:
            for u in seg.units:
                idx[u.id] = (li, seg.surface, u.surface)
    return idx


def export_csv(project: Project, result: AlignmentResult) -> ExportOutput:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["line_index", "line_id", "line_text", "segment_surface", "unit_id", "unit_surface", "reading",
                "start_ms", "end_ms", "status", "reason", "model_start_ms", "model_end_ms",
                "manual_locked", "tail_method", "flags"])
    idx = _unit_index(project)
    texts = {ln.id: ln.text for ln in project.lyrics.lines}
    for ut in result.units:
        li, seg_surface, unit_surface = idx.get(ut.unit_id, (-1, "", ""))
        w.writerow([li, ut.line_id, texts.get(ut.line_id, ""), seg_surface, ut.unit_id, unit_surface, ut.reading,
                    _blank(ut.start_ms), _blank(ut.end_ms), ut.status, ut.reason or "",
                    _blank(ut.model_start_ms), _blank(ut.model_end_ms),
                    "1" if ut.locked else "", ut.tail.method if ut.tail else "", ";".join(ut.flags)])
    warnings = ["CSV 不包含锚点、候选、问题列表与模型信息；完整信息请使用 alignment.json"]
    return ExportOutput("alignment.csv", "text/csv", buf.getvalue(), warnings)


def _blank(v: Optional[int]) -> str:
    return "" if v is None else str(v)


def _line_units(result: AlignmentResult) -> dict[str, list[UnitTiming]]:
    out: dict[str, list[UnitTiming]] = {}
    for ut in result.units:
        out.setdefault(ut.line_id, []).append(ut)
    return out


def _meta_tags(doc: LyricsDoc) -> list[str]:
    tags = []
    for key, val in (("ti", doc.meta.title), ("ar", doc.meta.artist), ("al", doc.meta.album)):
        if val:
            tags.append(f"[{key}:{val}]")
    return tags


def export_lrc_line(project: Project, result: AlignmentResult) -> ExportOutput:
    """Line LRC aggregated from the model alignment (first aligned unit start)."""
    by_line = _line_units(result)
    out = _meta_tags(project.lyrics)
    warnings = ["行级 LRC 无法表达单元时间、终点、读音映射与失败原因"]
    missing = []
    for ln in project.lyrics.lines:
        if ln.id not in by_line:
            continue
        starts = [u.start_ms for u in by_line[ln.id] if u.start_ms is not None]
        if not starts:
            missing.append(ln.text)
            continue
        out.append(f"[{fmt_lrc_ts(min(starts))}]{ln.text}")
    if missing:
        warnings.append(f"{len(missing)} 行没有可用时间，已从 LRC 中省略（详见 alignment.json）")
    if not result.coverage.full:
        warnings.append("结果只覆盖部分歌词，LRC 仅包含已覆盖的行")
    return ExportOutput("aligned-line.lrc", "text/plain", "\n".join(out) + "\n", warnings)


def export_lrc_unit(project: Project, result: AlignmentResult) -> ExportOutput:
    """Enhanced LRC: ``[line]<t>text<t>text…<end>``.

    Units of one segment that has no 1:1 surface mapping (e.g. kanji with a
    multi-mora reading) are written as one timed chunk for the segment.
    """
    timings = {u.unit_id: u for u in result.units}
    out = _meta_tags(project.lyrics)
    warnings = ["增强 LRC 以片段为单位合并多拍汉字读音；无法表达读音、间隙与失败原因"]
    gaps = 0
    for ln in project.lyrics.lines:
        if not any(u.id in timings for u in ln.units()):
            continue
        parts: list[str] = []
        line_start: Optional[int] = None
        last_end: Optional[int] = None
        pending_text = ""
        for seg in ln.segments:
            if not seg.units:
                pending_text += seg.surface
                continue
            one_to_one = all(u.surface for u in seg.units) and "".join(u.surface for u in seg.units) == seg.surface
            chunks = ([(u.surface, [u]) for u in seg.units] if one_to_one else [(seg.surface, seg.units)])
            for text, units in chunks:
                ts = [timings.get(u.id) for u in units]
                starts = [t.start_ms for t in ts if t and t.start_ms is not None]
                ends = [t.end_ms for t in ts if t and t.end_ms is not None]
                if not starts:
                    gaps += 1
                    pending_text += text
                    continue
                st = min(starts)
                if line_start is None:
                    line_start = st
                    parts.append(pending_text)
                else:
                    parts[-1] += pending_text
                pending_text = ""
                parts.append(f"<{fmt_lrc_ts(st)}>{text}")
                if ends:
                    last_end = max(ends)
        if line_start is None:
            continue
        if pending_text:
            parts[-1] += pending_text
        tail = f"<{fmt_lrc_ts(last_end)}>" if last_end is not None else ""
        out.append(f"[{fmt_lrc_ts(line_start)}]" + "".join(parts) + tail)
    if gaps:
        warnings.append(f"{gaps} 个片段没有时间，文字并入前一个时间标签")
    return ExportOutput("aligned-unit.lrc", "text/plain", "\n".join(out) + "\n", warnings)


def export_lrc_calibrated(project: Project) -> ExportOutput:
    """LRC with calibrated effective line starts; embedded [offset] is removed.

    Re-importing this file therefore never shifts the times again.
    """
    from ..align.calibration import effective_line_starts

    doc = project.lyrics
    starts = effective_line_starts(doc, project.calibration)
    out = _meta_tags(doc)
    warnings = ["仅校准了原始行锚点（整体平移），不含模型对齐结果；已清除 [offset]"]
    if not project.calibration.confirmed:
        warnings.append("校准尚未确认")
    skipped = 0
    for ln in doc.lines:
        if ln.id not in starts:
            if ln.kind == "lyric":
                skipped += 1
            continue
        ms, _kind = starts[ln.id]
        if ms < 0:
            raise ValueError(f"行 {ln.id} 的校准时间为负，请先修正校准")
        out.append(f"[{fmt_lrc_ts(ms)}]{ln.text}")
    if skipped:
        warnings.append(f"{skipped} 行没有时间锚点，已省略")
    return ExportOutput("calibrated.lrc", "text/plain", "\n".join(out) + "\n", warnings)


def export(project: Project, fmt: str, result: Optional[AlignmentResult] = None) -> ExportOutput:
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"未知导出格式: {fmt}")
    if fmt == "prepared":
        return export_prepared(project)
    if fmt == "project":
        return ExportOutput("project.json", "application/json", project.model_dump_json(indent=2))
    if fmt == "lrc-calibrated":
        return export_lrc_calibrated(project)
    result = result or project.result()
    if result is None:
        raise ValueError("没有可导出的对齐结果")
    return {
        "alignment": export_alignment,
        "csv": export_csv,
        "lrc-line": export_lrc_line,
        "lrc-unit": export_lrc_unit,
    }[fmt](project, result)
