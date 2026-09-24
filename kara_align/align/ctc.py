"""CTC Viterbi forced alignment with time priors (design §4.5).

Target tokens ``y`` (length U) are expanded with blanks into states
``z = [b, y1, b, y2, ..., yU, b]`` (S = 2U + 1).  ``D[t, s]`` is the best score
of a path that is in state ``s`` at frame ``t``::

    D[t,s] = logp[t, z_s] + max over legal s' of (D[t-1, s'] + A(t, s', s))

Legal predecessors: ``s`` (stay), ``s-1``, and ``s-2`` when ``z_s`` is not blank
and ``z_s != z_{s-2}`` (so adjacent identical tokens need a blank between them).
Frame 0 may only be in state 0 (leading blank) or 1 (first token); the path
must end in the last token (S-2) or the trailing blank (S-1).

Anchor cost ``A`` is charged exactly once, on the *entry* into an anchored
token state (transition from ``s-1`` / ``s-2``, or starting in it at frame 0);
self loops are free.

* soft: ``A = -lam * Huber((t - frame) / sigma)``
* hard: ``A = 0`` if ``floor(lo) <= t <= ceil(hi)`` else ``-inf``

Optional per-frame priors (``FramePriors``, all costs <= 0) are added to the
emission score of a state at frame ``t``:

* ``token[t]``  on every token state (e.g. singing placed where the vocal stem
  is silent);
* ``gap[t]``    on the *interior* blank states listed in ``gap_states`` (the
  blanks between two tokens of the same lyric line), so a line cannot stretch
  across an interlude for free.  Leading / trailing / between-line blanks stay
  free.

Tie-breaking (deterministic, identical in both implementations): among equal
predecessor scores prefer *stay*, then ``s-1``, then ``s-2``; at the end prefer
the trailing blank ``S-1`` over the last token ``S-2`` when scores are equal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Optional, Sequence

import numpy as np

NEG_INF = -np.inf


class NoFeasiblePath(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class AnchorSpec:
    token_index: int  # index into targets (not state index)
    kind: Literal["soft", "hard"] = "soft"
    frame: float = 0.0  # centre, in frames of the given logp
    sigma_frames: float = 1.0
    lam: float = 1.0
    huber_delta: float = 1.0
    lo_frame: Optional[float] = None
    hi_frame: Optional[float] = None


@dataclass(frozen=True)
class FramePriors:
    token: Optional[np.ndarray] = None  # [T] cost (<= 0) per frame on token states
    gap: Optional[np.ndarray] = None  # [T] cost (<= 0) per frame on gap_states
    gap_states: Optional[np.ndarray] = None  # [S] bool: interior blank states


@dataclass
class TokenSpan:
    token_index: int
    start_frame: int
    end_frame: int  # exclusive
    acoustic_score: float  # mean logp over the span's frames


@dataclass
class CtcPath:
    spans: list[TokenSpan]
    total_score: float
    acoustic_score: float
    anchor_cost: float
    num_frames: int
    states: Optional[np.ndarray] = field(default=None, repr=False)  # state per frame
    prior_cost: float = 0.0  # sum of FramePriors along the path (<= 0)

    @property
    def mean_acoustic(self) -> float:
        """Acoustic log-score normalised per frame (not a probability)."""
        return self.acoustic_score / max(1, self.num_frames)


def huber(x: np.ndarray | float, delta: float = 1.0):
    a = np.abs(x)
    return np.where(a <= delta, 0.5 * a * a, delta * (a - 0.5 * delta))


def anchor_cost_curve(anchor: AnchorSpec, num_frames: int) -> np.ndarray:
    """Cost A(t) (<= 0) for entering the anchored token at frame t."""
    t = np.arange(num_frames, dtype=np.float64)
    if anchor.kind == "hard":
        lo = -np.inf if anchor.lo_frame is None else math.floor(anchor.lo_frame)
        hi = np.inf if anchor.hi_frame is None else math.ceil(anchor.hi_frame)
        return np.where((t >= lo) & (t <= hi), 0.0, NEG_INF)
    sigma = max(float(anchor.sigma_frames), 1e-9)
    return -float(anchor.lam) * huber((t - anchor.frame) / sigma, anchor.huber_delta)


def _states(targets: Sequence[int], blank: int) -> np.ndarray:
    z = np.full(2 * len(targets) + 1, blank, dtype=np.int64)
    z[1::2] = np.asarray(targets, dtype=np.int64)
    return z


def _allow_skip(z: np.ndarray, blank: int) -> np.ndarray:
    allow = np.zeros(len(z), dtype=bool)
    if len(z) > 2:
        allow[2:] = (z[2:] != blank) & (z[2:] != z[:-2])
    return allow


def min_frames_needed(targets: Sequence[int]) -> int:
    reps = sum(1 for i in range(1, len(targets)) if targets[i] == targets[i - 1])
    return len(targets) + reps


def _validate(logp: np.ndarray, targets: Sequence[int], blank: int, anchors: Sequence[AnchorSpec]) -> None:
    if logp.ndim != 2:
        raise ValueError("logp 必须是 [T, V] 矩阵")
    if len(targets) == 0:
        raise NoFeasiblePath("empty target sequence")
    V = logp.shape[1]
    for tok in targets:
        if tok == blank:
            raise ValueError("目标序列不能包含 blank token")
        if not 0 <= tok < V:
            raise ValueError(f"token id {tok} 超出词表大小 {V}")
    for a in anchors:
        if not 0 <= a.token_index < len(targets):
            raise ValueError(f"锚点 token_index {a.token_index} 越界")
    if logp.shape[0] < min_frames_needed(targets):
        raise NoFeasiblePath(
            f"帧数不足：{logp.shape[0]} < {len(targets)} 个 token 所需的 {min_frames_needed(targets)} 帧"
        )


def _entry_costs(anchors: Sequence[AnchorSpec], T: int) -> dict[int, np.ndarray]:
    """state index -> combined entry cost per frame."""
    out: dict[int, np.ndarray] = {}
    for a in anchors:
        s = 2 * a.token_index + 1
        c = anchor_cost_curve(a, T)
        out[s] = out[s] + c if s in out else c
    return out


def _check_priors(priors: Optional[FramePriors], T: int, z: np.ndarray, blank: int
                  ) -> tuple[Optional[np.ndarray], Optional[np.ndarray], Optional[np.ndarray]]:
    """Validated (token[T], gap[T], gap_states[S]) arrays; None for absent parts."""
    if priors is None:
        return None, None, None
    tok = gap = mask = None
    if priors.token is not None:
        tok = np.asarray(priors.token, dtype=np.float64)
        if tok.shape != (T,):
            raise ValueError("token prior 长度必须等于帧数")
    if priors.gap is not None and priors.gap_states is not None:
        gap = np.asarray(priors.gap, dtype=np.float64)
        mask = np.asarray(priors.gap_states, dtype=bool)
        if gap.shape != (T,) or mask.shape != (len(z),):
            raise ValueError("gap prior 尺寸不匹配")
        if np.any(mask & (z != blank)):
            raise ValueError("gap_states 只能包含 blank 状态")
    for a in (tok, gap):
        if a is not None and (np.any(a > 0) or not np.all(np.isfinite(a))):
            raise ValueError("先验代价必须是有限的非正数")
    return tok, gap, mask


def _prior_at(pri, t: int, s: int) -> float:
    tok, gap, mask = pri
    v = 0.0
    if tok is not None and s % 2 == 1:
        v += float(tok[t])
    if gap is not None and mask[s]:
        v += float(gap[t])
    return v


def _path_prior_cost(pri, states: np.ndarray) -> float:
    tok, gap, mask = pri
    v = 0.0
    if tok is not None:
        v += float(tok[states % 2 == 1].sum())
    if gap is not None:
        v += float(gap[mask[states]].sum())
    return v


def _band_mask(T: int, S: int, band: Optional[int]) -> Optional[tuple[np.ndarray, np.ndarray]]:
    if band is None:
        return None
    centre = np.arange(T) * (S - 1) / max(1, T - 1)
    lo = np.floor(centre - band).astype(np.int64)
    hi = np.ceil(centre + band).astype(np.int64)
    return np.clip(lo, 0, S - 1), np.clip(hi, 0, S - 1)


def _backtrack(bp: np.ndarray, end_state: int) -> np.ndarray:
    T = bp.shape[0]
    states = np.empty(T, dtype=np.int64)
    s = end_state
    for t in range(T - 1, -1, -1):
        states[t] = s
        if t > 0:
            s -= int(bp[t, s])
    return states


def _path_from_states(logp, z, states, anchors_by_state, U, pri=(None, None, None)) -> CtcPath:
    T = len(states)
    frame_scores = logp[np.arange(T), z[states]]
    acoustic = float(frame_scores.sum())
    prior_cost = _path_prior_cost(pri, states)
    anchor_cost = 0.0
    prev = -1
    for t in range(T):
        s = int(states[t])
        if s != prev and s in anchors_by_state:
            anchor_cost += float(anchors_by_state[s][t])
        prev = s
    spans: list[TokenSpan] = []
    for j in range(U):
        idx = np.nonzero(states == 2 * j + 1)[0]
        # a legal CTC path visits every token state at least once
        st, en = int(idx[0]), int(idx[-1]) + 1
        spans.append(TokenSpan(j, st, en, float(frame_scores[st:en].mean())))
    return CtcPath(spans, acoustic + anchor_cost + prior_cost, acoustic, anchor_cost, T, states, prior_cost)


def ctc_align(
    logp: np.ndarray,
    targets: Sequence[int],
    blank: int,
    anchors: Sequence[AnchorSpec] = (),
    band: Optional[int] = None,
    priors: Optional[FramePriors] = None,
) -> CtcPath:
    """Vectorised Viterbi forced alignment. Raises :class:`NoFeasiblePath`."""
    logp = np.asarray(logp, dtype=np.float64)
    targets = [int(x) for x in targets]
    _validate(logp, targets, blank, anchors)
    T = logp.shape[0]
    U = len(targets)
    z = _states(targets, blank)
    S = len(z)
    allow2 = _allow_skip(z, blank)
    entry = _entry_costs(anchors, T)
    entry_states = np.array(sorted(entry), dtype=np.int64)
    entry_mat = np.stack([entry[s] for s in entry_states]) if len(entry_states) else None
    bandlim = _band_mask(T, S, band)

    emit = logp[:, z]  # [T, S] (a copy: priors are added in place)
    pri = _check_priors(priors, T, z, blank)
    if pri[0] is not None:
        emit[:, 1::2] += pri[0][:, None]
    if pri[1] is not None:
        for s_ in np.nonzero(pri[2])[0]:
            emit[:, s_] += pri[1]
    bp = np.zeros((T, S), dtype=np.int8)
    D = np.full(S, NEG_INF)
    D[0] = emit[0, 0]
    D[1] = emit[0, 1] + (entry[1][0] if 1 in entry else 0.0)
    if bandlim is not None:
        lo, hi = bandlim
        outside = np.ones(S, dtype=bool)
        outside[lo[0]:hi[0] + 1] = False
        D[outside] = NEG_INF

    cand = np.empty((3, S))
    ecost = np.zeros(S)
    for t in range(1, T):
        cand[0] = D
        cand[1, 0] = NEG_INF
        cand[1, 1:] = D[:-1]
        cand[2, :2] = NEG_INF
        cand[2, 2:] = D[:-2]
        cand[2, ~allow2] = NEG_INF
        if entry_mat is not None:
            ecost[entry_states] = entry_mat[:, t]
            cand[1] += ecost
            cand[2] += ecost
        choice = np.argmax(cand, axis=0)  # first max wins: stay > s-1 > s-2
        best = cand[choice, np.arange(S)]
        bp[t] = choice
        D = best + emit[t]
        if bandlim is not None:
            lo, hi = bandlim
            outside = np.ones(S, dtype=bool)
            outside[lo[t]:hi[t] + 1] = False
            D[outside] = NEG_INF

    end_state = S - 1 if D[S - 1] >= D[S - 2] else S - 2
    if not np.isfinite(D[end_state]):
        reason = "没有满足硬锚点约束的路径" if any(a.kind == "hard" for a in anchors) else "没有可行的 CTC 路径"
        if band is not None:
            reason += f" (band={band})"
        raise NoFeasiblePath(reason)
    states = _backtrack(bp, end_state)
    return _path_from_states(logp, z, states, entry, U, pri)


def ctc_align_reference(
    logp: np.ndarray,
    targets: Sequence[int],
    blank: int,
    anchors: Sequence[AnchorSpec] = (),
    priors: Optional[FramePriors] = None,
) -> CtcPath:
    """Plain-loop reference implementation with identical semantics."""
    logp = np.asarray(logp, dtype=np.float64)
    targets = [int(x) for x in targets]
    _validate(logp, targets, blank, anchors)
    T = logp.shape[0]
    U = len(targets)
    z = _states(targets, blank)
    S = len(z)
    entry = _entry_costs(anchors, T)
    pri = _check_priors(priors, T, z, blank)

    def A(t: int, s: int) -> float:
        return float(entry[s][t]) if s in entry else 0.0

    def E(t: int, s: int) -> float:
        return float(logp[t, z[s]]) + _prior_at(pri, t, s)

    D = [[NEG_INF] * S for _ in range(T)]
    bp = np.zeros((T, S), dtype=np.int8)
    D[0][0] = E(0, 0)
    D[0][1] = E(0, 1) + A(0, 1)
    for t in range(1, T):
        for s in range(S):
            best, arg = D[t - 1][s], 0
            if s >= 1:
                v = D[t - 1][s - 1] + A(t, s)
                if v > best:
                    best, arg = v, 1
            if s >= 2 and z[s] != blank and z[s] != z[s - 2]:
                v = D[t - 1][s - 2] + A(t, s)
                if v > best:
                    best, arg = v, 2
            D[t][s] = best + E(t, s)
            bp[t, s] = arg
    end_state = S - 1 if D[T - 1][S - 1] >= D[T - 1][S - 2] else S - 2
    if not math.isfinite(D[T - 1][end_state]):
        raise NoFeasiblePath("没有可行的 CTC 路径")
    states = _backtrack(bp, end_state)
    return _path_from_states(logp, z, states, entry, U, pri)
