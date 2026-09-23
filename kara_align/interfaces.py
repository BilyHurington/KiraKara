"""Small adapter interfaces shared by modules (no plugin framework)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, Protocol, Sequence

import numpy as np

from .models import BackendInfo
from .timebase import FrameMap


@dataclass
class Emission:
    """Frame-wise log-probabilities ``logp[t, token]`` for an analysed signal."""

    logp: np.ndarray  # float32 [T, V], log-softmax over tokens
    frame_map: FrameMap
    blank_id: int
    cache_key: Optional[str] = None

    @property
    def num_frames(self) -> int:
        return int(self.logp.shape[0])

    def slice(self, start_frame: int, end_frame: int) -> "Emission":
        s = max(0, int(start_frame))
        e = min(self.num_frames, int(end_frame))
        return Emission(self.logp[s:e], self.frame_map.shifted(s), self.blank_id, None)


@dataclass
class TokenizedUnit:
    """Model tokens for one public Unit, identified by position (not value)."""

    unit_id: str
    token_text: str  # profile output, e.g. romaji "ki"
    token_ids: list[int]
    unknown: list[str] = field(default_factory=list)  # characters the vocab lacks


class TranslitProfile(Protocol):
    """Fixed deterministic transliteration from unit readings to model text."""

    name: str

    def unit_texts(self, readings: Sequence[str], langs: Sequence[str], flags: Sequence[Sequence[str]]) -> list[str]:
        """Return model-side text for each unit (same length as input)."""
        ...


class AcousticBackend(Protocol):
    """CTC style backend: audio -> logp[t, token]; decoding happens elsewhere."""

    def info(self) -> BackendInfo: ...

    @property
    def sample_rate(self) -> int: ...

    def supports_language(self, lang: str) -> bool: ...

    def tokenize(self, unit_ids: Sequence[str], unit_texts: Sequence[str]) -> list[TokenizedUnit]: ...

    def emissions(self, audio: np.ndarray, origin_samples: int = 0, cancel=None, progress=None) -> Emission:
        """``audio`` is mono float32 at ``sample_rate``; chunked internally."""
        ...


class CancelToken:
    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def check(self) -> None:
        if self._cancelled:
            raise Cancelled()


class Cancelled(Exception):
    pass
