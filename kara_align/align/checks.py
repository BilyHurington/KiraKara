"""Diagnostic checks on alignment results (design §4.6).

All thresholds come from :class:`CheckConfig`.  Checks only produce issues and
flags; they never claim a confidence probability.
"""

from __future__ import annotations

from typing import Iterable, Optional

from ..models import CheckConfig, Issue, LineTiming, UnitTiming


def _flag(obj, code: str) -> None:
    if code not in obj.flags:
        obj.flags.append(code)


def check_units(units: list[UnitTiming], cfg: CheckConfig) -> list[Issue]:
    issues: list[Issue] = []
    for u in units:
        if u.start_ms is None or u.end_ms is None:
            if (u.start_ms is None) != (u.end_ms is None):
                issues.append(Issue(code="illegal_interval", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                    message="unit has only one of start/end"))
            continue
        d = u.end_ms - u.start_ms
        if d <= 0:
            _flag(u, "illegal_interval")
            issues.append(Issue(code="illegal_interval", severity="error", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"end {u.end_ms} <= start {u.start_ms}"))
        elif d < cfg.min_unit_ms:
            _flag(u, "short_unit")
            issues.append(Issue(code="short_unit", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"unit '{u.reading}' lasts only {d} ms", data={"duration_ms": d}))
        elif d > cfg.max_unit_ms:
            _flag(u, "long_unit")
            issues.append(Issue(code="long_unit", severity="warning", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"unit '{u.reading}' lasts {d} ms", data={"duration_ms": d}))
    return issues


def check_coverage(units: list[UnitTiming], lines: list[LineTiming], cfg: CheckConfig) -> tuple[list[Issue], float]:
    issues: list[Issue] = []
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
                            message=f"only {timed}/{total} units are aligned", data={"coverage": cov}))
    return issues, cov


def check_lines(lines: list[LineTiming], cfg: CheckConfig, voices: Optional[dict[str, str]] = None) -> list[Issue]:
    issues: list[Issue] = []
    last: dict[str, LineTiming] = {}
    for lt in lines:
        if lt.anchor_residual_ms is not None and abs(lt.anchor_residual_ms) > cfg.anchor_deviation_ms:
            _flag(lt, "anchor_deviation")
            issues.append(Issue(code="anchor_deviation", severity="warning", line_id=lt.line_id,
                                message=f"line start deviates {lt.anchor_residual_ms:+d} ms from its LRC anchor",
                                data={"residual_ms": lt.anchor_residual_ms}))
        if lt.window_ms is not None and lt.start_ms is not None and lt.end_ms is not None:
            w0, w1 = lt.window_ms
            if w0 > 0 and lt.start_ms - w0 < cfg.edge_crowd_ms:
                _flag(lt, "window_edge")
                issues.append(Issue(code="window_edge", severity="warning", line_id=lt.line_id,
                                    message="line starts at the left edge of its decode window",
                                    data={"window_ms": [w0, w1]}))
            # the right edge is next anchor + margin, only a search bound: touching it
            # means the line ran into audio that belongs to later lines
            if lt.end_ms >= w1 - cfg.edge_crowd_ms:
                _flag(lt, "window_edge")
                issues.append(Issue(code="window_edge", severity="warning", line_id=lt.line_id,
                                    message="line ends at the right edge of its decode window",
                                    data={"window_ms": [w0, w1]}))
        v = (voices or {}).get(lt.line_id, "main")
        prev = last.get(v)
        if prev is not None and prev.end_ms is not None and lt.start_ms is not None:
            if lt.start_ms < prev.start_ms:  # type: ignore[operator]
                _flag(lt, "order_conflict")
                issues.append(Issue(code="order_conflict", severity="error", line_id=lt.line_id,
                                    message=f"line starts before previous line {prev.line_id}",
                                    data={"previous_line_id": prev.line_id}))
            elif lt.start_ms < prev.end_ms:
                _flag(lt, "line_overlap")
                issues.append(Issue(code="line_overlap", severity="warning", line_id=lt.line_id,
                                    message=f"overlaps previous line {prev.line_id} by {prev.end_ms - lt.start_ms} ms",
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
                                message=f"unit starts before previous unit ends ({u.start_ms} < {p.end_ms})"))
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
                                message=f"line start moves {a - b:+d} ms when the decoding context changes",
                                data={"base_ms": b, "alt_ms": a}))
    return issues


def run_checks(units: list[UnitTiming], lines: list[LineTiming], cfg: CheckConfig,
               voices: Optional[dict[str, str]] = None) -> tuple[list[Issue], float]:
    cov_issues, cov = check_coverage(units, lines, cfg)
    issues = check_units(units, cfg) + cov_issues + check_lines(lines, cfg, voices) + check_unit_order(units, voices)
    return issues, cov


# issue codes that a retry could plausibly improve
RETRYABLE = {"short_unit", "long_unit", "anchor_deviation", "window_edge", "line_overlap", "order_conflict",
             "unstable_boundary", "line_incomplete", "decode_failed", "boundary_conflict"}
