"""Alignment orchestration: plan -> decode -> stitch -> check -> retry -> tail.

Emissions are computed once for the whole song by the caller (and cached);
decode windows are slices of it.  Times are converted to integer ms only when
building the public result.  Manual locks from a previous result are carried
over for unchanged unit ids and are never overwritten by new predictions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .. import ALGORITHM_VERSION
from ..interfaces import CancelToken, Emission, TokenizedUnit, TranslitProfile
from ..models import (
    AlignConfig,
    AlignMode,
    AlignmentResult,
    AudioAsset,
    BackendInfo,
    Calibration,
    Coverage,
    InputSnapshot,
    Issue,
    LineTiming,
    LyricsDoc,
    UnitTiming,
    new_id,
    stable_hash,
    utcnow,
)
from . import checks as chk
from .calibration import calibration_hash, check_issues, validate_anchors
from .decoding import Prepared, TaskOutcome, decode_task, prepare, unit_timings_for_line
from .planning import Task, merge_tasks, plan_lrc, plan_plain
from .retry import RetryContext, retry_lines
from .tail import apply_tail

MAX_STABILITY_TASKS = 200


class AlignmentInputError(Exception):
    """Inputs must be corrected before aligning (never silently clipped)."""

    def __init__(self, issues: list[Issue]):
        super().__init__("; ".join(i.message for i in issues))
        self.issues = issues


@dataclass
class AlignInputs:
    lyrics: LyricsDoc
    mode: AlignMode
    calibration: Calibration
    config: AlignConfig
    backend_info: BackendInfo
    tokenize: Callable[[list[str], list[str]], list[TokenizedUnit]]
    profile: TranslitProfile
    emission_for: Callable[[str], Emission]
    available_roles: list[str]
    audio_assets: dict[str, AudioAsset]
    audio_duration_ms: int
    # (dB RMS envelope on the original timeline, hop in ms); index i ~ i*hop ms
    energy_for: Optional[Callable[[str], tuple[np.ndarray, float]]] = None
    previous: Optional[AlignmentResult] = None
    line_ids: Optional[list[str]] = None
    supports_language: Optional[Callable[[str], bool]] = None
    extra_stats: dict = field(default_factory=dict)


def _noop(*_a, **_k) -> None:
    pass


def run_alignment(inp: AlignInputs, cancel: Optional[CancelToken] = None,
                  progress: Optional[Callable[[float, str], None]] = None) -> AlignmentResult:
    t0 = time.time()
    prog = progress or _noop
    cfg = inp.config
    doc = inp.lyrics
    cal = inp.calibration

    def check_cancel() -> None:
        if cancel is not None:
            cancel.check()

    role = cfg.audio_role
    if role not in inp.available_roles or role not in inp.audio_assets:
        raise AlignmentInputError([Issue(code="audio_role_missing", severity="error",
                                         message=f"alignment input '{role}' is not available; "
                                                 f"available: {inp.available_roles}")])
    issues: list[Issue] = []
    if inp.mode == "lrc":
        v = validate_anchors(doc, cal, inp.audio_duration_ms)
        errors = [i for i in v if i.severity == "error"]
        if errors:
            raise AlignmentInputError(errors)
        issues += v + check_issues(cal)
        if not cal.confirmed:
            issues.append(Issue(code="calibration_unconfirmed", severity="info",
                                message="global offset not confirmed; LRC times used as imported"))

    prog(0.02, "preparing tokens")
    prep = prepare(doc, inp.profile, inp.tokenize, inp.supports_language)
    issues += prep.issues
    if inp.line_ids is not None:
        unknown = [x for x in inp.line_ids if x not in prep.lines]
        if unknown:
            raise AlignmentInputError([Issue(code="unknown_lines", severity="error",
                                             message=f"lines not sung / unknown: {unknown}")])
    selected = [lid for lid in prep.order if inp.line_ids is None or lid in inp.line_ids]
    voices = {lid: prep.lines[lid].voice for lid in prep.order}

    prog(0.05, "acoustic scores")
    emissions: dict[str, Emission] = {role: inp.emission_for(role)}
    check_cancel()
    em = emissions[role]
    fm, nf = em.frame_map, em.num_frames

    def emission(r: str) -> Emission:
        if r not in emissions:
            emissions[r] = inp.emission_for(r)
        return emissions[r]

    n_decodes = 0

    def decode(task: Task, r: str, sigma_scale: float = 1.0, line_units_override=None, label: str = "base") -> TaskOutcome:
        nonlocal n_decodes
        check_cancel()
        n_decodes += 1
        return decode_task(task, emission(r), prep, cfg.decode, r, sigma_scale, line_units_override, label)

    def plan(decode_cfg=None, extra_context: int = 0, line_ids=None, force_joint=None) -> list[Task]:
        if inp.mode == "plain":
            return plan_plain(doc, nf, line_ids)
        return plan_lrc(doc, cal, decode_cfg or cfg.decode, fm, nf, inp.audio_duration_ms, line_ids,
                        force_joint, extra_context)

    tasks = plan(line_ids=inp.line_ids)
    prog(0.1, f"decoding {len(tasks)} task(s)")
    outcomes: dict[str, tuple[Task, TaskOutcome]] = {}
    for i, task in enumerate(tasks):
        o = decode(task, role)
        for lid in task.retained_line_ids:
            outcomes[lid] = (task, o)
        prog(0.1 + 0.4 * (i + 1) / max(1, len(tasks)), f"decoded {i + 1}/{len(tasks)}")

    # --- stitch: boundary / order conflicts between separately decoded tasks -> joint rerun
    resolved, unresolved = 0, []
    skip: set[tuple[str, str]] = set()
    for _ in range(len(tasks) + 1):
        conflict = None
        by_voice: dict[str, list[str]] = {}
        for lid in selected:
            by_voice.setdefault(voices[lid], []).append(lid)
        for ids in by_voice.values():
            for a, b in zip(ids, ids[1:]):
                if a not in outcomes or b not in outcomes or (a, b) in skip:
                    continue
                (ta, oa), (tb, ob) = outcomes[a], outcomes[b]
                if ta is tb:
                    continue
                ra, rb = oa.line_ranges.get(a), ob.line_ranges.get(b)
                if ra and rb and rb[0] < ra[1]:
                    conflict = (a, b)
                    break
            if conflict:
                break
        if conflict is None:
            break
        a, b = conflict
        merged = merge_tasks(outcomes[a][0], outcomes[b][0], doc)
        o = decode(merged, role, label="joint")
        if o.feasible:
            resolved += 1
            for lid in merged.retained_line_ids:
                outcomes[lid] = (merged, o)
        else:
            skip.add(conflict)
            unresolved.append(conflict)
            issues.append(Issue(code="boundary_conflict", severity="warning", line_id=b,
                                message=f"lines {a} and {b} overlap and a joint re-decode failed ({o.reason}); "
                                        "kept unclipped for manual review"))

    # --- build, check, stability
    def build(outs: dict[str, tuple[Task, TaskOutcome]]) -> tuple[list[UnitTiming], list[LineTiming], list[Issue]]:
        units: list[UnitTiming] = []
        lines: list[LineTiming] = []
        extra: list[Issue] = []
        for lid in selected:
            task, o = outs.get(lid, (None, None))
            uts = unit_timings_for_line(prep, o, lid)
            units += uts
            lt = LineTiming(line_id=lid, audio_role=o.role if o else None)
            if o is not None and o.feasible and lid in o.line_ranges:
                lt.start_ms, lt.end_ms = o.line_ranges[lid]
                if lid in o.anchor_ms:
                    lt.anchor_ms, lt.anchor_kind = o.anchor_ms[lid][0], o.anchor_ms[lid][1]  # type: ignore[assignment]
                    lt.anchor_residual_ms = o.residual(lid)
                if inp.mode == "lrc":
                    lt.window_ms = o.window_ms
                lt.context_line_ids = [x for x in task.participating_line_ids if x != lid and x not in task.retained_line_ids]
                if o.label != "base":
                    lt.candidate = o.label
            elif uts and all(u.status == "unaligned" for u in uts):
                lt.status, lt.reason = "unaligned", "no tokenizable units"
            else:
                lt.status = "failed"
                lt.reason = (o.reason if o is not None else None) or "not decoded"
                extra.append(Issue(code="decode_failed", severity="error", line_id=lid,
                                   message=f"no alignment: {lt.reason}"))
            lines.append(lt)
        return units, lines, extra

    units, lines, dec_issues = build(outcomes)
    check_issues_, coverage = chk.run_checks(units, lines, cfg.checks, voices)
    stab_issues: list[Issue] = []
    if inp.mode == "lrc" and tasks:
        prog(0.55, "stability check")
        alt_cfg = cfg.decode.model_copy(update={"joint_context_lines": 0 if cfg.decode.joint_context_lines > 0 else 1})
        alt_tasks = plan(decode_cfg=alt_cfg, line_ids=inp.line_ids)[:MAX_STABILITY_TASKS]
        alt_starts: dict[str, Optional[int]] = {}
        for t in alt_tasks:
            base_t = outcomes.get(t.retained_line_ids[0], (None, None))[0]
            if base_t is not None and base_t.participating_line_ids == t.participating_line_ids:
                continue
            o = decode(t, role, label="stability")
            for lid in t.retained_line_ids:
                alt_starts[lid] = o.line_ranges[lid][0] if (o.feasible and lid in o.line_ranges) else None
        base_starts = {lt.line_id: lt.start_ms for lt in lines if lt.line_id in alt_starts}
        stab_issues = chk.stability_issues(base_starts, alt_starts, cfg.checks.stability_tolerance_ms, lines)

    # --- limited retries for flagged lines
    locked_ids = _locked_unit_ids(inp.previous)
    flagged = []
    for lid in selected:
        codes = {i.code for i in check_issues_ + stab_issues + dec_issues if i.line_id == lid}
        line_units = prep.line_units.get(lid, [])
        if codes & chk.RETRYABLE and not (line_units and all(u in locked_ids for u in line_units)):
            flagged.append(lid)
    retry_report = None
    if flagged and cfg.retry.enabled:
        prog(0.65, f"retrying {len(flagged)} line(s)")
        current = dict(outcomes)

        def evaluate(o: TaskOutcome, lid: str) -> tuple[list[Issue], float]:
            uts = unit_timings_for_line(prep, o, lid)
            lt = LineTiming(line_id=lid)
            if o.feasible and lid in o.line_ranges:
                lt.start_ms, lt.end_ms = o.line_ranges[lid]
                lt.anchor_residual_ms = o.residual(lid)
                if inp.mode == "lrc":
                    lt.window_ms = o.window_ms
            idx = prep.order.index(lid)
            neigh: list[LineTiming] = []
            for j in (idx - 1,):
                if j >= 0 and prep.order[j] in current:
                    po = current[prep.order[j]][1]
                    r = po.line_ranges.get(prep.order[j])
                    if r and voices[prep.order[j]] == voices[lid]:
                        neigh.append(LineTiming(line_id=prep.order[j], start_ms=r[0], end_ms=r[1]))
            iss = chk.check_units(uts, cfg.checks) + chk.check_lines(neigh + [lt], cfg.checks, voices)
            iss = [i for i in iss if i.line_id == lid]
            timed = sum(1 for u in uts if u.start_ms is not None)
            cov = timed / len(uts) if uts else 1.0
            if not o.feasible:
                iss.append(Issue(code="decode_failed", severity="error", line_id=lid, message=o.reason or ""))
            return iss, cov

        def expand(task: Task, lid: str) -> Optional[Task]:
            if inp.mode == "lrc":
                ts = [t for t in plan(extra_context=1, line_ids=[lid]) if lid in t.retained_line_ids]
                if not ts or ts[0].participating_line_ids == task.participating_line_ids:
                    return None
                t = ts[0]
                t.retained_line_ids = [lid]
                return t
            idx = prep.order.index(lid)
            ids = [x for x in prep.order[max(0, idx - 1): idx + 2] if voices[x] == voices[lid]]
            rng = [current[x][1].line_ranges.get(x) for x in ids if x in current]
            rng = [r for r in rng if r]
            if not rng:
                return None
            lo = min(r[0] for r in rng) - cfg.decode.left_margin_ms
            hi = max(r[1] for r in rng) + cfg.decode.right_margin_ms
            s = max(0, int(np.floor(fm.ms_to_frame(max(0, lo)))))
            e = min(nf, int(np.ceil(fm.ms_to_frame(min(inp.audio_duration_ms, hi)))))
            return Task(f"local-{lid}", voices[lid], ids, [lid], s, e, [], "joint")

        ctx = RetryContext(prep=prep, mode=inp.mode, decode=decode, evaluate=evaluate, expand_task=expand,
                           available_roles=[r for r in inp.available_roles if r in ("original", "vocals")],
                           cfg=cfg.retry, stability_tolerance_ms=cfg.checks.stability_tolerance_ms,
                           profile=inp.profile, tokenize=inp.tokenize)
        retry_report = retry_lines(flagged, {lid: outcomes[lid] for lid in flagged if lid in outcomes}, ctx)
        if retry_report.committed:
            for lid, o in retry_report.committed.items():
                outcomes[lid] = (o.task, o)
            units, lines, dec_issues = build(outcomes)
            check_issues_, coverage = chk.run_checks(units, lines, cfg.checks, voices)
            stab_issues = [i for i in stab_issues if i.line_id not in retry_report.committed]

    # --- manual locks from the previous result
    manual_issues = _carry_manual(units, inp.previous)

    # --- tail strategy
    tail_issues: list[Issue] = []
    if cfg.tail.strategy != "off":
        env = None
        if inp.energy_for is not None:
            env_role = "vocals" if "vocals" in inp.available_roles else role
            try:
                env = inp.energy_for(env_role)
            except Exception as e:  # envelope is optional evidence
                tail_issues.append(Issue(code="tail_no_energy", severity="info", message=str(e)))
        by_voice_units: dict[str, list[UnitTiming]] = {}
        for u in units:
            by_voice_units.setdefault(voices[u.line_id], []).append(u)
        for vu in by_voice_units.values():
            tail_issues += apply_tail(vu, cfg.tail, env, cfg.checks.min_unit_ms)

    # final line ranges follow final unit times
    by_line: dict[str, list[UnitTiming]] = {}
    for u in units:
        by_line.setdefault(u.line_id, []).append(u)
    for lt in lines:
        timed = [u for u in by_line.get(lt.line_id, []) if u.start_ms is not None and u.end_ms is not None]
        if timed:
            lt.start_ms = min(u.start_ms for u in timed)  # type: ignore[type-var]
            lt.end_ms = max(u.end_ms for u in timed)  # type: ignore[type-var]
            if lt.status == "failed" and all(u.locked for u in timed):
                lt.flags.append("manual_only")

    all_issues = issues + dec_issues + check_issues_ + stab_issues + manual_issues + tail_issues
    all_issues = [i for i in all_issues if i.line_id is None or i.line_id in set(selected)]
    prog(0.95, "assembling result")
    asset = inp.audio_assets[role]
    snapshot = InputSnapshot(
        mode=inp.mode,
        lyrics_text_revision=doc.text_revision(),
        lyrics_reading_revision=doc.reading_revision(),
        calibration_hash=calibration_hash(doc, cal) if inp.mode == "lrc" else None,
        audio_asset_id=asset.id,
        audio_sha256=asset.sha256,
        audio_role=role,
        line_ids=selected,
        config_hash=stable_hash(cfg.model_dump(mode="json")),
    )
    timed_lines = [lt for lt in lines if lt.start_ms is not None]
    cov = Coverage(
        full=inp.line_ids is None,
        line_ids=selected,
        from_ms=min((lt.start_ms for lt in timed_lines), default=None),  # type: ignore[type-var]
        to_ms=max((lt.end_ms for lt in timed_lines), default=None),  # type: ignore[type-var]
    )
    stats = {
        "algorithm": ALGORITHM_VERSION,
        "tasks": len(tasks),
        "decodes": n_decodes,
        "conflicts_resolved": resolved,
        "conflicts_unresolved": len(unresolved),
        "unit_coverage": coverage,
        "frame_ms": fm.frame_ms,
        "num_frames": nf,
        "retry": retry_report.log if retry_report else [],
        "elapsed_s": round(time.time() - t0, 3),
        **inp.extra_stats,
    }
    return AlignmentResult(
        id=new_id("r"), created=utcnow(), mode=inp.mode, backend=inp.backend_info, config=cfg,
        snapshot=snapshot, coverage=cov, lines=lines, units=units, issues=all_issues,
        candidates=retry_report.candidates if retry_report else [],
        parent_result_id=inp.previous.id if (inp.previous is not None and inp.line_ids is not None) else None,
        stats=stats,
    )


def _locked_unit_ids(prev: Optional[AlignmentResult]) -> set[str]:
    if prev is None:
        return set()
    return {u.unit_id for u in prev.units if u.locked}


def _carry_manual(units: list[UnitTiming], prev: Optional[AlignmentResult]) -> list[Issue]:
    if prev is None:
        return []
    old = {u.unit_id: u for u in prev.units}
    issues = []
    for u in units:
        p = old.get(u.unit_id)
        if p is None or (p.manual is None and not p.manual_history):
            continue
        u.manual = p.manual.model_copy() if p.manual else None
        u.manual_history = [m.model_copy() for m in p.manual_history]
        if p.reading != u.reading and u.manual is not None:
            issues.append(Issue(code="manual_reading_changed", severity="warning", line_id=u.line_id,
                                unit_id=u.unit_id,
                                message=f"manual time kept although reading changed '{p.reading}' -> '{u.reading}'"))
        if u.manual is not None and u.manual.locked:
            u.start_ms, u.end_ms = u.manual.start_ms, u.manual.end_ms
            if "manual" not in u.flags:
                u.flags.append("manual")
            if u.status in ("failed",) and u.start_ms is not None:
                u.flags.append("model_failed")
    return issues


def merge_partial(base: AlignmentResult, partial: AlignmentResult) -> AlignmentResult:
    """Combine a local rerun into a full result (new id; base untouched)."""
    ids = set(partial.coverage.line_ids)
    new = base.model_copy(deep=True)
    new.id = new_id("r")
    new.created = utcnow()
    new.parent_result_id = base.id
    p_units: dict[str, list[UnitTiming]] = {}
    for u in partial.units:
        p_units.setdefault(u.line_id, []).append(u)
    units: list[UnitTiming] = []
    seen: set[str] = set()
    for u in new.units:
        if u.line_id in ids:
            if u.line_id not in seen:
                units += [x.model_copy(deep=True) for x in p_units.get(u.line_id, [])]
                seen.add(u.line_id)
        else:
            units.append(u)
    for lid in ids - seen:
        units += [x.model_copy(deep=True) for x in p_units.get(lid, [])]
    new.units = units
    p_lines = {lt.line_id: lt for lt in partial.lines}
    new.lines = [p_lines[lt.line_id].model_copy(deep=True) if lt.line_id in p_lines else lt for lt in new.lines]
    new.issues = [i for i in new.issues if i.line_id not in ids] + [i for i in partial.issues if i.line_id in ids]
    new.candidates = [c for c in new.candidates if c.line_id not in ids] + list(partial.candidates)
    new.snapshot = partial.snapshot.model_copy(update={"line_ids": base.snapshot.line_ids})
    new.stats = {**base.stats, "local_rerun": sorted(ids), "local_rerun_result_id": partial.id}
    new.stale, new.stale_reason = False, None
    return new
