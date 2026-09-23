"""Deterministic scripted CTC backend for tests and demos.

Given a script of ``(text, start_ms, end_ms)`` entries it produces a
log-probability matrix where the characters of ``text`` are spread evenly over
their interval (blank elsewhere).  Audio content is ignored except its length.
It is *not* an aligner – it only fabricates emissions whose ground truth is
known, so decoding can be tested end to end.
"""

from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from ...interfaces import Emission, TokenizedUnit
from ...models import BackendInfo
from ...timebase import FrameMap
from .wav2vec2_ctc import tokenize_chars

VOCAB = {"<blank>": 0, **{chr(ord("a") + i): i + 1 for i in range(26)}, "'": 27}


class ScriptedBackend:
    name = "scripted"
    profile = "ja-hepburn"
    languages = ("ja", "en")
    # tests may set a default script on the class so get_backend(config) can build it
    default_script: list[tuple[str, int, int]] = []

    def __init__(self, script: Optional[Sequence[tuple[str, int, int]]] = None, *, noise: float = 0.0,
                 seed: int = 0, peak: float = 0.9, sample_rate: int = 16000, hop: int = 320) -> None:
        self.script = list(script if script is not None else self.default_script)
        self.noise = noise
        self.seed = seed
        self.peak = peak
        self._sr = sample_rate
        self.hop = hop

    @property
    def sample_rate(self) -> int:
        return self._sr

    def info(self) -> BackendInfo:
        return BackendInfo(name=self.name, model_id="scripted", model_revision="1", license="n/a",
                           profile=self.profile, sample_rate=self._sr, frame_hop_samples=self.hop,
                           extra={"noise": self.noise, "seed": self.seed})

    def supports_language(self, lang: str) -> bool:
        return lang in self.languages

    def tokenize(self, unit_ids: Sequence[str], unit_texts: Sequence[str]) -> list[TokenizedUnit]:
        return tokenize_chars(unit_ids, unit_texts, VOCAB, {"<blank>"})

    def emissions(self, audio: np.ndarray, origin_samples: int = 0, cancel=None, progress=None) -> Emission:
        n_frames = int(len(audio)) // self.hop
        fmap = FrameMap(self._sr, self.hop, 0, int(origin_samples))
        V = len(VOCAB)
        rest = (1.0 - self.peak) / (V - 1)
        probs = np.full((n_frames, V), rest, dtype=np.float64)
        probs[:, 0] = self.peak
        for text, start_ms, end_ms in self.script:
            chars = [VOCAB[c] for c in text.lower() if c in VOCAB and c != "<blank>"]
            if not chars:
                continue
            f0 = max(0, int(round(fmap.ms_to_frame(start_ms))))
            f1 = min(n_frames, int(round(fmap.ms_to_frame(end_ms))))
            if f1 <= f0:
                continue
            edges = np.linspace(f0, f1, len(chars) + 1)
            for i, tok in enumerate(chars):
                a, b = int(round(edges[i])), max(int(round(edges[i])) + 1, int(round(edges[i + 1])))
                probs[a:b] = rest
                probs[a:b, tok] = self.peak
        if self.noise > 0:
            rng = np.random.default_rng(self.seed)
            probs = probs * np.exp(rng.normal(0.0, self.noise, probs.shape))
            probs /= probs.sum(axis=1, keepdims=True)
        if progress is not None:
            progress(1.0)
        return Emission(logp=np.log(probs).astype(np.float32), frame_map=fmap, blank_id=0)
