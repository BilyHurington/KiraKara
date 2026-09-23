"""Tail (sustained note end) strategies (design §4.6).

* ``off``    – keep model boundaries (default)
* ``trim``   – conservative: only shortens tails, using energy evidence when
               available, otherwise caps implausibly long line-final units
* ``energy`` – bounded local correction using vocal energy continuity; may
               extend or trim, never past the next unit's start

CTC blanks are *not* treated as silence.  Each change records the original
value, method and reason; manually locked units are never touched.  When no
reliable boundary is found the model value is kept and the unit is flagged.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from ..models import Issue, TailAdjustment, TailConfig, UnitTiming

REL_DROP_DB = 18.0  # a tail ends where energy falls this far below the unit's peak
GUARD_MS = 20  # never extend closer than this to the next unit start
GAP_MS = 100  # units followed by at least this much gap are tail candidates


def _tail_candidates(units: list[UnitTiming]) -> list[tuple[int, Optional[int]]]:
    """(index, next_start_ms) for line-final units and units before a gap."""
    out = []
    for i, u in enumerate(units):
        if u.start_ms is None or u.end_ms is None:
            continue
        nxt = next((v for v in units[i + 1:] if v.start_ms is not None), None)
        next_start = nxt.start_ms if nxt is not None else None
        line_final = nxt is None or nxt.line_id != u.line_id
        gap = (next_start - u.end_ms) if next_start is not None else None
        if line_final or (gap is not None and gap >= GAP_MS):
            out.append((i, next_start))
    return out


def _idx(ms: float, hop_ms: float, n: int) -> int:
    return int(min(max(0, round(ms / hop_ms)), n))


def _energy_boundary(u: UnitTiming, next_start: Optional[int], env: np.ndarray, hop_ms: float,
                     cfg: TailConfig, min_unit_ms: int, allow_extend: bool) -> tuple[Optional[int], str]:
    n = len(env)
    s, e = u.start_ms, u.end_ms
    assert s is not None and e is not None
    i0, i1 = _idx(s, hop_ms, n), _idx(e, hop_ms, n)
    if i1 <= i0:
        return None, "unit shorter than one envelope frame"
    peak = float(np.max(env[i0:i1]))
    thr = max(cfg.energy_floor_db, peak - REL_DROP_DB)
    if peak <= cfg.energy_floor_db:
        return None, "no vocal energy above the floor inside the unit"
    above_at_end = env[min(i1, n - 1)] > thr if i1 < n else False
    if above_at_end and allow_extend:
        limit_ms = e + cfg.max_extend_ms
        if next_start is not None:
            limit_ms = min(limit_ms, next_start - GUARD_MS)
        lim = _idx(limit_ms, hop_ms, n)
        j = i1
        while j < lim and env[j] > thr:
            j += 1
        if j >= lim:
            return None, "voicing continues up to the search limit; boundary not reliable"
        return int(round(j * hop_ms)), f"energy stays above {thr:.1f} dB until here"
    if above_at_end:
        return None, "energy continues past the model end (trim-only strategy)"
    lo = _idx(max(s + min_unit_ms, e - cfg.max_trim_ms), hop_ms, n)
    j = i1 - 1
    while j >= lo and env[j] <= thr:
        j -= 1
    if j < lo:
        return None, "energy already below threshold across the whole trim range"
    new_end = int(round((j + 1) * hop_ms))
    if new_end >= e:
        return None, "model end already matches the energy drop"
    return new_end, f"energy falls below {thr:.1f} dB here"


def apply_tail(units: list[UnitTiming], cfg: TailConfig, envelope: Optional[tuple[np.ndarray, float]],
               min_unit_ms: int = 40) -> list[Issue]:
    """Mutates ``units`` (ordered per voice) in place, returns issues."""
    if cfg.strategy == "off":
        return []
    issues: list[Issue] = []
    env, hop = (envelope if envelope is not None else (None, None))
    for i, next_start in _tail_candidates(units):
        u = units[i]
        if u.locked:
            continue
        orig = u.end_ms
        new: Optional[int] = None
        reason = ""
        method = cfg.strategy
        if env is not None and hop:
            new, reason = _energy_boundary(u, next_start, np.asarray(env, dtype=float), float(hop), cfg,
                                           min_unit_ms, allow_extend=(cfg.strategy == "energy"))
            method = f"{cfg.strategy}:energy"
        elif cfg.strategy == "trim":
            line_units = [v for v in units if v.line_id == u.line_id and v.start_ms is not None and v.end_ms is not None]
            durs = sorted(v.end_ms - v.start_ms for v in line_units)  # type: ignore[operator]
            med = durs[len(durs) // 2] if durs else 0
            cap = max(3 * med, min_unit_ms)
            cur = u.end_ms - u.start_ms  # type: ignore[operator]
            if cur > cap:
                new = u.start_ms + max(cap, cur - cfg.max_trim_ms)  # type: ignore[operator]
                reason = f"line-final unit {cur} ms > 3x line median ({med} ms)"
                method = "trim:duration_cap"
        else:
            reason = "no energy envelope available"
        if new is None:
            if "tail_unresolved" not in u.flags:
                u.flags.append("tail_unresolved")
            issues.append(Issue(code="tail_unresolved", severity="info", line_id=u.line_id, unit_id=u.unit_id,
                                message=f"tail kept at model boundary: {reason}"))
            continue
        if new == orig:
            continue
        u.end_ms = new
        u.tail = TailAdjustment(original_end_ms=orig, new_end_ms=new, method=method, reason=reason)
        if "tail_adjusted" not in u.flags:
            u.flags.append("tail_adjusted")
    return issues
