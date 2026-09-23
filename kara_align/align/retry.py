"""Bounded candidate retries for flagged lines (design §4.6).

Variants per flagged line (limited by :class:`RetryConfig` budgets):

* ``wide_sigma``   – looser soft anchors (lrc mode)
* ``context``      – decode jointly with neighbouring lines, commit the target only
* ``audio:<role>`` – switch between original and vocals when both exist
* ``reading:<r>``  – a few alternative readings listed on the line's segments;
                     these change the unit grouping, so they are **never
                     committed automatically** and only kept for listening.

Selection: feasibility, coverage, error / warning count, anchor residual; the
normalised acoustic score is only used to break ties between variants decoded
with the same model on the same audio slice.  Clearly divergent variants are
kept as candidates; times are never averaged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from ..interfaces import TokenizedUnit, TranslitProfile
from ..models import Candidate, Issue, RetryConfig, UnitTiming, new_id
from .decoding import Prepared, TaskOutcome, unit_timings_for_line
from .planning import Task


@dataclass
class RetryContext:
    prep: Prepared
    mode: str
    decode: Callable[..., TaskOutcome]  # (task, role, sigma_scale=, line_units_override=, label=)
    evaluate: Callable[[TaskOutcome, str], tuple[list[Issue], float]]
    expand_task: Callable[[Task, str], Optional[Task]]  # add neighbour context around a line
    available_roles: Sequence[str]
    cfg: RetryConfig
    stability_tolerance_ms: int = 150
    profile: Optional[TranslitProfile] = None
    tokenize: Optional[Callable[[list[str], list[str]], list[TokenizedUnit]]] = None
    used: int = 0


@dataclass
class Scored:
    outcome: TaskOutcome
    issues: list[Issue]
    coverage: float
    committable: bool = True

    def key(self, line_id: str) -> tuple:
        errors = sum(1 for i in self.issues if i.severity == "error")
        warns = sum(1 for i in self.issues if i.severity == "warning")
        res = self.outcome.residual(line_id)
        return (self.outcome.feasible, round(self.coverage, 6), -errors, -warns, -abs(res) if res is not None else 0)

    def slice_id(self) -> tuple:
        t = self.outcome.task
        return (self.outcome.role, t.start_frame, t.end_frame, tuple(t.participating_line_ids))


@dataclass
class RetryReport:
    committed: dict[str, TaskOutcome] = field(default_factory=dict)
    candidates: list[Candidate] = field(default_factory=list)
    log: list[dict] = field(default_factory=list)


def reading_overrides(prep: Prepared, line_id: str, profile: TranslitProfile,
                      tokenize: Callable[[list[str], list[str]], list[TokenizedUnit]], max_n: int = 2
                      ) -> list[tuple[str, dict[str, list[tuple[str, list[int]]]], dict[str, str]]]:
    """Alternative unit/token sequences built from ``segment.candidates``.

    Returns (label, {line_id: [(synthetic_unit_id, tokens)]}, {synthetic_id: reading}).
    """
    from ..reading.japanese import split_morae  # written by the reading module

    line = prep.lines[line_id]
    out = []
    for seg in line.segments:
        for cand in seg.candidates[:max_n]:
            if seg.confirmed and cand == seg.reading:
                continue
            seq: list[tuple[str, list[int]]] = []
            readings: dict[str, str] = {}
            ok = True
            for s2 in line.segments:
                if s2.id != seg.id:
                    for u in s2.units:
                        seq.append((u.id, prep.units[u.id].token_ids))
                    continue
                raw = split_morae(cand)
                morae = [m if isinstance(m, str) else getattr(m, "text", str(m)) for m in raw]
                mflags = [[] if isinstance(m, str) else list(getattr(m, "flags", [])) for m in raw]
                ids = [f"{seg.id}~{k}" for k in range(len(morae))]
                texts = profile.unit_texts(morae, [seg.lang] * len(morae), mflags)
                toks = {t.unit_id: t for t in tokenize(ids, list(texts))}
                for uid, m in zip(ids, morae):
                    t = toks.get(uid)
                    if t is None or not t.token_ids:
                        ok = False
                    seq.append((uid, list(t.token_ids) if t else []))
                    readings[uid] = m
            if ok:
                out.append((f"reading:{seg.surface}={cand}", {line_id: seq}, readings))
            if len(out) >= max_n:
                return out
    return out


def _timings_from_override(outcome: TaskOutcome, line_id: str, seq, readings: dict[str, str],
                           prep: Prepared) -> list[UnitTiming]:
    res = []
    for uid, _ in seq:
        info = prep.units.get(uid)
        reading = info.reading if info else readings.get(uid, "")
        seg_id = info.segment_id if info else uid.split("~")[0]
        ut = UnitTiming(unit_id=uid, line_id=line_id, segment_id=seg_id, reading=reading)
        sp = outcome.units.get(uid)
        if sp is None:
            ut.status, ut.reason = "failed", outcome.reason or "未解码"
        else:
            ut.start_ms = ut.model_start_ms = sp.start_ms
            ut.end_ms = ut.model_end_ms = sp.end_ms
            ut.acoustic_score = sp.score
        res.append(ut)
    return res


def _diverges(a: TaskOutcome, b: TaskOutcome, line_id: str, prep: Prepared, tol: int) -> bool:
    ra, rb = a.line_ranges.get(line_id), b.line_ranges.get(line_id)
    if ra is None or rb is None:
        return ra != rb
    if abs(ra[0] - rb[0]) > tol or abs(ra[1] - rb[1]) > tol:
        return True
    for uid in prep.line_units.get(line_id, []):
        ua, ub = a.units.get(uid), b.units.get(uid)
        if ua and ub and abs(ua.start_ms - ub.start_ms) > tol:
            return True
    return False


def retry_line(line_id: str, base_task: Task, base: TaskOutcome, ctx: RetryContext, report: RetryReport) -> None:
    per_line = 0

    def budget_ok() -> bool:
        return per_line < ctx.cfg.max_candidates_per_line and ctx.used < ctx.cfg.max_total_candidates

    def run(task: Task, role: str, label: str, sigma: float = 1.0, override=None) -> Optional[TaskOutcome]:
        nonlocal per_line
        if not budget_ok():
            return None
        per_line += 1
        ctx.used += 1
        return ctx.decode(task, role, sigma_scale=sigma, line_units_override=override, label=label)

    scored = [Scored(base, *ctx.evaluate(base, line_id))]
    variants: list[tuple[Task, str, str, float]] = []
    if ctx.mode == "lrc" and base_task.anchors:
        variants.append((base_task, base.role, "wide_sigma", 2.5))
    wider = ctx.expand_task(base_task, line_id)
    if wider is not None:
        variants.append((wider, base.role, "context", 1.0))
    for role in ctx.available_roles:
        if role != base.role:
            variants.append((base_task, role, f"audio:{role}", 1.0))
    for task, role, label, sigma in variants:
        o = run(task, role, label, sigma)
        if o is None:
            break
        scored.append(Scored(o, *ctx.evaluate(o, line_id)))

    best = scored[0]
    for s in scored[1:]:
        if s.key(line_id) > best.key(line_id):
            best = s
        elif (s.key(line_id) == best.key(line_id) and s.slice_id() == best.slice_id()
              and s.outcome.mean_acoustic is not None and best.outcome.mean_acoustic is not None
              and s.outcome.mean_acoustic > best.outcome.mean_acoustic):
            best = s
    report.log.append({"line_id": line_id, "tried": [s.outcome.label for s in scored], "chosen": best.outcome.label,
                       "keys": {s.outcome.label: list(s.key(line_id)) for s in scored}})
    if best is not scored[0]:
        report.committed[line_id] = best.outcome
    for s in scored:
        if s is best or not s.outcome.feasible:
            continue
        if _diverges(s.outcome, best.outcome, line_id, ctx.prep, ctx.stability_tolerance_ms):
            report.candidates.append(Candidate(
                line_id=line_id, label=s.outcome.label,
                units=unit_timings_for_line(ctx.prep, s.outcome, line_id),
                summary={"issues": [i.code for i in s.issues], "coverage": s.coverage,
                         "anchor_residual_ms": s.outcome.residual(line_id), "role": s.outcome.role,
                         "window_ms": list(s.outcome.window_ms)},
            ))

    # alternative readings: kept for manual listening only
    if ctx.profile is not None and ctx.tokenize is not None and budget_ok():
        try:
            alts = reading_overrides(ctx.prep, line_id, ctx.profile, ctx.tokenize)
        except ImportError:
            alts = []
        for label, override, readings in alts:
            o = run(base_task, base.role, label, 1.0, override)
            if o is None:
                break
            if o.feasible:
                report.candidates.append(Candidate(
                    id=new_id("c"), line_id=line_id, label=label,
                    units=_timings_from_override(o, line_id, override[line_id], readings, ctx.prep),
                    summary={"note": "alternative reading; apply the reading to adopt it", "role": o.role,
                             "anchor_residual_ms": o.residual(line_id)},
                ))


def retry_lines(flagged: Sequence[str], base: dict[str, tuple[Task, TaskOutcome]], ctx: RetryContext) -> RetryReport:
    report = RetryReport()
    if not ctx.cfg.enabled:
        return report
    for lid in flagged:
        if ctx.used >= ctx.cfg.max_total_candidates:
            report.log.append({"line_id": lid, "skipped": "total retry budget exhausted"})
            continue
        if lid not in base:
            continue
        task, outcome = base[lid]
        retry_line(lid, task, outcome, ctx, report)
    return report
