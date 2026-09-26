"""Diagnostic checks on alignment results (design §4.6).

All thresholds come from :class:`CheckConfig`.  Checks only produce issues and
flags; they never claim a confidence probability.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import CheckConfig, Issue, LineTiming, UnitTiming
from .activity import VocalActivity


def _flag(obj, code: str) -> None:
    if code not in obj.flags:
        obj.flags.append(code)


def check_units(units: list[UnitTiming], cfg: CheckConfig) -> list[Issue]:
    issues: list[Issue] = []
    for u in units:
        if u.start_ms is None or u.end_ms is None:
            if (u.start_ms is None) != (u.end_ms is None):
                issues.append(Issue(code="illegal_interval", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                    message="单元只有起点或终点之一"))
            continue
        d = u.end_ms - u.start_ms
        if d <= 0:
            _flag(u, "illegal_interval")
            issues.append(Issue(code="illegal_interval", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"终点 {u.end_ms} ≤ 起点 {u.start_ms}"))
        elif d < cfg.min_unit_ms:
            _flag(u, "short_unit")
            issues.append(Issue(code="short_unit", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"单元「{u.reading}」只有 {d} ms", data={"duration_ms": d}))
        else:
            # both can hold for one unit: a token gap does not hide an overlong unit
            if "token_gap" in u.flags:
                issues.append(Issue(code="token_gap", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                    message=f"单元「{u.reading}」的 token 之间有长停顿（共 {d} ms）；"
                                            "读音可能与演唱不符", data={"duration_ms": d}))
            if d > cfg.max_unit_ms:
                _flag(u, "long_unit")
                issues.append(Issue(code="long_unit", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                    message=f"单元「{u.reading}」持续 {d} ms", data={"duration_ms": d}))
    return issues


def check_line_gaps(units: list[UnitTiming], cfg: CheckConfig) -> list[Issue]:
    """A long pause between two consecutive units of the same line."""
    issues: list[Issue] = []
    prev: dict[str, UnitTiming] = {}
    for u in units:
        if u.start_ms is None or u.end_ms is None:
            continue
        p = prev.get(u.line_id)
        prev[u.line_id] = u
        if p is None or p.end_ms is None:
            continue
        gap = u.start_ms - p.end_ms
        if gap > cfg.max_line_gap_ms:
            _flag(u, "line_gap")
            issues.append(Issue(code="line_gap", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"「{p.reading}」与「{u.reading}」之间停顿 {gap / 1000:.1f} 秒；"
                                        "可能被放进了间奏", data={"gap_ms": gap}))
    return issues


def check_rest(units: list[UnitTiming], activity: Optional[VocalActivity]) -> list[Issue]:
    """A unit placed where the separated vocal stem is silent."""
    if activity is None:
        return []
    issues: list[Issue] = []
    for u in units:
        if u.start_ms is None or u.end_ms is None or u.end_ms <= u.start_ms:
            continue
        if activity.is_rest(u.start_ms, u.end_ms):
            _flag(u, "in_rest")
            issues.append(Issue(code="unit_in_rest", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"单元「{u.reading}」所在位置人声分轨几乎无声"
                                        f"（{activity.peak_db(u.start_ms, u.end_ms):.0f} dB，"
                                        f"演唱约 {activity.reference_db:.0f} dB）",
                                data={"peak_db": round(activity.peak_db(u.start_ms, u.end_ms), 1)}))
    return issues


def alignable(u: UnitTiming) -> bool:
    """Units without model tokens (``unaligned``: a っ at the end of a line, a character the model
    cannot spell) can never get a time; they are reported once when tokens are prepared and do not
    count as missing coverage (no incomplete line, no retries for them)."""
    return u.status != "unaligned" or u.start_ms is not None


def check_coverage(units: list[UnitTiming], lines: list[LineTiming], cfg: CheckConfig) -> tuple[list[Issue], float]:
    issues: list[Issue] = []
    units = [u for u in units if alignable(u)]
    total = len(units)
    timed = sum(1 for u in units if u.start_ms is not None and u.end_ms is not None)
    cov = timed / total if total else 1.0
    by_line: dict[str, list[UnitTiming]] = {}
    for u in units:
        by_line.setdefault(u.line_id, []).append(u)
    for lt in lines:
        us = by_line.get(lt.line_id, [])
        missing = [u for u in us if u.start_ms is None]
        if us and missing:
            _flag(lt, "incomplete")
            issues.append(Issue(code="line_incomplete", severity="error" if len(missing) == len(us) else "warning",
                                line_id=lt.line_id,
                                message=f"{len(missing)}/{len(us)} units have no time",
                                data={"unit_ids": [u.unit_id for u in missing]}))
    if cov < cfg.min_coverage:
        issues.append(Issue(code="low_coverage", severity="warning",
                            message=f"只有 {timed}/{total} 个单元得到时间", data={"coverage": cov}))
    return issues, cov


def check_lines(lines: list[LineTiming], cfg: CheckConfig, voices: Optional[dict[str, str]] = None,
                audio_end_ms: Optional[float] = None) -> list[Issue]:
    """Line-level checks.  ``audio_end_ms``: where the audio (the emission) ends; a window that
    ends there has no later lyrics behind its right edge."""
    issues: list[Issue] = []
    last: dict[str, LineTiming] = {}
    for lt in lines:
        if lt.anchor_residual_ms is not None and abs(lt.anchor_residual_ms) > cfg.anchor_deviation_ms:
            _flag(lt, "anchor_deviation")
            issues.append(Issue(code="anchor_deviation", severity="warning", line_id=lt.line_id,
                                message=f"句首与 LRC 锚点相差 {lt.anchor_residual_ms:+d} ms",
                                data={"residual_ms": lt.anchor_residual_ms}))
        if lt.window_ms is not None and lt.start_ms is not None and lt.end_ms is not None:
            w0, w1 = lt.window_ms
            if w0 > 0 and lt.start_ms - w0 < cfg.edge_crowd_ms:
                _flag(lt, "window_edge")
                issues.append(Issue(code="window_edge", severity="warning", line_id=lt.line_id,
                                    message="句首贴在解码窗口左边缘",
                                    data={"window_ms": [w0, w1]}))
            # the right edge is next anchor + margin, only a search bound: touching it
            # means the line ran into audio that belongs to later lines (not when the window
            # simply ends with the audio: a song cut right after its last note)
            at_audio_end = audio_end_ms is not None and w1 >= audio_end_ms - 1
            if lt.end_ms >= w1 - cfg.edge_crowd_ms and not at_audio_end:
                _flag(lt, "window_edge")
                issues.append(Issue(code="window_edge", severity="warning", line_id=lt.line_id,
                                    message="句尾贴在解码窗口右边缘",
                                    data={"window_ms": [w0, w1]}))
        v = (voices or {}).get(lt.line_id, "main")
        prev = last.get(v)
        if prev is not None and prev.end_ms is not None and lt.start_ms is not None:
            if lt.start_ms < prev.start_ms:  # type: ignore[operator]
                _flag(lt, "order_conflict")
                issues.append(Issue(code="order_conflict", severity="error", line_id=lt.line_id,
                                    message=f"句首早于上一行 {prev.line_id}",
                                    data={"previous_line_id": prev.line_id}))
            elif lt.start_ms < prev.end_ms:
                _flag(lt, "line_overlap")
                issues.append(Issue(code="line_overlap", severity="warning", line_id=lt.line_id,
                                    message=f"与上一行 {prev.line_id} 重叠 {prev.end_ms - lt.start_ms} ms",
                                    data={"previous_line_id": prev.line_id, "overlap_ms": prev.end_ms - lt.start_ms}))
        if lt.start_ms is not None:
            last[v] = lt
    return issues


def check_unit_order(units: list[UnitTiming], voices: Optional[dict[str, str]] = None) -> list[Issue]:
    issues = []
    last: dict[str, UnitTiming] = {}
    for u in units:
        if u.start_ms is None:
            continue
        v = (voices or {}).get(u.line_id, "main")
        p = last.get(v)
        if p is not None and p.end_ms is not None and u.start_ms < p.end_ms and p.line_id == u.line_id:
            _flag(u, "unit_overlap")
            issues.append(Issue(code="unit_overlap", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"单元在前一单元结束前开始（{u.start_ms} < {p.end_ms}）"))
        last[v] = u
    return issues


def stability_issues(base: dict[str, Optional[int]], alt: dict[str, Optional[int]], tolerance_ms: int,
                     lines: Iterable[LineTiming] = ()) -> list[Issue]:
    """Compare line starts decoded with different context."""
    by_id = {lt.line_id: lt for lt in lines}
    issues = []
    for lid, b in base.items():
        a = alt.get(lid)
        if b is None or a is None:
            continue
        if abs(a - b) > tolerance_ms:
            if lid in by_id:
                _flag(by_id[lid], "unstable")
            issues.append(Issue(code="unstable_boundary", severity="warning", line_id=lid,
                                message=f"改变解码上下文后句首移动 {a - b:+d} ms",
                                data={"base_ms": b, "alt_ms": a}))
    return issues


def run_checks(units: list[UnitTiming], lines: list[LineTiming], cfg: CheckConfig,
               voices: Optional[dict[str, str]] = None,
               activity: Optional[VocalActivity] = None,
               audio_end_ms: Optional[float] = None) -> tuple[list[Issue], float]:
    cov_issues, cov = check_coverage(units, lines, cfg)
    issues = (check_units(units, cfg) + check_line_gaps(units, cfg) + check_rest(units, activity) + cov_issues
              + check_lines(lines, cfg, voices, audio_end_ms) + check_unit_order(units, voices))
    return issues, cov


# codes produced by the checks above (recomputed when unit times change, e.g. adopting a rerun)
CHECK_CODES = {"illegal_interval", "short_unit", "token_gap", "long_unit", "line_gap", "unit_in_rest",
               "line_incomplete", "low_coverage", "anchor_deviation", "window_edge", "order_conflict",
               "line_overlap", "unit_overlap"}


# issue codes that a retry could plausibly improve
RETRYABLE = {"short_unit", "token_gap", "long_unit", "anchor_deviation", "window_edge", "line_overlap", "order_conflict",
             "unstable_boundary", "line_incomplete", "decode_failed", "boundary_conflict", "line_gap",
             "unit_in_rest"}
