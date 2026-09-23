"""Stem synchronisation checks.

Equal length does not imply sync: a separated or imported stem is compared to
the original by cross-correlation to measure its lag.  Instrumental tracks are
never manufactured as ``original - vocals`` unless the vocals are proven to be
sample-aligned with the original.
"""

from __future__ import annotations

from typing import Optional

import numpy as np

from .resample import resample, to_mono

ANALYSIS_SR = 8000


class SyncError(Exception):
    pass


def _prep(x: np.ndarray, sr: int, analysis_sr: Optional[int]) -> tuple[np.ndarray, int]:
    x = to_mono(x).astype(np.float64)
    if analysis_sr and sr > analysis_sr:
        x = resample(x.astype(np.float32), sr, analysis_sr).astype(np.float64)
        sr = analysis_sr
    return x - x.mean() if len(x) else x, sr


def _xcorr_lag(a: np.ndarray, b: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Lag (samples) such that ``b[n] ≈ a[n - lag]`` (positive: b is late)."""
    n = len(a) + len(b) - 1
    nfft = 1 << (n - 1).bit_length()
    A = np.fft.rfft(a, nfft)
    B = np.fft.rfft(b, nfft)
    cc = np.fft.irfft(B * np.conj(A), nfft)
    # lags 0..max_lag live at cc[0..], negative lags at the end
    max_lag = min(max_lag, len(a) - 1, len(b) - 1) if len(a) and len(b) else 0
    lags = np.concatenate([np.arange(0, max_lag + 1), np.arange(-max_lag, 0)])
    vals = np.concatenate([cc[: max_lag + 1], cc[nfft - max_lag:]]) if max_lag > 0 else cc[:1]
    i = int(np.argmax(vals))
    denom = np.sqrt(np.dot(a, a) * np.dot(b, b))
    corr = float(vals[i] / denom) if denom > 0 else 0.0
    return int(lags[i]), corr


def estimate_lag(reference: np.ndarray, other: np.ndarray, sr: int, max_lag_ms: float = 500,
                 analysis_sr: Optional[int] = ANALYSIS_SR) -> tuple[float, float]:
    """Estimate how late ``other`` is relative to ``reference``.

    Returns ``(lag_ms, peak_corr)``; positive lag means ``other`` starts later
    (its content must be moved earlier by ``lag_ms`` to line up).  Signals are
    downsampled to ``analysis_sr`` first, so resolution is 1/analysis_sr.
    """
    a, asr = _prep(reference, sr, analysis_sr)
    b, _ = _prep(other, sr, analysis_sr)
    max_lag = int(round(max_lag_ms * asr / 1000.0))
    lag, corr = _xcorr_lag(a, b, max_lag)
    return lag * 1000.0 / asr, corr


def estimate_lag_samples(reference: np.ndarray, other: np.ndarray, max_lag: int) -> tuple[int, float]:
    """Full-rate lag in samples (no downsampling)."""
    a = to_mono(reference).astype(np.float64)
    b = to_mono(other).astype(np.float64)
    return _xcorr_lag(a - a.mean(), b - b.mean(), max_lag)


def check_stem_sync(original: np.ndarray, stem: np.ndarray, sr: int, role: str,
                    other_stem: Optional[np.ndarray] = None, *, tolerance_ms: float = 2.0,
                    min_corr: float = 0.2, max_lag_ms: float = 500) -> dict:
    """Report whether ``stem`` shares the original's timeline.

    If ``other_stem`` (the complementary stem) is supplied, also reports the
    residual of ``stem + other_stem`` against the original in dB.
    """
    o = to_mono(original)
    s = to_mono(stem)
    lag_ms, corr = estimate_lag(o, s, sr, max_lag_ms=max_lag_ms)
    length_diff = int(len(s) - len(o))
    ok = abs(lag_ms) <= tolerance_ms and corr >= min_corr
    msgs = []
    if length_diff:
        msgs.append(f"长度与原曲相差 {length_diff} 个样本")
    if abs(lag_ms) > tolerance_ms:
        msgs.append(f"{role} 比原曲{'晚' if lag_ms > 0 else '早'} {abs(lag_ms):.1f} ms")
    if corr < min_corr:
        msgs.append(f"与原曲相关性低（{corr:.2f}），无法确认同步")
    report = {
        "role": role,
        "lag_ms": round(float(lag_ms), 3),
        "correlation": round(float(corr), 4),
        "length_diff_samples": length_diff,
        "ok": bool(ok),
        "message": "; ".join(msgs) if msgs else "与原曲同步（延迟在容差内）",
    }
    if other_stem is not None:
        t = to_mono(other_stem)
        n = min(len(o), len(s), len(t))
        resid = o[:n] - (s[:n] + t[:n])
        e_o = float(np.sum(o[:n].astype(np.float64) ** 2))
        e_r = float(np.sum(resid.astype(np.float64) ** 2))
        resid_db = 10 * np.log10(e_r / e_o) if e_o > 0 and e_r > 0 else (-np.inf if e_o > 0 else 0.0)
        report["sum_residual_db"] = round(float(resid_db), 2) if np.isfinite(resid_db) else None
    return report


def derive_instrumental(original: np.ndarray, vocals: np.ndarray, sr: int, *, min_corr: float = 0.5) -> np.ndarray:
    """``original - vocals`` – only when the vocals are proven sample-aligned.

    Raises :class:`SyncError` if the measured lag isn't 0±1 sample, the
    correlation is weak, or the channel/length layout doesn't match.
    """
    o = np.asarray(original, dtype=np.float32)
    v = np.asarray(vocals, dtype=np.float32)
    if o.shape != v.shape:
        raise SyncError(f"形状不一致 {o.shape} vs {v.shape}，拒绝推导伴奏")
    max_lag = int(sr * 0.05)
    lag, corr = estimate_lag_samples(o, v, max_lag)
    if abs(lag) > 1:
        raise SyncError(f"人声相对原曲有 {lag} 个样本延迟，拒绝相减")
    if corr < min_corr:
        raise SyncError(f"人声与原曲相关性 {corr:.2f} 过低，无法确认对齐")
    return (o - v).astype(np.float32)
