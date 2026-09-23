"""Accuracy evaluation against a manually annotated reference (design §8).

Reference formats:

* alignment JSON (an :class:`AlignmentResult`, e.g. a hand-corrected export):
  final unit / line times are used;
* CSV with header ``id,start_ms,end_ms`` and optional ``level`` column
  (``unit`` or ``line``; default ``unit``).  Empty cells mean "no time".

Matching is by unit / line id (``match="id"``) or by order of units within
lines (``match="order"``) for references produced from a different project.
Reports onset / offset error statistics, gross errors, missing counts and the
number of manually corrected units in the hypothesis.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Union

import numpy as np

from ..models import AlignmentResult


@dataclass
class RefTimes:
    units: dict[str, tuple[Optional[int], Optional[int]]] = field(default_factory=dict)
    lines: dict[str, tuple[Optional[int], Optional[int]]] = field(default_factory=dict)
    unit_order: list[str] = field(default_factory=list)
    line_order: list[str] = field(default_factory=list)


def _int(x) -> Optional[int]:
    if x is None or (isinstance(x, str) and x.strip() == ""):
        return None
    return int(round(float(x)))


def load_reference(source: Union[str, Path, dict, AlignmentResult]) -> RefTimes:
    if isinstance(source, AlignmentResult):
        return _from_result(source)
    if isinstance(source, dict):
        return _from_result(AlignmentResult.model_validate(source))
    text = str(source)
    p = Path(text)
    if "\n" not in text and p.exists():
        text = p.read_text(encoding="utf-8-sig")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return _from_result(AlignmentResult.model_validate(json.loads(stripped)))
    ref = RefTimes()
    for row in csv.DictReader(io.StringIO(text)):
        rid = (row.get("id") or row.get("unit_id") or row.get("line_id") or "").strip()
        if not rid:
            continue
        level = (row.get("level") or ("line" if "line_id" in row and "unit_id" not in row else "unit")).strip()
        val = (_int(row.get("start_ms")), _int(row.get("end_ms")))
        if level == "line":
            ref.lines[rid] = val
            ref.line_order.append(rid)
        else:
            ref.units[rid] = val
            ref.unit_order.append(rid)
    return ref


def _from_result(r: AlignmentResult) -> RefTimes:
    ref = RefTimes()
    for u in r.units:
        ref.units[u.unit_id] = (u.start_ms, u.end_ms)
        ref.unit_order.append(u.unit_id)
    for lt in r.lines:
        ref.lines[lt.line_id] = (lt.start_ms, lt.end_ms)
        ref.line_order.append(lt.line_id)
    return ref


def _stats(pairs: list[tuple[int, int]], gross_ms: int) -> dict:
    if not pairs:
        return {"n": 0, "mae_ms": None, "median_ms": None, "p90_ms": None, "gross": 0}
    err = np.array([abs(h - r) for h, r in pairs], dtype=float)
    return {
        "n": len(pairs),
        "mae_ms": round(float(err.mean()), 2),
        "median_ms": round(float(np.median(err)), 2),
        "p90_ms": round(float(np.percentile(err, 90)), 2),
        "mean_signed_ms": round(float(np.mean([h - r for h, r in pairs])), 2),
        "gross": int((err > gross_ms).sum()),
    }


def _compare(hyp: dict[str, tuple[Optional[int], Optional[int]]], ref: dict[str, tuple[Optional[int], Optional[int]]],
             gross_ms: int) -> dict:
    on, off = [], []
    missing_hyp = missing_ref = 0
    for k, (rs, re) in ref.items():
        hs, he = hyp.get(k, (None, None))
        if rs is None and re is None:
            missing_ref += 1
            continue
        if hs is None and rs is not None:
            missing_hyp += 1
            continue
        if rs is not None and hs is not None:
            on.append((hs, rs))
        if re is not None and he is not None:
            off.append((he, re))
    return {"reference_items": len(ref), "missing_in_hypothesis": missing_hyp, "untimed_in_reference": missing_ref,
            "onset": _stats(on, gross_ms), "offset": _stats(off, gross_ms)}


def evaluate(hyp: AlignmentResult, reference: Union[str, Path, dict, AlignmentResult, RefTimes],
             gross_ms: int = 300, match: Literal["id", "order"] = "id") -> dict:
    ref = reference if isinstance(reference, RefTimes) else load_reference(reference)
    h_units = {u.unit_id: (u.start_ms, u.end_ms) for u in hyp.units}
    h_lines = {lt.line_id: (lt.start_ms, lt.end_ms) for lt in hyp.lines}
    if match == "order":
        hu = [u.unit_id for u in hyp.units]
        hl = [lt.line_id for lt in hyp.lines]
        h_units = {ref.unit_order[i]: h_units[hu[i]] for i in range(min(len(hu), len(ref.unit_order)))}
        h_lines = {ref.line_order[i]: h_lines[hl[i]] for i in range(min(len(hl), len(ref.line_order)))}
    report = {
        "gross_threshold_ms": gross_ms,
        "match": match,
        "units": _compare(h_units, ref.units, gross_ms) if ref.units else None,
        "lines": _compare(h_lines, ref.lines, gross_ms) if ref.lines else None,
        "manual_corrections": sum(1 for u in hyp.units if u.manual is not None),
        "manual_locked": sum(1 for u in hyp.units if u.locked),
        "hypothesis": {"result_id": hyp.id, "mode": hyp.mode, "backend": hyp.backend.model_id,
                       "audio_role": hyp.snapshot.audio_role},
    }
    return report


def compare_runs(runs: dict[str, AlignmentResult], reference, gross_ms: int = 300, match: str = "id") -> dict:
    """Evaluate several configurations (e.g. base / lrc / separated / reading-fixed) on one reference."""
    ref = reference if isinstance(reference, RefTimes) else load_reference(reference)
    return {name: evaluate(r, ref, gross_ms, match) for name, r in runs.items()}  # type: ignore[arg-type]
