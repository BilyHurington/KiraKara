"""LRC first-onset calibration and global offset (design §4.2).

::

    base_i      = imported_start_i + embedded_shift
    effective_i = base_i + user_shift
    marking line k at marked_ms:  user_shift = marked_ms - base_k

Positive shifts move lyrics later.  ``user_shift`` is recomputed from scratch
on every mark (never accumulated).  A line with a manual ``anchor`` uses that
absolute original-audio time and does not follow the global shift.

The audio is never moved or trimmed; only lyric anchors are computed.
"""

from __future__ import annotations

from typing import Literal, Optional

from ..models import Calibration, CalibrationCheck, Issue, Line, LyricsDoc, stable_hash, utcnow

MISMATCH_THRESHOLD_MS = 250


def base_ms(doc: LyricsDoc, line: Line) -> Optional[int]:
    if line.imported_start_ms is None:
        return None
    return int(line.imported_start_ms) + int(doc.embedded_shift_ms)


def effective_ms(doc: LyricsDoc, cal: Calibration, line: Line) -> Optional[int]:
    if line.anchor is not None:
        return int(line.anchor.abs_ms)
    b = base_ms(doc, line)
    return None if b is None else b + int(cal.user_shift_ms)


def anchor_kind(line: Line) -> Literal["soft", "hard"]:
    return "hard" if (line.anchor is not None and line.anchor.hard) else "soft"


def effective_line_starts(doc: LyricsDoc, cal: Calibration) -> dict[str, tuple[int, str]]:
    """line_id -> (effective ms, 'soft'|'hard') for sung lines that have a time."""
    out: dict[str, tuple[int, str]] = {}
    for ln in doc.sung_lines():
        ms = effective_ms(doc, cal, ln)
        if ms is not None:
            out[ln.id] = (ms, anchor_kind(ln))
    return out


def effective_line_ends(doc: LyricsDoc, cal: Calibration) -> dict[str, int]:
    """line_id -> effective end hint for sung lines whose LRC marks an end.

    An end is marked either explicitly (``imported_end_ms``) or, as NetEase and
    most LRC files do before an interlude, by a *timed blank line* right after
    the lyric.  The hint moves with the line (global shift or manual anchor).
    It is only approximate: singers often hold the last note past it.
    """
    starts = effective_line_starts(doc, cal)
    out: dict[str, int] = {}
    lines = doc.lines
    for i, ln in enumerate(lines):
        if ln.id not in starts or ln.imported_start_ms is None:
            continue
        end = ln.imported_end_ms
        if end is None and i + 1 < len(lines):
            nxt = lines[i + 1]
            if nxt.kind == "blank" and not nxt.text.strip() and nxt.imported_start_ms is not None:
                end = nxt.imported_start_ms
        if end is None or end <= ln.imported_start_ms:
            continue
        out[ln.id] = starts[ln.id][0] + (int(end) - int(ln.imported_start_ms))
    return out


def _snapshot(cal: Calibration) -> dict:
    return {
        "user_shift_ms": cal.user_shift_ms,
        "confirmed": cal.confirmed,
        "reference_line_id": cal.reference_line_id,
        "marked_ms": cal.marked_ms,
        "at": utcnow(),
    }


def _with_history(cal: Calibration, action: str) -> Calibration:
    new = cal.model_copy(deep=True)
    snap = _snapshot(cal)
    snap["action"] = action
    new.history.append(snap)
    return new


def mark_first_onset(cal: Calibration, doc: LyricsDoc, line_id: str, marked_ms: int) -> Calibration:
    """Mark the first actual sung onset of ``line_id`` at ``marked_ms``."""
    line = doc.line(line_id)
    b = base_ms(doc, line)
    if b is None:
        raise ValueError(f"行 {line_id} 没有导入的 LRC 时间；请选择其他行或添加锚点")
    new = _with_history(cal, "mark")
    new.user_shift_ms = int(marked_ms) - b
    new.reference_line_id = line_id
    new.marked_ms = int(marked_ms)
    new.confirmed = True
    new.checks = [_recheck(doc, new, c) for c in new.checks]
    return new


def confirm_zero(cal: Calibration) -> Calibration:
    new = _with_history(cal, "confirm_zero")
    new.user_shift_ms = 0
    new.reference_line_id = None
    new.marked_ms = None
    new.confirmed = True
    return new


