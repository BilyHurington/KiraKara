"""Synthetic backend / emission helpers for alignment tests (no model)."""

from __future__ import annotations

import numpy as np

from kara_align.align.runner import AlignInputs
from kara_align.interfaces import Emission, TokenizedUnit
from kara_align.models import (
    AlignConfig,
    AudioAsset,
    BackendInfo,
    Calibration,
    Line,
    LyricsDoc,
    Segment,
    Unit,
)
from kara_align.timebase import FrameMap

SR = 16000
HOP = 320  # 20 ms
VOCAB = "_abcdefghijklmnopqrstuvwxyz"  # index 0 = blank


class IdentityProfile:
    name = "test-identity"

    def unit_texts(self, readings, langs, flags):
        return [r.lower() for r in readings]


def tokenize(unit_ids, texts):
    out = []
    for uid, t in zip(unit_ids, texts):
        ids = [VOCAB.index(c) for c in t if c in VOCAB[1:]]
        unk = [c for c in t if c not in VOCAB[1:]]
        out.append(TokenizedUnit(uid, t, ids, unk))
    return out


def make_emission(script, duration_ms, origin_samples=0, noise=0.0, seed=0) -> Emission:
    """script: list of (unit_text, start_ms, end_ms) -> logp with the unit's chars evenly spread."""
    T = int(np.ceil(duration_ms / 20))
    V = len(VOCAB)
    p = np.full((T, V), 0.02 / (V - 1))
    p[:, 0] = 0.98
    for text, s, e in script:
        fs, fe = int(round(s / 20)), int(round(e / 20))
        n = len(text)
        bounds = np.linspace(fs, fe, n + 1).round().astype(int)
        for c, a, b in zip(text, bounds[:-1], bounds[1:]):
            b = max(b, a + 1)
            p[a:b, :] = 0.02 / (V - 1)
            p[a:b, VOCAB.index(c)] = 0.98
    if noise:
        rng = np.random.default_rng(seed)
        p = p * np.exp(rng.normal(scale=noise, size=p.shape))
    p = p / p.sum(axis=1, keepdims=True)
    return Emission(np.log(p).astype(np.float32), FrameMap(SR, HOP, 0, origin_samples), 0)


def make_doc(lines_units, starts=None, ids=None) -> LyricsDoc:
    """lines_units: list of lists of unit readings (one segment per unit)."""
    lines = []
    for i, us in enumerate(lines_units):
        segs = [Segment(surface=u, reading=u, lang="en", units=[Unit(reading=u, surface=u)]) for u in us]
        ln = Line(text="".join(us), segments=segs)
        if ids:
            ln.id = ids[i]
        if starts is not None and starts[i] is not None:
            ln.imported_start_ms = starts[i]
        lines.append(ln)
    return LyricsDoc(language="en", lines=lines)


def asset(role="original", duration_ms=10000):
    return AudioAsset(role=role, sha256="0" * 64, duration_ms=duration_ms, sample_rate=SR, channels=1,
                      num_samples=duration_ms * 16)


def inputs(doc, emission, mode="plain", cal=None, config=None, duration_ms=None, **kw) -> AlignInputs:
    dur = duration_ms or int(emission.num_frames * 20)
    emissions = kw.pop("emissions", {"original": emission})
    return AlignInputs(
        lyrics=doc, mode=mode, calibration=cal or Calibration(), config=config or AlignConfig(),
        backend_info=BackendInfo(name="test", model_id="synthetic", profile="test-identity", sample_rate=SR,
                                 frame_hop_samples=HOP),
        tokenize=tokenize, profile=IdentityProfile(), emission_for=lambda r: emissions[r],
        available_roles=list(emissions), audio_assets={r: asset(r, dur) for r in emissions},
        audio_duration_ms=dur, **kw,
    )
