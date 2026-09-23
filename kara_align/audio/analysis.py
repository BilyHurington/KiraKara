"""Signal analysis helpers: energy envelope and waveform peaks for the UI."""

from __future__ import annotations

from typing import Optional, Union

import numpy as np

from .resample import to_mono


def rms_envelope_db(x: np.ndarray, sr: int, hop_ms: float = 10.0, win_ms: float = 30.0,
                    floor_db: float = -120.0) -> np.ndarray:
    """RMS energy in dBFS.

    Frame ``i`` is a window of ``win_ms`` *centred* at ``i * hop_ms`` on the
    timeline of ``x`` (sample 0 = time 0; the signal is zero-padded at both
    ends), so ``env[i]`` describes time ``i*hop_ms`` directly.
    """
    y = to_mono(x).astype(np.float64)
    hop = max(1, int(round(sr * hop_ms / 1000.0)))
    win = max(1, int(round(sr * win_ms / 1000.0)))
    half = win // 2
    n_frames = int(np.ceil(len(y) / hop)) if len(y) else 0
    if n_frames == 0:
        return np.zeros(0, dtype=np.float32)
    sq = np.concatenate([np.zeros(half), y * y, np.zeros(win)])
    cs = np.concatenate([[0.0], np.cumsum(sq)])
    starts = np.arange(n_frames) * hop  # index into padded signal == centre - half
    sums = cs[starts + win] - cs[starts]
    rms = np.sqrt(np.maximum(sums / win, 0.0))
    with np.errstate(divide="ignore"):
        db = 20 * np.log10(np.maximum(rms, 1e-12))
    return np.maximum(db, floor_db).astype(np.float32)


def waveform_peaks(x: np.ndarray, sr: int, samples_per_peak: int) -> tuple[np.ndarray, np.ndarray]:
    """Min/max per bucket of ``samples_per_peak`` samples (bucket k starts at k*spp)."""
    y = to_mono(x)
    spp = max(1, int(samples_per_peak))
    n = int(np.ceil(len(y) / spp)) if len(y) else 0
    if n == 0:
        z = np.zeros(0, dtype=np.float32)
        return z, z.copy()
    padded = np.pad(y, (0, n * spp - len(y)))
    # pad region should not inflate peaks: use edge value 0 is fine (silence)
    b = padded.reshape(n, spp)
    return b.min(axis=1).astype(np.float32), b.max(axis=1).astype(np.float32)


def peaks_json(path_or_array: Union[str, np.ndarray], sr: Optional[int] = None,
               samples_per_peak: Optional[int] = None, peaks_per_second: float = 100.0,
               decimals: int = 3) -> dict:
    """JSON-able waveform summary for the WebUI."""
    if isinstance(path_or_array, np.ndarray):
        if sr is None:
            raise ValueError("传入数组时必须提供采样率 sr")
        x = path_or_array
    else:
        from .io import load_audio

        x, sr = load_audio(path_or_array, mono=True)
    spp = int(samples_per_peak or max(1, round(sr / peaks_per_second)))
    mins, maxs = waveform_peaks(x, sr, spp)
    n = x.shape[-1]
    return {
        "sample_rate": int(sr),
        "samples_per_peak": spp,
        "num_samples": int(n),
        "duration_ms": int(round(n * 1000.0 / sr)),
        "mins": np.round(mins, decimals).tolist(),
        "maxs": np.round(maxs, decimals).tolist(),
    }
