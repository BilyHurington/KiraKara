"""Vocal activity from the separated vocal stem.

The stem is never perfectly clean: interludes still carry instrument bleed a
dozen dB or more below the singing.  Activity is therefore judged *relative* to
the song's own vocal level, with a soft ramp instead of a hard threshold::

    ref       = 90th percentile of the smoothed envelope (loud singing)
    rest(t)   = clip((ref - ACTIVE_DROP_DB - level(t)) / (REST_DROP_DB - ACTIVE_DROP_DB), 0, 1)

``rest = 0`` is certainly singing, ``rest = 1`` certainly not.  The decoder uses
it as a soft cost (never a hard constraint), the checks use ``rest == 1``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

from ..timebase import FrameMap

SMOOTH_MS = 300.0  # power-domain moving average: bridges consonants and short dips
ACTIVE_DROP_DB = 8.0  # within this of the reference level: singing
REST_DROP_DB = 20.0  # this far below the reference level: not singing
MIN_REFERENCE_DB = -60.0  # quieter than this the stem holds no usable singing
FLOOR_DB = -80.0  # frames below this are ignored for the reference level


@dataclass
class VocalActivity:
    rest: np.ndarray  # float32 in [0, 1] per envelope hop; index i ~ i * hop_ms
    hop_ms: float
    reference_db: float
    level_db: np.ndarray  # smoothed envelope

    def _range(self, a_ms: float, b_ms: float) -> tuple[int, int]:
        n = len(self.rest)
        i0 = int(min(max(0, np.floor(a_ms / self.hop_ms)), n))
        i1 = int(min(max(i0 + 1, np.ceil(b_ms / self.hop_ms)), n))
        return i0, i1

    def rest_fraction(self, a_ms: float, b_ms: float) -> float:
        """Mean rest value over ``[a_ms, b_ms)`` (1.0 outside the envelope)."""
        i0, i1 = self._range(a_ms, b_ms)
        return float(self.rest[i0:i1].mean()) if i1 > i0 else 1.0

    def peak_db(self, a_ms: float, b_ms: float) -> float:
        i0, i1 = self._range(a_ms, b_ms)
        return float(self.level_db[i0:i1].max()) if i1 > i0 else FLOOR_DB

    def is_rest(self, a_ms: float, b_ms: float) -> bool:
        """True when even the loudest moment of the span is below the rest level."""
        return self.peak_db(a_ms, b_ms) <= self.reference_db - REST_DROP_DB

    def for_frames(self, frame_map: FrameMap, start_frame: int, num_frames: int) -> np.ndarray:
        """Rest value per emission frame ``start_frame .. start_frame + num_frames``."""
        out = np.ones(num_frames, dtype=np.float64)
        if num_frames <= 0 or len(self.rest) == 0:
            return out
        edges = frame_map.frame_time_ms_float(start_frame + np.arange(num_frames + 1, dtype=np.float64))
        idx = np.clip(np.round(edges / self.hop_ms).astype(np.int64), 0, len(self.rest))
        cs = np.concatenate([[0.0], np.cumsum(self.rest, dtype=np.float64)])
        lo, hi = idx[:-1], np.maximum(idx[1:], idx[:-1] + 1)
        inside = hi <= len(self.rest)
        out[inside] = (cs[hi[inside]] - cs[lo[inside]]) / (hi[inside] - lo[inside])
        return out


def detect_activity(env_db: np.ndarray, hop_ms: float) -> Optional[VocalActivity]:
    """Activity from a dB RMS envelope of the vocal stem, or None when it is silent."""
    env = np.asarray(env_db, dtype=np.float64)
    if env.size == 0:
        return None
    k = max(1, int(round(SMOOTH_MS / hop_ms)))
    power = np.power(10.0, env / 10.0)
    smooth = np.convolve(power, np.ones(k) / k, mode="same")
    level = 10.0 * np.log10(np.maximum(smooth, 1e-12))
    audible = level[level > FLOOR_DB]
    if audible.size == 0:
        return None
    ref = float(np.percentile(audible, 90))
    if ref < MIN_REFERENCE_DB:
        return None
    rest = np.clip((ref - ACTIVE_DROP_DB - level) / (REST_DROP_DB - ACTIVE_DROP_DB), 0.0, 1.0)
    return VocalActivity(rest.astype(np.float32), float(hop_ms), ref, level.astype(np.float32))
