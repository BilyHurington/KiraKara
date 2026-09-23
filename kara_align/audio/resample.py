"""Origin-preserving resampling.

``scipy.signal.resample_poly`` applies a linear-phase FIR and compensates its
group delay, so output sample ``k`` corresponds to input time ``k/sr_to`` –
sample 0 stays at time 0 (no shift of the timeline).  Output length is
``round(n * sr_to / sr_from)`` (half-up) regardless of scipy's ceil rule.
"""

from __future__ import annotations

from fractions import Fraction

import numpy as np
from scipy.signal import resample_poly


def to_mono(x: np.ndarray) -> np.ndarray:
    """Average channels of ``[channels, n]`` (or pass 1-D through) -> 1-D float32."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        return x
    if x.shape[0] == 1:
        return x[0]
    return x.mean(axis=0).astype(np.float32)


def resampled_length(n: int, sr_from: int, sr_to: int) -> int:
    return int((Fraction(n) * sr_to / sr_from) + Fraction(1, 2))


def resample(x: np.ndarray, sr_from: int, sr_to: int) -> np.ndarray:
    """Resample along the last axis with exact rational factors."""
    x = np.asarray(x, dtype=np.float32)
    if sr_from == sr_to:
        return x.copy()
    if sr_from <= 0 or sr_to <= 0:
        raise ValueError("采样率必须为正数")
    frac = Fraction(sr_to, sr_from)
    up, down = frac.numerator, frac.denominator
    n = x.shape[-1]
    target = resampled_length(n, sr_from, sr_to)
    if n == 0:
        return np.zeros(x.shape[:-1] + (0,), dtype=np.float32)
    y = resample_poly(x, up, down, axis=-1).astype(np.float32)
    if y.shape[-1] > target:
        y = y[..., :target]
    elif y.shape[-1] < target:
        pad = [(0, 0)] * (y.ndim - 1) + [(0, target - y.shape[-1])]
        y = np.pad(y, pad)
    return y
