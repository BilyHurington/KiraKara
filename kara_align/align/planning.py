"""Lyric task planning (design §4.4).

Plain mode: one ordered task per voice over the whole emission; no anchors,
no distribution of lyrics over audio chunks.  A local rerun of some lines
(``plan_plain_local``) decodes only the stretch between the neighbouring lines'
times in the previous result, with those neighbours as context.

LRC mode: each reliable anchor starts a group; lines without an anchor are
aligned jointly with the group they follow, bounded by the neighbouring
reliable anchors.  A task decodes its *participating* lines (targets plus
optional context lines) inside

    W = [eff(first participating) - left_margin, next_anchor + right_margin]

but only commits its *retained* lines.  The next anchor only bounds the
search; it is not the end of the line.  When the LRC marks where the last
participating line ends (a timed blank line before an interlude), the window
also stops ``end_marker_margin`` after that mark, so a line cannot reach into
a long interlude.  Lines written *before* the first timed line have no anchor
of their own: their window starts at the audio start, not ``left_margin``
before the first anchor (they may be sung long before it).  Windows are
clipped to the audio.  Lines left out of a run (``exclude``: lines after the
end of the audio) take no part at all – neither as targets nor as context.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Iterable, Optional

from ..models import Calibration, DecodeConfig, Line, LyricsDoc
from ..timebase import FrameMap
from .calibration import effective_line_ends, effective_line_starts


@dataclass
class TaskAnchor:
    line_id: str
    ms: int
    kind: str  # 'soft' | 'hard'
    tolerance_ms: int = 0


@dataclass
class Task:
    id: str
    voice: str
    participating_line_ids: list[str]
    retained_line_ids: list[str]
    start_frame: int
    end_frame: int  # exclusive, in full-emission frames
    anchors: list[TaskAnchor] = field(default_factory=list)
    kind: str = "plain"  # plain | window | joint
    sigma_scale: float = 1.0
    # no lyrics follow inside the window (it runs to the end of the audio): singing after the last
    # line (an outro the lyrics do not have) may stay unexplained (ctc free tail)
    free_tail: bool = False

    @property
    def context_line_ids(self) -> list[str]:
        r = set(self.retained_line_ids)
        return [x for x in self.participating_line_ids if x not in r]


def _voices(lines: Iterable[Line]) -> dict[str, list[Line]]:
    out: dict[str, list[Line]] = {}
    for ln in lines:
        out.setdefault(ln.voice, []).append(ln)
    return out


def _frames(ms_lo: float, ms_hi: float, fm: FrameMap, num_frames: int) -> tuple[int, int]:
    s = max(0, int(math.floor(fm.ms_to_frame(ms_lo))))
    e = min(num_frames, int(math.ceil(fm.ms_to_frame(ms_hi))))
    return s, max(s, e)


def plan_plain(doc: LyricsDoc, num_frames: int, line_ids: Optional[list[str]] = None,
               exclude: Optional[set[str]] = None) -> list[Task]:
    tasks = []
    exclude = exclude or set()
    for voice, lines in _voices(ln for ln in doc.sung_lines() if ln.id not in exclude).items():
        ids = [ln.id for ln in lines]
        retained = [i for i in ids if line_ids is None or i in line_ids]
        if not retained:
            continue
        # participating = the whole ordered stream; lyrics are never split by chunk
        tasks.append(Task(f"plain-{voice}", voice, ids, retained, 0, num_frames, [], "plain", free_tail=True))
    return tasks


def plan_plain_local(doc: LyricsDoc, line_ids: list[str], ranges: dict[str, tuple[int, int]], cfg: DecodeConfig,
                     frame_map: FrameMap, num_frames: int, audio_duration_ms: int,
                     exclude: Optional[set[str]] = None) -> Optional[list[Task]]:
    """Plain-mode local rerun: each run of consecutive target lines is decoded between its
    neighbours' times in the previous result (``ranges``: line_id -> (start, end) ms), with those
    neighbours as context lines, instead of decoding the whole song again.

    Returns None when a neighbour has no time (then the whole stream must be decoded)."""
    exclude = exclude or set()
    wanted = set(line_ids)
    tasks: list[Task] = []
    for voice, lines in _voices(ln for ln in doc.sung_lines() if ln.id not in exclude).items():
        ids = [ln.id for ln in lines]
        i = 0
        while i < len(ids):
            if ids[i] not in wanted:
                i += 1
                continue
            j = i
            while j + 1 < len(ids) and ids[j + 1] in wanted:
                j += 1
            prev_id = ids[i - 1] if i > 0 else None
            next_id = ids[j + 1] if j + 1 < len(ids) else None
            if (prev_id is not None and prev_id not in ranges) or (next_id is not None and next_id not in ranges):
                return None
            lo = ranges[prev_id][0] - cfg.left_margin_ms if prev_id else 0
            hi = ranges[next_id][1] + cfg.right_margin_ms if next_id else audio_duration_ms
            lo, hi = max(0, lo), min(audio_duration_ms, max(hi, lo))
            s, e = _frames(lo, hi, frame_map, num_frames)
            part = ([prev_id] if prev_id else []) + ids[i:j + 1] + ([next_id] if next_id else [])
            tasks.append(Task(f"plain-local-{voice}-{i}", voice, part, ids[i:j + 1], s, e, [], "joint"))
            i = j + 1
    return tasks


def _line_gap_ms(ends: dict[str, int], prev: Line, nxt_start: int) -> Optional[int]:
    """Gap between line end and next start, when the LRC marks the line end."""
    end = ends.get(prev.id)
    return None if end is None else nxt_start - end


def plan_lrc(
    doc: LyricsDoc,
    cal: Calibration,
    cfg: DecodeConfig,
    frame_map: FrameMap,
    num_frames: int,
    audio_duration_ms: int,
    line_ids: Optional[list[str]] = None,
    force_joint: Optional[set[str]] = None,
    extra_context: int = 0,
    exclude: Optional[set[str]] = None,
) -> list[Task]:
    starts = effective_line_starts(doc, cal)
    ends = effective_line_ends(doc, cal)
    force_joint = force_joint or set()
    exclude = exclude or set()
    tasks: list[Task] = []
    for voice, lines in _voices(ln for ln in doc.sung_lines() if ln.id not in exclude).items():
        # 1. groups: an anchored line starts a group; unanchored lines follow it
        groups: list[list[Line]] = []
        for ln in lines:
            if ln.id in starts or not groups:
                groups.append([ln])
            else:
                groups[-1].append(ln)
        # leading unanchored lines join the first anchored group
        if len(groups) > 1 and groups[0][0].id not in starts:
            groups[1] = groups[0] + groups[1]
            groups.pop(0)
        if not any(ln.id in starts for g in groups for ln in g):
            continue  # nothing to anchor; caller reports lrc_no_times

        def g_anchor(i: int) -> Optional[int]:
            for ln in groups[i]:
                if ln.id in starts:
                    return starts[ln.id][0]
            return None

        for gi, grp in enumerate(groups):
            retained = [ln.id for ln in grp if line_ids is None or ln.id in line_ids]
            if not retained:
                continue
            # 2. context: tight neighbours / unknown gaps / forced lines, per side
            #    (a line whose LRC marks a clear pause after it needs no context there)
            ctx = cfg.joint_context_lines + extra_context
            lo_g, hi_g = gi, gi
            forced = any(i in force_joint for i in retained) or extra_context > 0
            need_next = need_prev = forced
            if not forced and ctx > 0:
                if gi + 1 < len(groups):
                    na = g_anchor(gi + 1)
                    gap = _line_gap_ms(ends, grp[-1], na) if na is not None else None
                    need_next = gap is None or gap < cfg.tight_gap_ms
                if gi > 0:
                    ga = g_anchor(gi)
                    gap = _line_gap_ms(ends, groups[gi - 1][-1], ga) if ga is not None else None
                    need_prev = gap is None or gap < cfg.tight_gap_ms
            if ctx > 0:
                if need_prev:
                    lo_g = max(0, gi - ctx)
                if need_next:
                    hi_g = min(len(groups) - 1, gi + ctx)
            part_lines = [ln for g in groups[lo_g:hi_g + 1] for ln in g]
            first_anchor = g_anchor(lo_g)
            if first_anchor is None:
                first_anchor = 0
            nxt = g_anchor(hi_g + 1) if hi_g + 1 < len(groups) else None
            lo_ms = first_anchor - cfg.left_margin_ms
            if part_lines[0].id not in starts:
                # lines before the first timed line: nothing bounds them but the audio start
                # (the soft anchor of the timed line still pulls it to its time)
                lo_ms = 0
            hi_ms = (nxt + cfg.right_margin_ms) if nxt is not None else audio_duration_ms
            last_end = ends.get(part_lines[-1].id)
            free_tail = nxt is None and last_end is None
            if last_end is not None:
                hi_ms = min(hi_ms, last_end + cfg.end_marker_margin_ms)
            anchors = []
            for ln in part_lines:
                if ln.id in starts:
                    ms, kind = starts[ln.id]
                    tol = ln.anchor.tolerance_ms if (ln.anchor is not None) else cfg.hard_tolerance_ms
                    anchors.append(TaskAnchor(ln.id, ms, kind, tol))
                    if kind == "hard":  # a hard anchor must lie inside its window
                        lo_ms = min(lo_ms, ms - tol - cfg.left_margin_ms)
            lo_ms = max(0, lo_ms)
            hi_ms = min(audio_duration_ms, max(hi_ms, lo_ms))
            s, e = _frames(lo_ms, hi_ms, frame_map, num_frames)
            kind = "joint" if len(part_lines) > len(retained) or len(grp) > 1 else "window"
            tasks.append(Task(f"lrc-{voice}-{gi}", voice, [ln.id for ln in part_lines], retained, s, e, anchors, kind,
                              free_tail=free_tail))
    return tasks


def merge_tasks(a: Task, b: Task, doc: LyricsDoc) -> Task:
    """Joint task covering two tasks (used to resolve boundary conflicts)."""
    order = {ln.id: i for i, ln in enumerate(doc.lines)}
    part = sorted(set(a.participating_line_ids) | set(b.participating_line_ids), key=order.__getitem__)
    ret = sorted(set(a.retained_line_ids) | set(b.retained_line_ids), key=order.__getitem__)
    anchors = {x.line_id: x for x in a.anchors + b.anchors}
    return Task(
        f"{a.id}+{b.id}", a.voice, part, ret, min(a.start_frame, b.start_frame), max(a.end_frame, b.end_frame),
        [anchors[i] for i in part if i in anchors], "joint", max(a.sigma_scale, b.sigma_scale),
        free_tail=(a if a.end_frame >= b.end_frame else b).free_tail,
    )