def set_user_shift(cal: Calibration, shift_ms: int, doc: Optional[LyricsDoc] = None) -> Calibration:
    """Numeric fine adjustment of the global shift (absolute value, not a delta)."""
    new = _with_history(cal, "set_shift")
    new.user_shift_ms = int(shift_ms)
    new.confirmed = True
    if doc is not None:
        new.checks = [_recheck(doc, new, c) for c in new.checks]
    return new


def undo(cal: Calibration) -> Calibration:
    if not cal.history:
        return cal
    new = cal.model_copy(deep=True)
    snap = new.history.pop()
    new.user_shift_ms = snap["user_shift_ms"]
    new.confirmed = snap["confirmed"]
    new.reference_line_id = snap["reference_line_id"]
    new.marked_ms = snap["marked_ms"]
    return new


def _recheck(doc: LyricsDoc, cal: Calibration, c: CalibrationCheck) -> CalibrationCheck:
    eff = effective_ms(doc, cal, doc.line(c.line_id))
    return CalibrationCheck(line_id=c.line_id, marked_ms=c.marked_ms,
                            residual_ms=0 if eff is None else c.marked_ms - eff)


def add_check(
    cal: Calibration, doc: LyricsDoc, line_id: str, marked_ms: int, threshold_ms: int = MISMATCH_THRESHOLD_MS
) -> tuple[Calibration, list[Issue]]:
    """Mark another (middle / end) line to verify that a single shift fits.

    Never stretches time; a large residual only produces a warning suggesting a
    different version / tempo and offering per-line anchors.
    """
    line = doc.line(line_id)
    eff = effective_ms(doc, cal, line)
    if eff is None:
        raise ValueError(f"行 {line_id} 没有可用于检查的时间")
    new = cal.model_copy(deep=True)
    new.checks = [c for c in new.checks if c.line_id != line_id]
    new.checks.append(CalibrationCheck(line_id=line_id, marked_ms=int(marked_ms), residual_ms=int(marked_ms) - eff))
    return new, check_issues(new, threshold_ms)


def check_issues(cal: Calibration, threshold_ms: int = MISMATCH_THRESHOLD_MS) -> list[Issue]:
    issues = []
    for c in cal.checks:
        if abs(c.residual_ms) > threshold_ms:
            issues.append(Issue(
                code="calibration_mismatch", severity="warning", line_id=c.line_id,
                message=(f"标记的首音与校准后的 LRC 时间相差 {c.residual_ms:+d} ms；"
                         "歌词可能对应其他版本或速度。请添加单行锚点，"
                         "程序不会拉伸时间轴。"),
                data={"residual_ms": c.residual_ms},
            ))
    return issues


def validate_anchors(doc: LyricsDoc, cal: Calibration, audio_duration_ms: Optional[int]) -> list[Issue]:
    """Errors for anchors that must be corrected (never silently clipped)."""
    issues: list[Issue] = []
    sung = doc.sung_lines()
    starts = effective_line_starts(doc, cal)
    if sung and not starts:
        issues.append(Issue(code="lrc_no_times", severity="error",
                            message="LRC 增强模式需要行时间；请补充时间或切换到普通模式"))
        return issues
    last: dict[str, tuple[str, int]] = {}
    for ln in sung:
        if ln.id not in starts:
            continue
        ms, _ = starts[ln.id]
        if ms < 0:
            issues.append(Issue(code="anchor_negative", severity="error", line_id=ln.id,
                                message=f"有效句首 {ms} ms 早于音频开头", data={"ms": ms}))
        if audio_duration_ms is not None and ms >= audio_duration_ms:
            issues.append(Issue(code="anchor_out_of_range", severity="error", line_id=ln.id,
                                message=f"有效句首 {ms} ms 超出音频结尾（{audio_duration_ms} ms）",
                                data={"ms": ms, "duration_ms": audio_duration_ms}))
        prev = last.get(ln.voice)
        if prev is not None and ms < prev[1]:
            issues.append(Issue(code="anchor_order_conflict", severity="error", line_id=ln.id,
                                message=f"起点 {ms} ms 早于上一行 {prev[0]}（{prev[1]} ms）",
                                data={"ms": ms, "previous_line_id": prev[0], "previous_ms": prev[1]}))
        last[ln.voice] = (ln.id, ms)
    return issues


def calibration_hash(doc: LyricsDoc, cal: Calibration) -> str:
    return stable_hash({
        "embedded_shift_ms": doc.embedded_shift_ms,
        "user_shift_ms": cal.user_shift_ms,
        "starts": sorted((k, v[0], v[1]) for k, v in effective_line_starts(doc, cal).items()),
    })
