"""Automatic LRC offset suggestion (detailed mode, "自动匹配").

A plain-mode alignment on a throw-away copy of the project gives each line's
sung start; the global offset is the median of (sung start − LRC time), which
one badly aligned line cannot move.  ``agree`` is the share of lines within
0.7 s of that median: a low value means the LRC was timed on another
recording.  The separated vocals' first clear onset is returned as a hint.
Stems are only used while they belong to the current original
(``service.stems_current``); otherwise the trial runs on the original and no
vocal onset is given.  Nothing is saved: the user still marks / confirms the
first onset.
"""

from __future__ import annotations

import statistics
from typing import Callable, Optional

from . import service as S
from .interfaces import CancelToken, Cancelled


def _audio_role(h: "S.ProjectHandle") -> str:
    # stems of a replaced original would place every line on another recording's timeline
    return "vocals" if S.stems_current(h.project) else "original"


def suggest_calibration(h: "S.ProjectHandle", *, cancel: Optional[CancelToken] = None,
                        progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """{shift_ms, agree, lines_checked, line_starts, vocal_onset_ms, audio_role}.

    ``shift_ms`` is the suggested global user shift: the first onset of any
    line is suggested at its LRC time + ``shift_ms``.
    """
    est = estimate_lrc_shift(h, cancel=cancel, progress=progress)
    return {
        "shift_ms": est["shift_ms"], "agree": round(est["agree"], 3), "lines_checked": est["lines"],
        "line_starts": est["starts"], "audio_role": _audio_role(h),
        "vocal_onset_ms": _vocal_onset_ms(h) if S.stems_current(h.project) else None,
    }


def _vocal_onset_ms(h: "S.ProjectHandle") -> Optional[int]:
    """First moment the separated vocals are clearly singing for at least 0.3 s."""
    from .align.activity import detect_activity
    from .audio.analysis import rms_envelope_db
    from .audio.io import load_audio

    try:
        voc = h.project.asset("vocals")
        data, sr = load_audio(S.asset_path(h, voc), mono=True)
        # sample 0 of the stem lies at its origin on the original timeline
        origin_ms = voc.origin_offset_samples * 1000.0 / voc.sample_rate
        act = detect_activity(rms_envelope_db(data[0], sr, hop_ms=10.0), 10.0)
    except Exception:
        return None
    if act is None:
        return None
    singing = act.rest < 0.5
    run = int(300 / act.hop_ms)
    for i in range(len(singing) - run):
        if singing[i] and singing[i:i + run].all():
            return int(round(i * act.hop_ms + origin_ms))
    return None


def estimate_lrc_shift(h: "S.ProjectHandle", *, cancel: Optional[CancelToken] = None,
                       progress: Optional[Callable[[float, str], None]] = None) -> dict:
    """Estimate the LRC's global offset from a trial alignment (nothing is saved).

    A plain-mode alignment (on a throw-away copy of the project) gives each
    line's sung start; the global shift is the median of (sung start − LRC
    time).  Also returns the share of lines that agree within 0.7 s, which
    tells whether the LRC fits this recording at all.
    """
    import copy

    from .align import calibration as C

    with h.lock:
        tmp = S.ProjectHandle(h.dir, copy.deepcopy(h.project))
    tmp.save = lambda: None  # type: ignore[method-assign]
    tmp.project.mode = "plain"
    tmp.project.results, tmp.project.active_result_id = [], None
    r = S.run_align(tmp, audio_role=_audio_role(h), cancel=cancel, progress=progress)
    starts = {lt.line_id: lt.start_ms for lt in r.lines if lt.start_ms is not None}
    diffs = []
    doc = h.project.lyrics
    for ln in doc.sung_lines():
        b = C.base_ms(doc, ln)
        if b is not None and ln.anchor is None and ln.id in starts:
            diffs.append(starts[ln.id] - b)
    if len(diffs) < 3:
        raise S.ServiceError("带时间的行太少，无法估计偏移")
    shift = int(round(statistics.median(diffs)))
    agree = sum(1 for d in diffs if abs(d - shift) <= 700) / len(diffs)
    return {"shift_ms": shift, "agree": agree, "lines": len(diffs), "starts": starts}
