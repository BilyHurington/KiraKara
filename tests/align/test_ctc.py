import itertools
import math

import numpy as np
import pytest

from kara_align.align.ctc import (
    AnchorSpec,
    NoFeasiblePath,
    anchor_cost_curve,
    ctc_align,
    ctc_align_reference,
    min_frames_needed,
)


def brute_force(logp, targets, blank, anchors=()):
    """Enumerate every legal state sequence; return (best score, states)."""
    T = logp.shape[0]
    z = [blank]
    for tok in targets:
        z += [tok, blank]
    S = len(z)
    entry = {}
    for a in anchors:
        entry.setdefault(2 * a.token_index + 1, np.zeros(T))
        entry[2 * a.token_index + 1] = entry[2 * a.token_index + 1] + anchor_cost_curve(a, T)
    best = (-math.inf, None)

    def rec(t, s, score, path):
        nonlocal best
        if t == T - 1:
            if s in (S - 1, S - 2) and score > best[0]:
                best = (score, list(path))
            return
        for d in (0, 1, 2):
            ns = s + d
            if ns >= S:
                continue
            if d == 2 and (z[ns] == blank or z[ns] == z[ns - 2]):
                continue
            c = logp[t + 1, z[ns]] + (entry[ns][t + 1] if d > 0 and ns in entry else 0.0)
            rec(t + 1, ns, score + c, path + [ns])

    for s0 in (0, 1):
        c = logp[0, z[s0]] + (entry[s0][0] if s0 in entry else 0.0)
        rec(0, s0, c, [s0])
    return best


def rand_logp(T, V, seed):
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(T, V))
    return x - np.log(np.exp(x).sum(axis=1, keepdims=True))


@pytest.mark.parametrize("seed", range(12))
def test_vectorised_reference_bruteforce_agree(seed):
    rng = np.random.default_rng(100 + seed)
    T = int(rng.integers(4, 8))
    V = 4
    U = int(rng.integers(1, 4))
    targets = [int(x) for x in rng.integers(1, V, size=U)]
    if T < min_frames_needed(targets):
        targets = targets[:1]
    logp = rand_logp(T, V, seed)
    anchors = []
    if seed % 3 == 1:
        anchors = [AnchorSpec(token_index=0, kind="soft", frame=T / 2, sigma_frames=1.0, lam=2.0)]
    elif seed % 3 == 2:
        anchors = [AnchorSpec(token_index=len(targets) - 1, kind="hard", lo_frame=T - 3, hi_frame=T - 1)]
    bf_score, bf_states = brute_force(logp, targets, 0, anchors)
    a = ctc_align(logp, targets, 0, anchors)
    b = ctc_align_reference(logp, targets, 0, anchors)
    assert a.total_score == pytest.approx(bf_score)
    assert b.total_score == pytest.approx(bf_score)
    assert list(a.states) == bf_states
    assert list(b.states) == bf_states


def test_repeated_tokens_need_blank():
    # "aa": needs at least 3 frames
    logp = np.log(np.full((2, 3), 1 / 3))
    with pytest.raises(NoFeasiblePath):
        ctc_align(logp, [1, 1], 0)
    logp = np.log(np.array([[0.1, 0.8, 0.1], [0.8, 0.1, 0.1], [0.1, 0.8, 0.1]]))
    p = ctc_align(logp, [1, 1], 0)
    assert [(s.start_frame, s.end_frame) for s in p.spans] == [(0, 1), (2, 3)]
    assert list(p.states) == [1, 2, 3]


def test_too_few_frames_and_empty():
    with pytest.raises(NoFeasiblePath):
        ctc_align(np.zeros((1, 3)), [1, 2], 0)
    with pytest.raises(NoFeasiblePath):
        ctc_align(np.zeros((5, 3)), [], 0)


def _flat(T, V=3):
    return np.log(np.full((T, V), 1.0 / V))


def test_soft_anchor_pulls_entry_and_charged_once():
    T = 20
    logp = _flat(T)
    a = AnchorSpec(token_index=0, kind="soft", frame=12, sigma_frames=2, lam=3.0)
    p = ctc_align(logp, [1], 0, [a])
    assert p.spans[0].start_frame == 12
    assert p.anchor_cost == pytest.approx(0.0)
    # entry at frame 12 while staying 5 frames is still charged only once
    a2 = AnchorSpec(token_index=0, kind="soft", frame=12.5, sigma_frames=1, lam=1.0)
    p2 = ctc_align(logp, [1], 0, [a2])
    assert p2.anchor_cost == pytest.approx(-0.125)


def test_frame0_entry_is_charged():
    # token strongly present at frame 0 only; anchor far away -> cost applies at frame 0
    T = 6
    logp = np.log(np.full((T, 3), 0.01))
    logp[:, 0] = np.log(0.98)
    logp[0, 1] = np.log(0.98)
    logp[0, 0] = np.log(0.01)
    a = AnchorSpec(token_index=0, kind="soft", frame=5, sigma_frames=1, lam=1.0, huber_delta=1.0)
    p = ctc_align(logp, [1], 0, [a])
    ref = ctc_align_reference(logp, [1], 0, [a])
    assert p.total_score == pytest.approx(ref.total_score)
    if p.spans[0].start_frame == 0:
        assert p.anchor_cost == pytest.approx(-(5 - 0.5))
    bf, _ = brute_force(logp, [1], 0, [a])
    assert p.total_score == pytest.approx(bf)


def test_hard_anchor_window_and_infeasible():
    T = 10
    logp = _flat(T)
    a = AnchorSpec(token_index=1, kind="hard", lo_frame=6.4, hi_frame=6.6)
    p = ctc_align(logp, [1, 2], 0, [a])
    assert 6 <= p.spans[1].start_frame <= 7
    bad = AnchorSpec(token_index=0, kind="hard", lo_frame=50, hi_frame=60)
    with pytest.raises(NoFeasiblePath) as e:
        ctc_align(logp, [1], 0, [bad])
    assert "hard" in e.value.reason


def test_band_matches_unbanded_for_wide_band():
    logp = rand_logp(40, 5, 3)
    t = [1, 2, 3, 4, 1]
    assert ctc_align(logp, t, 0, band=100).total_score == pytest.approx(ctc_align(logp, t, 0).total_score)


def test_spans_follow_evidence():
    T, V = 30, 4
    logp = np.full((T, V), np.log(0.02))
    logp[:, 0] = np.log(0.94)
    for tok, (s, e) in zip([1, 2, 3], [(3, 6), (10, 14), (20, 22)]):
        logp[s:e, :] = np.log(0.02)
        logp[s:e, tok] = np.log(0.94)
    p = ctc_align(logp, [1, 2, 3], 0)
    assert [(s.start_frame, s.end_frame) for s in p.spans] == [(3, 6), (10, 14), (20, 22)]
